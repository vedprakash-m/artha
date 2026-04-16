"""
context_budget.py — Shared token-budget utilities
===================================================
Single source of truth for token estimation constants and helpers.

RD-50: Eliminates duplicated _CHARS_PER_TOKEN = 4 across three modules
       (session_summarizer.py, prompt_composer.py, context_offloader.py).
RD-21: Corrects the estimate from 4 to 3.5 chars/token (Claude tokenizer
       produces ~3.5 chars/token for English prose, not 4 — fixing the ~14%
       underestimate that caused proactive summarization to trigger too late).

All context-size estimators must import from here. The CI test
test_context_budget.py::test_chars_per_token_not_duplicated enforces this.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Core constants
# ---------------------------------------------------------------------------

#: Claude 3.x character-to-token estimate.
#: Corrected from 4 → 3.5 per RD-21: Claude tokenizer benchmarks yield
#: ~3.5 chars/token for English prose, reducing context window underestimation
#: from ~14% to ~0%.
CHARS_PER_TOKEN: float = 3.5

#: Claude 3.x / 3.5 / 4 context window in tokens.
MAX_CONTEXT_TOKENS: int = 200_000

#: Total context window in characters (derived).
MAX_CONTEXT_CHARS: int = int(MAX_CONTEXT_TOKENS * CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def estimate_token_count(text: str) -> int:
    """Return estimated token count for the given text.

    Uses the CHARS_PER_TOKEN heuristic. Suitable for threshold checks and
    budget planning; not a substitute for the actual tokenizer.
    """
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN)


def estimate_context_pct(
    text: str,
    model_tokens: int = MAX_CONTEXT_TOKENS,
) -> float:
    """Return the estimated fraction of context window consumed (0.0–1.0).

    Args:
        text: The text whose token count to estimate.
        model_tokens: Context window size in tokens (default: 200K).

    Returns:
        A float in [0.0, ∞). Values > 1.0 indicate overflow.
    """
    if not text or model_tokens <= 0:
        return 0.0
    return estimate_token_count(text) / model_tokens


def chars_for_tokens(token_budget: int) -> int:
    """Return the maximum character count for a given token budget."""
    return int(token_budget * CHARS_PER_TOKEN)
