"""repository.py — Gallery I/O, atomic writes, event journal, GalleryMemory.

Handles all file-system operations for gallery.yaml and gallery_memory.yaml:
  - Atomic YAML writes (§4.4 — fsync + os.replace + fcntl lock)
  - Corruption recovery with fallback to .bak
  - Lazy loading (§8.1.1) — only deserializes required fields
  - 50-card hard cap enforcement (§8.1.1)
  - GalleryMemory — archive store for completed cards

Spec: §4.4, §4.5, §4.6, §8.1.1
"""
from __future__ import annotations

import fcntl
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from pr_stage.domain import ContentCard, CardStatus
from pr_stage.telemetry import StageLogger

# Gallery capacity limits (§8.1.1)
GALLERY_SOFT_CAP = 30
GALLERY_HARD_CAP = 50

SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# Atomic write helper (§4.4)
# ─────────────────────────────────────────────────────────────────────────────

def _write_yaml_atomic(dest_path: Path, data: dict) -> None:
    """Atomic YAML write: tempfile → fsync → os.replace() + fcntl lock.

    Platform scope: macOS/Linux (fcntl). Windows users must use WSL2.
    """
    lock_path = dest_path.with_suffix(dest_path.suffix + ".lock")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=dest_path.parent,
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, str(dest_path))
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _safe_read_yaml(path: Path) -> dict | None:
    """Read a YAML file, returning None on parse error or missing file."""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Gallery Repository
# ─────────────────────────────────────────────────────────────────────────────

class GalleryRepository:
    """Manages gallery.yaml — the active card store.

    All reads and writes go through this class. Callers never touch the
    file directly. This enforces the atomic write pattern and corruption
    recovery invariants.

    Spec: §4.4, §4.5, §8.1.1
    """

    def __init__(self, gallery_path: Path, logger: StageLogger) -> None:
        self._path   = gallery_path
        self._logger = logger
        self._cache: list[ContentCard] | None = None

    # ── Load ────────────────────────────────────────────────────────────

    def load(self, *, force: bool = False) -> list[ContentCard]:
        """Load and return all cards. Uses in-memory cache unless force=True.

        Corruption recovery: on parse error, quarantines the corrupted file
        and returns an empty list (read-only degraded mode until vault decrypt
        restores from backup).
        """
        if self._cache is not None and not force:
            return self._cache

        data = _safe_read_yaml(self._path)
        if data is None:
            raw = self._path.read_text(encoding="utf-8") if self._path.exists() else None
            if raw is not None:
                # Parse error path — quarantine
                self._quarantine_corrupted()
                self._logger.event(
                    "GalleryCorrupted",
                    result="error",
                    detail="yaml_parse_failure",
                )
            self._cache = []
            return self._cache

        cards = []
        for card_d in (data.get("cards") or []):
            try:
                cards.append(ContentCard.from_dict(card_d))
            except (KeyError, ValueError) as exc:
                self._logger.event(
                    "CardDeserializationError",
                    card_id=card_d.get("id", "?"),
                    result="error",
                    detail=str(exc),
                )
        self._cache = cards
        return self._cache

    def load_minimal(self) -> list[dict]:
        """Lazy load — returns only id, occasion, event_date, status dicts (§8.1.1).

        Used during duplicate-check in process_moments() to avoid full deserialization.
        """
        data = _safe_read_yaml(self._path)
        if not data:
            return []
        return [
            {
                "id":         c.get("id", ""),
                "occasion":   c.get("occasion", ""),
                "event_date": c.get("event_date", ""),
                "status":     c.get("status", "seed"),
            }
            for c in (data.get("cards") or [])
        ]

    # ── Save ────────────────────────────────────────────────────────────

    def save(self, cards: list[ContentCard]) -> None:
        """Write cards to gallery.yaml atomically.

        Creates a .bak safety copy before writing.
        """
        # Safety backup before overwrite
        if self._path.exists():
            bak = self._path.with_suffix(".yaml.bak")
            try:
                shutil.copy2(str(self._path), str(bak))
            except OSError:
                pass  # Non-fatal — proceed with write

        data = {
            "schema_version": SCHEMA_VERSION,
            "last_updated":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "cards":          [c.to_dict() for c in cards],
        }
        _write_yaml_atomic(self._path, data)
        self._cache = list(cards)

    # ── Card mutations ───────────────────────────────────────────────────

    def upsert(self, card: ContentCard) -> None:
        """Add or update a single card, then persist."""
        cards = self.load()
        idx = next((i for i, c in enumerate(cards) if c.id == card.id), None)
        if idx is not None:
            cards[idx] = card
        else:
            cards.append(card)
        self._enforce_hard_cap(cards)
        self.save(cards)

    def remove(self, card_id: str) -> ContentCard | None:
        """Remove a card by ID. Returns the removed card or None if not found."""
        cards = self.load()
        idx = next((i for i, c in enumerate(cards) if c.id == card_id), None)
        if idx is None:
            return None
        removed = cards.pop(idx)
        self.save(cards)
        return removed

    def get_by_id(self, card_id: str) -> ContentCard | None:
        return next((c for c in self.load() if c.id == card_id), None)

    def next_card_id(self) -> str:
        """Return the next auto-incremented card ID for the current year (§4.2)."""
        from datetime import date
        year = date.today().year
        cards = self.load()
        year_seqs = []
        for c in cards:
            try:
                from pr_stage.domain import parse_card_id
                y, seq = parse_card_id(c.id)
                if y == year:
                    year_seqs.append(seq)
            except ValueError:
                pass

        # Also check gallery_memory for cross-year continuity (load from path sibling)
        next_seq = (max(year_seqs) + 1) if year_seqs else 1
        from pr_stage.domain import make_card_id
        return make_card_id(year, next_seq)

    # ── Hard cap enforcement (§8.1.1) ───────────────────────────────────

    def _enforce_hard_cap(self, cards: list[ContentCard]) -> None:
        """Enforce 50-card hard cap: purge dismissed first, then archive oldest posted."""
        if len(cards) <= GALLERY_HARD_CAP:
            return

        # Step 1: purge oldest dismissed
        dismissed = sorted(
            [c for c in cards if c.status == CardStatus.DISMISSED],
            key=lambda c: c.created_at,
        )
        for c in dismissed:
            if len(cards) <= GALLERY_HARD_CAP:
                break
            cards.remove(c)
            self._logger.event("HardCapPurge", card_id=c.id, reason="dismissed")

        # Step 2: force-archive oldest fully-resolved posted cards
        posted = sorted(
            [c for c in cards if c.status == CardStatus.POSTED and c.is_archive_ready],
            key=lambda c: c.created_at,
        )
        for c in posted:
            if len(cards) <= GALLERY_HARD_CAP:
                break
            c.status = CardStatus.ARCHIVED
            c.archived_at = datetime.now(timezone.utc)
            cards.remove(c)
            self._logger.event("HardCapForceArchive", card_id=c.id)

    # ── Corruption recovery ──────────────────────────────────────────────

    def _quarantine_corrupted(self) -> None:
        ts = int(time.time())
        dest = self._path.parent.parent / "tmp" / f"gallery_corrupt_{ts}.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(self._path), str(dest))
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Gallery Memory (archive store)
# ─────────────────────────────────────────────────────────────────────────────

class GalleryMemory:
    """Manages gallery_memory.yaml — the authoritative archive store.

    Archived cards are moved here from gallery.yaml after all platform
    drafts have been resolved (posted or skipped) or after dismissal.

    Never modified except by archive_card() below. Read-only for all
    other operations.

    Spec: §4.5 (consistency model), §5.2 (archive trigger)
    """

    def __init__(self, memory_path: Path, logger: StageLogger) -> None:
        self._path   = memory_path
        self._logger = logger

    def archive_card(self, card: ContentCard) -> None:
        """Move a card into gallery_memory.yaml (atomic write).

        Side effects:
          - Event journal entry written (CardArchived)
          - card.archived_at set to now if not already set
        """
        if card.archived_at is None:
            card.archived_at = datetime.now(timezone.utc)

        data = _safe_read_yaml(self._path) or {
            "schema_version": SCHEMA_VERSION,
            "archived_cards": [],
        }
        archived_list: list[dict] = data.setdefault("archived_cards", [])

        # Idempotent: don't duplicate
        existing_ids = {c.get("id") for c in archived_list}
        if card.id not in existing_ids:
            archived_list.append(card.to_dict())
            data["last_updated"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S+00:00"
            )
            _write_yaml_atomic(self._path, data)

        self._logger.event(
            "CardArchived",
            card_id=card.id,
            from_state=card.status.value,
            to_state="archived",
            occasion=card.occasion,
        )

    def find_last_year_card(self, occasion: str, current_year: int) -> dict | None:
        """Find the most recent card for this occasion from a prior year.

        Used by DraftPersonalizer for cross-year differentiation (§6.2 Step 6).
        """
        data = _safe_read_yaml(self._path)
        if not data:
            return None

        prior = [
            c for c in (data.get("archived_cards") or [])
            if c.get("occasion", "").lower() == occasion.lower()
            and c.get("event_date", "")[:4].isdigit()
            and int(c["event_date"][:4]) < current_year
        ]
        if not prior:
            return None
        # Most recent prior year
        return max(prior, key=lambda c: c.get("event_date", ""))

    def load_all(self) -> list[ContentCard]:
        """Load all archived cards. Used for reporting only."""
        data = _safe_read_yaml(self._path) or {}
        cards = []
        for d in (data.get("archived_cards") or []):
            try:
                cards.append(ContentCard.from_dict(d))
            except (KeyError, ValueError):
                pass
        return cards
