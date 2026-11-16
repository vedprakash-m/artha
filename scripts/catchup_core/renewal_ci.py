"""Verify normalized CI metadata from a separately trusted provider boundary.

These pure checks cannot authenticate GitHub, enroll gate policy or authorize a
release. The delivery adapter must obtain metadata and workflow bytes independently
and recheck the remote tip; caller-supplied JSON is not a trusted provider receipt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from catchup_core.renewal_inputs import InputRejected

_ERROR = "invalid renewal CI metadata"
_PENDING = frozenset({"queued", "in_progress", "pending", "waiting", "requested"})


@dataclass(frozen=True)
class Gate:
    workflow: str
    definition_sha256: str
    jobs: tuple[str, ...]


@dataclass(frozen=True)
class Job:
    name: str
    status: str
    conclusion: str


@dataclass(frozen=True)
class Run:
    repository: str
    commit: str
    ref: str
    event: str
    workflow: str
    definition_sha256: str
    run_id: int
    attempt: int
    created_at: datetime
    status: str
    conclusion: str
    jobs: tuple[Job, ...]


@dataclass(frozen=True)
class CIResult:
    status: Literal["passed", "pending", "held"]
    reason: str


def _digest(value: str, *, git: bool = False) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}" if git else
                                                     r"[0-9a-f]{64}", value))


def _stamp(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _name(value: str) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 200
            and all(32 <= ord(char) < 127 for char in value))


def _gate_valid(gate: Gate) -> bool:
    return (isinstance(gate, Gate) and isinstance(gate.workflow, str)
            and bool(re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", gate.workflow))
            and _digest(gate.definition_sha256) and isinstance(gate.jobs, tuple)
            and 0 < len(gate.jobs) <= 128 and all(_name(name) for name in gate.jobs)
            and len(set(gate.jobs)) == len(gate.jobs))


def _run_valid(run: Run) -> bool:
    return (isinstance(run, Run) and _stamp(run.created_at)
            and isinstance(run.status, str) and isinstance(run.conclusion, str)
            and type(run.run_id) is int and run.run_id > 0
            and type(run.attempt) is int and run.attempt > 0
            and isinstance(run.jobs, tuple) and len(run.jobs) <= 256
            and all(isinstance(job, Job) and _name(job.name) and isinstance(job.status, str)
                    and isinstance(job.conclusion, str) for job in run.jobs)
            and len({job.name for job in run.jobs}) == len(run.jobs))


def _check_gate(gate: Gate, runs: tuple[Run, ...], *, repository: str, commit: str,
                now: datetime, max_age_seconds: int) -> CIResult:
    selected = [run for run in runs if run.workflow == gate.workflow]
    if not selected:
        return CIResult("pending", "required workflow unavailable")
    if (any(not _run_valid(run) for run in selected)
            or len({(run.run_id, run.attempt) for run in selected}) != len(selected)):
        return CIResult("held", "invalid or conflicting workflow metadata")
    latest = max(selected, key=lambda run: (run.created_at, run.run_id, run.attempt))
    age = (now - latest.created_at).total_seconds()
    if (latest.repository != repository or latest.commit != commit
            or latest.ref != "refs/heads/main" or latest.event != "push"
            or latest.definition_sha256 != gate.definition_sha256
            or age < 0 or age > max_age_seconds):
        return CIResult("held", "workflow identity or freshness mismatch")
    if latest.status in _PENDING:
        return CIResult("pending", "required workflow incomplete")
    jobs = {job.name: job for job in latest.jobs}
    reason = ""
    if latest.status != "completed" or latest.conclusion != "success":
        reason = "required workflow did not pass"
    elif any(run.status == "completed" and run.conclusion != "success" for run in selected):
        reason = "earlier workflow failure requires disposition"
    elif any(name not in jobs or jobs[name].status != "completed" or jobs[name].conclusion != "success"
             for name in gate.jobs):
        reason = "required job missing or did not pass"
    return CIResult("held", reason) if reason else CIResult("passed", "required workflow verified")


def verify_ci(*, repository: str, commit: str, gates: tuple[Gate, ...], runs: tuple[Run, ...],
              now: datetime, max_age_seconds: int = 86400) -> CIResult:
    """Verify all required workflows, without selecting an older successful attempt.

    Known prior failures remain held until a separate approved disposition mechanism
    establishes their resolution. This API intentionally cannot invent that approval.
    """
    if (not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            or not _digest(commit, git=True) or not _stamp(now)
            or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400
            or not isinstance(gates, tuple) or not 0 < len(gates) <= 64
            or any(not _gate_valid(gate) for gate in gates)
            or len({gate.workflow for gate in gates}) != len(gates)
            or not isinstance(runs, tuple) or len(runs) > 1000
            or any(not isinstance(run, Run) for run in runs)):
        raise InputRejected(_ERROR)
    results = [_check_gate(gate, runs, repository=repository, commit=commit,
                           now=now, max_age_seconds=max_age_seconds) for gate in gates]
    for status in ("held", "pending"):
        for result in results:
            if result.status == status:
                return result
    return CIResult("passed", "all required CI verified")
