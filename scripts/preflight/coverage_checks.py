"""preflight/coverage_checks.py — post-fetch coverage view (re-artha §6.13, WP-02).

Preflight answers two different questions and keeps them apart:

* **capability** — can this machine run the operation at all (keys, auth,
  storage safety)?  A failure blocks the affected operation.  That is the rest
  of this package.
* **coverage** — what did the last fetch actually deliver?  A failure degrades
  the exact scope and is always advisory (P1): it never stops a Catch-up, and a
  valid empty result is *complete*, not a failure.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from preflight._types import CheckResult

_BAD = {"degraded": "degraded", "unavailable": "unavailable", "anomaly": "returned nothing (usually returns data)"}


def _default_path() -> Path:
    import os  # noqa: PLC0415
    local = os.environ.get("ARTHA_LOCAL_DIR") or str(Path.home() / ".artha-local")
    return Path(local).expanduser() / "connector_coverage.json"


def _days(since: str, now: str | None) -> int:
    def parse(v: str) -> datetime:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    current = parse(now) if now else datetime.now(timezone.utc)
    return max(0, (current - parse(since)).days)


def check_connector_coverage(path: Path | None = None, now: str | None = None) -> CheckResult:
    path = Path(path) if path else _default_path()
    name = "Connector coverage (last fetch)"
    try:
        connectors = json.loads(path.read_text(encoding="utf-8")).get("connectors", {})
    except (OSError, ValueError, AttributeError):
        return CheckResult(name, "P1", True, "no fetch recorded yet")
    if not connectors:
        return CheckResult(name, "P1", True, "no fetch recorded yet")

    gaps: list[str] = []
    for conn, entry in sorted(connectors.items()):
        run = (entry or {}).get("last_run") or {}
        status = run.get("status", "complete")
        if status not in _BAD:
            continue
        detail = _BAD[status]
        if status == "degraded" and run.get("rejected"):
            detail += f", {run['rejected']} record(s) rejected"
        if status == "unavailable" and run.get("error"):
            detail += f": {run['error']}"
        since = (entry or {}).get("failing_since")
        if since and status in ("degraded", "unavailable"):
            days = _days(since, now)
            if days >= 1:
                detail += f" — failing {days} days"
        gaps.append(f"{conn} {detail}")

    if not gaps:
        return CheckResult(name, "P1", True, f"all {len(connectors)} connector(s) complete at last fetch")
    return CheckResult(
        name, "P1", False, "; ".join(gaps),
        fix_hint="Scopes above get no all-clear in the Briefing. Re-run `brief`; "
                 "see `python scripts/pipeline.py --health --source <name>`.",
    )


def check_plaintext_in_sync_root(artha_dir: Path | str | None = None) -> CheckResult:
    """Fetched content and vault markers must not sit in the synced workspace (P1).

    ``tmp/pipeline_output.jsonl`` holds fetched email bodies; the pipeline now
    writes it to the machine-local directory (WP-03).  A leftover copy, or a
    sync-conflict copy of the vault lock marker, is reported until removed.
    """
    from preflight._types import ARTHA_DIR  # noqa: PLC0415
    root = Path(artha_dir) if artha_dir else Path(ARTHA_DIR)
    found: list[str] = []
    legacy = root / "tmp" / "pipeline_output.jsonl"
    if legacy.is_file():
        found.append("tmp/pipeline_output.jsonl")
    for marker in root.glob("*.artha-decrypted"):
        if marker.name != ".artha-decrypted":
            found.append("sync-conflict copy of .artha-decrypted")
            break
    name = "Plaintext in synced workspace"
    if not found:
        return CheckResult(name, "P1", True, "none found")
    return CheckResult(
        name, "P1", False, ", ".join(found),
        fix_hint="Move or delete it: fetched email content belongs in ~/.artha-local, "
                 "outside the cloud-synced workspace.",
    )
