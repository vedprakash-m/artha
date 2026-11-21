"""Work privacy drift check (P1, advisory).

Work state under ``state/work`` is journaled once enrolled. A write that bypasses the journaled
publication path (an agent file tool, or a script that does not use ``lib.work_publication``) makes
every guarded Work command refuse to run until the owner reviews and reconciles the drift.
This check reports that condition up front instead of letting the first Work command fail.

It uses the same bounded, metadata-only path as ``work-privacy-status``: it publishes nothing,
repairs nothing and prints no member names.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from ._types import CheckResult

_SCRIPTS = Path(__file__).resolve().parents[1]
_REPO = _SCRIPTS.parent

_FIX = (
    "Owner terminal, from scripts/: python -m catchup_core.checkpoint_cli work-privacy-reconcile-preview "
    "--details, then work-privacy-reconcile --sha256 <digest> --reviewed (docs/catchup-core.md)"
)


def _default_probe() -> Any:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    from catchup_core.work_privacy import for_installation  # noqa: PLC0415
    return for_installation(_REPO)


def check_work_state_integrity(state_dir: Path | None = None) -> CheckResult:
    """P1 advisory: Work state files parse, and the Work refresh prerequisites exist."""
    name = "Work state integrity"
    try:
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        from lib.work_state_integrity import find_integrity_issues, refresh_blockers  # noqa: PLC0415
        target = state_dir or _REPO / "state" / "work"
        issues = find_integrity_issues(target)
        blockers: list[str] = []
        if state_dir is None:
            try:
                from work_loop import WorkLoop  # noqa: PLC0415
            except ImportError:
                return CheckResult(name, "P1", True, "Work code not present on this checkout; skipped")
            blockers = refresh_blockers(bool(WorkLoop._work_token("msgraph.work")), None)
        if issues or blockers:
            detail = "; ".join(issues[:3] + blockers)
            more = f" (+{len(issues) - 3} more integrity issues)" if len(issues) > 3 else ""
            return CheckResult(name, "P1", False, f"{detail}{more}",
                               fix_hint="python scripts/work_reader.py --command health  (lists every issue)")
        return CheckResult(name, "P1", True, "Work state parses; refresh prerequisites present ✓")
    except Exception as exc:  # noqa: BLE001 - report, never raise into preflight
        return CheckResult(name, "P1", False,
                           f"Work state integrity not verified ({type(exc).__name__})")


def check_work_privacy_drift(probe: Callable[[], Any] | None = None) -> CheckResult:
    name = "Work privacy drift"
    try:
        protocol = (probe or _default_probe)()
        if not protocol.is_enrolled():
            return CheckResult(name, "P1", True, "Work privacy not enrolled; nothing to check")
        current = protocol.current()
        return CheckResult(name, "P1", True,
                           f"Work state matches the journal (generation {current.get('generation', '?')}) ✓")
    except Exception as exc:  # noqa: BLE001 - any failure is reported, never raised into preflight
        text = str(exc)
        if "drift" in text.lower():
            return CheckResult(name, "P1", False,
                               f"Work state changed outside journaled publication: {text}. "
                               "Work commands (work, work reflect, ...) will refuse until reconciled",
                               fix_hint=_FIX)
        return CheckResult(name, "P1", False,
                           f"Work privacy status unavailable ({type(exc).__name__}); not verified",
                           fix_hint="python -m catchup_core.checkpoint_cli work-privacy-status (from scripts/)")
