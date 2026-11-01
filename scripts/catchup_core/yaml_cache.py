"""Digest-free memo of parsed YAML keyed by the exact text, so a changed file is always re-read."""
from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

import yaml


@lru_cache(maxsize=32)
def _parsed(text: str) -> Any:
    return yaml.safe_load(text)


def safe_load(text: str) -> Any:
    return copy.deepcopy(_parsed(text))
