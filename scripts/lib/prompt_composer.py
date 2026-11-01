"""AR-9 External Agent Composition — Context Composition Pipeline (§4.4).

Implements the 6-step pipeline:
  Collect → Classify → Filter → Scrub → Detect → Compose

The main entry-point is PromptComposer.compose().

EAR-9 extension: SOUL principles injected FIRST in the prompt (before question
and context) to avoid recency-bias loss.  Principles filtered per-principle
through soul_allowlist before injection.  (R-3, R-8)

EAR-11 extension: max_context_chars_absolute per-agent absolute cap overrides
computed budget (prevents context starvation on complex agents).
"""

from __future__ import annotations

import logging
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
# Delegation prompt template — EAR-9 principles-first ordering (R-3)
# Structure: [Principles + Role] → [Question] → [Context] → [Safety constraints]
# ---------------------------------------------------------------------------

_DELEGATION_TEMPLATE_WITH_SOUL = textwrap.dedent("""\
    {soul_block}
    ## Question
    {user_question}

    ## Relevant Context
    {scrubbed_context}
{corrections_block}
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

_DELEGATION_TEMPLATE = textwrap.dedent("""\
    You are being consulted as a domain expert. Answer the following question
    using your specialized knowledge and available tools.

    ## Question
    {user_question}

    ## Relevant Context
    {scrubbed_context}
{corrections_block}
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

# Safety limit for query length in delegation prompts (chars).
_MAX_QUERY_CHARS = 8_000

_log = logging.getLogger("artha.prompt_composer")

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
        block = agent.pii_profile.block if agent.pii_profile else []
        self._scrubber = scrubber or ContextScrubber(allowed_pii=allow, blocked_pii=block)
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
        # EAR-11: Adaptive context budget (replaces static max_context_chars)
        try:
            from lib.adaptive_context import compute_context_budget  # noqa: PLC0415
            max_context_chars = compute_context_budget(
                agent=self._agent,
                query=query,
                kb_fragments=[(text, path) for text, path in scrubbed_fragments],
            )
        except (ImportError, Exception):
            # Graceful fallback: use raw config value
            max_context_chars = (
                self._agent.invocation.max_context_chars
                if self._agent.invocation
                else 2000
            )
            # Per-agent absolute cap overrides computed budget
            max_context_chars_absolute = getattr(
                self._agent.invocation, "max_context_chars_absolute", None
            ) if self._agent.invocation else None
            if max_context_chars_absolute is not None:
                max_context_chars = max_context_chars_absolute

        # EAR-1: Load agent memory (prior invocation learnings)
        memory_block = self._load_agent_memory(query)

        # EAR-10: Load cross-agent propagated context
        propagated_block = self._load_propagated_context()

        context_str, trimmed = self._build_context(
            scrubbed_fragments, max_context_chars
        )
        result.context_trimmed = trimmed

        # Prepend memory and propagated context blocks ahead of KB context
        prefix_parts: list[str] = []
        if memory_block:
            prefix_parts.append(memory_block)
        if propagated_block:
            prefix_parts.append(propagated_block)
        if prefix_parts and context_str != _NO_CONTEXT_PLACEHOLDER:
            context_str = "\n".join(prefix_parts) + "\n" + context_str
        elif prefix_parts:
            context_str = "\n".join(prefix_parts)

        # Step 6: Compose final prompt ─────────────────────────────────────
        budget = (
            self._agent.invocation.max_budget if self._agent.invocation else 10
        )
        max_response = (
            self._agent.invocation.max_response_chars
            if self._agent.invocation
            else 5000
        )
        # Sanitise user query: escape braces to prevent str.format() crashes,
        # and cap length to avoid unbounded prompt expansion.
        safe_query = query.strip().replace("{", "{{").replace("}", "}}")
        if len(safe_query) > _MAX_QUERY_CHARS:
            safe_query = safe_query[:_MAX_QUERY_CHARS] + " …(truncated)"
            _log.debug("Query truncated from %d to %d chars",
                       len(query), _MAX_QUERY_CHARS)

        # EAR-9: Build SOUL principles block (principles-first, R-3)
        soul_block = self._build_soul_block()
        # EAR-12: Build corrections anti-pattern block
        corrections_block = self._build_correction_block()
        if soul_block:
            prompt = _DELEGATION_TEMPLATE_WITH_SOUL.format(
                soul_block=soul_block,
                user_question=safe_query,
                scrubbed_context=context_str,
                corrections_block=corrections_block,
                budget=budget,
                max_response_chars=max_response,
            )
        else:
            prompt = _DELEGATION_TEMPLATE.format(
                user_question=safe_query,
                scrubbed_context=context_str,
                corrections_block=corrections_block,
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

    def _build_soul_block(self) -> str:
        """Build the SOUL principles block for injection at prompt head (EAR-9, R-3, R-8).

        Scans each principle individually through soul_allowlist.
        Returns formatted markdown block, or empty string if no principles.
        """
        agent = self._agent
        raw_principles: list[str] = []

        # From .agent.md soul_principles field
        if hasattr(agent, "soul_principles") and agent.soul_principles:
            raw_principles.extend(agent.soul_principles)

        # From blueprint examples (stop_conditions)
        if hasattr(agent, "stop_conditions") and agent.stop_conditions:
            raw_principles.extend(agent.stop_conditions)

        if not raw_principles:
            return ""

        try:
            from lib.soul_allowlist import filter_principles  # noqa: PLC0415
            allowed, scan_results = filter_principles(raw_principles)
        except ImportError:
            # Graceful degradation: soul_allowlist unavailable → skip injection
            _log.warning("soul_allowlist not available; skipping SOUL injection")
            return ""

        if not allowed:
            return ""

        lines = ["## Agent Principles"]
        for p in allowed:
            lines.append(f"- {p}")
        lines.append("")  # blank line separator
        return "\n".join(lines) + "\n"

    def _load_agent_memory(self, query: str) -> str:
        """Load relevant agent memory entries for context injection (EAR-1).

        Returns formatted memory block (≤1200 chars) or empty string.
        Integration point: called before context composition when EAR-1 enabled.
        """
        try:
            from lib.agent_memory import AgentMemory  # noqa: PLC0415
            memory = AgentMemory(agent_name=self._agent.name)
            return memory.load_relevant(query=query, max_chars=1200)
        except (ImportError, Exception):
            return ""

    def _load_propagated_context(self) -> str:
        """Load cross-agent propagated facts for context injection (EAR-10).

        Returns formatted propagation block or empty string.
        """
        try:
            from lib.knowledge_propagator import KnowledgePropagator  # noqa: PLC0415
            propagator = KnowledgePropagator()
            return propagator.load_for_agent(agent_name=self._agent.name)
        except (ImportError, Exception):
            return ""

    def _build_correction_block(self) -> str:
        """Build corrections anti-pattern block for injection (EAR-12).

        Returns formatted 'KNOWN CORRECTIONS' markdown block (indented for
        template injection) or empty string when no corrections exist.
        """
        try:
            from lib.correction_tracker import CorrectionTracker  # noqa: PLC0415
            tracker = CorrectionTracker(agent_name=self._agent.name)
            block = tracker.build_anti_pattern_block()
            if not block:
                return ""
            # Indent so it sits cleanly between Relevant Context and Constraints
            return "    " + block.replace("\n", "\n    ").rstrip() + "\n"
        except (ImportError, Exception):
            return ""

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
