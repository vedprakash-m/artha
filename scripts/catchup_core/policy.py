"""Trusted local release and disclosure policy. Model output never supplies authority."""
from __future__ import annotations

import hashlib
import io
import json
import math
import socket
import sqlite3
import stat
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import closing, nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, TypeGuard, cast

from catchup_core.contracts import CatchupRequest, ExecutionMode
from catchup_core.crypto import EnvelopeCipher
from catchup_core.ledger import Ledger, PermissionDenied
from catchup_core.scorecard import Case, score


def validate_local_authority(request: CatchupRequest, config: Mapping[str, Any], *, viewer: str,
                             hostname: str | None = None) -> None:
    """Local safety controls authenticate authority independently of pilot quality evidence."""
    from dataclasses import replace  # noqa: PLC0415
    validate_release(replace(request, mode=ExecutionMode.SHADOW), config, viewer=viewer, domains=())
    if request.mode is ExecutionMode.SHADOW:
        return
    host = hostname or socket.gethostname().split(".")[0]
    release = config.get("release", {})
    authority = config.get("authority_host") or (release.get("authority_host") if isinstance(release, dict) else None)
    if authority != host or request.host != host:
        raise PermissionDenied("local safety control requires the designated authority host")


def validate_release(request: CatchupRequest, config: Mapping[str, Any], *, viewer: str,
                     domains: Sequence[str], hostname: str | None = None,
                     cohort: Mapping[str, str] | None = None) -> None:
    if request.actor != viewer:
        raise PermissionDenied("request actor does not match the configured local viewer")
    if request.channel not in ("cli", "vscode", "codex"):
        raise PermissionDenied("this surface is not authorized for personal evidence")
    if request.mode is ExecutionMode.SHADOW:
        return
    if config.get('cutover') is not True:
        raise PermissionDenied('authoritative cutover is disabled; use --shadow')
    _validate_release_evidence(request, config, domains=domains, hostname=hostname, cohort=cohort)


def _validate_release_evidence(request: CatchupRequest, config: Mapping[str, Any], *,  # noqa: PLR0912 - explicit release gates
                               domains: Sequence[str], hostname: str | None = None,
                               cohort: Mapping[str, str] | None = None) -> None:
    """Quality evidence for read models; this helper does not authorize execution or enable cutover."""
    host = hostname or socket.gethostname().split(".")[0]
    release = config.get("release")
    if not isinstance(release, dict):
        raise PermissionDenied("pilot release evidence is missing; use --shadow")
    if release.get("authority_host") != host or request.host != host:
        raise PermissionDenied("this machine is not the designated authority host")
    released_domains = release.get("domains")
    if (not isinstance(released_domains, list) or not released_domains
            or any(not isinstance(domain, str) or not domain.strip() for domain in released_domains)
            or len(set(released_domains)) != len(released_domains)):
        raise PermissionDenied("release needs distinct explicit Domain identifiers")
    if not set(domains) <= set(released_domains):
        raise PermissionDenied("requested Domain has not passed cutover")
    evidence = _evidence_field(release)
    if evidence == "slice_waiver" and list(domains) != released_domains:
        raise PermissionDenied("a slice release covers exactly its named Domains, in order")
    for field in (
        "owner_approved_at",
        "stage1_clearance",
        "rollback_snapshot",
        "independent_copy",
        "recovery_rehearsal",
        evidence,
    ):
        if not release.get(field):
            raise PermissionDenied(f"release needs {field}; use --shadow")
    if not isinstance(release["owner_approved_at"], str) or not release["owner_approved_at"].strip():
        raise PermissionDenied("release has not passed owner review")
    try:
        clearance = json.loads(_approved_file(release["stage1_clearance"], "Stage 1 clearance"))
    except (ValueError, TypeError):
        raise PermissionDenied("Stage 1 clearance must be valid JSON") from None
    if (not isinstance(clearance, Mapping) or clearance.get("passed") is not True
            or not isinstance(clearance.get("owner_signed_at"), str) or not clearance["owner_signed_at"].strip()):
        raise PermissionDenied("Stage 1 clearance has not passed owner review")
    minimum_precision = release.get("minimum_precision", 0.8)
    if not _number(minimum_precision, minimum=0, maximum=1):
        raise PermissionDenied("release minimum precision must be a finite probability")
    _validate_preservation_evidence(release)
    if evidence == "slice_waiver":
        _validate_slice_waiver(_approved_file(release[evidence], "slice waiver"), domains,
                               cohort or evaluation_cohort(config, domains))
        return
    data = _approved_file(release["scorecard"], "release scorecard")
    try:
        result = json.loads(data)
        if not isinstance(result, Mapping):
            raise PermissionDenied("release scorecard needs a JSON object")
        cases = _release_cases(result.get("evaluations"))
    except (ValueError, KeyError, TypeError) as exc:
        raise PermissionDenied("release scorecard needs labeled evaluations") from exc
    measured = score(cases)
    live_cases = sum(case.get("consented_live") is True for case in result["evaluations"])
    if (not measured.passes or measured.precision < minimum_precision
            or not _count(result.get("cases"), positive=True) or result["cases"] != measured.cases
            or not _count(result.get("live_cases"), positive=True) or result["live_cases"] != live_cases
            or not isinstance(result.get("owner_signed_at"), str) or not result["owner_signed_at"].strip()):
        raise PermissionDenied("release scorecard lacks passing consented live evidence")
    _validate_domains(result, domains)
    _validate_evaluation_cohort(result, cohort or evaluation_cohort(config, domains))


def evaluation_cohort(config: Mapping[str, Any], domains: Sequence[str], *,
                      extractor_command: str | None = None, workspace: Path | None = None) -> dict[str, str]:
    """Pin evaluation inputs independently of release approval and the cutover switch."""
    from catchup_core import CORE_CONTRACT_VERSION  # noqa: PLC0415
    from catchup_core.runtime import _PACKS, _PROMPTS, _implementation_fingerprint  # noqa: PLC0415
    root = Path(__file__).resolve().parents[2]
    package = Path(__file__).parent
    def digest(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    def files(paths: Sequence[Path]) -> str:
        return digest({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    workspace_inputs: dict[str, str | None] = {}
    for name in ('user_profile.yaml', 'connectors.yaml'):
        try:
            with ((workspace or root) / 'config' / name).open('rb') as handle:
                data = handle.read(_MAX_RELEASE_JSON + 1)
        except FileNotFoundError:
            workspace_inputs[name] = None
        except OSError:
            raise PermissionDenied('evaluation workspace inputs cannot be verified') from None
        else:
            if len(data) > _MAX_RELEASE_JSON:
                raise PermissionDenied('evaluation workspace inputs exceed the size bound')
            workspace_inputs[name] = hashlib.sha256(data).hexdigest()
    command = extractor_command or config.get('extractor_cmd')
    identity = config.get('model_identity')
    model = 'unverified'
    if isinstance(identity, dict) and all(isinstance(identity.get(key), str) and identity[key].strip()
                                         for key in ('provider', 'model', 'revision', 'retention_policy')):
        model = digest(identity)
    if command:
        # The command boundary permits only the bundled inert extractor; its exact bytes are pinned here too.
        model = digest({'provider': 'local', 'model': 'pattern-extractor',
                        'revision': files([package / 'extractor_cmd.py']), 'command': command})
    return {'implementation': _implementation_fingerprint(root),
            'prompts': files(sorted(_PROMPTS.glob('*.md'))),
            'packs': files([_PACKS / f'{domain}.yaml' for domain in sorted(domains)]),
            'configuration': digest({'policy': {k: v for k, v in config.items() if k not in {'release', 'cutover'}},
                                     'workspace_inputs': workspace_inputs}),
            'model': model, 'contract': CORE_CONTRACT_VERSION}


SLICE_WAIVABLE = ("cross_domain_and_goal_cases", "document_field_accuracy", "held_out_labeling",
                  "other_platform_observations", "windows_work_recovery", "workload_scenarios")
SLICE_MIN_WINDOW_DAYS = 14


def _evidence_field(release: Mapping[str, Any]) -> str:
    """Which approved artifact carries the quality evidence: the v4 scorecard or the named-waiver slice record."""
    profile = release.get("profile", "full")
    if profile not in ("full", "slice"):
        raise PermissionDenied("release profile must be full or slice")
    return "slice_waiver" if profile == "slice" else "scorecard"


def _validate_slice_waiver(data: bytes, domains: Sequence[str], current: Mapping[str, str]) -> None:
    """The first-slice record: every waived gate is named and declared not run; kept gates are never waivable."""
    try:
        waiver = json.loads(data)
    except ValueError:
        raise PermissionDenied("slice waiver must be valid JSON") from None
    window = waiver.get("observation_window_days") if isinstance(waiver, Mapping) else None
    if (not isinstance(waiver, Mapping) or waiver.get("schema") != "artha.release.slice.v1"
            or not domains or waiver.get("domains") != list(domains)
            or not isinstance(waiver.get("waived"), list) or sorted(waiver["waived"]) != list(SLICE_WAIVABLE)
            or waiver.get("not_run") is not True
            or not isinstance(waiver.get("owner_signed_at"), str) or not waiver["owner_signed_at"].strip()
            or type(window) is not int or window < SLICE_MIN_WINDOW_DAYS):
        raise PermissionDenied("slice waiver must name exactly the waivable gates, declare them not run, "
                               "be owner-signed and commit to the observation window")
    if current.get("model") in (None, "unverified") or waiver.get("cohort") != current:
        raise PermissionDenied("slice waiver cohort differs from the current code, policy, packs or pinned model")


def _validate_evaluation_cohort(result: Mapping[str, Any], current: Mapping[str, str]) -> None:
    if current.get('model') in (None, 'unverified') or result.get('cohort') != current:
        raise PermissionDenied('release evaluation cohort differs from the current code, policy, packs or pinned model')
    for case in result['evaluations']:
        if case.get('cohort') != current or not isinstance(case.get('catchup_id'), str) or not case['catchup_id']:
            raise PermissionDenied('release case cohort is missing, mixed or lacks returned Catch-up evidence')
    for measurement in result.get('measurements', {}).values():
        if not isinstance(measurement, dict) or measurement.get('cohort') != current:
            raise PermissionDenied('release measurement cohort differs from the evaluated implementation')


def _validate_domains(result: Mapping[str, Any], domains: Sequence[str]) -> None:
    if (not isinstance(result, Mapping) or result.get("schema") != "artha.release.v4"
            or not isinstance(result.get("bounds"), Mapping)
            or not isinstance(result.get("measurements"), Mapping)):
        raise PermissionDenied("release needs a versioned per-Domain scorecard")
    _validate_acceptance_corpus(result)
    _validate_document_accuracy(result)
    for domain in domains:
        bounds = result["bounds"].get(domain, {})
        cases = [c for c in result["evaluations"] if c.get("domain") == domain]
        required = ("minimum_precision", "minimum_recall", "max_source_age_hours", "source_precedence",
                    "normal_workload", "maximum_review_minutes", "maximum_p95_seconds", "maximum_monthly_usd")
        if (not isinstance(bounds, Mapping) or not cases
                or any(bounds.get(k) in (None, "", [], {}) for k in required)):
            raise PermissionDenied(f"{domain} needs cases and concrete release bounds")
        _validate_cohorts(cases, domain)
        _validate_bounds(bounds, domain)
        evaluated = score(_release_cases(cases))
        if (not evaluated.passes or evaluated.precision < float(bounds["minimum_precision"])
                or evaluated.recall < float(bounds["minimum_recall"])):
            raise PermissionDenied(f"{domain} failed precision/recall or safety gates")
        measurements = result.get("measurements", {}).get(domain, {})
        _validate_workloads(result, measurements, bounds, domain)
        from catchup_core.domain_pack import load_pack_file  # noqa: PLC0415
        pack = load_pack_file(Path(__file__).parent / "packs" / f"{domain}.yaml")
        precedence = {name: list(predicate.authority) for name, predicate in pack.predicates.items()}
        if bounds["source_precedence"] != precedence:
            raise PermissionDenied(f"{domain} release source precedence differs from the runtime pack")
        if measurements.get("workload") != bounds["normal_workload"]:
            raise PermissionDenied(f"{domain} workload measurement does not match the approved workload")
        source_ages = measurements.get("source_age_hours", {})
        if not isinstance(source_ages, Mapping):
            raise PermissionDenied(f"{domain} lacks scoped freshness measurements")
        for source in pack.sources:
            if source.required:
                age = source_ages.get(source.connector)
                if not _number(age, minimum=0,
                               maximum=min(source.freshness_hours, float(bounds["max_source_age_hours"]))):
                    raise PermissionDenied(f"{domain} lacks passing scoped freshness measurements")
        for metric, bound in (("review_minutes", "maximum_review_minutes"), ("p95_seconds", "maximum_p95_seconds"),
                              ("monthly_usd", "maximum_monthly_usd")):
            value = measurements.get(metric)
            if not _number(value, minimum=0, maximum=float(bounds[bound])):
                raise PermissionDenied(f"{domain} has no passing {metric} measurement")


_RELEASE_WORKLOADS = ('normal_cold', 'normal_warm', 'long_gap', 'provider_reset',
                      'retention_1y', 'retention_2y', 'digest_limited')


def _count(value: Any, *, positive: bool = False) -> TypeGuard[int]:
    return type(value) is int and value >= (1 if positive else 0)


def _ids(value: Any, *, nonempty: bool = False) -> set[str]:
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(item, str) or not item.strip() or len(item) > 1024 for item in value)
            or len(set(value)) != len(value)):
        raise PermissionDenied('release corpus requires explicit distinct obligation/thread identities')
    return set(value)


def _release_cases(evaluations: Any) -> list[Case]:
    if not isinstance(evaluations, list) or not evaluations:
        raise PermissionDenied('release scorecard needs labeled evaluations')
    cases = []
    for case in evaluations:
        if (not isinstance(case, Mapping) or not isinstance(case.get('case_id'), str)
                or not case['case_id'].strip()):
            raise PermissionDenied('release scorecard needs labeled case objects')
        for flag in ('quiet_claim', 'coverage_complete', 'citations_resolve',
                     'consented_live', 'reviewed', 'source_negative_reviewed'):
            if flag in case and type(case[flag]) is not bool:
                raise PermissionDenied(f'release case {flag} must be a boolean')
        cases.append(Case(case['case_id'], frozenset(_ids(case.get('expected'))),
                          frozenset(_ids(case.get('surfaced'))), case.get('quiet_claim', False),
                          case.get('coverage_complete', True), case.get('citations_resolve', True),
                          frozenset(_ids(case.get('incumbent_surfaced', [])))))
    return cases


def _release_time(value: Any) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError('timestamp is missing')
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('timezone is missing')
        return stamp
    except ValueError:
        raise PermissionDenied('release corpus requires explicit dated evidence with timezones') from None


def _validate_acceptance_corpus(result: Mapping[str, Any]) -> None:
    _release_cases(result.get('evaluations'))
    corpus = result.get('corpus')
    if (not isinstance(corpus, Mapping) or corpus.get('schema') != 'artha.release-corpus.v1'
            or corpus.get('held_out') is not True or corpus.get('independently_labeled') is not True
            or not isinstance(corpus.get('id'), str) or not corpus['id']
            or corpus.get('cohort') != result.get('cohort')):
        raise PermissionDenied('release requires an independently labeled held-out corpus bound to this cohort')
    development_obligations = _ids(corpus.get('development_obligation_ids'))
    development_threads = _ids(corpus.get('development_thread_ids'))
    development_end = _release_time(corpus.get('development_end_at'))
    for case in result['evaluations']:
        if case.get('split') != 'held_out' or case.get('corpus_id') != corpus['id']:
            raise PermissionDenied('release case is not in the declared held-out corpus')
        obligations = _ids(case.get('obligation_ids'), nonempty=bool(case.get('expected')))
        threads = _ids(case.get('thread_ids'), nonempty=True)
        if obligations & development_obligations or threads & development_threads:
            raise PermissionDenied('release corpus leaks a development obligation or thread')
        as_of = _release_time(case.get('as_of'))
        if as_of <= development_end or _release_time(case.get('latest_evidence_at')) > as_of:
            raise PermissionDenied('release corpus violates chronological split or includes future evidence')


def _validate_document_accuracy(result: Mapping[str, Any]) -> None:
    fields = result.get('document_fields')
    if (not isinstance(fields, Mapping) or fields.get('cohort') != result.get('cohort')
            or fields.get('corpus_id') != result['corpus']['id']):
        raise PermissionDenied('document field measurements require the independent held-out corpus and current cohort')
    for name in ('dates', 'amounts', 'names', 'false_confirmations'):
        row = fields.get(name)
        if not isinstance(row, Mapping):
            raise PermissionDenied(f'document {name} lacks measured numerator and denominator')
        total = row.get('total')
        numerator = row.get('false' if name == 'false_confirmations' else 'correct')
        if not _count(total, positive=True) or not _count(numerator) or numerator > total:
            raise PermissionDenied(f'document {name} requires exact nonzero measured counts')
        if name == 'false_confirmations':
            if numerator * 200 > total:
                raise PermissionDenied('document false-confirm rate exceeds the approved 0.5% ceiling')
        elif numerator * 100 < total * 99:
            raise PermissionDenied(f'document {name} accuracy is below the approved 99% floor; route to review')


def _validate_workloads(  # noqa: PLR0912 - explicit scenario requirements
                        result: Mapping[str, Any], measurements: Any, bounds: Mapping[str, Any],
                        domain: str) -> None:
    if not isinstance(measurements, Mapping):
        raise PermissionDenied(f'{domain} lacks workload measurements')
    total, complete = measurements.get('total_runs'), measurements.get('completed_runs')
    if (not _count(total, positive=True) or not _count(complete) or complete > total
            or complete * 100 < total * 95):
        raise PermissionDenied(f'{domain} needs measured complete/total run counts meeting the 95% completion goal')
    scenarios = measurements.get('workloads')
    if not isinstance(scenarios, Mapping):
        raise PermissionDenied(f'{domain} lacks the required workload scenarios')
    for name in _RELEASE_WORKLOADS:
        row = scenarios.get(name)
        if (not isinstance(row, Mapping) or row.get('cohort') != result.get('cohort')
                or row.get('passed') is not True or not _count(row.get('runs'), positive=True)
                or not isinstance(row.get('reference_machine'), str) or not row['reference_machine'].strip()
                or not isinstance(row.get('workload'), Mapping) or not row['workload']
                or not _number(row.get('p95_seconds'), minimum=0)):
            raise PermissionDenied(f'{domain} lacks passing, dated-cohort {name} workload evidence')
        workload = row['workload']
        if name in ('normal_cold', 'normal_warm'):
            if workload != bounds['normal_workload'] or row['p95_seconds'] > float(bounds['maximum_p95_seconds']):
                raise PermissionDenied(f'{domain} {name} differs from the approved normal workload or latency limit')
        elif name == 'digest_limited':
            if (row['p95_seconds'] >= 10 or row.get('fetch_skipped') is not True
                    or row.get('vault_skipped') is not True):
                raise PermissionDenied(f'{domain} limited Digest must skip fetch/vault and finish below ten seconds')
        elif name == 'long_gap':
            if (not _count(workload.get('gap_days'), positive=True) or row.get('fair_progress') is not True
                    or row.get('incomplete_interpretation_safe') is not True):
                raise PermissionDenied(f'{domain} long-gap evidence lacks progress or incomplete-processing safety')
        elif name == 'provider_reset':
            if (workload.get('provider_reset') is not True or row.get('verified_alerts_preserved') is not True
                    or row.get('coverage_degradation_reported') is not True):
                raise PermissionDenied(f'{domain} reset evidence lacks preserved Alerts or coverage degradation')
        elif (type(workload.get('retention_years')) is not int
                or workload.get('retention_years') != (1 if name == 'retention_1y' else 2)
                or row.get('query_verified') is not True or row.get('restore_verified') is not True
                or row.get('plaintext_fallback') is not False):
            raise PermissionDenied(f'{domain} retention-volume evidence lacks verified queries, restore or privacy')


def _validate_bounds(bounds: Mapping[str, Any], domain: str) -> None:
    for key in ("minimum_precision", "minimum_recall"):
        if not _number(bounds[key], minimum=0, maximum=1):
            raise PermissionDenied(f"{domain} has invalid {key}")
    for key in ("max_source_age_hours", "maximum_review_minutes", "maximum_p95_seconds", "maximum_monthly_usd"):
        if not _number(bounds[key], minimum=0) or bounds[key] == 0:
            raise PermissionDenied(f"{domain} has invalid {key}")


def _number(value: Any, *, minimum: float, maximum: float = math.inf) -> bool:
    return (isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
            and minimum <= value <= maximum)


def _validate_cohorts(cases: Sequence[Mapping[str, Any]], domain: str) -> None:
    identities = [c.get("case_id") for c in cases]
    if (any(not isinstance(cid, str) or not cid for cid in identities)
            or len(set(identities)) != len(identities)):
        raise PermissionDenied(f"{domain} needs distinct labeled case identities")
    reviewed = [c for c in cases if c.get("consented_live") is True and c.get("reviewed") is True]
    if any(not any(c.get("severity") == severity and c.get("expected") for c in reviewed)
           for severity in ("P1", "P2")):
        raise PermissionDenied(f"{domain} lacks reviewed positive P1/P2 outcomes")
    if not any(c.get("case_type") == "negative" and c.get("source_negative_reviewed") is True
               and c.get("expected") == [] for c in reviewed):
        raise PermissionDenied(f"{domain} lacks reviewed source-negative outcomes")
    if sum(c.get("case_type") == "cross_domain" and bool(c.get("expected")) for c in reviewed) < 2:
        raise PermissionDenied(f"{domain} needs two reviewed cross-Domain outcomes")
    if not any(c.get("case_type") == "goal_correction" and c.get("before") is not None
               and c.get("after") is not None and c["before"] != c["after"] and c.get("expected") for c in reviewed):
        raise PermissionDenied(f"{domain} lacks a correction-sensitive Goal outcome")


_MAX_RELEASE_JSON = 16 << 20
_PRESERVATION_SCHEMA = 'artha.installation-preservation.v1'


def _approved_path(record: Any, label: str) -> Path:
    if (not isinstance(record, dict) or not isinstance(record.get('path'), str)
            or not record['path'] or any(ord(char) < 32 for char in record['path'])
            or not isinstance(record.get('sha256'), str) or len(record['sha256']) != 64
            or any(c not in '0123456789abcdef' for c in record['sha256'])):
        raise PermissionDenied(f'{label} needs a path and approved digest')
    return Path(record['path']).expanduser()


def _approved_file(record: Any, label: str) -> bytes:
    path = _approved_path(record, label)
    try:
        with path.open('rb') as handle:
            data = handle.read(_MAX_RELEASE_JSON + 1)
    except OSError:
        raise PermissionDenied(f'{label} cannot be verified') from None
    if len(data) > _MAX_RELEASE_JSON:
        raise PermissionDenied(f'{label} exceeds its size limit')
    if hashlib.sha256(data).hexdigest() != record['sha256']:
        raise PermissionDenied(f'{label} differs from approved bytes')
    return data


class _DigestingReader:
    """Hash exactly the bytes consumed by the authenticated reader over its already-open file."""
    def __init__(self, source: BinaryIO):
        self.source = source
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self.source.read(size)
        self.digest.update(data)
        return data


def _check_installation_scope(manifest: Mapping[str, Any]) -> None:
    context = manifest.get('context', {})
    scope = context.get('preservation') if isinstance(context, Mapping) else None
    if (not isinstance(scope, Mapping) or scope.get('schema') != _PRESERVATION_SCHEMA
            or scope.get('repository_root') != 'repo' or scope.get('runtime_root') != 'local'
            or scope.get('inventory') != 'recursive-fail-closed'):
        raise ValueError('checkpoint does not declare complete installation preservation')
    roots = scope.get('root_names')
    if (not isinstance(roots, list) or any(not isinstance(root, str) or not root for root in roots)
            or len(set(roots)) != len(roots) or not {'repo', 'local'} <= set(roots)):
        raise ValueError('checkpoint installation roots are incomplete or ambiguous')
    entries = manifest['entries']
    paths = {entry['path'] for entry in entries}
    if (any(path.split('/')[0] not in roots for path in paths)
            or 'repo/config/catchup_core.yaml' not in paths
            or not any(path.startswith('repo/state/') for path in paths)
            or not any(entry['kind'] == 'sqlite' and entry['path'] in {
                'local/catchup_core/ledger.db', 'local/catchup_core/shadow/ledger.db'} for entry in entries)):
        raise ValueError('checkpoint lacks repository configuration, State Files or runtime Ledger')
    required_escrow = {'age-key/artha', 'artha-ledger/key.1', 'artha-ledger-ids/key.1'}
    if not required_escrow <= set(manifest.get('escrow_items') or ()):
        raise ValueError('checkpoint lacks required recovery escrow items')


_VERIFY_IN_MEMORY = 8 << 20  # a member database above this is checked from a private temporary file, not memory


class _DatabaseCheck:
    """Collects one database member as it streams past, then runs SQLite's integrity and key checks on it.

    Small images stay in memory.  A larger one spills to an owner-only temporary file that is deleted before
    the check returns, so memory never holds more than the spill threshold.
    """

    def __init__(self) -> None:
        self.size = 0
        self.memory: bytearray | None = bytearray()
        self.spill: Any = None

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.memory is not None and self.size > _VERIFY_IN_MEMORY:
            self.spill = tempfile.NamedTemporaryFile(prefix='artha-verify-', delete=False)  # noqa: SIM115 - closed and unlinked in discard(); mode 0600
            self.spill.write(self.memory)
            self.memory = None
        if self.spill is not None:
            self.spill.write(chunk)
        elif self.memory is not None:
            self.memory.extend(chunk)

    def check(self) -> None:
        try:
            if self.spill is None:
                with closing(sqlite3.connect(':memory:')) as db:
                    db.deserialize(self.memory)  # type: ignore[arg-type]  # the bytearray: no second copy
                    self._verify(db)
                return
            self.spill.close()
            uri = Path(self.spill.name).resolve().as_uri() + '?mode=ro&immutable=1'
            with closing(sqlite3.connect(uri, uri=True)) as db:
                self._verify(db)
        finally:
            self.discard()

    def discard(self) -> None:
        self.memory = None
        if self.spill is not None:
            self.spill.close()
            Path(self.spill.name).unlink(missing_ok=True)
            self.spill = None

    @staticmethod
    def _verify(db: sqlite3.Connection) -> None:
        if (db.execute('PRAGMA integrity_check').fetchone() != ('ok',)
                or db.execute('PRAGMA foreign_key_check').fetchone()):
            raise ValueError('checkpoint database integrity failed')


def _scan_preservation(source: BinaryIO, cipher: EnvelopeCipher) -> dict[str, Any]:
    from catchup_core import checkpoint  # noqa: PLC0415
    current: _DatabaseCheck | None = None

    def finish() -> None:
        nonlocal current
        if current is not None:
            finished, current = current, None
            finished.check()

    def opened(header: dict[str, Any]) -> None:
        nonlocal current
        finish()
        current = _DatabaseCheck() if header['kind'] == 'sqlite' else None

    def collect(_header: dict[str, Any], chunk: bytes) -> None:
        if current is not None:
            current.write(chunk)

    try:
        manifest = checkpoint._scan(source, cipher, collect, opened)
        finish()
    finally:
        if current is not None:
            current.discard()
    if manifest.get('profile') != 'personal':
        raise ValueError('checkpoint is not a personal installation')
    _check_installation_scope(manifest)
    return manifest


def verify_preservation_checkpoint(source: bytes | Path | BinaryIO, cipher: EnvelopeCipher | None = None, *,
                                   expected_sha256: str | None = None) -> dict[str, Any]:
    """Verify approved ACK2 bytes on one pinned handle; memory is bounded by one SQLite image."""
    from catchup_core import checkpoint  # noqa: PLC0415
    try:
        context = closing(io.BytesIO(source)) if isinstance(source, bytes) else (
            source.open('rb') if isinstance(source, Path) else nullcontext(source))
        with context as handle:
            handle.seek(0)
            if handle.read(3) == b'AE1':
                raise PermissionDenied('selective rollback snapshot cannot satisfy full-state preservation; '
                                       'use an ACK2 full-state checkpoint')
            handle.seek(0)
            generation, escrow = checkpoint._read_head(handle)
            if not escrow.startswith(checkpoint._ESCROW_MAGIC) or len(escrow) < 31:
                raise ValueError('checkpoint recovery escrow is missing or malformed')
            handle.seek(0)
            reader = _DigestingReader(handle)
            if cipher is None:
                while reader.read(1 << 20):
                    pass
                manifest: dict[str, Any] = {'generation': generation}
            else:
                manifest = _scan_preservation(cast(BinaryIO, reader), cipher)
            if expected_sha256 is not None and reader.digest.hexdigest() != expected_sha256:
                raise PermissionDenied('rollback snapshot differs from approved bytes')
            return manifest
    except PermissionDenied:
        raise
    except (OSError, ValueError, TypeError, sqlite3.Error, checkpoint.CheckpointError):
        raise PermissionDenied('rollback snapshot could not be decrypted and verified') from None


def _approved_digest(record: Any, label: str) -> str:
    path = _approved_path(record, label)
    digest = hashlib.sha256()
    try:
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b''):
                digest.update(chunk)
    except OSError:
        raise PermissionDenied(f'{label} cannot be verified') from None
    if digest.hexdigest() != record['sha256']:
        raise PermissionDenied(f'{label} differs from approved bytes')
    return digest.hexdigest()


def _review_time(value: Any) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def _independent_location(snapshot: Path, copy: Path, record: Mapping[str, Any], digest: str) -> None:
    try:
        original, duplicate = snapshot.stat(), copy.stat()
    except OSError:
        raise PermissionDenied('independent checkpoint copy is unavailable') from None
    if (not stat.S_ISREG(original.st_mode) or not stat.S_ISREG(duplicate.st_mode)
            or (original.st_dev, original.st_ino) == (duplicate.st_dev, duplicate.st_ino)):
        raise PermissionDenied('independent checkpoint copy cannot alias the original')
    if original.st_dev != duplicate.st_dev:
        return
    if (record.get('failure_domain') != 'cloud' or not _review_time(record.get('owner_verified_at'))
            or record.get('recovered_sha256') != digest):
        raise PermissionDenied('independent checkpoint copy needs a separate device or reviewed cloud recovery proof')


def _recovery_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate recovery acceptance field')
        result[key] = value
    return result


def _recovery_fields(value: Any, fields: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PermissionDenied('recovery acceptance requires the exact versioned observation schema')
    return value


def _recovery_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in '0123456789abcdef' for char in value)


def _validate_recovery_observation(value: Any, *, profile: str, os_name: str | None,
                                 digest: str, generation: str, reviewed_at: datetime) -> None:
    fields = {'profile', 'checkpoint_sha256', 'checkpoint_generation', 'observed_at',
              'owner_reviewed_at', 'outcome', 'privacy'}
    fields |= {'os', 'native'} if os_name else {'source', 'recovered_sha256'}
    row = _recovery_fields(value, fields)
    if (row['profile'] != profile or row['checkpoint_sha256'] != digest
            or row['checkpoint_generation'] != generation or row['outcome'] != 'success'
            or not _review_time(row['observed_at']) or not _review_time(row['owner_reviewed_at'])):
        raise PermissionDenied('recovery observation lacks dated success bound to its profile and checkpoint')
    observed, reviewed = _release_time(row['observed_at']), _release_time(row['owner_reviewed_at'])
    if not observed <= reviewed <= reviewed_at:
        raise PermissionDenied('recovery observation must precede its owner review and acceptance')
    if os_name:
        if row['os'] != os_name or row['native'] is not True:
            raise PermissionDenied('recovery requires explicit native macOS personal and Windows Work observations')
    elif row['source'] != 'onedrive_web' or row['recovered_sha256'] != digest:
        raise PermissionDenied('recovery requires the actual recovered personal OneDrive web-copy digest')
    privacy = _recovery_fields(row['privacy'], {'current_history_verified', 'replay_verified', 'history_sha256'})
    if (privacy['current_history_verified'] is not True or privacy['replay_verified'] is not True
            or not _recovery_digest(privacy['history_sha256'])):
        raise PermissionDenied('recovery requires explicit verified current privacy continuity and replay')


def _validate_recovery_acceptance(rehearsal: Any, release: Mapping[str, Any],
                                  head: Mapping[str, Any], generation: str) -> None:
    sliced = _evidence_field(release) == 'slice_waiver'   # Windows Work recovery is a named waiver (SR-09)
    receipt = _recovery_fields(rehearsal, {'schema', 'passed', 'reconciled', 'owner_signed_at',
        'checkpoint_sha256', 'checkpoint_generation', 'cohort', 'observations'}
        | (set() if sliced else {'work_checkpoint'}))
    digest = release['rollback_snapshot']['sha256']
    if (receipt['schema'] != ('artha.recovery-rehearsal.slice.v1' if sliced else 'artha.recovery-rehearsal.v2')
            or receipt['passed'] is not True
            or receipt['reconciled'] is not True or not _review_time(receipt['owner_signed_at'])
            or receipt['checkpoint_sha256'] != digest or receipt['checkpoint_generation'] != generation
            or generation != head['generation']):
        raise PermissionDenied('recovery acceptance is unreviewed or not bound to the preservation generation')
    cohort = receipt['cohort']
    if (not isinstance(cohort, dict) or not cohort
            or any(not isinstance(key, str) or not key or not isinstance(value, str) or not value
                   for key, value in cohort.items())):
        raise PermissionDenied('recovery acceptance requires an explicit cohort')
    evidence = _evidence_field(release)
    if release.get(evidence):
        approved = json.loads(_approved_file(release[evidence], 'release evidence'))
        if cohort != approved.get('cohort'):
            raise PermissionDenied('recovery acceptance cohort differs from the approved release cohort')
    context = head.get('context', {})
    if isinstance(context, Mapping) and 'cohort' in context and context['cohort'] != cohort:
        raise PermissionDenied('recovery acceptance cohort differs from the preservation cohort')
    steps: list[tuple[str, str, str | None, str, str]] = [
        ('mac_personal', 'personal', 'macos', digest, generation),
        ('personal_cloud_copy', 'personal', None, digest, generation)]
    if not sliced:
        work = _recovery_fields(receipt['work_checkpoint'], {'path', 'sha256', 'profile', 'generation'})
        if work['profile'] != 'work' or work['sha256'] == digest:
            raise PermissionDenied('recovery requires a separate approved Work-profile checkpoint, '
                                   'never personal authority')
        work_head = verify_preservation_checkpoint(_approved_path(work, 'Work recovery checkpoint'),
                                                   expected_sha256=work['sha256'])
        if work['generation'] != work_head['generation']:
            raise PermissionDenied('recovery Work checkpoint generation differs from its approved artifact')
        steps.insert(1, ('windows_work', 'work', 'windows', work['sha256'], work['generation']))
    observations = _recovery_fields(receipt['observations'], {name for name, *_ in steps})
    reviewed_at = _release_time(receipt['owner_signed_at'])
    for name, profile, os_name, bound_digest, bound_generation in steps:
        _validate_recovery_observation(observations[name], profile=profile, os_name=os_name,
            digest=bound_digest, generation=bound_generation, reviewed_at=reviewed_at)
    if (observations['mac_personal']['privacy']['history_sha256']
            != observations['personal_cloud_copy']['privacy']['history_sha256']):
        raise PermissionDenied('recovery personal cloud copy must preserve the reviewed current privacy history')
    if 'created_at' in head and any(
            _release_time(observations[name]['observed_at']) < _release_time(head['created_at'])
            for name in ('mac_personal', 'personal_cloud_copy')):
        raise PermissionDenied('recovery observation predates the authenticated preservation checkpoint')


def _validate_preservation_evidence(release: Mapping[str, Any], *, expected_generation: str | None = None,
                                    verified_checkpoint: Mapping[str, Any] | None = None) -> None:
    for field, label in (('rollback_snapshot', 'rollback snapshot'),
                         ('independent_copy', 'independent checkpoint copy'),
                         ('recovery_rehearsal', 'recovery rehearsal')):
        if not release.get(field):
            raise PermissionDenied(f'release needs {label}; use --shadow')
    snapshot_record, copy_record = release['rollback_snapshot'], release['independent_copy']
    snapshot = _approved_path(snapshot_record, 'rollback snapshot')
    copy = _approved_path(copy_record, 'independent checkpoint copy')
    head = (verified_checkpoint if verified_checkpoint is not None else
            verify_preservation_checkpoint(snapshot, expected_sha256=snapshot_record['sha256']))
    if _approved_digest(copy_record, 'independent checkpoint copy') != snapshot_record['sha256']:
        raise PermissionDenied('independent checkpoint copy digest does not match rollback snapshot')
    _independent_location(snapshot, copy, copy_record, snapshot_record['sha256'])
    try:
        rehearsal = json.loads(_approved_file(release['recovery_rehearsal'], 'recovery rehearsal'),
                               object_pairs_hook=_recovery_object)
    except (ValueError, TypeError):
        raise PermissionDenied('recovery rehearsal must be valid JSON') from None
    generation = expected_generation or str(head['generation'])
    _validate_recovery_acceptance(rehearsal, release, head, generation)


def verify_rollback(config: Mapping[str, Any], cipher: EnvelopeCipher) -> dict[str, Any]:
    """Release needs approved, decryptable full installation preservation, not a selective snapshot."""
    try:
        record = config['release']['rollback_snapshot']
    except (KeyError, TypeError):
        raise PermissionDenied('rollback snapshot could not be decrypted and verified') from None
    return verify_preservation_checkpoint(_approved_path(record, 'rollback snapshot'), cipher,
                                          expected_sha256=record['sha256'])


class ReleaseEvidenceGuard:
    """Cache preservation only while all approved records and trusted local file revisions remain unchanged."""
    def __init__(self, config: Mapping[str, Any], cipher: EnvelopeCipher, cohort: Mapping[str, str] | None = None):
        self._config = config
        self._cipher = cipher
        self._cohort = cohort or evaluation_cohort(config, config['release']['domains'])
        self._verified: tuple[tuple[str, str, tuple[int, int, int, int, int]], ...] | None = None

    @staticmethod
    def _revision(path: Path) -> tuple[int, int, int, int, int]:
        try:
            metadata = path.stat()
        except OSError:
            raise PermissionDenied('preservation artifact is unavailable') from None
        return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns

    def _identity(self, release: Mapping[str, Any]) -> tuple[tuple[str, str, tuple[int, int, int, int, int]], ...]:
        records = []
        try:
            receipt = json.loads(_approved_file(release.get('recovery_rehearsal'), 'recovery rehearsal'),
                                 object_pairs_hook=_recovery_object)
        except (ValueError, TypeError):
            raise PermissionDenied('recovery rehearsal must be valid JSON') from None
        names = ['rollback_snapshot', 'independent_copy', 'recovery_rehearsal']
        inputs = [release.get(field) for field in names]
        if _evidence_field(release) != 'slice_waiver':   # a slice receipt carries no Work checkpoint
            names.append('work_checkpoint')
            inputs.append(receipt.get('work_checkpoint') if isinstance(receipt, dict) else None)
        for field, record in zip(names, inputs, strict=True):
            path = _approved_path(record, field)
            try:
                approved_record = json.dumps(record, sort_keys=True)
            except (TypeError, ValueError):
                raise PermissionDenied('preservation artifact metadata is invalid') from None
            records.append((str(path.resolve()), approved_record, self._revision(path)))
        return tuple(records)

    def validate(self) -> None:
        release = self._config['release']
        evidence = _evidence_field(release)
        for field, label in (('stage1_clearance', 'Stage 1 clearance'), (evidence, 'release evidence')):
            data = _approved_file(release[field], label)
            if field == 'scorecard':
                _validate_evaluation_cohort(json.loads(data), self._cohort)
            elif field == 'slice_waiver':
                _validate_slice_waiver(data, release['domains'], self._cohort)
        identity = self._identity(release)
        if identity != self._verified:
            manifest = verify_rollback(self._config, self._cipher)
            _validate_preservation_evidence(release, verified_checkpoint=manifest)
            if identity != self._identity(release):
                raise PermissionDenied('preservation artifacts changed during verification; retry')
            self._verified = identity


def authorize_source(ledger: Ledger, subjects: Sequence[str], required: frozenset[str],
                     scope: str, viewer: str, *, operations: Sequence[str] = ("ingest", "disclose", "view")) -> None:
    for subject in subjects:
        if ledger._known_subjects is not None and subject not in ledger._known_subjects:
            raise PermissionDenied("source has an unresolved person; resolve identity before access")
        if subject in required:
            for operation in operations:
                if not ledger.consent_active(subject, operation, viewer, scope=scope):
                    raise PermissionDenied(f"source requires {subject}'s scoped {operation} consent")
