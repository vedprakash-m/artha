"""Backward-compatible facade. See scripts/narrative/ for implementation."""
from __future__ import annotations
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from narrative import NarrativeEngine  # noqa: F401
from narrative import main  # noqa: F401

if __name__ == "__main__":
    main()
