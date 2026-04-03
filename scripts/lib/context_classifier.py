# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/context_classifier.py — Context trust-tier classification.

Classifies context fragments before external-agent delegation to determine
what information is safe to share based on the agent's trust tier.

Trust tiers (ascending privilege):
    PUBLIC    — safe to share with any agent
    SCOPED    — shareable with trusted/owned agents only
    PRIVATE   — shareable only with owned agents
    SENSITIVE — never shared with external agents

Classification is keyword + prefix based (deterministic, no LLM).
Fast path: classifies thousands of fragments in <1ms total.

Ref: specs/subagent-ext-agent.md §3.4, EA-0c
"""
from __future__ import annotations

import re
from enum import Enum
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class ContextTier(str, Enum):
    """Trust tier for a context fragment.

    Ordered from least to most sensitive. Comparison is by enum value.
    """
    PUBLIC    = "PUBLIC"
    SCOPED    = "SCOPED"
    PRIVATE   = "PRIVATE"
    SENSITIVE = "SENSITIVE"


_TIER_ORDER: dict[ContextTier, int] = {
    ContextTier.PUBLIC:    0,
    ContextTier.SCOPED:    1,
    ContextTier.PRIVATE:   2,
    ContextTier.SENSITIVE: 3,
}


class ClassificationResult(NamedTuple):
    tier: ContextTier
    reason: str     # Short explanation for audit trail


# ---------------------------------------------------------------------------
# Trust tier per agent trust level
# ---------------------------------------------------------------------------

# Map agent trust level → maximum ContextTier they may receive
_TRUST_LEVEL_MAX_TIER: dict[str, ContextTier] = {
    "owned":     ContextTier.PRIVATE,
    "trusted":   ContextTier.SCOPED,
    "verified":  ContextTier.SCOPED,
    "external":  ContextTier.PUBLIC,
    "untrusted": ContextTier.PUBLIC,  # query text only, no context
}


# ---------------------------------------------------------------------------
# Classification rules (applied top-down, first match wins)
# ---------------------------------------------------------------------------

# SENSITIVE: NEVER share externally.
_SENSITIVE_PREFIXES = (
    "state/finance", "state/health", "state/immigration",
    "state/kids", "state/estate", "state/insurance",
    "state/legal", "state/medical",
)

_SENSITIVE_KEYWORDS = re.compile(
    r'\b('
    r'ssn|social security|passport number|visa number|i-?140|i-?485|'
    r'ein|routing number|account number|credit card|cvv|pin|'
    r'diagnosis|prescription|dob|date of birth|insurance id|'
    r'beneficiary|will |trust fund|net worth'
    r')\b',
    re.IGNORECASE,
)

# PRIVATE: share only with owned agents.
_PRIVATE_PREFIXES = (
    "state/work/",
    "state/goals",
    "state/open_items",
    "state/self_model",
    "state/memory",
    "state/audit",
    "state/calendar",
)

_PRIVATE_KEYWORDS = re.compile(
    r'\b('
    r'performance review|compensation|salary|bonus|stock|equity|'
    r'promotion|pip|coaching|hr|headcount|layoff|reorg|'
    r'manager feedback|annual review|calibration|'
    r'family|spouse|child|children|son|daughter|parent'
    r')\b',
    re.IGNORECASE,
)

# SCOPED: share with trusted/verified agents (IDs, not content).
_SCOPED_PREFIXES = (
    "knowledge/",
    "state/work",      # work state (no trailing slash → catches all)
    "tmp/work",
)

_SCOPED_KEYWORDS = re.compile(
    r'\b('
    r'icm|pbi|bug|task|sprint|ado|devops|pullrequest|pull request|'
    r'incident|escalation|oncall|on-call|deployment|release|rollout|'
    r'sev[0-9]|severity|p[0-9] ticket|workitem|work item'
    r')\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def classify_context(text: str, source_path: str = "") -> ClassificationResult:
    """Classify a context fragment and return its trust tier.

    Args:
        text:        The content of the context fragment.
        source_path: Optional file path the fragment came from (used for
                     prefix-based classification before keyword scanning).

    Returns:
        ClassificationResult(tier, reason) — tier is the most restrictive
        tier that applies.  reason is a short audit string.
    """
    path_lower = (source_path or "").lower().replace("\\", "/")
    text_lower = text.lower()

    # 1. SENSITIVE by path prefix
    for prefix in _SENSITIVE_PREFIXES:
        if path_lower.startswith(prefix):
            return ClassificationResult(
                ContextTier.SENSITIVE,
                f"sensitive path prefix: {prefix}",
            )

    # 2. SENSITIVE by keyword
    m = _SENSITIVE_KEYWORDS.search(text_lower)
    if m:
        return ClassificationResult(
            ContextTier.SENSITIVE,
            f"sensitive keyword: {m.group()}",
        )

    # 3. PRIVATE by path prefix
    for prefix in _PRIVATE_PREFIXES:
        if path_lower.startswith(prefix):
            return ClassificationResult(
                ContextTier.PRIVATE,
                f"private path prefix: {prefix}",
            )

    # 4. PRIVATE by keyword
    m = _PRIVATE_KEYWORDS.search(text_lower)
    if m:
        return ClassificationResult(
            ContextTier.PRIVATE,
            f"private keyword: {m.group()}",
        )

    # 5. SCOPED by path prefix
    for prefix in _SCOPED_PREFIXES:
        if path_lower.startswith(prefix):
            return ClassificationResult(
                ContextTier.SCOPED,
                f"scoped path prefix: {prefix}",
            )

    # 6. SCOPED by keyword
    m = _SCOPED_KEYWORDS.search(text_lower)
    if m:
        return ClassificationResult(
            ContextTier.SCOPED,
            f"scoped keyword: {m.group()}",
        )

    # 7. Default: PUBLIC
    return ClassificationResult(ContextTier.PUBLIC, "no sensitive signals detected")


def is_tier_allowed(
    tier: ContextTier,
    agent_trust_level: str,
) -> bool:
    """Return True if a context fragment at `tier` may be shared with an
    agent at `agent_trust_level`.

    Unknown trust levels are treated as 'untrusted' (most restrictive).
    """
    max_allowed = _TRUST_LEVEL_MAX_TIER.get(
        agent_trust_level.lower(),
        ContextTier.PUBLIC,   # safest fallback
    )
    return _TIER_ORDER[tier] <= _TIER_ORDER[max_allowed]


def filter_context_fragments(
    fragments: list[tuple[str, str]],   # [(text, source_path), ...]
    agent_trust_level: str,
) -> list[tuple[str, str, ClassificationResult]]:
    """Filter a list of context fragments to those allowed for an agent.

    Returns only fragments whose tier is at or below the agent's max tier.
    Each returned item is (text, source_path, classification).
    """
    result = []
    for text, path in fragments:
        classification = classify_context(text, path)
        if is_tier_allowed(classification.tier, agent_trust_level):
            result.append((text, path, classification))
    return result
