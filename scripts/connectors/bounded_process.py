"""Backward-compatible connector import for the shared process helper."""
from __future__ import annotations

import sys

from lib import bounded_process as _bounded_process

sys.modules[__name__] = _bounded_process
