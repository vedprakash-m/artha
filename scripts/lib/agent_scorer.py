"""AR-9 External Agent Composition — Response Quality Scorer (§4.6, §7.2).

Implements score_agent_response() — the deterministic, heuristic quality
scorer used during response integration and health tracking.

Four dimensions (weights per spec §4.6 Opus R3):
  Consistency   0.35 — agreement with local KB
  Relevance     0.25 — keyword overlap with query
  Specificity   0.25 — actionable items count
  Completeness  0.15 — coverage of query aspects

Honesty bonus: if the response expresses uncertainty, floor quality at 0.5.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.lib.response_verifier import KBCheckResult

# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

_W_CONSISTENCY = 0.35
_W_RELEVANCE = 0.25
_W_SPECIFICITY = 0.25
_W_COMPLETENESS = 0.15

# ---------------------------------------------------------------------------
# Actionable item patterns
# ---------------------------------------------------------------------------

_ACTIONABLE_RE = re.compile(
    r"(?mi)"  # multi-line, case-insensitive
    r"("
    r"^\s*[-*•]\s+.{10,}"          # bullet list items with substance
    r"|(?:^|\s)(check|run|verify|execute|restart|redeploy|investigate|"
    r"query|monitor|rollback|update|disable|enable|confirm|review|"
    r"escalate|contact)\b"         # imperative verbs
    r"|step\s+\d+"                 # enumerated steps
    r"|(?:^|\s)\d+\.\s+\w"        # numbered list
    r")"
)

# ---------------------------------------------------------------------------
# Uncertainty phrases
# ---------------------------------------------------------------------------

_UNCERTAINTY_RE = re.compile(
    r"(?i)"
    r"(i don[''`]?t know"
    r"|i need more information"
    r"|insufficient (data|context|information)"
    r"|cannot (determine|confirm|verify)"
    r"|unclear (from|without)"
    r"|more (context|information|data) (is |would be )?(needed|required)"
    r"|unable to (assess|confirm|determine))"
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_agent_response(
    response: str,
    query: str,
    kb_check: "KBCheckResult | None" = None,
) -> float:
    """Score an external agent's response (0.0–1.0).

    Args:
        response: Agent response text.
        query: Original user question.
        kb_check: KB cross-check result (from ResponseVerifier).  If None,
            consistency is scored 0.5 (neutral — no KB data available).

    Returns:
        Quality score between 0.0 and 1.0 (inclusive).
    """
    if not response or not response.strip():
        return 0.0

    consistency = kb_check.agreement_ratio if kb_check is not None else 0.5
    relevance = _keyword_overlap(response, query)
    expected_actions = max(1, _expected_actions(query))
    actual_actions = len(_ACTIONABLE_RE.findall(response))
    specificity = min(1.0, actual_actions / expected_actions)
    completeness = _aspect_coverage(response, query)

    base = (
        _W_CONSISTENCY * consistency
        + _W_RELEVANCE * relevance
        + _W_SPECIFICITY * specificity
        + _W_COMPLETENESS * completeness
    )

    # Honesty bonus (spec §4.6)
    if _expresses_uncertainty(response):
        base = max(base, 0.5)

    return round(min(1.0, max(0.0, base)), 4)


# ---------------------------------------------------------------------------
# Dimension helpers (internal, but importable for tests)
# ---------------------------------------------------------------------------

def _keyword_overlap(response: str, query: str) -> float:
    """Fraction of query keywords that appear in the response."""
    query_words = _significant_words(query)
    if not query_words:
        return 0.5  # neutral fallback
    response_lower = response.lower()
    hits = sum(1 for w in query_words if w in response_lower)
    return hits / len(query_words)


def _count_actionable_items(response: str) -> int:
    """Count distinct actionable items in the response."""
    return len(_ACTIONABLE_RE.findall(response))


def _expected_actions(query: str) -> int:
    """Estimate how many actionable items a good response should contain.

    For troubleshooting queries (why, how to fix), expect ≥3.
    For status/info queries, expect ≥1.
    """
    low = query.lower()
    if any(kw in low for kw in ("why", "how to fix", "troubleshoot", "resolve", "diagnose", "debug")):
        return 3
    if any(kw in low for kw in ("steps", "procedure", "runbook", "process", "how")):
        return 2
    return 1


def _aspect_coverage(response: str, query: str) -> float:
    """Check how many distinct aspects of the query the response covers.

    Aspects are heuristically defined by: entity mentions, error codes,
    and structural sections (headings, numbered lists) in the response.
    """
    query_words = _significant_words(query)
    if not query_words:
        return 0.5

    response_lower = response.lower()
    covered = sum(1 for w in query_words if w in response_lower)
    # Penalise one-word answers; reward structured multi-section responses
    section_count = _count_sections(response)
    coverage_ratio = covered / len(query_words)
    structure_bonus = min(0.15, section_count * 0.05)
    return min(1.0, coverage_ratio + structure_bonus)


def _expresses_uncertainty(response: str) -> bool:
    """True if the response explicitly acknowledges uncertainty."""
    return bool(_UNCERTAINTY_RE.search(response))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    "a an and are as at be by for from have how i in is it its "
    "of on or that the this to was what will with you your".split()
)


def _significant_words(text: str) -> list[str]:
    """Extract lowercase significant words (strip stopwords, short words)."""
    words = re.findall(r"[a-z0-9_-]{3,}", text.lower())
    return [w for w in words if w not in _STOPWORDS]


def _count_sections(text: str) -> int:
    """Count markdown headings or blank-line-separated paragraphs."""
    headings = len(re.findall(r"(?m)^#{1,3}\s+\S", text))
    if headings:
        return headings
    paragraphs = len([p for p in text.split("\n\n") if p.strip()])
    return max(0, paragraphs - 1)  # subtract 1: first paragraph is preamble
