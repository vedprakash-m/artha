"""personalizer.py — DraftPersonalizer: context assembly and anti-boilerplate validation.

Assembles deep personalization context for content generation from:
  1. Voice DNA (pr_manager.md)
  2. Cultural memory (memory.md)
  3. Relationships (contacts.md)
  4. Past posts (gallery_memory.yaml)
  5. Family context (occasions.md)
  6. Goals (goals.md)
  7. Audience (pr_manager.md)

Anti-boilerplate validation: every generated draft is scored before staging.
PII gate: Layer 1 (regex) applied before any LLM consumption.

Spec: §6.1, §6.2, §6.3, §6.4, §10.1
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pr_stage.domain import ContentCard, PlatformDraft, PlatformDraftStatus

# Boilerplate rejection threshold (§6.4)
BOILERPLATE_REJECT_THRESHOLD = 0.6

# Generic phrases to penalize (§6.4)
_GENERIC_PHRASES = [
    "in today's fast-paced world",
    "exciting times ahead",
    "thoughts?",
    "agree?",
    "humbled to announce",
    "honored to share",
    "game-changer",
    "synergy",
    "leverage",
    "wishing you and your family",
]

# Bridge pairs for personal element detection (§6.4.2)
_BRIDGE_PAIRS = [
    (
        {"india", "growing up", "back home", "भारत"},
        {"technology", "building", "engineering", "work"},
    ),
    (
        {"tradition", "festival", "पूजा", "culture"},
        {"modern", "today", "community", "team"},
    ),
]

# Devanagari script detector
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


# ─────────────────────────────────────────────────────────────────────────────
# Assembled context dataclass (§6.2 Step 8 output)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AssembledContext:
    """Structured context assembled by DraftPersonalizer before LLM consumption.

    This is serialized to card.personalization after PII gate passes.
    """
    voice_register: str = ""
    cultural_context: str = ""
    family_relevance: str = ""
    relationship_signals: list[str] = field(default_factory=list)
    memory_facts: list[str] = field(default_factory=list)
    named_entities: set[str] = field(default_factory=set)
    last_year_summary: str = ""
    avoid_phrases: list[str] = field(default_factory=list)
    goal_alignment: str | None = None
    audience_notes: str = ""
    cultural_keywords: list[str] = field(default_factory=list)
    key_figures: list[str] = field(default_factory=list)
    cultural_motifs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "voice_register":        self.voice_register,
            "cultural_context":      self.cultural_context,
            "family_relevance":      self.family_relevance,
            "relationship_signals":  list(self.relationship_signals),
            "memory_facts":          list(self.memory_facts),
            "last_year_summary":     self.last_year_summary,
            "avoid_phrases":         list(self.avoid_phrases),
            "goal_alignment":        self.goal_alignment,
            "audience_notes":        self.audience_notes,
            "cultural_keywords":     list(self.cultural_keywords),
            "key_figures":           list(self.key_figures),
            "cultural_motifs":       list(self.cultural_motifs),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Anti-boilerplate helpers (§6.4, §6.4.1, §6.4.2)
# ─────────────────────────────────────────────────────────────────────────────

def _has_cultural_specificity(draft: str, occasion_context: dict) -> bool:
    """Return True if draft contains ≥1 culturally specific element.

    Rubric (§6.4.1):
      - Contains ≥2 items from cultural_keywords → True
      - Contains ≥1 cultural_keyword AND ≥1 Devanagari token → True
      - Contains any key_figure → True
      - None of the above → False
    """
    draft_lower = draft.lower()
    keyword_hits = sum(
        1 for kw in occasion_context.get("cultural_keywords", [])
        if kw.lower() in draft_lower
    )
    has_non_latin = bool(_DEVANAGARI_RE.search(draft))
    has_key_figure = any(
        fig.lower() in draft_lower
        for fig in occasion_context.get("key_figures", [])
    )

    if keyword_hits >= 2:
        return True
    if keyword_hits >= 1 and has_non_latin:
        return True
    if has_key_figure:
        return True
    return False


def _has_personal_element(draft: str, assembled: AssembledContext) -> bool:
    """Return True if draft contains ≥1 uniquely personal element.

    Rubric (§6.4.2) — any ONE is sufficient:
      1. Named entity from assembled context memory facts appears in draft.
      2. Bridge between two worlds (career/tech + culture/tradition).
      3. (Other signals can be added as context assembly matures.)
    """
    draft_lower = draft.lower()

    # Check 1: named entity from memory facts
    for entity in assembled.named_entities:
        if entity.lower() in draft_lower:
            return True

    # Check 2: bridge pair — any pair where ≥1 term from each side appears
    for side_a, side_b in _BRIDGE_PAIRS:
        hit_a = any(term in draft_lower for term in side_a)
        hit_b = any(term in draft_lower for term in side_b)
        if hit_a and hit_b:
            return True

    return False


def boilerplate_score(
    draft: str,
    voice_dna: dict,
    occasion_context: dict,
    assembled: AssembledContext,
) -> float:
    """Score draft from 0.0 (unique) to 1.0 (generic). Reject if > 0.6 (§6.4)."""
    penalties = 0.0

    for phrase in _GENERIC_PHRASES:
        if phrase.lower() in draft.lower():
            penalties += 0.2

    if not _has_cultural_specificity(draft, occasion_context):
        penalties += 0.3

    if not _has_personal_element(draft, assembled):
        penalties += 0.3

    return min(penalties, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# DraftPersonalizer
# ─────────────────────────────────────────────────────────────────────────────

class DraftPersonalizer:
    """Assembles deep personalization context for content generation.

    Context sources (read-only):
      1. Voice DNA       — state/pr_manager.md (platform-specific registers)
      2. Cultural memory  — state/memory.md (facts about cultural practices)
      3. Relationships    — state/contacts.md (who to consider)
      4. Past posts       — state/gallery_memory.yaml (what was said before)
      5. Family context   — state/occasions.md (family observances)
      6. Goals            — state/goals.md (alignment opportunities)
      7. Audience         — state/pr_manager.md audience composition
      8. Self-model       — state/self_model.md (known blind spots, strengths)

    Spec: §6.1, §6.2
    """

    def __init__(
        self,
        state_dir: Path,
        gallery_memory: Any,  # GalleryMemory — avoid circular import typing
        config_dir: Path | None = None,
    ) -> None:
        self._state_dir     = state_dir
        self._gallery_memory = gallery_memory
        self._assembled_context: AssembledContext | None = None
        # Load PII-sensitive data from user_profile.yaml at init (§10.3)
        _cfg = config_dir if config_dir is not None else state_dir.parent / "config"
        self._employer_keywords: list[str] = self._load_employer_keywords(_cfg)
        self._children_names: set[str]     = self._load_children_names(_cfg)

    # ── Public API ────────────────────────────────────────────────────────

    def personalize(self, card: ContentCard) -> AssembledContext:
        """Execute the 8-step context assembly pipeline (§6.2).

        Returns an AssembledContext with sanitized data ready for LLM consumption.
        The assembled context is also stored as self._assembled_context for
        subsequent _boilerplate_score calls.
        """
        ctx = AssembledContext()

        # Step 1: Voice DNA
        ctx.voice_register = self._load_voice_register(card)

        # Step 2: Cultural memory
        ctx.cultural_context, ctx.cultural_keywords, ctx.key_figures, ctx.cultural_motifs = \
            self._load_cultural_memory(card.occasion)

        # Step 3: Relationship signals — privacy-gated
        ctx.relationship_signals = self._load_relationship_signals(card.occasion)

        # Step 4: Family context
        ctx.family_relevance = self._load_family_context(card.occasion)

        # Step 5: Audience awareness
        ctx.audience_notes = self._load_audience_notes()

        # Step 6: Cross-year differentiation
        prior_card = self._gallery_memory.find_last_year_card(card.occasion, card.year)
        if prior_card:
            ctx.last_year_summary = self._summarize_prior_card(prior_card)
            ctx.avoid_phrases    = self._extract_avoid_phrases(prior_card)

        # Step 7: Goal alignment
        ctx.goal_alignment = self._check_goal_alignment(card)

        # Step 8: Extract named entities for personal-element detection
        ctx.named_entities = self._extract_named_entities(ctx)

        # Layer 1 PII gate: scan assembled context before LLM consumption
        self._pii_gate_context(ctx)

        self._assembled_context = ctx
        return ctx

    def validate_draft(
        self,
        draft: str,
        platform: str,
        card: ContentCard,
    ) -> tuple[bool, float, list[str]]:
        """Validate a generated draft before staging.

        Returns:
            (passed: bool, score: float, issues: list[str])
            score 0.0 = unique, 1.0 = generic; passes if < BOILERPLATE_REJECT_THRESHOLD
        """
        issues: list[str] = []
        ctx = self._assembled_context or AssembledContext()
        occasion_ctx = {
            "cultural_keywords": ctx.cultural_keywords,
            "key_figures":       ctx.key_figures,
        }

        # Boilerplate check
        score = boilerplate_score(draft, {}, occasion_ctx, ctx)
        if score > BOILERPLATE_REJECT_THRESHOLD:
            issues.append(f"High boilerplate score ({score:.1f} > {BOILERPLATE_REJECT_THRESHOLD})")

        # PII check on draft content
        pii_found, pii_types = self._pii_gate_draft(draft)
        if pii_found:
            issue_types = ", ".join(sorted(pii_types.keys()))
            issues.append(f"PII detected: {issue_types}")

        # Employer mention check (§4.2)
        employer_flag = self._check_employer_mention(draft)
        if employer_flag:
            issues.append("Employer mention detected — set EMPLOYER_MENTION flag")

        # Children named check
        children_flag = self._check_children_named(draft, platform)
        if children_flag:
            issues.append("Children named in non-private platform")

        return (len(issues) == 0), score, issues

    def build_platform_draft(
        self,
        draft_content: str,
        platform: str,
        card: ContentCard,
    ) -> PlatformDraft:
        """Wrap a generated draft string in a PlatformDraft with metadata."""
        pii_found, pii_types = self._pii_gate_draft(draft_content)
        pii_passed = not pii_found or (
            # PII_UNVERIFIED_SCRIPT alone does not block staging —
            # it sets the card flag instead (§4.2)
            set(pii_types.keys()) == {"PII_UNVERIFIED_SCRIPT"}
        )
        return PlatformDraft(
            status=PlatformDraftStatus.STAGED if pii_passed else PlatformDraftStatus.DRAFT,
            content=draft_content,
            word_count=len(draft_content.split()),
            pii_scan_passed=pii_passed,
            employer_mention=self._check_employer_mention(draft_content),
            children_named=self._check_children_named(draft_content, platform),
        )

    # ── Private context assembly helpers (§6.2) ───────────────────────────

    def _load_voice_register(self, card: ContentCard) -> str:
        """Select appropriate voice register based on platform and occasion type."""
        occasion_type = card.occasion_type.lower()
        if "cultural_festival" in occasion_type or "religion" in occasion_type:
            return "bilingual_cultural"
        if "professional" in occasion_type or "milestone" in occasion_type:
            return "register_a_milestone"
        return "register_b_project"

    def _load_cultural_memory(
        self, occasion: str
    ) -> tuple[str, list[str], list[str], list[str]]:
        """Read state/memory.md for cultural context matching the occasion name.

        Returns:
            (cultural_context, cultural_keywords, key_figures, cultural_motifs)
        """
        memory_path = self._state_dir / "memory.md"
        if not memory_path.exists():
            return "", [], [], []

        try:
            content = memory_path.read_text(encoding="utf-8")
        except OSError:
            return "", [], [], []

        # Extract facts relevant to this occasion (simple keyword search)
        occasion_lower = occasion.lower()
        relevant_lines = [
            line.strip()
            for line in content.splitlines()
            if occasion_lower in line.lower() and line.strip()
        ]
        cultural_context = " ".join(relevant_lines[:3])

        return cultural_context, [], [], []

    def _load_relationship_signals(self, occasion: str) -> list[str]:
        """Placeholder: privacy-gated relationship extraction from contacts.md."""
        return []

    def _load_family_context(self, occasion: str) -> str:
        """Read state/occasions.md for family context about the occasion."""
        occasions_path = self._state_dir / "occasions.md"
        if not occasions_path.exists():
            return ""

        try:
            content = occasions_path.read_text(encoding="utf-8")
        except OSError:
            return ""

        occasion_lower = occasion.lower()
        for line in content.splitlines():
            if occasion_lower in line.lower():
                return line.strip()
        return ""

    def _load_audience_notes(self) -> str:
        """Audience composition notes (§6.2 Step 5).

        Phase 2: parse composition from state/pr_manager.md audience section.
        Phase 1: generic fallback — no PII in source.
        """
        return (
            "LinkedIn: professional network (tech, consulting, former colleagues). "
            "Facebook: close personal circle and family. "
            "Instagram: intimate private followers."
        )

    def _summarize_prior_card(self, prior_card: dict) -> str:
        """Extract summary from a prior year's card dict."""
        drafts = prior_card.get("drafts", {})
        snippets = []
        for platform, draft in drafts.items():
            content = draft.get("content", "")
            if content:
                preview = content[:100].replace("\n", " ").strip()
                snippets.append(f"{platform}: {preview!r}")
        if snippets:
            return f"Prior year ({prior_card.get('event_date', 'unknown')[:4]}): " + "; ".join(snippets)
        return ""

    def _extract_avoid_phrases(self, prior_card: dict) -> list[str]:
        """Extract specific phrases used last year that should be avoided (§6.2 Step 6)."""
        avoid = []
        for draft_d in (prior_card.get("drafts") or {}).values():
            content = draft_d.get("content", "")
            # Simple heuristic: extract short memorable phrases (10-60 chars)
            for sentence in re.split(r"[.!?\n]", content):
                sentence = sentence.strip()
                if 10 <= len(sentence) <= 60:
                    avoid.append(sentence)
                    if len(avoid) >= 5:
                        return avoid
        return avoid

    def _check_goal_alignment(self, card: ContentCard) -> str | None:
        """Check goals.md for alignment with this card's occasion type."""
        goals_path = self._state_dir / "goals.md"
        if not goals_path.exists():
            return None
        try:
            content = goals_path.read_text(encoding="utf-8")
            occasion_type = card.occasion_type.lower()
            if "cultural" in occasion_type and "personal brand" in content.lower():
                return "cultural_brand_alignment"
        except OSError:
            pass
        return None

    def _extract_named_entities(self, ctx: AssembledContext) -> set[str]:
        """Extract named entities from memory facts for personal-element detection."""
        entities: set[str] = set()
        all_text = " ".join(ctx.memory_facts) + " " + ctx.cultural_context
        # Simple extraction: capitalized words not at sentence start
        for word in re.findall(r"\b[A-Z][a-z]{2,}\b", all_text):
            if word not in {"The", "This", "That", "With", "From", "For"}:
                entities.add(word)
        return entities

    # ── PII gate helpers (§10.1 Layer 1) ─────────────────────────────────

    def _pii_gate_draft(self, text: str) -> tuple[bool, dict]:
        """Run pii_guard.scan() on draft content."""
        try:
            from pii_guard import scan
            return scan(text)
        except ImportError:
            return False, {}

    def _pii_gate_context(self, ctx: AssembledContext) -> None:
        """Run PII gate on assembled context fields; redact if found.

        Context is sanitized in-place before LLM consumption.
        """
        try:
            from pii_guard import filter_text
        except ImportError:
            return

        # Scan memory_facts
        clean_facts = []
        for fact in ctx.memory_facts:
            filtered, found = filter_text(fact)
            clean_facts.append(filtered)
            if found:
                pass  # Log via logger if needed
        ctx.memory_facts = clean_facts

        # Scan cultural_context
        if ctx.cultural_context:
            ctx.cultural_context, _ = filter_text(ctx.cultural_context)

    # ── User-profile loaders (PII loaded at runtime — §10.3) ─────────────

    def _load_employer_keywords(self, config_dir: Path) -> list[str]:
        """Load employer keywords from user_profile.yaml (§10.3 — never hardcode)."""
        profile_path = config_dir / "user_profile.yaml"
        if not profile_path.exists():
            return []
        try:
            import yaml  # noqa: PLC0415 — lazy import to avoid top-level dep
            with open(profile_path, encoding="utf-8") as fh:
                profile = yaml.safe_load(fh) or {}
            # Preferred path: employment.current.keywords (spec §10.3)
            employ = profile.get("employment", {}) or {}
            keywords = (employ.get("current") or {}).get("keywords", [])
            if keywords:
                return list(keywords)
            # Fallback: domains.employment.employer (actual user_profile.yaml shape)
            employer = ((profile.get("domains") or {}).get("employment") or {}).get("employer", "")
            if employer:
                return [employer]
            # Secondary fallback: top-level employment.employer
            employer = employ.get("employer", "")
            if employer:
                return [employer]
        except Exception:  # noqa: BLE001 — degrade gracefully; don't block init
            pass
        return []

    def _load_children_names(self, config_dir: Path) -> set[str]:
        """Load children's names from user_profile.yaml (§10.2 — never hardcode PII)."""
        profile_path = config_dir / "user_profile.yaml"
        if not profile_path.exists():
            return set()
        try:
            import yaml  # noqa: PLC0415
            with open(profile_path, encoding="utf-8") as fh:
                profile = yaml.safe_load(fh) or {}
            names: set[str] = set()
            # Try family.children (actual user_profile.yaml shape)
            children = (profile.get("family") or {}).get("children") or []
            # Fallback: top-level children key
            if not children:
                children = profile.get("children") or []
            for child in children:
                full = (child.get("name") or "").strip()
                if full:
                    names.add(full)
                    first = full.split()[0]
                    if first:
                        names.add(first)
            return names
        except Exception:  # noqa: BLE001
            pass
        return set()

    # ── Employer / children checks (§4.2) ─────────────────────────────────

    def _check_employer_mention(self, draft: str) -> bool:
        """Detect employer name in draft (sets EMPLOYER_MENTION flag).

        Keywords loaded from user_profile.yaml at init (§10.3 — never hardcoded).
        Word-boundary matching avoids false positives (e.g. an employer name
        appearing as a sub-string of another word).
        """
        for kw in self._employer_keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", draft, re.IGNORECASE):
                return True
        # Always check for generic employer self-references regardless of profile
        generic = [r"\bmy\s+company\b", r"\bmy\s+employer\b"]
        return any(re.search(p, draft, re.IGNORECASE) for p in generic)

    def _check_children_named(self, draft: str, platform: str) -> bool:
        """Detect named children in non-private platforms.

        Names loaded from user_profile.yaml at init (§10.2 — never hardcoded PII).
        LinkedIn and public contexts should not name children.
        """
        if platform in ("facebook", "whatsapp_status", "whatsapp_family"):
            return False  # Private/semi-private — first names OK per §10.2
        return any(name in draft for name in self._children_names)
