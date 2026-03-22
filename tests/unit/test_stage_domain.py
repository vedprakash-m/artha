"""Phase 1 tests — pr_stage domain (FSM, serialization, deduplication).

Tests cover:
  - CardStatus and PlatformDraftStatus enum values
  - make_card_id / parse_card_id formatting and validation
  - VALID_TRANSITIONS FSM adjacency graph
  - validate_transition() — allow and block paths
  - ContentCard serialization round-trip (to_dict / from_dict)
  - ContentCard flag management (set_flag, has_flag, clear_flag)
  - PlatformDraft serialization round-trip
  - is_duplicate_occasion() deduplication logic
  - derive_card_status() card-level status aggregation

Spec: §4.1, §4.2, §5.1–5.5
"""
from __future__ import annotations

import pytest
from datetime import date, datetime, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from pr_stage.domain import (
    CardStatus,
    PlatformDraft,
    PlatformDraftStatus,
    ContentCard,
    VALID_TRANSITIONS,
    ALLOWED_FLAGS,
    InvalidTransitionError,
    make_card_id,
    parse_card_id,
    validate_transition,
    is_duplicate_occasion,
    derive_card_status,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_card(
    status: CardStatus = CardStatus.SEED,
    occasion: str = "Diwali",
    event_date: date | None = None,
    card_id: str = "CARD-2026-001",
) -> ContentCard:
    return ContentCard(
        id=card_id,
        occasion=occasion,
        occasion_type="cultural_festival",
        event_date=event_date or date(2026, 10, 20),
        created_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        status=status,
    )


# ─────────────────────────────────────────────────────────────────────────────
# make_card_id / parse_card_id
# ─────────────────────────────────────────────────────────────────────────────

class TestCardIdHelpers:
    def test_make_card_id_pads_sequence(self):
        assert make_card_id(2026, 1) == "CARD-2026-001"
        assert make_card_id(2026, 9) == "CARD-2026-009"
        assert make_card_id(2026, 42) == "CARD-2026-042"
        assert make_card_id(2026, 100) == "CARD-2026-100"

    def test_parse_card_id_roundtrip(self):
        year, seq = parse_card_id("CARD-2026-007")
        assert year == 2026 and seq == 7

    def test_parse_card_id_large_sequence(self):
        year, seq = parse_card_id("CARD-2027-123")
        assert year == 2027 and seq == 123

    def test_parse_card_id_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid card ID"):
            parse_card_id("INVALID-2026-001")

    def test_parse_card_id_too_short_raises(self):
        with pytest.raises(ValueError):
            parse_card_id("CARD-2026-01")   # only 2 digits in sequence


# ─────────────────────────────────────────────────────────────────────────────
# FSM transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestFSMTransitions:
    """VALID_TRANSITIONS adjacency and validate_transition guard."""

    def test_seed_to_drafting_allowed(self):
        card = _make_card(CardStatus.SEED)
        validate_transition(card, CardStatus.DRAFTING)  # no exception

    def test_seed_to_dismissed_allowed(self):
        card = _make_card(CardStatus.SEED)
        validate_transition(card, CardStatus.DISMISSED)

    def test_seed_to_approved_blocked(self):
        card = _make_card(CardStatus.SEED)
        with pytest.raises(InvalidTransitionError, match="SEED.*APPROVED|seed.*approved"):
            validate_transition(card, CardStatus.APPROVED)

    def test_seed_to_posted_blocked(self):
        card = _make_card(CardStatus.SEED)
        with pytest.raises(InvalidTransitionError):
            validate_transition(card, CardStatus.POSTED)

    def test_full_happy_path_each_step_valid(self):
        """SEED→DRAFTING→STAGED→APPROVED→POSTED→ARCHIVED is fully valid."""
        card = _make_card(CardStatus.SEED)
        steps = [
            CardStatus.DRAFTING,
            CardStatus.STAGED,
            CardStatus.APPROVED,
            CardStatus.POSTED,
            CardStatus.ARCHIVED,
        ]
        for step in steps:
            validate_transition(card, step)
            card.status = step

    def test_archived_is_terminal(self):
        card = _make_card(CardStatus.ARCHIVED)
        for target in CardStatus:
            if target != CardStatus.ARCHIVED:
                with pytest.raises(InvalidTransitionError):
                    validate_transition(card, target)

    def test_dismissed_is_terminal(self):
        card = _make_card(CardStatus.DISMISSED)
        with pytest.raises(InvalidTransitionError):
            validate_transition(card, CardStatus.SEED)

    def test_all_states_have_transition_entries(self):
        for status in CardStatus:
            assert status in VALID_TRANSITIONS, f"{status} missing from VALID_TRANSITIONS"

    def test_dismissal_allowed_from_any_active_state(self):
        """Non-terminal, non-POSTED states must allow DISMISSED.

        POSTED only allows ARCHIVED (§5.2 — POSTED cards move to archive
        automatically; the user cannot dismiss them directly).
        """
        # States that allow DISMISSED
        dismissable = {
            CardStatus.SEED, CardStatus.DRAFTING, CardStatus.STAGED, CardStatus.APPROVED
        }
        for status in dismissable:
            card = _make_card(status)
            validate_transition(card, CardStatus.DISMISSED)

        # POSTED → DISMISSED is blocked — only ARCHIVED allowed
        posted_card = _make_card(CardStatus.POSTED)
        with pytest.raises(InvalidTransitionError):
            validate_transition(posted_card, CardStatus.DISMISSED)


# ─────────────────────────────────────────────────────────────────────────────
# ContentCard serialization
# ─────────────────────────────────────────────────────────────────────────────

class TestContentCardSerialization:
    def test_round_trip_minimal(self):
        card = _make_card()
        d = card.to_dict()
        card2 = ContentCard.from_dict(d)
        assert card2.id == card.id
        assert card2.occasion == card.occasion
        assert card2.status == card.status
        assert card2.event_date == card.event_date

    def test_round_trip_with_platforms(self):
        card = _make_card()
        card.drafts["linkedin"] = PlatformDraft(
            status=PlatformDraftStatus.DRAFT,
            content="Happy Diwali from our family!",
            pii_scan_passed=True,
        )
        d = card.to_dict()
        card2 = ContentCard.from_dict(d)
        assert "linkedin" in card2.drafts
        assert card2.drafts["linkedin"].content == "Happy Diwali from our family!"
        assert card2.drafts["linkedin"].pii_scan_passed is True

    def test_archived_at_preserved(self):
        card = _make_card(CardStatus.ARCHIVED)
        card.archived_at = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
        d = card.to_dict()
        card2 = ContentCard.from_dict(d)
        assert card2.archived_at is not None
        assert card2.archived_at.year == 2026

    def test_flags_preserved(self):
        card = _make_card()
        card.set_flag("NEEDS_HUMAN_TOUCH")
        d = card.to_dict()
        card2 = ContentCard.from_dict(d)
        assert card2.has_flag("NEEDS_HUMAN_TOUCH")

    def test_from_dict_default_status_is_seed(self):
        d = {
            "id": "CARD-2026-002",
            "occasion": "Test",
            "occasion_type": "test",
            "event_date": "2026-04-01",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        card = ContentCard.from_dict(d)
        assert card.status == CardStatus.SEED


# ─────────────────────────────────────────────────────────────────────────────
# Flag management
# ─────────────────────────────────────────────────────────────────────────────

class TestFlagManagement:
    def test_set_flag_idempotent(self):
        card = _make_card()
        card.set_flag("DRAFT_BLOCKED")
        card.set_flag("DRAFT_BLOCKED")
        assert card.flags.count("DRAFT_BLOCKED") == 1

    def test_set_unknown_flag_raises(self):
        card = _make_card()
        with pytest.raises(ValueError, match="Unknown flag"):
            card.set_flag("MADE_UP_FLAG")

    def test_clear_flag(self):
        card = _make_card()
        card.set_flag("EMPLOYER_MENTION")
        assert card.has_flag("EMPLOYER_MENTION")
        card.clear_flag("EMPLOYER_MENTION")
        assert not card.has_flag("EMPLOYER_MENTION")

    def test_all_allowed_flags_settable(self):
        card = _make_card()
        for flag in ALLOWED_FLAGS:
            card.set_flag(flag)
            assert card.has_flag(flag)


# ─────────────────────────────────────────────────────────────────────────────
# PlatformDraft
# ─────────────────────────────────────────────────────────────────────────────

class TestPlatformDraft:
    def test_round_trip(self):
        draft = PlatformDraft(
            status=PlatformDraftStatus.STAGED,
            content="Test content",
            word_count=2,
            pii_scan_passed=True,
            employer_mention=False,
        )
        d = draft.to_dict()
        draft2 = PlatformDraft.from_dict(d)
        assert draft2.status == PlatformDraftStatus.STAGED
        assert draft2.content == "Test content"
        assert draft2.pii_scan_passed is True

    def test_is_terminal_posted(self):
        draft = PlatformDraft(status=PlatformDraftStatus.POSTED)
        assert draft.is_terminal is True

    def test_is_terminal_skipped(self):
        draft = PlatformDraft(status=PlatformDraftStatus.SKIPPED)
        assert draft.is_terminal is True

    def test_is_not_terminal_draft(self):
        draft = PlatformDraft(status=PlatformDraftStatus.DRAFT)
        assert draft.is_terminal is False


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────────────────────

class TestDeduplication:
    def test_exact_name_same_year_is_dup(self):
        card = _make_card(occasion="Diwali", event_date=date(2026, 10, 20))
        result = is_duplicate_occasion("Diwali", date(2026, 10, 20), [card])
        assert result is card

    def test_same_name_different_year_is_not_dup(self):
        card = _make_card(occasion="Diwali", event_date=date(2026, 10, 20))
        result = is_duplicate_occasion("Diwali", date(2027, 11, 8), [card])
        assert result is None

    def test_different_occasion_no_dup(self):
        card = _make_card(occasion="Diwali", event_date=date(2026, 10, 20))
        result = is_duplicate_occasion("Holi", date(2026, 3, 14), [card])
        assert result is None

    def test_terminal_card_excluded_from_dedup(self):
        """Archived/dismissed cards don't block new cards for same occasion."""
        card = _make_card(status=CardStatus.ARCHIVED, occasion="Diwali", event_date=date(2026, 10, 20))
        result = is_duplicate_occasion("Diwali", date(2026, 10, 20), [card])
        assert result is None

    def test_empty_gallery_no_dup(self):
        result = is_duplicate_occasion("Anything", date(2026, 5, 1), [])
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# derive_card_status
# ─────────────────────────────────────────────────────────────────────────────

class TestDeriveCardStatus:
    def test_no_drafts_stays_drafting(self):
        card = _make_card(CardStatus.DRAFTING)
        result = derive_card_status(card)
        assert result == CardStatus.DRAFTING

    def test_all_staged_gives_staged(self):
        card = _make_card(CardStatus.DRAFTING)
        card.drafts["linkedin"] = PlatformDraft(status=PlatformDraftStatus.STAGED)
        card.drafts["whatsapp"] = PlatformDraft(status=PlatformDraftStatus.STAGED)
        result = derive_card_status(card)
        assert result == CardStatus.STAGED

    def test_all_approved_gives_approved(self):
        card = _make_card(CardStatus.STAGED)
        card.drafts["linkedin"] = PlatformDraft(status=PlatformDraftStatus.APPROVED)
        result = derive_card_status(card)
        assert result == CardStatus.APPROVED

    def test_all_posted_or_skipped_gives_posted(self):
        card = _make_card(CardStatus.APPROVED)
        card.drafts["linkedin"] = PlatformDraft(status=PlatformDraftStatus.POSTED)
        card.drafts["whatsapp"] = PlatformDraft(status=PlatformDraftStatus.SKIPPED)
        result = derive_card_status(card)
        assert result == CardStatus.POSTED

    def test_any_staged_advances_card_to_staged(self):
        """When at least one draft is STAGED, card advances to STAGED (§5.5).

        The presence of a STAGED draft is sufficient to move the card forward —
        remaining DRAFT platforms may be filled in later.
        """
        card = _make_card(CardStatus.DRAFTING)
        card.drafts["linkedin"] = PlatformDraft(status=PlatformDraftStatus.STAGED)
        card.drafts["whatsapp"] = PlatformDraft(status=PlatformDraftStatus.DRAFT)
        result = derive_card_status(card)
        assert result == CardStatus.STAGED
