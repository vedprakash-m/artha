"""AR-9 External Agent Composition — Context Composition Pipeline (§4.4).

Implements the 6-step pipeline:
  Collect → Classify → Filter → Scrub → Detect → Compose

The main entry-point is PromptComposer.compose().
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.agent_registry import ExternalAgent

from lib.context_classifier import (
    ClassificationResult,
    ContextTier,
    classify_context,
    filter_context_fragments,
)
from lib.context_scrubber import ContextScrubber, ScrubResult
from lib.injection_detector import InjectionDetector, ScanResult

# ---------------------------------------------------------------------------
# Delegation prompt template (verbatim from spec §4.4)
# ---------------------------------------------------------------------------

_DELEGATION_TEMPLATE = textwrap.dedent("""\
    You are being consulted as a domain expert. Answer the following question
    using your specialized knowledge and available tools.

    ## Question
    {user_question}

    ## Relevant Context
    {scrubbed_context}

    ## Constraints
    - Budget: Complete in ≤{budget} tool calls. Return best partial result if
      budget reached.
    - Return a concise expert analysis (≤{max_response_chars} characters).
    - Focus on actionable root causes and specific troubleshooting steps.
    - If you need information not provided, state what's missing rather than
      guessing.
    - Do not ask follow-up questions — provide your best analysis with the
      available context.
""")

_NO_CONTEXT_PLACEHOLDER = "(No additional context available for this trust tier.)"

# ---------------------------------------------------------------------------
# Fragment priority for context budget trimming (spec §4.4)
# ---------------------------------------------------------------------------

_PRIORITY_HIGH = 1    # error messages, error codes
_PRIORITY_MED_HI = 2  # system/service names and identifiers
_PRIORITY_MED = 3     # timeline/chronology data
_PRIORITY_LOW = 4     # background knowledge (trimmed first)


def _fragment_priority(text: str, source_path: str | None) -> int:
    """Heuristic priority for context budget ordering.

    High-signal fragments (errors, codes) are kept; background KB trimmed first.
    """
    low = text.lower()
    sp = (source_path or "").lower()
    # Error messages / codes → highest priority
    if any(kw in low for kw in ("error", "exception", "failed", "failure", "icm-", "code:")):
        return _PRIORITY_HIGH
    # System/service identifiers
    if any(kw in low for kw in ("service", "region", "cluster", "host", "node", "sku")):
        return _PRIORITY_MED_HI
    if any(kw in sp for kw in ("state/work", "state/")):
        return _PRIORITY_MED_HI
    # Timeline / chronology
    if any(kw in low for kw in ("minutes ago", "hours ago", "deployed at", "initiated", "timestamp")):
        return _PRIORITY_MED
    # Background KB → lowest
    if any(kw in sp for kw in ("knowledge/", "tmp/")):
        return _PRIORITY_LOW
    return _PRIORITY_MED


# ---------------------------------------------------------------------------
# Result dataclass (audit metadata for each composition)
# ---------------------------------------------------------------------------

@dataclass
class CompositionResult:
    """Audit record produced by PromptComposer.compose()."""

    prompt: str
    """Final composed delegation prompt."""

    fragments_collected: int
    """Total fragments provided as input."""

    fragments_after_classify: int
    """Fragments remaining after tier classification."""

    fragments_after_scrub: int
    """Fragments remaining after PII scrubbing (i.e., not blocked)."""

    pii_types_found: dict[str, int] = field(default_factory=dict)
    """Aggregate PII type counts across all scrubbed fragments."""

    injection_detected: bool = False
    """True if composed prompt failed injection scan."""

    context_trimmed: bool = False
    """True if context was truncated to fit budget."""

    blocked_fragments: int = 0
    """Fragments blocked by strict-mode PII guard."""

    class_results: list[tuple[str, ClassificationResult]] = field(default_factory=list)
    """(snippet, classification) for each fragment (debug aid)."""

    scrub_results: list[ScrubResult] = field(default_factory=list)
    """One ScrubResult per allowed fragment."""

    injection_scan: ScanResult | None = None
    """Full injection scan result on the final prompt."""


# ---------------------------------------------------------------------------
# PromptComposer
# ---------------------------------------------------------------------------

class PromptComposer:
    """Composes a delegation prompt for an external agent.

    Usage::

        composer = PromptComposer(agent)
        result = composer.compose("Why is the deployment stuck?", fragments)
        if not result.injection_detected:
            invoked = invoker.invoke(agent, result.prompt)
    """

    def __init__(
        self,
        agent: "ExternalAgent",
        scrubber: ContextScrubber | None = None,
        detector: InjectionDetector | None = None,
    ) -> None:
        self._agent = agent
        allow = agent.pii_profile.allow if agent.pii_profile else []
        self._scrubber = scrubber or ContextScrubber(allowed_pii=allow)
        self._detector = detector or InjectionDetector()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compose(
        self,
        query: str,
        context_fragments: list[tuple[str, str | None]],
    ) -> CompositionResult:
        """Run the 6-step composition pipeline and return CompositionResult.

        Args:
            query: The user's original question (never filtered or trimmed).
            context_fragments: List of (text, source_path) pairs.  source_path
                may be None for programmatically constructed fragments.

        Returns:
            CompositionResult with `prompt` set.  If injection_detected is True
            the caller MUST NOT forward the prompt to the agent.
        """
        result = CompositionResult(
            prompt="",
            fragments_collected=len(context_fragments),
            fragments_after_classify=0,
            fragments_after_scrub=0,
        )

        # Step 1 & 2: Collect + Classify ─────────────────────────────────
        allowed = filter_context_fragments(
            context_fragments, self._agent.trust_tier
        )
        result.fragments_after_classify = len(allowed)
        result.class_results = [(frag[0][:80], frag[2]) for frag in allowed]

        # Step 3: Filter (already done by filter_context_fragments)

        # Step 4: Scrub ───────────────────────────────────────────────────
        scrubbed_fragments: list[tuple[str, str | None]] = []
        all_pii: dict[str, int] = {}

        for text, path, _cls in allowed:
            sr = self._scrubber.scrub(text)
            result.scrub_results.append(sr)
            # Merge PII type counts
            for ptype, count in sr.pii_types_found.items():
                all_pii[ptype] = all_pii.get(ptype, 0) + count

            if sr.blocked:
                result.blocked_fragments += 1
                continue
            scrubbed_fragments.append((sr.scrubbed_text, path))

        result.pii_types_found = all_pii
        result.fragments_after_scrub = len(scrubbed_fragments)

        # Step 5: Compose context string with budget cap ──────────────────
        max_context_chars = (
            self._agent.invocation.max_context_chars
            if self._agent.invocation
            else 2000
        )
        context_str, trimmed = self._build_context(
            scrubbed_fragments, max_context_chars
        )
        result.context_trimmed = trimmed

        # Step 6: Compose final prompt ─────────────────────────────────────
        budget = (
            self._agent.invocation.max_budget if self._agent.invocation else 10
        )
        max_response = (
            self._agent.invocation.max_response_chars
            if self._agent.invocation
            else 5000
        )
        prompt = _DELEGATION_TEMPLATE.format(
            user_question=query.strip(),
            scrubbed_context=context_str,
            budget=budget,
            max_response_chars=max_response,
        )
        result.prompt = prompt

        # Step 5 (continued): Injection scan on composed prompt ────────────
        scan = self._detector.scan(prompt)
        result.injection_scan = scan
        result.injection_detected = scan.injection_detected

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_context(
        self,
        fragments: list[tuple[str, str | None]],
        max_chars: int,
    ) -> tuple[str, bool]:
        """Concatenate fragments, trimming lowest-priority ones first.

        Returns (context_string, was_trimmed).
        """
        if not fragments:
            return _NO_CONTEXT_PLACEHOLDER, False

        # Sort by priority (ascending = most important first)
        ranked = sorted(
            fragments,
            key=lambda f: _fragment_priority(f[0], f[1]),
        )

        lines: list[str] = []
        total = 0
        trimmed = False

        for text, path in ranked:
            label = f"[{path}] " if path else ""
            entry = f"{label}{text.strip()}"
            entry_len = len(entry) + 1  # +1 for newline

            if total + entry_len > max_chars:
                trimmed = True
                # Try to fit a truncated version of background fragments only
                if _fragment_priority(text, path) == _PRIORITY_LOW:
                    remaining = max_chars - total - 30
                    if remaining > 50:
                        lines.append(f"{entry[:remaining]}…(trimmed)")
                        break
                # Otherwise skip entirely
                continue

            lines.append(entry)
            total += entry_len

        return "\n".join(lines) if lines else _NO_CONTEXT_PLACEHOLDER, trimmed
