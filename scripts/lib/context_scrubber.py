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
from __future__ import annotations

import importlib.util
import logging
import sys
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
    """
    strict_mode: bool = True
    allowed_pii: list[str] = field(default_factory=list)

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
