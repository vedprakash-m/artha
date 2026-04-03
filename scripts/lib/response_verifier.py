"""AR-9 External Agent Composition — Response Verifier (§4.6, Steps 2-3).

Responsibilities:
  Step 2: Injection scan on inbound agent response.
  Step 3: Cross-check named entities in the response against local KB.

The verifier is intentionally scoped to *entity matching*, not arbitrary
claim extraction (spec §4.6 Ops R2 — LLM-as-judge deferred to V1.1-J).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from scripts.lib.injection_detector import InjectionDetector

# ---------------------------------------------------------------------------
# Entity extraction patterns
# ---------------------------------------------------------------------------

# IcM incident numbers (e.g., IcM-36153544, ICM-123456)
_ICM_RE = re.compile(r"\b(?:ICM|IcM|icm)-?(\d{6,})\b")

# Azure region identifiers
_REGION_RE = re.compile(
    r"\b(eastus|westus|centralus|northeurope|westeurope|eastasia|"
    r"southeastasia|australiaeast|brazilsouth|canadacentral|"
    r"japaneast|koreacentral|uksouth|[a-z]+east\d*|[a-z]+west\d*|"
    r"[a-z]+central\d*)\b",
    re.IGNORECASE,
)

# Error codes (e.g., E_DISK_FULL, 0x8007001F, HTTP 503, error: 404)
_ERROR_CODE_RE = re.compile(
    r"\b(E_[A-Z_]{3,}|0x[0-9A-Fa-f]{4,}|HTTP\s+[45]\d{2}|"
    r"error(?:\s+code)?:\s*\S+|exception:\s*\S+)\b",
    re.IGNORECASE,
)

# Service/system names (generic CamelCase identifiers ≥ 6 chars)
_SYSTEM_RE = re.compile(r"\b([A-Z][a-zA-Z]{5,}(?:[A-Z][a-zA-Z]+)*)\b")

# PBI / ADO work item numbers
_PBI_RE = re.compile(r"\b(?:PBI|ADO|Task|Bug|Feature)-?(\d{4,})\b", re.IGNORECASE)

# SKU identifiers (e.g., SKU-S1234-P, SKU4502)
_SKU_RE = re.compile(r"\b(?:SKU[-_]?[A-Z0-9]{3,})\b", re.IGNORECASE)


def _extract_entities(text: str) -> dict[str, list[str]]:
    """Extract named entities from text by category."""
    return {
        "icm": _ICM_RE.findall(text),
        "region": _REGION_RE.findall(text),
        "error_code": _ERROR_CODE_RE.findall(text),
        "system": _SYSTEM_RE.findall(text),
        "pbi": _PBI_RE.findall(text),
        "sku": _SKU_RE.findall(text),
    }


# ---------------------------------------------------------------------------
# KB entity indexing (lightweight, no LLM)
# ---------------------------------------------------------------------------

def _load_kb_text(knowledge_dir: Path) -> str:
    """Concatenate all .md files under knowledge/ into a single string."""
    if not knowledge_dir.is_dir():
        return ""
    parts: list[str] = []
    for path in sorted(knowledge_dir.glob("**/*.md")):
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# KBCheckResult
# ---------------------------------------------------------------------------

class KBCheckResult(NamedTuple):
    """Result of cross-checking a response against the local KB.

    Fields:
        agreement_ratio: 0.0–1.0, fraction of entity mentions corroborated.
        contradictions: Entity IDs present in response but contradicted by KB.
        corroborations: Entity IDs present in both response and KB.
        confidence_label: 'HIGH' | 'EXTERNAL' | 'MIXED' | 'NONE'
    """

    agreement_ratio: float
    contradictions: list[str]
    corroborations: list[str]
    confidence_label: str


_KB_CHECK_NONE = KBCheckResult(
    agreement_ratio=0.5,
    contradictions=[],
    corroborations=[],
    confidence_label="NONE",
)


# ---------------------------------------------------------------------------
# ResponseVerifier
# ---------------------------------------------------------------------------

class ResponseVerifier:
    """Verifies an agent response via injection scan and KB entity cross-check.

    Usage::

        verifier = ResponseVerifier(knowledge_dir=Path("knowledge"))
        injection_clean, kb_check = verifier.verify("deployment is stuck...", "why stuck")
        if not injection_clean:
            # Discard — P0 alert, fall to fallback
            ...
        score = score_agent_response(response, query, kb_check)
    """

    def __init__(
        self,
        knowledge_dir: Path | None = None,
        detector: InjectionDetector | None = None,
    ) -> None:
        self._knowledge_dir = knowledge_dir
        self._detector = detector or InjectionDetector()
        self._kb_text: str | None = None  # lazy-loaded

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(
        self,
        response: str,
        query: str,  # noqa: ARG002 – stored for future V1.1-J expansion
    ) -> tuple[bool, KBCheckResult]:
        """Run Step 2 (injection scan) and Step 3 (KB entity check).

        Returns:
            (injection_clean, kb_check):
                injection_clean — False means caller MUST discard the response.
                kb_check        — Entity cross-check result.
        """
        # Step 2: Injection scan -----------------------------------------
        scan = self._detector.scan(response)
        if scan.injection_detected:
            return False, _KB_CHECK_NONE

        # Step 3: KB entity cross-check -----------------------------------
        kb_check = self._kb_entity_check(response)
        return True, kb_check

    # ------------------------------------------------------------------
    # KB entity check
    # ------------------------------------------------------------------

    def _kb_entity_check(self, response: str) -> KBCheckResult:
        """Cross-check entity mentions in response against local KB."""
        kb_text = self._get_kb_text()
        if not kb_text:
            return _KB_CHECK_NONE

        entities = _extract_entities(response)
        all_mentioned: list[str] = []
        for category, values in entities.items():
            for val in values:
                all_mentioned.append(f"{category}:{val}")

        if not all_mentioned:
            # No entities to check — neutral score
            return KBCheckResult(
                agreement_ratio=0.5,
                contradictions=[],
                corroborations=[],
                confidence_label="NONE",
            )

        kb_lower = kb_text.lower()
        corroborations: list[str] = []
        # Contraction detection is simple: entity present in response but KB
        # explicitly says the opposite (e.g., "not found", "does not exist").
        # V1: we only check presence; V1.1-J adds semantic contradiction detection.
        contradictions: list[str] = []

        for entity_id in all_mentioned:
            _, raw = entity_id.split(":", 1)
            raw_lower = raw.lower()
            if raw_lower in kb_lower:
                corroborations.append(entity_id)
            # No V1 contradiction detection (requires LLM-as-judge)

        total = len(all_mentioned)
        corr_count = len(corroborations)
        agreement_ratio = corr_count / total if total > 0 else 0.5

        if agreement_ratio >= 0.7:
            confidence_label = "HIGH"
        elif corr_count == 0:
            confidence_label = "EXTERNAL"
        else:
            confidence_label = "MIXED"

        return KBCheckResult(
            agreement_ratio=agreement_ratio,
            contradictions=contradictions,
            corroborations=corroborations,
            confidence_label=confidence_label,
        )

    def _get_kb_text(self) -> str:
        if self._kb_text is None:
            if self._knowledge_dir is None:
                self._kb_text = ""
            else:
                self._kb_text = _load_kb_text(self._knowledge_dir)
        return self._kb_text
