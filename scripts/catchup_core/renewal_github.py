"""Read-only GitHub Actions evidence through gh; no rerun, dispatch or provider-content output."""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import datetime
from time import monotonic
from typing import Any

from catchup_core.renewal_ci import Gate, Job, Run, verify_ci
from catchup_core.renewal_store import RenewalError, _pairs
from catchup_core.renewal_workspace import Workspace


def _object(path: str, *, runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
            timeout: float = 30) -> dict[str, Any]:
    runner = runner or subprocess.run
    try:
        result = runner(
            ['gh', 'api', path],
            capture_output=True, check=False, timeout=timeout)
        if result.returncode or len(result.stdout) > 1024 * 1024:
            raise RenewalError('required GitHub evidence unavailable')
        value = json.loads(result.stdout, object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise RenewalError('required GitHub evidence unavailable') from None


def collect(workspace: Workspace, repository: str, commit: str, gates: tuple[Gate, ...],
            *, now: datetime, request_budget: int = 30,
            runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
            clock: Callable[[], datetime] | None = None,
            ) -> tuple[dict[str, Any], tuple[Run, ...]]:
    runner = runner or subprocess.run
    # Validate all identifiers before using them as API or Git object selectors.
    verify_ci(repository=repository, commit=commit, gates=gates, runs=(), now=now)
    if type(request_budget) is not int or not 1 <= request_budget <= 30:
        raise RenewalError('CI request budget is invalid')
    deadline = monotonic() + 900
    def remaining() -> float:
        value = deadline - monotonic()
        if value <= 0:
            raise RenewalError('foreground CI verification time limit reached')
        return min(value, 30)
    def fetch(path: str) -> dict[str, Any]:
        return _object(path, timeout=remaining())
    path = f'repos/{repository}/actions/runs?head_sha={commit}&branch=main&per_page=100'
    listing = fetch(path)
    try:
        workflows = {gate.workflow: gate for gate in gates}
        _identities(listing, workflows)
        values = listing['workflow_runs']
        records = []
        requests = 1
        for value in values:
            if value['path'] not in workflows:
                continue
            if (value['head_sha'] != commit or value['head_branch'] != 'main'
                    or value['head_repository']['full_name'] != repository):
                raise ValueError
            run_id, attempt = value['id'], value['run_attempt']
            if type(run_id) is not int or run_id < 1 or type(attempt) is not int or not 1 <= attempt <= 29:
                raise ValueError
            # Compare exact committed bytes; do not hash stripped CLI text.
            blob = runner(
                ['git', '-C', str(workspace.root), 'show', f"{commit}:{value['path']}"],
                                  capture_output=True, check=False, timeout=remaining())
            if blob.returncode or len(blob.stdout) > 1024 * 1024:
                raise ValueError
            definition = hashlib.sha256(blob.stdout).hexdigest()
            for number in range(1, attempt + 1):
                requests += 2
                if requests > request_budget:
                    raise RenewalError('CI evidence requires more than the request bound')
                current = fetch(f'repos/{repository}/actions/runs/{run_id}/attempts/{number}')
                jobs = fetch(f'repos/{repository}/actions/runs/{run_id}/attempts/{number}/jobs?per_page=100')
                if (current['head_sha'] != commit or current['run_attempt'] != number
                        or current['id'] != run_id or type(jobs['total_count']) is not int
                        or jobs['total_count'] > 100 or not isinstance(jobs['jobs'], list)
                        or len(jobs['jobs']) != jobs['total_count']
                        or any(job['run_id'] != run_id for job in jobs['jobs'])):
                    raise ValueError
                records.append(Run(repository, commit, 'refs/heads/' + current['head_branch'], current['event'],
                                   value['path'], definition, run_id, number,
                                   datetime.fromisoformat(current['created_at'].replace('Z', '+00:00')),
                                   current['status'], current.get('conclusion') or '',
                                   tuple(Job(job['name'], job['status'], job.get('conclusion') or '')
                                         for job in jobs['jobs'])))
        requests += 1
        if requests > request_budget:
            raise RenewalError('CI readback requires more than the request bound')
        final = fetch(path)
        if _identities(final, workflows) != _identities(listing, workflows):
            raise RenewalError('CI run or attempt changed during verification; resume in a user session')
        observed = clock() if clock is not None else now
        result = verify_ci(repository=repository, commit=commit, gates=gates, runs=tuple(records), now=observed)
        proof = {'status': result.status, 'reason': result.reason,
                 'requests': requests,
                 'runs': [{'id': run.run_id, 'attempt': run.attempt, 'workflow': run.workflow}
                          for run in records], 'observed_at': observed.isoformat()}
        return proof, tuple(records)
    except (KeyError, TypeError, ValueError, AttributeError, OSError, subprocess.TimeoutExpired):
        raise RenewalError('GitHub evidence is incomplete or malformed') from None


def _identities(listing: dict[str, Any], workflows: dict[str, Gate]) -> tuple[tuple[Any, ...], ...]:
    values = listing['workflow_runs']
    if (not isinstance(values, list) or type(listing['total_count']) is not int
            or len(values) != listing['total_count'] or len(values) > 100):
        raise RenewalError('CI readback is incomplete')
    return tuple(sorted((value['id'], value['run_attempt'], value['path'], value['head_sha'], value['head_branch'],
                         value['head_repository']['full_name']) for value in values if value['path'] in workflows))
