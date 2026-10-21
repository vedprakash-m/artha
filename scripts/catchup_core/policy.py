"""Trusted local release and disclosure policy. Model output never supplies authority."""
from __future__ import annotations

import hashlib
import json
import socket
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from catchup_core.contracts import CatchupRequest, ExecutionMode
from catchup_core.ledger import Ledger, PermissionDenied
from catchup_core.scorecard import Case, score


def validate_release(request: CatchupRequest, config: Mapping[str, Any], *, viewer: str,
                     domains: Sequence[str], hostname: str | None = None) -> None:
    if request.actor != viewer:
        raise PermissionDenied("request actor does not match the configured local viewer")
    if request.channel not in ("cli", "vscode", "codex"):
        raise PermissionDenied("this surface is not authorized for personal evidence")
    if request.mode is ExecutionMode.SHADOW:
        return
    host = hostname or socket.gethostname().split(".")[0]
    release = config.get("release")
    if not isinstance(release, dict):
        raise PermissionDenied("pilot release evidence is missing; use --shadow")
    if release.get("authority_host") != host or request.host != host:
        raise PermissionDenied("this machine is not the designated authority host")
    if not set(domains) <= set(release.get("domains", [])):
        raise PermissionDenied("requested Domain has not passed cutover")
    for field in ("owner_approved_at", "stage1_clearance", "rollback_snapshot", "scorecard"):
        if not release.get(field):
            raise PermissionDenied(f"release needs {field}; use --shadow")
    clearance = json.loads(_approved_file(release["stage1_clearance"], "Stage 1 clearance"))
    if clearance.get("passed") is not True or not clearance.get("owner_signed_at"):
        raise PermissionDenied("Stage 1 clearance has not passed owner review")
    if not _approved_file(release["rollback_snapshot"], "rollback snapshot").startswith(b"AE1"):
        raise PermissionDenied("rollback snapshot must be encrypted")
    data = _approved_file(release["scorecard"], "release scorecard")
    try:
        result = json.loads(data)
        cases = [Case(str(c["case_id"]), frozenset(c["expected"]), frozenset(c["surfaced"]),
                      c.get("quiet_claim", False), c.get("coverage_complete", True),
                      c.get("citations_resolve", True), frozenset(c.get("incumbent_surfaced", [])))
                 for c in result["evaluations"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise PermissionDenied("release scorecard needs labeled evaluations") from exc
    measured = score(cases)
    if (not measured.passes or measured.precision < float(release.get("minimum_precision", 0.8))
            or result.get("cases") != measured.cases or not result.get("live_cases")
            or not result.get("owner_signed_at")):
        raise PermissionDenied("release scorecard lacks passing consented live evidence")
    _validate_domains(result, domains)


def _validate_domains(result: Mapping[str, Any], domains: Sequence[str]) -> None:
    if result.get("schema") != "artha.release.v1":
        raise PermissionDenied("release needs a versioned per-Domain scorecard")
    for domain in domains:
        bounds = result.get("bounds", {}).get(domain, {})
        cases = [c for c in result["evaluations"] if c.get("domain") == domain]
        required = ("minimum_precision", "minimum_recall", "max_source_age_hours", "source_precedence",
                    "normal_workload", "maximum_review_minutes", "maximum_p95_seconds", "maximum_monthly_usd")
        if not cases or any(bounds.get(k) in (None, "", [], {}) for k in required):
            raise PermissionDenied(f"{domain} needs cases and concrete release bounds")
        if not any(c.get("consented_live") is True for c in cases):
            raise PermissionDenied(f"{domain} lacks a consented live outcome")
        evaluated = score([Case(str(c["case_id"]), frozenset(c["expected"]), frozenset(c["surfaced"]),
            c.get("quiet_claim", False), c.get("coverage_complete", True), c.get("citations_resolve", True),
            frozenset(c.get("incumbent_surfaced", []))) for c in cases])
        if (not evaluated.passes or evaluated.precision < float(bounds["minimum_precision"])
                or evaluated.recall < float(bounds["minimum_recall"])):
            raise PermissionDenied(f"{domain} failed precision/recall or safety gates")
        measurements = result.get("measurements", {}).get(domain, {})
        for metric, bound in (("review_minutes", "maximum_review_minutes"), ("p95_seconds", "maximum_p95_seconds"),
                              ("monthly_usd", "maximum_monthly_usd")):
            value = measurements.get(metric)
            if not isinstance(value, int | float) or not 0 <= value <= float(bounds[bound]):
                raise PermissionDenied(f"{domain} has no passing {metric} measurement")


def _approved_file(record: Any, label: str) -> bytes:
    if not isinstance(record, dict) or not record.get("path") or not record.get("sha256"):
        raise PermissionDenied(f"{label} needs a path and approved digest")
    path = Path(str(record["path"])).expanduser()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PermissionDenied(f"{label} cannot be verified") from exc
    if hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise PermissionDenied(f"{label} differs from approved bytes")
    return data


def authorize_source(ledger: Ledger, subjects: Sequence[str], required: frozenset[str],
                     scope: str, viewer: str) -> None:
    for subject in subjects:
        if subject in required:
            for operation in ("ingest", "disclose", "view"):
                if not ledger.consent_active(subject, operation, viewer, scope=scope):
                    raise PermissionDenied(f"source requires {subject}'s scoped {operation} consent")
