# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/adaptive_context.py — Adaptive context budgeting (EAR-11).

Replaces static `max_context_chars: 2000` with a dynamic budget derived from:
  - Base: agent.invocation.max_context_chars (per-agent default)
  - Absolute cap: agent.invocation.max_context_chars_absolute (hard ceiling)
  - Query complexity factor: unique token count relative to baseline
  - KB fragment depth: total available KB chars (signals query richness)
  - Invocation history: agents with poor quality scores get larger budgets
    (they previously got insufficient context)

Algorithm (spec §EAR-11):
  base = agent.invocation.max_context_chars
  if heavy_query: base *= 1.5
  if rich_kb:    base *= 1.3
  if low_quality_history: base *= 1.4
  budget = min(max_context_chars_absolute or ∞, base)
  budget = max(MIN_BUDGET, min(MAX_BUDGET, budget))

Ref: specs/ext-agent-reloaded.md §EAR-11
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MIN_BUDGET_CHARS = 500
_MAX_BUDGET_CHARS = 16_000       # Hard ceiling even without absolute cap
_DEFAULT_BASE = 2_000            # Default when agent has no invocation config
_QUERY_HEAVY_THRESHOLD = 15      # Unique tokens above this → heavy query
_KB_RICH_THRESHOLD = 8_000       # Available KB chars above this → rich KB
_QUALITY_LOW_THRESHOLD = 0.55    # Mean quality below this → expand budget


def compute_context_budget(
    agent,
    query: str,
    kb_fragments: list[tuple[str, str | None]] | None = None,
) -> int:
    """Compute adaptive context budget for an agent invocation.

    Parameters:
        agent:         ExternalAgent instance.
        query:         User query string.
        kb_fragments:  Available context fragments (text, source_path).

    Returns integer char budget (clamped to [_MIN_BUDGET_CHARS, _MAX_BUDGET_CHARS]).
    """
    # Base budget from per-agent config
    invocation = getattr(agent, "invocation", None)
    base = (
        getattr(invocation, "max_context_chars", _DEFAULT_BASE)
        if invocation else _DEFAULT_BASE
    ) or _DEFAULT_BASE

    max_absolute = None
    if invocation:
        max_absolute = getattr(invocation, "max_context_chars_absolute", None)

    # Factor 1: Query complexity (unique token count)
    query_tokens = len(set(query.lower().split()))
    if query_tokens > _QUERY_HEAVY_THRESHOLD:
        base = int(base * 1.5)

    # Factor 2: KB fragment availability
    if kb_fragments:
        total_kb_chars = sum(len(f[0]) for f in kb_fragments)
        if total_kb_chars > _KB_RICH_THRESHOLD:
            base = int(base * 1.3)

    # Factor 3: Historical quality — expand budget for low-quality-history agents
    health = getattr(agent, "health", None)
    if health is not None:
        total = getattr(health, "total_invocations", 0) or 0
        mean_q = getattr(health, "mean_quality_score", 1.0) or 1.0
        if total >= 3 and mean_q < _QUALITY_LOW_THRESHOLD:
            base = int(base * 1.4)

    # Apply absolute cap (per-agent override, e.g. icm-triage: 12000)
    if max_absolute is not None:
        budget = min(max_absolute, base)
    else:
        budget = base

    # Global min/max clamp
    return max(_MIN_BUDGET_CHARS, min(_MAX_BUDGET_CHARS, budget))
