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
