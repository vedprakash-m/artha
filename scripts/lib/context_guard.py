# F-C1: merged context_classifier.py + context_scrubber.py → context_guard.py (re-artha.md). Shims removed after 2026-06-16.

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


# ---------------------------------------------------------------------------
# Merged from context_scrubber.py
# ---------------------------------------------------------------------------

# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/context_scrubber.py — PII scrubbing for external agent context.

Before any context fragment is sent to an external agent, it is run through
this scrubber.  The scrubber applies pii_guard.filter_text() and enforces
additional redaction rules based on the agent's trust tier and PII profile.

Defense-in-depth layer 2 (layer 1 = classification, layer 3 = injection
detection).  Even PUBLIC-tier context is scrubbed because classification
may miss novel PII patterns.

Ref: specs/subagent-ext-agent.md §3.5, §4.4 Step 4, EA-0d
"""

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger("artha.context_scrubber")

# ---------------------------------------------------------------------------
# Lazy import of pii_guard from scripts/
# ---------------------------------------------------------------------------

_PII_GUARD_MOD = None


def _get_pii_guard():
    """Import pii_guard lazily to avoid circular imports and path issues.

    pii_guard lives in scripts/ (not scripts/lib/), so we use importlib.
    """
    global _PII_GUARD_MOD
    if _PII_GUARD_MOD is not None:
        return _PII_GUARD_MOD

    # Locate pii_guard.py relative to this file (scripts/lib/../pii_guard.py)
    guard_path = Path(__file__).resolve().parent.parent / "pii_guard.py"
    if not guard_path.exists():
        # Fallback: identity function (no-op) — keeps pipeline running but
        # logs a warning so the operator knows to investigate.
        _log.warning(
            "pii_guard.py not found at %s — PII scrubbing disabled", guard_path
        )
        return None

    spec = importlib.util.spec_from_file_location("pii_guard", guard_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _PII_GUARD_MOD = mod
    return mod


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class ScrubResult:
    original_length: int
    scrubbed_text: str
    pii_types_found: dict[str, int]    # {pii_type: count}
    was_modified: bool
    blocked: bool = False              # True if strict_mode blocked the fragment


@dataclass
class ContextScrubber:
    """Stateless scrubber for context fragments sent to external agents.

    Parameters:
        strict_mode:    If True, fragments with PII are blocked (not redacted).
                        If False, PII is replaced with typed placeholders.
        allowed_pii:    Agent-specific PII types that are allowed through
                        without replacement (e.g. ["IP_ADDRESS", "HOSTNAME"]).
                        Types listed here are still logged to audit.
        blocked_pii:    PII types that are ALWAYS blocked regardless of
                        the allow-list.  Block wins over allow on conflict.
                        (DEBT-024: ssn, passport_number, bank_account, etc.)
    """
    strict_mode: bool = True
    allowed_pii: list[str] = field(default_factory=list)
    blocked_pii: list[str] = field(default_factory=list)

    def scrub(self, text: str) -> ScrubResult:
        """Scrub PII from a context fragment.

        If strict_mode is True and PII is detected (outside the allowlist),
        the result has blocked=True and scrubbed_text is an empty string.

        If strict_mode is False, PII is replaced with typed placeholders:
        <SSN>, <CREDIT_CARD>, etc.

        Args:
            text: Raw context fragment text.

        Returns:
            ScrubResult with the scrubbed text and metadata.
        """
        if not text:
            return ScrubResult(
                original_length=0,
                scrubbed_text="",
                pii_types_found={},
                was_modified=False,
            )

        pii_guard = _get_pii_guard()
        if pii_guard is None:
            # No PII guard available — in strict mode this is a hard block
            # because we cannot guarantee the fragment is PII-free.
            if self.strict_mode:
                _log.warning("pii_guard unavailable in strict mode — blocking fragment")
                return ScrubResult(
                    original_length=len(text),
                    scrubbed_text="",
                    pii_types_found={},
                    was_modified=True,
                    blocked=True,
                )
            _log.warning("pii_guard unavailable — passing fragment unscrubbed (non-strict)")
            return ScrubResult(
                original_length=len(text),
                scrubbed_text=text,
                pii_types_found={},
                was_modified=False,
            )

        # Step 1: Apply PII filter (always — identifies what types are present)
        try:
            filtered_text, found_types = pii_guard.filter_text(text)
        except Exception:
            # PII guard raised — treat as unanalyzable.  In strict mode,
            # block the fragment (fail-safe).  Otherwise pass through but
            # log a warning so the issue gets investigated.
            _log.exception("pii_guard.filter_text() raised — fail-safe triggered")
            if self.strict_mode:
                return ScrubResult(
                    original_length=len(text),
                    scrubbed_text="",
                    pii_types_found={},
                    was_modified=True,
                    blocked=True,
                )
            return ScrubResult(
                original_length=len(text),
                scrubbed_text=text,
                pii_types_found={},
                was_modified=False,
            )

        if not found_types:
            # No PII detected — pass through unchanged
            return ScrubResult(
                original_length=len(text),
                scrubbed_text=text,
                pii_types_found={},
                was_modified=False,
            )

        # Step 2: Check if all found types are in the agent's allowlist
        # DEBT-024: block list wins over allow list — check block first.
        _blocked_set = {t.upper() for t in self.blocked_pii}
        explicitly_blocked = {
            pii_type: count
            for pii_type, count in found_types.items()
            if pii_type.upper() in _blocked_set
        }
        if explicitly_blocked:
            # Block-list hit — block regardless of allow-list
            return ScrubResult(
                original_length=len(text),
                scrubbed_text="",
                pii_types_found=explicitly_blocked,
                was_modified=True,
                blocked=True,
            )

        disallowed = {
            pii_type: count
            for pii_type, count in found_types.items()
            if pii_type not in self.allowed_pii
        }

        if not disallowed:
            # All found PII is on the agent allowlist — pass through with note
            return ScrubResult(
                original_length=len(text),
                scrubbed_text=text,
                pii_types_found=found_types,
                was_modified=False,
            )

        # Step 3: Disallowed PII found
        if self.strict_mode:
            # Block the entire fragment
            return ScrubResult(
                original_length=len(text),
                scrubbed_text="",
                pii_types_found=disallowed,
                was_modified=True,
                blocked=True,
            )

        # Non-strict: return the filtered text (PII replaced with placeholders)
        return ScrubResult(
            original_length=len(text),
            scrubbed_text=filtered_text,
            pii_types_found=disallowed,
            was_modified=True,
            blocked=False,
        )

    def scrub_many(self, fragments: list[str]) -> list[ScrubResult]:
        """Scrub a list of context fragments, returning one result per fragment."""
        return [self.scrub(f) for f in fragments]
