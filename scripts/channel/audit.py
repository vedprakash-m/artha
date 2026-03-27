"""channel/audit.py — Audit log writer for Artha channel operations."""
from __future__ import annotations
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_STATE_DIR = _ARTHA_DIR / "state"
_AUDIT_LOG = _STATE_DIR / "audit.md"

log = logging.getLogger("channel_listener")


def _get_audit_path() -> Path:
    """Return active audit log path — honours monkeypatching of channel_listener._AUDIT_LOG."""
    cl = sys.modules.get("channel_listener")
    if cl is not None and hasattr(cl, "_AUDIT_LOG"):
        return cl._AUDIT_LOG  # type: ignore[return-value]
    return _AUDIT_LOG



# ── _audit_log ──────────────────────────────────────────────────

def _audit_log(event_type: str, **kwargs: str | int | bool | None) -> None:
    audit_path = _get_audit_path()
    ts = datetime.now(timezone.utc).isoformat()
    parts = [f"[{ts}] {event_type}"]
    for k, v in kwargs.items():
        parts.append(f"{k}: {v}")
    entry = " | ".join(parts)
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass
    log.debug("Audit: %s", entry)
