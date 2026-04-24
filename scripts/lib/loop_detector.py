#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/lib/loop_detector.py — Loop Detection + Item Recurrence Scoring (ST-05).

Detects items that repeatedly appear in briefings without resolution,
assigning them a "stuck" flag and a recurrence score.

## Design
- Data source: state/catch_up_runs.yaml — each entry may include an optional
  `items_surfaced` list of item IDs (e.g. ["OI-024", "OI-066"]).
- `item_recurrence_score(item_id, briefing_history)` computes:
    - appearances: how many runs surfaced this item
    - closures: how many runs include it in `items_closed`
    - corrections: how many runs include it in `items_corrected`
    - recurrence_score: appearances - closures
    - stuck: True when appearances >= 3 and closures == 0
- preflight.py surfaces stuck items with 🔁 badge.

Ref: specs/steal.md §14.1 ST-05
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Threshold for marking an item as "stuck"
STUCK_APPEARANCE_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RecurrenceScore:
    """Recurrence analysis result for a single item."""
    item_id: str
    appearances: int          # number of runs where item was surfaced
    closures: int             # number of runs where item was closed
    corrections: int          # number of runs with corrections for this item
    recurrence_score: int     # = appearances - closures
    stuck: bool               # True if appearances >= 3 and closures == 0


# ---------------------------------------------------------------------------
# Core scoring function
# ---------------------------------------------------------------------------


def item_recurrence_score(
    item_id: str,
    briefing_history: list[dict[str, Any]],
) -> RecurrenceScore:
    """Compute the recurrence score for a single item across briefing history.

    Args:
        item_id: Item identifier (e.g. "OI-024").
        briefing_history: List of run dicts from catch_up_runs.yaml.
            Each dict may include:
                items_surfaced: list[str]  — items that appeared in this run
                items_closed:   list[str]  — items marked complete in this run
                items_corrected: list[str] — items that received corrections

    Returns:
        RecurrenceScore dataclass with computed fields.
    """
    appearances = 0
    closures = 0
    corrections = 0

    for run in briefing_history:
        if not isinstance(run, dict):
            continue

        surfaced = run.get("items_surfaced") or []
        closed = run.get("items_closed") or []
        corrected = run.get("items_corrected") or []

        if item_id in surfaced:
            appearances += 1
        if item_id in closed:
            closures += 1
        if item_id in corrected:
            corrections += 1

    recurrence_score = appearances - closures
    stuck = appearances >= STUCK_APPEARANCE_THRESHOLD and closures == 0

    return RecurrenceScore(
        item_id=item_id,
        appearances=appearances,
        closures=closures,
        corrections=corrections,
        recurrence_score=recurrence_score,
        stuck=stuck,
    )


# ---------------------------------------------------------------------------
# Bulk analysis helpers
# ---------------------------------------------------------------------------


def all_surfaced_items(briefing_history: list[dict[str, Any]]) -> set[str]:
    """Return the set of all item IDs that appear in any run."""
    items: set[str] = set()
    for run in briefing_history:
        if not isinstance(run, dict):
            continue
        for item_id in (run.get("items_surfaced") or []):
            if item_id:
                items.add(str(item_id))
    return items


def find_stuck_items(briefing_history: list[dict[str, Any]]) -> list[RecurrenceScore]:
    """Return RecurrenceScore list for all stuck items, sorted by recurrence_score desc."""
    all_items = all_surfaced_items(briefing_history)
    results = []
    for item_id in all_items:
        score = item_recurrence_score(item_id, briefing_history)
        if score.stuck:
            results.append(score)
    results.sort(key=lambda s: s.recurrence_score, reverse=True)
    return results


# ---------------------------------------------------------------------------
# State file loader
# ---------------------------------------------------------------------------


def load_briefing_history(
    runs_path: Path | None = None,
    max_entries: int = 100,
) -> list[dict[str, Any]]:
    """Load briefing history from state/catch_up_runs.yaml.

    Args:
        runs_path: Path to catch_up_runs.yaml. Defaults to state/catch_up_runs.yaml.
        max_entries: Maximum number of entries to load (most recent first).

    Returns:
        List of run dicts (may be empty if file absent or malformed).
    """
    if runs_path is None:
        runs_path = _REPO_ROOT / "state" / "catch_up_runs.yaml"

    if not runs_path.exists():
        return []

    try:
        import yaml  # type: ignore[import-not-found]
        raw = yaml.safe_load(runs_path.read_text(encoding="utf-8")) or []
        if not isinstance(raw, list):
            return []
        return raw[:max_entries]
    except Exception:
        return []


def format_stuck_items_badge(stuck: list[RecurrenceScore]) -> str:
    """Format stuck items for display in briefings / preflight output.

    Example output:
        🔁 Stuck items (3+ appearances, 0 closures):
          • OI-024 (appeared 5×, score 5)
          • OI-066 (appeared 3×, score 3)
    """
    if not stuck:
        return ""
    lines = ["🔁 Stuck items (3+ appearances, 0 closures):"]
    for s in stuck:
        lines.append(f"  • {s.item_id} (appeared {s.appearances}×, score {s.recurrence_score})")
    return "\n".join(lines)
