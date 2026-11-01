"""
tests/unit/test_pr_stage.py — Unit tests for scripts/pr_stage/domain.py

Coverage:
  - CardStatus enum values
  - VALID_TRANSITIONS adjacency graph
  - validate_transition: valid transitions do not raise
  - validate_transition: invalid transitions raise InvalidTransitionError
  - validate_transition: terminal states (ARCHIVED, DISMISSED) have no outgoing transitions
  - ContentCard.is_terminal: ARCHIVED and DISMISSED are terminal, others are not
  - ContentCard.is_archive_ready: False when no drafts, True when all drafts terminal
  - ContentCard.has_flag / set_flag / clear_flag
  - ContentCard.set_flag raises ValueError for unknown flag
  - parse_card_id / make_card_id round-trip
  - is_duplicate_occasion: exact name+year match detected
  - is_duplicate_occasion: terminal cards ignored
  - is_duplicate_occasion: no match returns None
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# fcntl is Unix-only; stub it out on Windows before importing any pr_stage module
if sys.platform == "win32" and "fcntl" not in sys.modules:
    from unittest.mock import MagicMock
    sys.modules["fcntl"] = MagicMock()

from pr_stage.domain import (
    VALID_TRANSITIONS,
    CardStatus,
    ContentCard,
    InvalidTransitionError,
    is_duplicate_occasion,
    make_card_id,
    parse_card_id,
    validate_transition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_card(
    card_id: str = "CARD-2026-001",
    status: CardStatus = CardStatus.SEED,
) -> ContentCard:
    return ContentCard(
        id=card_id,
        occasion="Birthday party",
        occasion_type="personal",
        event_date=date(2026, 6, 15),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status=status,
    )


# ---------------------------------------------------------------------------
# CardStatus
# ---------------------------------------------------------------------------

class TestCardStatus:
    def test_all_seven_states_defined(self):
        states = {s.value for s in CardStatus}
        assert states == {"seed", "drafting", "staged", "approved", "posted", "archived", "dismissed"}

    def test_string_equality(self):
        assert CardStatus.SEED == "seed"
        assert CardStatus.ARCHIVED == "archived"


# ---------------------------------------------------------------------------
# VALID_TRANSITIONS graph
# ---------------------------------------------------------------------------

class TestValidTransitions:
    def test_seed_can_go_to_drafting(self):
        assert CardStatus.DRAFTING in VALID_TRANSITIONS[CardStatus.SEED]

    def test_seed_can_be_dismissed(self):
        assert CardStatus.DISMISSED in VALID_TRANSITIONS[CardStatus.SEED]

    def test_seed_cannot_go_to_posted(self):
        assert CardStatus.POSTED not in VALID_TRANSITIONS[CardStatus.SEED]

    def test_posted_can_only_go_to_archived(self):
        assert VALID_TRANSITIONS[CardStatus.POSTED] == frozenset({CardStatus.ARCHIVED})

    def test_archived_has_no_outgoing_transitions(self):
        assert len(VALID_TRANSITIONS[CardStatus.ARCHIVED]) == 0

    def test_dismissed_has_no_outgoing_transitions(self):
        assert len(VALID_TRANSITIONS[CardStatus.DISMISSED]) == 0

    def test_approved_can_go_to_posted(self):
        assert CardStatus.POSTED in VALID_TRANSITIONS[CardStatus.APPROVED]

    def test_approved_can_be_dismissed(self):
        assert CardStatus.DISMISSED in VALID_TRANSITIONS[CardStatus.APPROVED]


# ---------------------------------------------------------------------------
# validate_transition
# ---------------------------------------------------------------------------

class TestValidateTransition:
    def test_valid_seed_to_drafting_does_not_raise(self):
        card = _make_card(status=CardStatus.SEED)
        validate_transition(card, CardStatus.DRAFTING)  # should not raise

    def test_valid_staged_to_approved_does_not_raise(self):
        card = _make_card(status=CardStatus.STAGED)
        validate_transition(card, CardStatus.APPROVED)

    def test_valid_posted_to_archived_does_not_raise(self):
        card = _make_card(status=CardStatus.POSTED)
        validate_transition(card, CardStatus.ARCHIVED)

    def test_invalid_seed_to_posted_raises(self):
        card = _make_card(status=CardStatus.SEED)
        with pytest.raises(InvalidTransitionError):
            validate_transition(card, CardStatus.POSTED)

    def test_invalid_seed_to_archived_raises(self):
        card = _make_card(status=CardStatus.SEED)
        with pytest.raises(InvalidTransitionError):
            validate_transition(card, CardStatus.ARCHIVED)

    def test_invalid_archived_to_seed_raises(self):
        card = _make_card(status=CardStatus.ARCHIVED)
        with pytest.raises(InvalidTransitionError):
            validate_transition(card, CardStatus.SEED)

    def test_invalid_dismissed_to_drafting_raises(self):
        card = _make_card(status=CardStatus.DISMISSED)
        with pytest.raises(InvalidTransitionError):
            validate_transition(card, CardStatus.DRAFTING)

    def test_error_message_contains_card_id(self):
        card = _make_card(card_id="CARD-2026-007", status=CardStatus.ARCHIVED)
        with pytest.raises(InvalidTransitionError, match="CARD-2026-007"):
            validate_transition(card, CardStatus.SEED)

    def test_any_state_can_be_dismissed_except_terminals_and_posted(self):
        """SEED, DRAFTING, STAGED, APPROVED can all be dismissed.

        POSTED can only transition to ARCHIVED (not DISMISSED) per the FSM.
        ARCHIVED and DISMISSED have no outgoing transitions.
        """
        dismissable = [
            CardStatus.SEED, CardStatus.DRAFTING, CardStatus.STAGED, CardStatus.APPROVED,
        ]
        for status in dismissable:
            card = _make_card(status=status)
            validate_transition(card, CardStatus.DISMISSED)  # should not raise


# ---------------------------------------------------------------------------
# ContentCard properties
# ---------------------------------------------------------------------------

class TestContentCardIsTerminal:
    def test_archived_is_terminal(self):
        assert _make_card(status=CardStatus.ARCHIVED).is_terminal is True

    def test_dismissed_is_terminal(self):
        assert _make_card(status=CardStatus.DISMISSED).is_terminal is True

    def test_seed_is_not_terminal(self):
        assert _make_card(status=CardStatus.SEED).is_terminal is False

    def test_posted_is_not_terminal(self):
        assert _make_card(status=CardStatus.POSTED).is_terminal is False

    def test_approved_is_not_terminal(self):
        assert _make_card(status=CardStatus.APPROVED).is_terminal is False


class TestContentCardIsArchiveReady:
    def test_no_drafts_returns_false(self):
        card = _make_card()
        assert card.is_archive_ready is False

    def test_all_posted_drafts_returns_true(self):
        from pr_stage.domain import PlatformDraft, PlatformDraftStatus
        card = _make_card()
        card.drafts["linkedin"] = PlatformDraft(status=PlatformDraftStatus.POSTED)
        card.drafts["instagram"] = PlatformDraft(status=PlatformDraftStatus.SKIPPED)
        assert card.is_archive_ready is True

    def test_one_draft_in_progress_returns_false(self):
        from pr_stage.domain import PlatformDraft, PlatformDraftStatus
        card = _make_card()
        card.drafts["linkedin"] = PlatformDraft(status=PlatformDraftStatus.POSTED)
        card.drafts["twitter"] = PlatformDraft(status=PlatformDraftStatus.DRAFT)
        assert card.is_archive_ready is False


class TestContentCardFlags:
    def test_set_known_flag(self):
        card = _make_card()
        card.set_flag("NEEDS_HUMAN_TOUCH")
        assert card.has_flag("NEEDS_HUMAN_TOUCH")

    def test_set_unknown_flag_raises(self):
        card = _make_card()
        with pytest.raises(ValueError, match="Unknown flag"):
            card.set_flag("NONEXISTENT_FLAG")

    def test_clear_flag(self):
        card = _make_card()
        card.set_flag("EMPLOYER_MENTION")
        card.clear_flag("EMPLOYER_MENTION")
        assert not card.has_flag("EMPLOYER_MENTION")

    def test_duplicate_flag_not_added_twice(self):
        card = _make_card()
        card.set_flag("DRAFT_BLOCKED")
        card.set_flag("DRAFT_BLOCKED")
        assert card.flags.count("DRAFT_BLOCKED") == 1


# ---------------------------------------------------------------------------
# Card ID helpers
# ---------------------------------------------------------------------------

class TestCardIdHelpers:
    def test_parse_card_id(self):
        year, seq = parse_card_id("CARD-2026-032")
        assert year == 2026
        assert seq == 32

    def test_make_card_id(self):
        assert make_card_id(2026, 32) == "CARD-2026-032"

    def test_round_trip(self):
        card_id = "CARD-2027-100"
        year, seq = parse_card_id(card_id)
        assert make_card_id(year, seq) == card_id

    def test_invalid_card_id_raises(self):
        with pytest.raises(ValueError, match="Invalid card ID"):
            parse_card_id("INVALID-ID")

    def test_card_id_with_leading_zeros(self):
        year, seq = parse_card_id("CARD-2026-007")
        assert seq == 7


# ---------------------------------------------------------------------------
# is_duplicate_occasion
# ---------------------------------------------------------------------------

class TestIsDuplicateOccasion:
    def test_exact_match_detected(self):
        card = _make_card()
        result = is_duplicate_occasion("Birthday party", date(2026, 6, 15), [card])
        assert result is card

    def test_different_year_no_match(self):
        card = _make_card()
        result = is_duplicate_occasion("Birthday party", date(2027, 6, 15), [card])
        assert result is None

    def test_different_occasion_no_match(self):
        card = _make_card()
        result = is_duplicate_occasion("Anniversary", date(2026, 6, 15), [card])
        assert result is None

    def test_terminal_card_ignored(self):
        card = _make_card(status=CardStatus.ARCHIVED)
        result = is_duplicate_occasion("Birthday party", date(2026, 6, 15), [card])
        assert result is None

    def test_empty_list_returns_none(self):
        assert is_duplicate_occasion("Birthday party", date(2026, 6, 15), []) is None
