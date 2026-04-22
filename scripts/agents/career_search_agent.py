#!/usr/bin/env python3
# pii-guard: strict — career data processed here; no raw comp/contact values in logs
"""
scripts/agents/career_search_agent.py — CareerSearchAgent pre-compute (EAR-3, §9.3).

Reads config/career_portals.yaml and state/career_search.md (if decrypted),
invokes PortalScanner to fetch new ATS listings, and appends matches to the
## Pipeline section of career_search.md.

All filtering and dedup is deterministic Python — NOT inferred by the LLM.
The LLM's role is only to evaluate and present matched postings on demand.

Invoked by:
    python scripts/precompute.py --domain career
    (or directly: python scripts/agents/career_search_agent.py)

Exits 0 on success/skip, 1 on error (heartbeat written either way).

State files written:
    state/career_search.md           — ## Pipeline section updated (atomic)
    state/career_audit.jsonl         — one JSONL line per run appended
    tmp/career_last_run.json         — EAR-8 heartbeat
    ~/.artha-local/career/scan_ttl.json        — per-portal TTL cache
    ~/.artha-local/career/scan_fingerprints.json — dedup fingerprints

Ref: specs/career-ops.md §9.3, §15.2 Phase 2
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

_STATE_FILE = _REPO_ROOT / "state" / "career_search.md"
_AUDIT_FILE = _REPO_ROOT / "state" / "career_audit.jsonl"
_HEARTBEAT = _REPO_ROOT / "tmp" / "career_last_run.json"
_PORTALS_CONFIG = _REPO_ROOT / "config" / "career_portals.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_heartbeat(status: str, records_written: int, trace_id: str) -> None:
    payload = {
        "domain": "career",
        "status": status,
        "timestamp_utc": _now_utc(),
        "records_written": records_written,
        "trace_id": trace_id,
    }
    try:
        _HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        _HEARTBEAT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"⚠ career heartbeat write failed: {e}", file=sys.stderr)


def _append_audit(record: dict) -> None:
    try:
        with _AUDIT_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _campaign_active() -> bool:
    """Return True if career campaign is active. Handles encrypted/missing state file."""
    try:
        from lib.career_state import is_campaign_active  # noqa: PLC0415
        return is_campaign_active(_STATE_FILE)
    except Exception:
        return False


def _portals_configured() -> bool:
    """Return True if there are enabled portals to scan."""
    if _PORTALS_CONFIG.exists():
        try:
            import yaml  # noqa: PLC0415
            cfg = yaml.safe_load(_PORTALS_CONFIG.read_text(encoding="utf-8")) or {}
            return any(p.get("enabled", False) for p in cfg.get("companies", []))
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    trace_id = f"pre-compute-career-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    print(f"▶ CareerSearchAgent [{trace_id}]")

    # Early-exit guards
    if not _campaign_active():
        print("  ⏭ No active career campaign — skipping portal scan.")
        _write_heartbeat("SKIPPED_NO_CAMPAIGN", 0, trace_id)
        return 0

    if not _portals_configured():
        print("  ⏭ No enabled portals in config/career_portals.yaml — skipping.")
        _write_heartbeat("SKIPPED_NO_PORTALS", 0, trace_id)
        return 0

    try:
        from skills.portal_scanner import PortalScanner  # noqa: PLC0415
    except ImportError as e:
        print(f"⛔ Cannot import PortalScanner: {e}", file=sys.stderr)
        _write_heartbeat("error", 0, trace_id)
        return 1

    try:
        scanner = PortalScanner()
        result = scanner.execute()

        if result.get("status") == "failed":
            print(f"⛔ PortalScanner failed", file=sys.stderr)
            _write_heartbeat("error", 0, trace_id)
            return 1

        # Access result["data"] per BaseSkill.execute() contract
        data = result.get("data", {})
        new_matches = int(data.get("new_matches", 0))
        portals_scanned = int(data.get("portals_scanned", 0))
        errors = data.get("errors", [])
        total_found = int(data.get("total_found", 0))
        filtered = int(data.get("filtered", 0))
        duplicates = int(data.get("duplicates", 0))
        ttl_skipped = int(data.get("ttl_skipped", 0))

        for err in errors:
            print(f"  ⚠ {err}", file=sys.stderr)

        status = "success"
        print(
            f"✓ CareerSearchAgent: portals_scanned={portals_scanned} "
            f"ttl_skipped={ttl_skipped} total_found={total_found} "
            f"filtered={filtered} duplicates={duplicates} new_matches={new_matches}"
        )

        _append_audit({
            "trace_id": trace_id,
            "timestamp_utc": _now_utc(),
            "status": status,
            "portals_scanned": portals_scanned,
            "ttl_skipped": ttl_skipped,
            "total_found": total_found,
            "filtered": filtered,
            "duplicates": duplicates,
            "new_matches": new_matches,
            "errors": errors,
        })
        _write_heartbeat(status, new_matches, trace_id)
        return 0

    except Exception as exc:
        print(f"⛔ CareerSearchAgent failed: {exc}", file=sys.stderr)
        _write_heartbeat("error", 0, trace_id)
        return 1


if __name__ == "__main__":
    sys.exit(main())
