"""Phase 1 tests — pr_stage repository (GalleryRepository, GalleryMemory).

Tests cover:
  - _write_yaml_atomic — atomic write produces valid YAML at dest
  - GalleryRepository.load — empty file, exiting cards, in-memory cache
  - GalleryRepository.load_minimal — lazy loading (id/occasion/event_date/status)
  - GalleryRepository.save / upsert / remove — persistence and card mutations
  - GalleryRepository.next_card_id — sequential ID generation
  - GalleryRepository._enforce_hard_cap — dismissal purge + force-archive
  - GalleryRepository._quarantine_corrupted — malformed YAML handling
  - GalleryMemory.archive_card — write + idempotency
  - GalleryMemory.find_last_year_card — cross-year recall
  - GalleryMemory.load_all — full deserialization

Spec: §4.4, §4.5, §4.6, §8.1.1
"""
from __future__ import annotations

import pytest
import yaml
from datetime import date, datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from pr_stage.domain import CardStatus, ContentCard, PlatformDraft, PlatformDraftStatus
from pr_stage.repository import (
    GalleryRepository,
    GalleryMemory,
    _write_yaml_atomic,
    GALLERY_HARD_CAP,
)
from pr_stage.telemetry import StageLogger


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    return state


@pytest.fixture()
def gallery_path(tmp_state: Path) -> Path:
    return tmp_state / "gallery.yaml"


@pytest.fixture()
def memory_path(tmp_state: Path) -> Path:
    return tmp_state / "gallery_memory.yaml"


@pytest.fixture()
def logger(tmp_state: Path) -> StageLogger:
    return StageLogger(
        journal_path=tmp_state / "stage_journal.jsonl",
        audit_path=tmp_state / "stage_audit.md",
    )


@pytest.fixture()
def repo(gallery_path: Path, logger: StageLogger) -> GalleryRepository:
    return GalleryRepository(gallery_path, logger)


@pytest.fixture()
def memory(memory_path: Path, logger: StageLogger) -> GalleryMemory:
    return GalleryMemory(memory_path, logger)


def _make_card(
    card_id: str = "CARD-2026-001",
    status: CardStatus = CardStatus.SEED,
    occasion: str = "Diwali",
    event_date: date | None = None,
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
# _write_yaml_atomic
# ─────────────────────────────────────────────────────────────────────────────

class TestAtomicWrite:
    def test_creates_valid_yaml(self, tmp_path: Path):
        dest = tmp_path / "test.yaml"
        _write_yaml_atomic(dest, {"key": "value", "num": 42})
        loaded = yaml.safe_load(dest.read_text())
        assert loaded["key"] == "value"
        assert loaded["num"] == 42

    def test_creates_parent_dirs(self, tmp_path: Path):
        dest = tmp_path / "nested" / "deep" / "file.yaml"
        _write_yaml_atomic(dest, {"a": 1})
        assert dest.exists()

    def test_overwrites_existing(self, tmp_path: Path):
        dest = tmp_path / "overwrite.yaml"
        _write_yaml_atomic(dest, {"v": 1})
        _write_yaml_atomic(dest, {"v": 2})
        loaded = yaml.safe_load(dest.read_text())
        assert loaded["v"] == 2

    def test_no_tmp_files_left(self, tmp_path: Path):
        dest = tmp_path / "clean.yaml"
        _write_yaml_atomic(dest, {"data": True})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0


# ─────────────────────────────────────────────────────────────────────────────
# GalleryRepository — load
# ─────────────────────────────────────────────────────────────────────────────

class TestGalleryRepositoryLoad:
    def test_load_returns_empty_for_missing_file(self, repo: GalleryRepository):
        cards = repo.load()
        assert cards == []

    def test_load_returns_empty_for_empty_cards_key(self, gallery_path: Path, repo: GalleryRepository):
        _write_yaml_atomic(gallery_path, {"schema_version": "1.0", "cards": []})
        cards = repo.load()
        assert cards == []

    def test_load_deserializes_cards(self, gallery_path: Path, repo: GalleryRepository):
        card = _make_card()
        _write_yaml_atomic(gallery_path, {"schema_version": "1.0", "cards": [card.to_dict()]})
        cards = repo.load()
        assert len(cards) == 1
        assert cards[0].id == "CARD-2026-001"

    def test_load_uses_cache(self, gallery_path: Path, repo: GalleryRepository):
        card = _make_card()
        _write_yaml_atomic(gallery_path, {"schema_version": "1.0", "cards": [card.to_dict()]})
        cards1 = repo.load()
        # Overwrite file — should still return cached version
        _write_yaml_atomic(gallery_path, {"schema_version": "1.0", "cards": []})
        cards2 = repo.load()
        assert len(cards2) == 1  # still cached

    def test_load_force_bypasses_cache(self, gallery_path: Path, repo: GalleryRepository):
        card = _make_card()
        _write_yaml_atomic(gallery_path, {"schema_version": "1.0", "cards": [card.to_dict()]})
        repo.load()
        _write_yaml_atomic(gallery_path, {"schema_version": "1.0", "cards": []})
        cards = repo.load(force=True)
        assert cards == []

    def test_load_quarantines_corrupt_yaml(self, gallery_path: Path, repo: GalleryRepository, tmp_state: Path):
        # Write malformed YAML
        gallery_path.write_text("{invalid: yaml: ]:}", encoding="utf-8")
        cards = repo.load()
        assert cards == []
        # Quarantine file should be in tmp/
        root = tmp_state.parent
        quarantine_files = list(root.glob("tmp/gallery_corrupt_*.yaml"))
        assert len(quarantine_files) == 1


# ─────────────────────────────────────────────────────────────────────────────
# GalleryRepository — load_minimal
# ─────────────────────────────────────────────────────────────────────────────

class TestGalleryRepositoryLoadMinimal:
    def test_returns_minimal_fields_only(self, gallery_path: Path, repo: GalleryRepository):
        card = _make_card()
        card.primary_thread = "Should not appear in minimal load"
        _write_yaml_atomic(gallery_path, {"schema_version": "1.0", "cards": [card.to_dict()]})
        minimal = repo.load_minimal()
        assert len(minimal) == 1
        row = minimal[0]
        assert set(row.keys()) == {"id", "occasion", "event_date", "status"}
        assert row["id"] == "CARD-2026-001"
        assert row["status"] == "seed"

    def test_returns_empty_for_missing_file(self, repo: GalleryRepository):
        assert repo.load_minimal() == []


# ─────────────────────────────────────────────────────────────────────────────
# GalleryRepository — save / upsert / remove
# ─────────────────────────────────────────────────────────────────────────────

class TestGalleryRepositoryMutations:
    def test_save_round_trip(self, repo: GalleryRepository):
        card = _make_card()
        repo.save([card])
        cards = repo.load(force=True)
        assert len(cards) == 1
        assert cards[0].id == card.id

    def test_save_creates_bak(self, gallery_path: Path, repo: GalleryRepository):
        card = _make_card()
        repo.save([card])  # First write (no existing file yet)
        repo.save([card])  # Second write creates .bak from first
        bak = gallery_path.with_suffix(".yaml.bak")
        assert bak.exists()

    def test_upsert_adds_new_card(self, repo: GalleryRepository):
        repo.upsert(_make_card("CARD-2026-001"))
        repo.upsert(_make_card("CARD-2026-002", occasion="Holi"))
        cards = repo.load(force=True)
        assert len(cards) == 2

    def test_upsert_updates_existing(self, repo: GalleryRepository):
        card = _make_card("CARD-2026-001")
        repo.upsert(card)
        card.status = CardStatus.DRAFTING
        repo.upsert(card)
        reloaded = repo.load(force=True)
        assert len(reloaded) == 1
        assert reloaded[0].status == CardStatus.DRAFTING

    def test_remove_existing_card(self, repo: GalleryRepository):
        repo.upsert(_make_card("CARD-2026-001"))
        removed = repo.remove("CARD-2026-001")
        assert removed is not None
        assert removed.id == "CARD-2026-001"
        assert repo.load(force=True) == []

    def test_remove_nonexistent_returns_none(self, repo: GalleryRepository):
        result = repo.remove("CARD-9999-999")
        assert result is None

    def test_get_by_id_found(self, repo: GalleryRepository):
        repo.upsert(_make_card("CARD-2026-001"))
        card = repo.get_by_id("CARD-2026-001")
        assert card is not None
        assert card.id == "CARD-2026-001"

    def test_get_by_id_missing(self, repo: GalleryRepository):
        assert repo.get_by_id("CARD-9999-999") is None


# ─────────────────────────────────────────────────────────────────────────────
# GalleryRepository — next_card_id
# ─────────────────────────────────────────────────────────────────────────────

class TestNextCardId:
    def test_returns_001_for_empty_gallery(self, repo: GalleryRepository):
        from datetime import date as _date
        year = _date.today().year
        cid = repo.next_card_id()
        assert cid.startswith(f"CARD-{year}-")

    def test_increments_from_existing(self, repo: GalleryRepository):
        from datetime import date as _date
        year = _date.today().year
        repo.upsert(_make_card(f"CARD-{year}-005", event_date=_date(year, 6, 1)))
        cid = repo.next_card_id()
        assert cid == f"CARD-{year}-006"


# ─────────────────────────────────────────────────────────────────────────────
# GalleryRepository — hard cap
# ─────────────────────────────────────────────────────────────────────────────

class TestHardCap:
    def test_dismissed_purged_first(self, repo: GalleryRepository):
        """Hard cap should purge dismissed cards before archiving posted ones."""
        cards = []
        for i in range(1, GALLERY_HARD_CAP + 2):  # 51 cards
            status = CardStatus.DISMISSED if i <= 10 else CardStatus.SEED
            card = _make_card(
                card_id=f"CARD-2026-{i:03d}",
                status=status,
                event_date=date(2026, 1 + (i % 12) or 1, 1),
            )
            cards.append(card)
        repo._enforce_hard_cap(cards)
        assert len(cards) <= GALLERY_HARD_CAP
        remaining_ids = {c.id for c in cards}
        # At least some dismissed should have been purged
        dismissed_remaining = sum(1 for c in cards if c.status == CardStatus.DISMISSED)
        assert dismissed_remaining < 10


# ─────────────────────────────────────────────────────────────────────────────
# GalleryMemory
# ─────────────────────────────────────────────────────────────────────────────

class TestGalleryMemory:
    def test_archive_card_creates_file(self, memory: GalleryMemory, memory_path: Path):
        card = _make_card(status=CardStatus.ARCHIVED)
        memory.archive_card(card)
        assert memory_path.exists()
        data = yaml.safe_load(memory_path.read_text())
        assert len(data["archived_cards"]) == 1
        assert data["archived_cards"][0]["id"] == "CARD-2026-001"

    def test_archive_card_idempotent(self, memory: GalleryMemory, memory_path: Path):
        card = _make_card(status=CardStatus.ARCHIVED)
        memory.archive_card(card)
        memory.archive_card(card)  # Second call should not duplicate
        data = yaml.safe_load(memory_path.read_text())
        assert len(data["archived_cards"]) == 1

    def test_archive_sets_archived_at(self, memory: GalleryMemory):
        card = _make_card(status=CardStatus.ARCHIVED)
        assert card.archived_at is None
        memory.archive_card(card)
        assert card.archived_at is not None

    def test_find_last_year_card_returns_prior_year(self, memory: GalleryMemory):
        # Archive a card from 2025
        card_2025 = _make_card(card_id="CARD-2025-001", event_date=date(2025, 10, 22))
        card_2025.status = CardStatus.ARCHIVED
        memory.archive_card(card_2025)

        result = memory.find_last_year_card("Diwali", 2026)
        assert result is not None
        assert result["id"] == "CARD-2025-001"

    def test_find_last_year_card_ignores_same_year(self, memory: GalleryMemory):
        card = _make_card(card_id="CARD-2026-001", event_date=date(2026, 10, 20))
        card.status = CardStatus.ARCHIVED
        memory.archive_card(card)

        result = memory.find_last_year_card("Diwali", 2026)
        assert result is None  # same year card doesn't count

    def test_find_last_year_card_returns_most_recent(self, memory: GalleryMemory):
        for yr in [2024, 2023, 2025]:
            c = _make_card(card_id=f"CARD-{yr}-001", event_date=date(yr, 10, 1))
            c.status = CardStatus.ARCHIVED
            memory.archive_card(c)

        result = memory.find_last_year_card("Diwali", 2026)
        assert result is not None
        assert result["event_date"].startswith("2025")

    def test_load_all_empty(self, memory: GalleryMemory):
        assert memory.load_all() == []

    def test_load_all_returns_cards(self, memory: GalleryMemory):
        card = _make_card(status=CardStatus.ARCHIVED)
        memory.archive_card(card)
        all_cards = memory.load_all()
        assert len(all_cards) == 1
        assert all_cards[0].id == "CARD-2026-001"
