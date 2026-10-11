"""Evidence-led Catch-up core (re-artha D-16).

Adjacent to the incumbent ``scripts/pipeline.py``; nothing here is imported by
the incumbent path until a work package cuts a Domain over.  Strictly typed
(``mypy --strict`` in ``make mypy``) and covered at >= 90% line and branch.
"""
from __future__ import annotations

__all__ = ["CORE_CONTRACT_VERSION"]

#: Bumped when a persisted or cross-process contract in this package changes.
CORE_CONTRACT_VERSION = "0.1.0"
