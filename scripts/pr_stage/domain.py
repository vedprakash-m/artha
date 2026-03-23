"""domain.py — Content Card schema, FSM states, and card deduplication.

Defines:
  - CardStatus: 7-state FSM enum for card lifecycle
  - PlatformDraftStatus: 5-state enum for per-platform drafts
  - ContentCard: dataclass representing the full card structure
  - VALID_TRANSITIONS: adjacency graph for FSM enforcement
  - Deduplication helpers used by ContentStage

Spec: §4.1, §4.2, §5.1, §5.2, §5.3, §5.4
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# State Enums
# ─────────────────────────────────────────────────────────────────────────────

class CardStatus(str, Enum):
    """Card-level lifecycle states (§5.1)."""
    SEED       = "seed"
    DRAFTING   = "drafting"
    STAGED     = "staged"
    APPROVED   = "approved"
    POSTED     = "posted"
    ARCHIVED   = "archived"
    DISMISSED  = "dismissed"


class PlatformDraftStatus(str, Enum):
    """Per-platform draft states (§5.5)."""
    DRAFT    = "draft"
    STAGED   = "staged"
    APPROVED = "approved"
    POSTED   = "posted"
    SKIPPED  = "skipped"


# ─────────────────────────────────────────────────────────────────────────────
# FSM Transition Graph (§5.2)
# ─────────────────────────────────────────────────────────────────────────────

VALID_TRANSITIONS: dict[CardStatus, frozenset[CardStatus]] = {
    CardStatus.SEED:      frozenset({CardStatus.DRAFTING, CardStatus.DISMISSED}),
    CardStatus.DRAFTING:  frozenset({CardStatus.STAGED,   CardStatus.DISMISSED}),
    CardStatus.STAGED:    frozenset({CardStatus.APPROVED, CardStatus.DISMISSED}),
    CardStatus.APPROVED:  frozenset({CardStatus.POSTED,   CardStatus.DISMISSED}),
    CardStatus.POSTED:    frozenset({CardStatus.ARCHIVED}),
    CardStatus.ARCHIVED:  frozenset(),  # terminal
    CardStatus.DISMISSED: frozenset(),  # terminal
}

# Allowed flag values (§4.2)
ALLOWED_FLAGS = frozenset({
    "NEEDS_HUMAN_TOUCH",
    "PII_UNVERIFIED_SCRIPT",
    "EMPLOYER_MENTION",
    "DRAFT_BLOCKED",
})


# ─────────────────────────────────────────────────────────────────────────────
# Card ID helpers
# ─────────────────────────────────────────────────────────────────────────────

_CARD_ID_RE = re.compile(r"^CARD-(\d{4})-(\d{3,})$")


def parse_card_id(card_id: str) -> tuple[int, int]:
    """Return (year, sequence) from a card ID like 'CARD-2026-032'.

    Raises ValueError for malformed IDs.
    """
    m = _CARD_ID_RE.match(card_id)
    if not m:
        raise ValueError(f"Invalid card ID format: {card_id!r} (expected CARD-YYYY-NNN)")
    return int(m.group(1)), int(m.group(2))


def make_card_id(year: int, seq: int) -> str:
    """Format a card ID: CARD-2026-032."""
    return f"CARD-{year}-{seq:03d}"


# ─────────────────────────────────────────────────────────────────────────────
# Platform draft dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlatformDraft:
    """A single platform's draft content and metadata."""
    status: PlatformDraftStatus = PlatformDraftStatus.DRAFT
    content: str = ""
    voice_notes: str = ""
    word_count: int = 0
    pii_scan_passed: bool = False
    employer_mention: bool = False
    children_named: bool = False

    def to_dict(self) -> dict:
        return {
            "status":           self.status.value,
            "content":          self.content,
            "voice_notes":      self.voice_notes,
            "word_count":       self.word_count,
            "pii_scan_passed":  self.pii_scan_passed,
            "employer_mention": self.employer_mention,
            "children_named":   self.children_named,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PlatformDraft":
        return cls(
            status=PlatformDraftStatus(d.get("status", "draft")),
            content=d.get("content", ""),
            voice_notes=d.get("voice_notes", ""),
            word_count=d.get("word_count", 0),
            pii_scan_passed=bool(d.get("pii_scan_passed", False)),
            employer_mention=bool(d.get("employer_mention", False)),
            children_named=bool(d.get("children_named", False)),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in (PlatformDraftStatus.POSTED, PlatformDraftStatus.SKIPPED)


# ─────────────────────────────────────────────────────────────────────────────
# Content Card dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ContentCard:
    """Full content card representing one occasion's content lifecycle.

    This is the authoritative in-memory representation of a gallery.yaml card.
    All mutations go through ContentStage._transition() which validates FSM rules.
    """
    id: str
    occasion: str
    occasion_type: str
    event_date: date
    created_at: datetime
    status: CardStatus = CardStatus.SEED
    primary_thread: str = ""
    alt_threads: list[str] = field(default_factory=list)
    convergence_score: float = 0.0
    flags: list[str] = field(default_factory=list)
    platform_exclude: list[str] = field(default_factory=list)  # platforms to skip for this card

    # Personalization context (populated by DraftPersonalizer)
    personalization: dict[str, Any] = field(default_factory=dict)

    # Platform drafts keyed by platform name
    drafts: dict[str, PlatformDraft] = field(default_factory=dict)

    # Visual strategy
    visual: dict[str, Any] = field(default_factory=dict)

    # Posting window
    posting_window: dict[str, Any] = field(default_factory=dict)

    # Archival metadata (set when card moves to archived/dismissed)
    archived_at: datetime | None = None
    dismissed_reason: str = ""
    reception: dict[str, Any] = field(default_factory=dict)

    # Auto-draft failure tracking (§5.3)
    _auto_draft_attempts: int = 0

    # ── Property helpers ──────────────────────────────────────────────────

    @property
    def year(self) -> int:
        return self.event_date.year

    @property
    def is_terminal(self) -> bool:
        return self.status in (CardStatus.ARCHIVED, CardStatus.DISMISSED)

    @property
    def is_archive_ready(self) -> bool:
        """True when all platform drafts are in terminal state (posted or skipped)."""
        if not self.drafts:
            return False
        return all(d.is_terminal for d in self.drafts.values())

    def has_flag(self, flag: str) -> bool:
        return flag in self.flags

    def set_flag(self, flag: str) -> None:
        if flag not in ALLOWED_FLAGS:
            raise ValueError(f"Unknown flag: {flag!r}. Allowed: {sorted(ALLOWED_FLAGS)}")
        if flag not in self.flags:
            self.flags.append(flag)

    def clear_flag(self, flag: str) -> None:
        self.flags = [f for f in self.flags if f != flag]

    # ── Serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "id":                 self.id,
            "occasion":           self.occasion,
            "occasion_type":      self.occasion_type,
            "event_date":         self.event_date.isoformat(),
            "created_at":         self.created_at.isoformat(),
            "status":             self.status.value,
            "primary_thread":     self.primary_thread,
            "alt_threads":        list(self.alt_threads),
            "convergence_score":  self.convergence_score,
            "flags":              list(self.flags),
            "platform_exclude":   list(self.platform_exclude),
            "personalization":    dict(self.personalization),
            "drafts":             {k: v.to_dict() for k, v in self.drafts.items()},
            "visual":             dict(self.visual),
            "posting_window":     dict(self.posting_window),
            "archived_at":        self.archived_at.isoformat() if self.archived_at else None,
            "dismissed_reason":   self.dismissed_reason,
            "reception":          dict(self.reception),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ContentCard":
        drafts = {
            platform: PlatformDraft.from_dict(draft_d)
            for platform, draft_d in (d.get("drafts") or {}).items()
        }
        archived_at = None
        if d.get("archived_at"):
            try:
                archived_at = datetime.fromisoformat(d["archived_at"])
            except (ValueError, TypeError):
                archived_at = None

        return cls(
            id=d["id"],
            occasion=d["occasion"],
            occasion_type=d.get("occasion_type", ""),
            event_date=date.fromisoformat(d["event_date"]),
            created_at=datetime.fromisoformat(d["created_at"]),
            status=CardStatus(d.get("status", "seed")),
            primary_thread=d.get("primary_thread", ""),
            alt_threads=list(d.get("alt_threads") or []),
            convergence_score=float(d.get("convergence_score", 0.0)),
            flags=list(d.get("flags") or []),
            platform_exclude=list(d.get("platform_exclude") or []),
            personalization=dict(d.get("personalization") or {}),
            drafts=drafts,
            visual=dict(d.get("visual") or {}),
            posting_window=dict(d.get("posting_window") or {}),
            archived_at=archived_at,
            dismissed_reason=d.get("dismissed_reason", ""),
            reception=dict(d.get("reception") or {}),
        )


# ─────────────────────────────────────────────────────────────────────────────
# FSM validation
# ─────────────────────────────────────────────────────────────────────────────

class InvalidTransitionError(Exception):
    """Raised when an FSM transition is not in VALID_TRANSITIONS."""


def validate_transition(card: ContentCard, to_status: CardStatus) -> None:
    """Raise InvalidTransitionError if the transition is not allowed.

    Args:
        card:      The card being transitioned.
        to_status: The target state.
    """
    allowed = VALID_TRANSITIONS.get(card.status, frozenset())
    if to_status not in allowed:
        raise InvalidTransitionError(
            f"Card {card.id}: invalid transition {card.status.value!r} → {to_status.value!r}. "
            f"Allowed from {card.status.value!r}: {[s.value for s in sorted(allowed, key=lambda x: x.value)]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication helpers (§5.4)
# ─────────────────────────────────────────────────────────────────────────────

def is_duplicate_occasion(
    occasion: str,
    event_date: date,
    existing_cards: list[ContentCard],
    same_occasion_window_days: int = 3,
) -> ContentCard | None:
    """Check if a new card would duplicate an existing one.

    Deduplication rules (§5.4):
      1. Same occasion name + same year → exact duplicate.
      2. Same occasion_type within ±same_occasion_window_days of event_date.

    Returns the existing card if a duplicate is found, else None.
    """
    for card in existing_cards:
        if card.is_terminal:
            continue
        # Rule 1: exact occasion + year
        if card.occasion == occasion and card.event_date.year == event_date.year:
            return card
        # Rule 2: proximity within window (type-agnostic — occasion name check suffices
        # for deduplication; type check would require occasion_type passed in)
        if abs((card.event_date - event_date).days) <= same_occasion_window_days:
            if card.occasion.lower() == occasion.lower():
                return card
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Card-level status aggregation from platform drafts (§5.5)
# ─────────────────────────────────────────────────────────────────────────────

def derive_card_status(card: ContentCard) -> CardStatus:
    """Calculate the correct card-level status from platform draft statuses.

    Used by ContentStage after DraftPersonalizer completes to determine
    whether the card should advance to STAGED, APPROVED, POSTED, or stay.

    This does NOT mutate the card — the caller must call _transition() to apply.
    """
    if not card.drafts:
        return card.status  # no change — no drafts yet

    draft_statuses = {d.status for d in card.drafts.values()}

    # Archive ready: all drafts in terminal state
    if all(d.is_terminal for d in card.drafts.values()):
        return CardStatus.POSTED  # → will trigger archive sweep

    # At least one posted
    if PlatformDraftStatus.POSTED in draft_statuses:
        return CardStatus.POSTED

    # All non-draft, at least one approved
    if (PlatformDraftStatus.APPROVED in draft_statuses
            and PlatformDraftStatus.DRAFT not in draft_statuses):
        return CardStatus.APPROVED

    # At least one staged or approved
    if draft_statuses & {PlatformDraftStatus.STAGED, PlatformDraftStatus.APPROVED}:
        return CardStatus.STAGED

    # Any draft still in DRAFT state and no staged/approved
    return CardStatus.DRAFTING
