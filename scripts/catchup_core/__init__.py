"""Evidence-led Catch-up core (re-artha D-16).

Adjacent to the incumbent ``scripts/pipeline.py``; nothing here is imported by
the incumbent path until a work package cuts a Domain over.  Strictly typed
(``mypy --strict`` in ``make mypy``) and covered at >= 90% line and branch.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

__all__ = ["CORE_CONTRACT_VERSION"]

#: Bumped when a persisted or cross-process contract in this package changes.
CORE_CONTRACT_VERSION = "0.1.0"
