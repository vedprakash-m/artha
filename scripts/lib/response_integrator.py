"""AR-9 External Agent Composition — Response Integrator (§4.6, Steps 4-7).

Responsibilities (integration pipeline):
  Step 4: Attribute — tag response sections with source labels.
  Step 5: Enrich   — layer Artha's own PRIVATE context on top.
  Step 6: Compose  — merge into Artha's response voice + Expert Consensus block.
  Step 7: Quality  — compute quality score (delegates to agent_scorer.py).

The integrator NEVER shares private enrichment content with external agents —
that information is layered on after the agent response is received.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lib.agent_scorer import score_agent_response
from lib.response_verifier import KBCheckResult

if TYPE_CHECKING:
    from lib.agent_invoker import AgentResult
    from lib.agent_registry import ExternalAgent

# ---------------------------------------------------------------------------
# Attribution labels (spec §4.6 Step 4)
# ---------------------------------------------------------------------------

_ATTRIBUTION_MAP = {
    "deployment": "Based on deployment guidance",
    "storage": "Based on storage expertise",
    "networking": "Based on networking expertise",
    "sprint": "From sprint board",
    "kusto": "Kusto telemetry",
    "ado": "From sprint board",
    "kb": "From local KB",
}

# ---------------------------------------------------------------------------
# IntegrationResult
# ---------------------------------------------------------------------------

@dataclass
class IntegrationResult:
    """Output of ResponseIntegrator.integrate()."""

    unified_prose: str
    """Full markdown response including Expert Consensus block."""

    quality_score: float
    """0.0–1.0 quality score from agent_scorer."""

    confidence_label: str
    """'HIGH' | 'MIXED' | 'EXTERNAL' | 'NONE' from KB check."""

    attribution: str
    """Short attribution label derived from the agent's domains."""

    expert_consensus_block: str
    """Standalone Expert Consensus block (may be appended separately)."""

    kb_corroborations: list[str] = field(default_factory=list)
    """Entity IDs corroborated by KB."""

    kb_contradictions: list[str] = field(default_factory=list)
    """Entity IDs where KB contradicts the response."""


# ---------------------------------------------------------------------------
# ResponseIntegrator
# ---------------------------------------------------------------------------

class ResponseIntegrator:
    """Integrates an external agent's response into Artha's answer.

    Usage::

        integrator = ResponseIntegrator()
        result = integrator.integrate(
            agent=agent,
            agent_result=agent_result,
            kb_check=kb_check,
            private_enrichment=["This blocks PBI-34521, due Friday"],
        )
        print(result.unified_prose)
    """

    def integrate(
        self,
        agent: "ExternalAgent",
        agent_result: "AgentResult",
        kb_check: KBCheckResult,
        private_enrichment: list[str] | None = None,
    ) -> IntegrationResult:
        """Run Steps 4-7 of the response integration pipeline.

        Args:
            agent: The ExternalAgent that produced the response.
            agent_result: Raw invocation result.
            kb_check: KB entity cross-check result (from ResponseVerifier).
            private_enrichment: Private context lines Artha layers on top.
                These are NEVER shared with the agent; they are added after.
        """
        response = agent_result.response
        enrichment = private_enrichment or []

        # Step 4: Attribution ────────────────────────────────────────────
        attribution = self._build_attribution(agent)

        # Step 5: Enrich ─────────────────────────────────────────────────
        enriched_prose = self._compose_prose(response, enrichment, attribution)

        # Step 6: Expert Consensus block ──────────────────────────────────
        consensus = self._build_consensus_block(
            agent=agent,
            response=response,
            kb_check=kb_check,
        )

        # Step 7: Quality score ───────────────────────────────────────────
        # (scorer imported from agent_scorer to avoid circular deps)
        quality = score_agent_response(response, "", kb_check)

        unified = enriched_prose.rstrip() + "\n\n" + consensus if consensus else enriched_prose

        return IntegrationResult(
            unified_prose=unified,
            quality_score=quality,
            confidence_label=kb_check.confidence_label,
            attribution=attribution,
            expert_consensus_block=consensus,
            kb_corroborations=list(kb_check.corroborations),
            kb_contradictions=list(kb_check.contradictions),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_attribution(self, agent: "ExternalAgent") -> str:
        """Pick the most specific attribution label from agent's domains."""
        domains = agent.routing.domains if agent.routing else []
        for domain in domains:
            label = _ATTRIBUTION_MAP.get(domain.lower())
            if label:
                return label
        return f"Based on {agent.label} analysis"

    def _compose_prose(
        self,
        response: str,
        enrichment: list[str],
        attribution: str,
    ) -> str:
        """Compose Artha's native-voice prose from agent response + enrichment.

        The goal is a single coherent response — not "the agent said... and
        I think...".  Attribution is woven in naturally.
        """
        lines: list[str] = []

        # Core agent insight (leading sentence mentions source naturally)
        intro = f"[{attribution}] " if attribution else ""
        lines.append(f"{intro}{response.strip()}")

        # Private enrichment layered on top
        if enrichment:
            lines.append("")
            lines.append("**Additional context:**")
            for item in enrichment:
                lines.append(f"- {item.strip()}")

        return "\n".join(lines)

    def _build_consensus_block(
        self,
        agent: "ExternalAgent",
        response: str,
        kb_check: KBCheckResult,
    ) -> str:
        """Build the Expert Consensus block (spec §4.6 Step 6)."""
        # Extract the first substantive sentence as the expert opinion quote
        first_sentence = _first_sentence(response)
        if not first_sentence:
            return ""

        lines: list[str] = []
        lines.append(
            f"> **Expert Opinion** ({agent.label}): \"{first_sentence}\""
        )

        # KB corroboration / contradiction line
        if kb_check.corroborations:
            corr_ids = ", ".join(kb_check.corroborations[:3])  # cap display
            lines.append(f"> **Local KB Check**: Corroborated by {corr_ids}.")
        elif kb_check.contradictions:
            contra_ids = ", ".join(kb_check.contradictions[:3])
            lines.append(
                f"> **Local KB Check**: ⚠️ Contradicted by {contra_ids} — "
                "review both perspectives."
            )
        elif kb_check.confidence_label == "EXTERNAL":
            lines.append(
                "> **Local KB Check**: Unverified — no matching KB entries."
            )
        else:
            lines.append("> **Local KB Check**: No entity cross-references found.")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_sentence(text: str) -> str:
    """Extract the first non-trivial sentence from text."""
    # Try splitting on common sentence terminators
    import re
    text = text.strip()
    # Remove markdown headings from the start
    text = re.sub(r"^#+\s+.*$", "", text, flags=re.MULTILINE).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for s in sentences:
        clean = s.strip()
        if len(clean) > 20:
            # Trim to ≤ 200 chars for the quote block
            if len(clean) > 200:
                clean = clean[:197] + "..."
            return clean
    return text[:200].strip() if text else ""
