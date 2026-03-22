"""Phase 1 tests — pr_stage service (ContentStage lifecycle orchestration).

Tests cover:
  - process_moments: seed card creation, deduplication, rescore on improved score
  - auto_draft_pending: filtering (SEED only, window, DRAFT_BLOCKED), STAGED on success
  - sweep_expired: POSTED+terminal drafts archived after time gate
  - count_by_status: per-status card counts
  - next_occasion_date: upcoming event_date from active cards  
  - dismiss_card: state transition + persistence
  - get_metrics: telemetry counter accuracy
  - _stage_enabled: config flag reading

Spec: §5.2, §5.3, §8.1, §8.4, §13.2
"""
from __future__ import annotations

import pytest
import yaml
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from pr_stage.service import (
    ContentStage,
    ScoredMoment,
    _stage_enabled,
    SEED_WINDOW_DAYS,
    AUTO_DRAFT_WINDOW_DAYS,
    ARCHIVE_AFTER_POSTED_DAYS,
)
from pr_stage.domain import CardStatus, ContentCard, PlatformDraft, PlatformDraftStatus


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    return state


@pytest.fixture()
def stage(tmp_state: Path) -> ContentStage:
    return ContentStage(
        tmp_state / "gallery.yaml",
        tmp_state / "gallery_memory.yaml",
        state_dir=tmp_state,
    )


def _moment(
    occasion: str = "Diwali",
    occasion_type: str = "cultural_festival",
    days_until: int = 5,
    score: float = 0.82,
) -> ScoredMoment:
    event_date = date.today() + timedelta(days=days_until)
    return ScoredMoment(occasion, occasion_type, event_date, days_until, score, "NT-2")


def _make_card(
    status: CardStatus = CardStatus.SEED,
    card_id: str = "CARD-2026-001",
    occasion: str = "Diwali",
    days_from_now: int = 5,
) -> ContentCard:
    return ContentCard(
        id=card_id,
        occasion=occasion,
        occasion_type="cultural_festival",
        event_date=date.today() + timedelta(days=days_from_now),
        created_at=datetime.now(timezone.utc),
        status=status,
    )


# ─────────────────────────────────────────────────────────────────────────────
# _stage_enabled
# ─────────────────────────────────────────────────────────────────────────────

class TestStageEnabled:
    def test_true_when_dict_stage_true(self):
        cfg = {"enhancements": {"pr_manager": {"enabled": True, "stage": True}}}
        assert _stage_enabled(cfg) is True

    def test_false_when_dict_stage_false(self):
        cfg = {"enhancements": {"pr_manager": {"enabled": True, "stage": False}}}
        assert _stage_enabled(cfg) is False

    def test_false_when_pr_manager_boolean_true(self):
        """top-level bool True does NOT enable stage (requires nested dict)."""
        cfg = {"enhancements": {"pr_manager": True}}
        assert _stage_enabled(cfg) is False

    def test_false_when_no_enhancements(self):
        assert _stage_enabled({}) is False

    def test_false_when_pr_manager_missing(self):
        assert _stage_enabled({"enhancements": {}}) is False


# ─────────────────────────────────────────────────────────────────────────────
# process_moments
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessMoments:
    def test_creates_seed_cards_within_window(self, stage: ContentStage):
        moments = [_moment("Diwali", days_until=5), _moment("Holi", days_until=10)]
        new_cards = stage.process_moments(moments)
        assert len(new_cards) == 2
        assert all(c.status == CardStatus.SEED for c in new_cards)

    def test_skips_moments_outside_window(self, stage: ContentStage):
        far_future = _moment("New Year", days_until=SEED_WINDOW_DAYS + 1)
        new_cards = stage.process_moments([far_future])
        assert len(new_cards) == 0

    def test_deduplicates_same_occasion_same_year(self, stage: ContentStage):
        m = _moment("Diwali", days_until=5)
        stage.process_moments([m])
        result = stage.process_moments([m])  # second call, same moment
        assert len(result) == 0
        assert stage.count_by_status("seed") == 1

    def test_rescores_card_on_improved_score(self, stage: ContentStage):
        m1 = ScoredMoment("Diwali", "cultural_festival",
                          date.today() + timedelta(days=5), 5, 0.75, "NT-2")
        stage.process_moments([m1])

        # Reload cards, capture initial score
        cards_before = [c for c in stage._gallery.load() if c.occasion == "Diwali"]
        assert cards_before[0].convergence_score == pytest.approx(0.75)

        m2 = ScoredMoment("Diwali", "cultural_festival",
                          date.today() + timedelta(days=5), 5, 0.95, "NT-2")
        stage.process_moments([m2])
        cards_after = [c for c in stage._gallery.load(force=True) if c.occasion == "Diwali"]
        assert cards_after[0].convergence_score == pytest.approx(0.95)

    def test_card_persisted_to_gallery(self, stage: ContentStage, tmp_state: Path):
        stage.process_moments([_moment("Holi", days_until=3)])
        data = yaml.safe_load((tmp_state / "gallery.yaml").read_text())
        assert len(data["cards"]) == 1
        assert data["cards"][0]["occasion"] == "Holi"

    def test_returns_empty_for_no_moments(self, stage: ContentStage):
        assert stage.process_moments([]) == []

    def test_metrics_tracks_created_count(self, stage: ContentStage):
        stage.process_moments([_moment("Diwali"), _moment("Holi", days_until=2)])
        assert stage.get_metrics()["cards_created"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# auto_draft_pending
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoDraftPending:
    def test_drafts_seed_card_within_window(self, stage: ContentStage):
        stage.process_moments([_moment("Diwali", days_until=3)])
        drafted = stage.auto_draft_pending()
        assert len(drafted) == 1
        assert drafted[0].status == CardStatus.STAGED

    def test_does_not_draft_outside_window(self, stage: ContentStage):
        stage.process_moments([_moment("FarEvent", days_until=AUTO_DRAFT_WINDOW_DAYS + 1)])
        drafted = stage.auto_draft_pending()
        assert len(drafted) == 0

    def test_does_not_draft_non_seed_cards(self, stage: ContentStage):
        m = _moment("Diwali", days_until=3)
        stage.process_moments([m])
        # Force card to STAGED
        cards = stage._gallery.load()
        cards[0].status = CardStatus.STAGED
        stage._gallery.save(cards)
        drafted = stage.auto_draft_pending()
        assert len(drafted) == 0

    def test_does_not_draft_blocked_cards(self, stage: ContentStage):
        stage.process_moments([_moment("Diwali", days_until=3)])
        cards = stage._gallery.load()
        cards[0].set_flag("DRAFT_BLOCKED")
        stage._gallery.save(cards)
        drafted = stage.auto_draft_pending()
        assert len(drafted) == 0

    def test_drafted_card_has_platform_drafts(self, stage: ContentStage):
        stage.process_moments([_moment("Diwali", days_until=3)])
        drafted = stage.auto_draft_pending()
        assert len(drafted[0].drafts) > 0

    def test_staged_card_pii_scan_passed(self, stage: ContentStage):
        stage.process_moments([_moment("Diwali", days_until=3)])
        drafted = stage.auto_draft_pending()
        # All platform drafts should have pii_scan_passed=True for placeholder content
        for platform, draft_obj in drafted[0].drafts.items():
            assert draft_obj.pii_scan_passed is True, f"{platform} pii_scan_passed should be True"


# ─────────────────────────────────────────────────────────────────────────────
# sweep_expired
# ─────────────────────────────────────────────────────────────────────────────

class TestSweepExpired:
    def _make_posted_card(self, stage: ContentStage, days_ago: int = ARCHIVE_AFTER_POSTED_DAYS + 1) -> ContentCard:
        """Create a POSTED card with all terminal drafts, created days_ago days ago."""
        from pr_stage.domain import make_card_id
        card = ContentCard(
            id=make_card_id(date.today().year, 1),
            occasion="Posted Festival",
            occasion_type="cultural_festival",
            event_date=date.today() - timedelta(days=days_ago),
            created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            status=CardStatus.POSTED,
            archived_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        )
        card.drafts["linkedin"] = PlatformDraft(status=PlatformDraftStatus.POSTED)
        card.drafts["facebook"] = PlatformDraft(status=PlatformDraftStatus.SKIPPED)
        stage._gallery.upsert(card)
        return card

    def test_archives_old_posted_card(self, stage: ContentStage, tmp_state: Path):
        self._make_posted_card(stage)
        archived = stage.sweep_expired()
        assert len(archived) == 1
        assert archived[0].status == CardStatus.ARCHIVED

    def test_card_removed_from_gallery_after_archive(self, stage: ContentStage, tmp_state: Path):
        card = self._make_posted_card(stage)
        stage.sweep_expired()
        assert stage._gallery.get_by_id(card.id) is None

    def test_card_moved_to_memory_after_archive(self, stage: ContentStage, tmp_state: Path):
        self._make_posted_card(stage)
        stage.sweep_expired()
        archived_cards = stage._memory.load_all()
        assert len(archived_cards) == 1

    def test_does_not_archive_recent_posted_card(self, stage: ContentStage):
        """Card archived less than ARCHIVE_AFTER_POSTED_DAYS ago should not be swept."""
        from pr_stage.domain import make_card_id
        card = ContentCard(
            id=make_card_id(date.today().year, 1),
            occasion="Recent Post",
            occasion_type="cultural_festival",
            event_date=date.today() - timedelta(days=1),
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
            status=CardStatus.POSTED,
            archived_at=datetime.now(timezone.utc),  # just archived
        )
        card.drafts["linkedin"] = PlatformDraft(status=PlatformDraftStatus.POSTED)
        stage._gallery.upsert(card)
        archived = stage.sweep_expired()
        assert len(archived) == 0

    def test_does_not_archive_posted_card_with_active_drafts(self, stage: ContentStage):
        """A POSTED card where some drafts are still DRAFT should not be swept."""
        from pr_stage.domain import make_card_id
        card = ContentCard(
            id=make_card_id(date.today().year, 1),
            occasion="Partial Post",
            occasion_type="cultural_festival",
            event_date=date.today() - timedelta(days=10),
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
            status=CardStatus.POSTED,
            archived_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        card.drafts["linkedin"] = PlatformDraft(status=PlatformDraftStatus.POSTED)
        card.drafts["facebook"] = PlatformDraft(status=PlatformDraftStatus.DRAFT)  # not terminal
        stage._gallery.upsert(card)
        archived = stage.sweep_expired()
        assert len(archived) == 0

    def test_metrics_archived_count(self, stage: ContentStage):
        self._make_posted_card(stage)
        stage.sweep_expired()
        assert stage.get_metrics()["cards_archived"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# count_by_status / next_occasion_date
# ─────────────────────────────────────────────────────────────────────────────

class TestCountAndDates:
    def test_count_by_status_empty(self, stage: ContentStage):
        assert stage.count_by_status("seed") == 0

    def test_count_by_status_correct(self, stage: ContentStage):
        stage.process_moments([_moment("Diwali", days_until=5), _moment("Holi", days_until=3)])
        assert stage.count_by_status("seed") == 2
        assert stage.count_by_status("staged") == 0

    def test_next_occasion_date_returns_nearest(self, stage: ContentStage):
        stage.process_moments([
            _moment("Far Event", days_until=10),
            _moment("Near Event", days_until=3),
        ])
        nxt = stage.next_occasion_date()
        assert nxt is not None
        assert nxt == date.today() + timedelta(days=3)

    def test_next_occasion_date_none_when_empty(self, stage: ContentStage):
        assert stage.next_occasion_date() is None

    def test_next_occasion_date_excludes_terminal(self, stage: ContentStage):
        """Terminal (archived/dismissed) cards should not contribute to next date."""
        stage.process_moments([_moment("Diwali", days_until=5)])
        stage.dismiss_card(stage._gallery.load()[0].id, reason="test")
        assert stage.next_occasion_date() is None


# ─────────────────────────────────────────────────────────────────────────────
# dismiss_card
# ─────────────────────────────────────────────────────────────────────────────

class TestDismissCard:
    def test_dismiss_seed_card(self, stage: ContentStage):
        stage.process_moments([_moment("Diwali", days_until=5)])
        card_id = stage._gallery.load()[0].id
        dismissed = stage.dismiss_card(card_id, reason="not_relevant")
        assert dismissed is not None
        assert dismissed.status == CardStatus.DISMISSED
        assert dismissed.dismissed_reason == "not_relevant"

    def test_dismiss_returns_none_for_missing(self, stage: ContentStage):
        result = stage.dismiss_card("CARD-9999-999")
        assert result is None

    def test_dismiss_returns_none_for_already_terminal(self, stage: ContentStage):
        stage.process_moments([_moment("Diwali", days_until=5)])
        cards = stage._gallery.load()
        cards[0].status = CardStatus.ARCHIVED
        stage._gallery.save(cards)
        result = stage.dismiss_card(cards[0].id)
        assert result is None

    def test_dismissed_card_persisted(self, stage: ContentStage, tmp_state: Path):
        stage.process_moments([_moment("Diwali", days_until=5)])
        card_id = stage._gallery.load()[0].id
        stage.dismiss_card(card_id, reason="test")
        # Reload from disk
        stage2 = ContentStage(
            tmp_state / "gallery.yaml",
            tmp_state / "gallery_memory.yaml",
            state_dir=tmp_state,
        )
        card = stage2._gallery.get_by_id(card_id)
        assert card is not None
        assert card.status == CardStatus.DISMISSED


# ─────────────────────────────────────────────────────────────────────────────
# Full lifecycle integration
# ─────────────────────────────────────────────────────────────────────────────

class TestFullLifecycle:
    def test_seed_to_staged_to_dismiss(self, stage: ContentStage):
        """SEED → STAGED (via auto_draft) → DISMISSED."""
        stage.process_moments([_moment("Diwali", days_until=3)])
        stage.auto_draft_pending()
        cards = stage._gallery.load()
        assert cards[0].status == CardStatus.STAGED

        dismissed = stage.dismiss_card(cards[0].id, reason="changed_mind")
        assert dismissed.status == CardStatus.DISMISSED

    def test_metrics_reflect_full_cycle(self, stage: ContentStage):
        stage.process_moments([_moment("Diwali", days_until=3)])
        stage.auto_draft_pending()
        m = stage.get_metrics()
        assert m["cards_created"] == 1
        assert m["cards_auto_drafted"] == 1
        assert m["cards_staged"] == 1
        assert m["pii_failures"] == 0

    def test_multiple_moments_correct_ids(self, stage: ContentStage):
        moments = [
            _moment("Diwali", days_until=5),
            _moment("Holi", days_until=7),
            _moment("Eid", days_until=10),
        ]
        new_cards = stage.process_moments(moments)
        ids = [c.id for c in new_cards]
        assert len(set(ids)) == 3  # All unique
        for cid in ids:
            from pr_stage.domain import parse_card_id
            y, seq = parse_card_id(cid)
            assert y == date.today().year
