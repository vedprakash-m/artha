"""service.py — ContentStage: catch-up integration, card lifecycle orchestration.

ContentStage is the public API for PR-2 catch-up integration.
It is instantiated once per catch-up cycle and delegates to:
  - GalleryRepository (storage)
  - GalleryMemory (archive)
  - DraftPersonalizer (context + draft validation)
  - StageLogger (telemetry)

Catch-up integration (Step 8):
    from pr_stage.service import ContentStage
    stage = ContentStage(gallery_path, gallery_memory_path)
    new_cards = stage.process_moments(scored_moments)
    auto_drafted = stage.auto_draft_pending()
    expired = stage.sweep_expired()

Spec: §5, §8.1, §8.4, §13.2
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pr_stage.domain import (
    CardStatus,
    ContentCard,
    InvalidTransitionError,
    PlatformDraftStatus,
    derive_card_status,
    is_duplicate_occasion,
    validate_transition,
)
from pr_stage.personalizer import DraftPersonalizer
from pr_stage.repository import GalleryMemory, GalleryRepository
from pr_stage.telemetry import StageLogger

# Auto-draft trigger window (§5.3): if days_until ≤ this, try auto-draft
AUTO_DRAFT_WINDOW_DAYS = 7
# Seed card creation window (§5.2): if days_until ≤ this, create seed card
SEED_WINDOW_DAYS = 14
# Archive after all drafts resolved and this many days have passed (§5.2)
ARCHIVE_AFTER_POSTED_DAYS = 7
# Max consecutive auto-draft failures before DRAFT_BLOCKED (§5.3)
MAX_AUTO_DRAFT_FAILURES = 3

# Feature flag config key (§13.2)
STAGE_FEATURE_FLAG = "enhancements.pr_manager.stage"


# ─────────────────────────────────────────────────────────────────────────────
# ScoredMoment input type (duck-typed from PR-1 output)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScoredMoment:
    """Minimal interface from PR-1 MomentDetector output.

    ContentStage only reads these fields; PR-1 may provide more.
    """
    occasion: str
    occasion_type: str
    event_date: date
    days_until: int
    convergence_score: float = 0.0
    primary_thread: str = ""
    alt_threads: list[str] = None  # type: ignore[assignment]
    platform_exclude: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.alt_threads is None:
            self.alt_threads = []
        if self.platform_exclude is None:
            self.platform_exclude = []


# ─────────────────────────────────────────────────────────────────────────────
# Feature gate helper
# ─────────────────────────────────────────────────────────────────────────────

def _stage_enabled(config: dict) -> bool:
    """Read the nested feature flag enhancements.pr_manager.stage (§13.2)."""
    enhancements = config.get("enhancements", {}) or {}
    pr_mgr = enhancements.get("pr_manager", {}) or {}
    if not isinstance(pr_mgr, dict):
        return False  # top-level bool does not enable stage; requires nested dict
    return bool(pr_mgr.get("stage", False))


# ─────────────────────────────────────────────────────────────────────────────
# ContentStage
# ─────────────────────────────────────────────────────────────────────────────

class ContentStage:
    """Orchestrates the full content card lifecycle for catch-up Step 8.

    Instantiate once per catch-up run. All mutations are journaled and
    written atomically. Failures degrade gracefully (read-only mode if
    vault is locked, etc.).

    Spec: §5, §8.1, §8.4
    """

    def __init__(
        self,
        gallery_path: Path,
        gallery_memory_path: Path,
        *,
        state_dir: Path | None = None,
        journal_path: Path | None = None,
        audit_path: Path | None = None,
    ) -> None:
        # Derive state_dir from gallery_path if not provided
        self._state_dir = state_dir or gallery_path.parent

        # Telemetry
        journal = journal_path or self._state_dir.parent / "tmp" / "stage_events.jsonl"
        audit   = audit_path   or self._state_dir / "audit.md"
        self._logger = StageLogger(journal, audit)

        # Repository and archive
        self._gallery = GalleryRepository(gallery_path, self._logger)
        self._memory  = GalleryMemory(gallery_memory_path, self._logger)

        # Personalizer
        self._personalizer = DraftPersonalizer(self._state_dir, self._memory)

        # Circuit breaker state
        self._circuit_open  = False
        self._circuit_start = 0.0

    # ── Public catch-up API ───────────────────────────────────────────────

    @staticmethod
    def _adapt_moment(moment: Any) -> ScoredMoment:
        """Adapt a PR-1 ScoredMoment (label/moment_type/str date) to PR-2 format."""
        occasion = getattr(moment, "occasion", None) or getattr(moment, "label", "")
        occasion_type = getattr(moment, "occasion_type", None) or getattr(moment, "moment_type", "")
        raw_date = getattr(moment, "event_date", None)
        if isinstance(raw_date, str):
            raw_date = date.fromisoformat(raw_date)
        return ScoredMoment(
            occasion=occasion,
            occasion_type=occasion_type,
            event_date=raw_date,
            days_until=moment.days_until,
            convergence_score=getattr(moment, "convergence_score", 0.0),
            primary_thread=getattr(moment, "primary_thread", ""),
            alt_threads=list(getattr(moment, "alt_threads", []) or []),
            platform_exclude=list(getattr(moment, "platform_exclude", []) or []),
        )

    def process_moments(self, moments: list) -> list[ContentCard]:
        """Create seed cards for new moments within SEED_WINDOW_DAYS (§5.2).

        Uses lazy loading (load_minimal) for deduplication check — does not
        deserialize full drafts.

        Returns list of newly created cards.
        """
        t0 = time.monotonic()
        new_cards: list[ContentCard] = []

        # Load minimal for dedup check
        minimal_records = self._gallery.load_minimal()
        existing_minimal = [
            ContentCard.from_dict({
                "id":           r["id"],
                "occasion":     r["occasion"],
                "occasion_type": "",
                "event_date":   r["event_date"],
                "created_at":   datetime.now(timezone.utc).isoformat(),
                "status":       r["status"],
            })
            for r in minimal_records
            if r.get("id") and r.get("event_date")
        ]

        for moment in moments:
            moment = self._adapt_moment(moment)
            if moment.days_until > SEED_WINDOW_DAYS:
                continue

            # Deduplication check (§5.4)
            dup = is_duplicate_occasion(
                moment.occasion,
                moment.event_date,
                existing_minimal,
            )
            if dup is not None:
                # Re-score if convergence has improved
                full_card = self._gallery.get_by_id(dup.id)
                if full_card and moment.convergence_score > full_card.convergence_score:
                    full_card.convergence_score = moment.convergence_score
                    self._gallery.upsert(full_card)
                    self._logger.event(
                        "CardRescored",
                        card_id=dup.id,
                        occasion=moment.occasion,
                        new_score=str(round(moment.convergence_score, 2)),
                    )
                continue

            # Create new seed card
            card_id = self._gallery.next_card_id()
            now = datetime.now(timezone.utc)
            card = ContentCard(
                id=card_id,
                occasion=moment.occasion,
                occasion_type=moment.occasion_type,
                event_date=moment.event_date,
                created_at=now,
                status=CardStatus.SEED,
                primary_thread=moment.primary_thread,
                alt_threads=list(moment.alt_threads),
                convergence_score=moment.convergence_score,
                platform_exclude=list(moment.platform_exclude),
            )
            self._gallery.upsert(card)
            existing_minimal.append(card)  # Update local dedup list
            new_cards.append(card)
            self._logger.inc("cards_created")
            self._logger.event(
                "CardCreated",
                card_id=card_id,
                occasion=moment.occasion,
                to_state="seed",
                days_until=str(moment.days_until),
            )

        elapsed = int((time.monotonic() - t0) * 1000)
        self._logger.event(
            "ProcessMomentsComplete",
            new_cards=str(len(new_cards)),
            elapsed_ms=elapsed,
        )
        return new_cards

    def auto_draft_pending(self) -> list[ContentCard]:
        """Auto-draft all seed cards within AUTO_DRAFT_WINDOW_DAYS (§5.3).

        Drafts that pass PII gate advance to STAGED.
        Cards with 3+ consecutive failures get DRAFT_BLOCKED.

        Returns list of cards that were drafted.
        """
        today = date.today()
        cards = self._gallery.load()
        drafted: list[ContentCard] = []

        for card in cards:
            if card.status != CardStatus.SEED:
                continue
            if card.has_flag("DRAFT_BLOCKED"):
                continue

            days_until = (card.event_date - today).days
            if days_until > AUTO_DRAFT_WINDOW_DAYS:
                continue

            success = self._auto_draft_card(card)
            if success:
                drafted.append(card)

        return drafted

    def sweep_expired(self) -> list[ContentCard]:
        """Archive cards whose platform drafts are all in terminal state (§5.2).

        A card is archive-ready when:
          - Status is POSTED
          - All platform drafts are posted or skipped
          - archived_at is unset or at least ARCHIVE_AFTER_POSTED_DAYS ago

        Returns list of archived cards.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_AFTER_POSTED_DAYS)
        cards  = self._gallery.load()
        archived: list[ContentCard] = []

        for card in cards[:]:  # iterate copy
            if card.status != CardStatus.POSTED:
                continue
            if not card.is_archive_ready:
                continue
            # Check time gate
            last_posted_at = card.archived_at or card.created_at
            if last_posted_at.tzinfo is None:
                last_posted_at = last_posted_at.replace(tzinfo=timezone.utc)
            if last_posted_at > cutoff:
                continue  # too recent

            card.status = CardStatus.ARCHIVED
            card.archived_at = datetime.now(timezone.utc)
            self._memory.archive_card(card)
            self._gallery.remove(card.id)
            archived.append(card)
            self._logger.inc("cards_archived")

        return archived

    def count_by_status(self, status: str) -> int:
        """Return number of cards with the given status string."""
        return sum(1 for c in self._gallery.load() if c.status.value == status)

    def next_occasion_date(self) -> date | None:
        """Return the nearest upcoming event_date across active cards."""
        today = date.today()
        upcoming = [
            c.event_date for c in self._gallery.load()
            if not c.is_terminal and c.event_date >= today
        ]
        return min(upcoming) if upcoming else None

    def get_metrics(self) -> dict[str, int]:
        """Return telemetry counters for the current catch-up cycle."""
        return self._logger.get_metrics()

    # ── FSM transitions ───────────────────────────────────────────────────

    def _transition(self, card: ContentCard, to_status: CardStatus, *, reason: str = "") -> None:
        """Validate and apply an FSM transition (§5.2).

        Raises InvalidTransitionError if not allowed.
        Writes a Transition event to the journal.
        """
        validate_transition(card, to_status)
        from_val = card.status.value
        card.status = to_status
        self._logger.event(
            "Transition",
            card_id=card.id,
            from_state=from_val,
            to_state=to_status.value,
            reason=reason,
        )

    def dismiss_card(self, card_id: str, reason: str = "") -> ContentCard | None:
        """Dismiss a card from any non-terminal state."""
        card = self._gallery.get_by_id(card_id)
        if card is None or card.is_terminal:
            return None
        self._transition(card, CardStatus.DISMISSED, reason=reason or "user_dismiss")
        card.dismissed_reason = reason
        card.archived_at = datetime.now(timezone.utc)
        self._gallery.upsert(card)
        return card

    # ── Auto-draft internals (§5.3) ───────────────────────────────────────

    def _auto_draft_card(self, card: ContentCard) -> bool:
        """Attempt to auto-draft a seed card. Returns True on success."""
        t0 = time.monotonic()

        # Circuit breaker: if open, bail immediately
        if self._circuit_open:
            self._logger.event(
                "AutoDraftCircuitBreakerOpen",
                card_id=card.id,
                result="skipped",
            )
            return False

        try:
            self._transition(card, CardStatus.DRAFTING, reason="auto_draft")

            # Assemble personalization context
            assembled = self._personalizer.personalize(card)

            # PII_UNVERIFIED_SCRIPT gate: flag card if non-Latin content found in context
            if any(
                "PII_UNVERIFIED_SCRIPT" in (v if isinstance(v, list) else [v])
                for v in assembled.to_dict().values()
                if v
            ):
                card.set_flag("PII_UNVERIFIED_SCRIPT")

            # Write context to card
            card.personalization = assembled.to_dict()

            # Generate draft content (placeholder — real implementation calls LLM)
            drafts = self._generate_drafts(card, assembled)

            # PII + boilerplate validation on each draft
            all_passed = True
            for platform, draft_obj in drafts.items():
                passed, score, issues = self._personalizer.validate_draft(
                    draft_obj.content, platform, card
                )
                if not passed:
                    self._logger.inc("pii_failures")
                    self._logger.inc("stage_pii_failures_total")
                    all_passed = False
                    self._logger.event(
                        "DraftValidationFailed",
                        card_id=card.id,
                        platform=platform,
                        result="error",
                        issues="; ".join(issues),
                    )

                if "PII_UNVERIFIED_SCRIPT" in (draft_obj and ""):
                    card.set_flag("PII_UNVERIFIED_SCRIPT")

            card.drafts = drafts

            if all_passed:
                self._transition(card, CardStatus.STAGED, reason="drafts_passed_gate")
                self._logger.inc("cards_auto_drafted")
                self._logger.inc("cards_staged")
            else:
                # Stay in DRAFTING with pii_scan_passed: false
                pass

            self._gallery.upsert(card)
            elapsed = int((time.monotonic() - t0) * 1000)
            self._logger.event(
                "AutoDraftComplete",
                card_id=card.id,
                to_state=card.status.value,
                elapsed_ms=elapsed,
            )
            # Reset failure counter on success
            card._auto_draft_attempts = 0
            return all_passed

        except (OSError, RuntimeError) as exc:
            card._auto_draft_attempts = getattr(card, "_auto_draft_attempts", 0) + 1
            self._logger.inc("auto_draft_failures")
            self._logger.event(
                "AutoDraftFailed",
                card_id=card.id,
                attempt=str(card._auto_draft_attempts),
                result="error",
                error=str(exc),
            )

            if card._auto_draft_attempts >= MAX_AUTO_DRAFT_FAILURES:
                card.set_flag("DRAFT_BLOCKED")
                self._gallery.upsert(card)

            # Circuit breaker: open if 3+ consecutive failures (§8.4)
            self._circuit_open = True
            self._circuit_start = time.monotonic()
            return False

    def _generate_drafts(
        self,
        card: ContentCard,
        assembled: Any,
    ) -> dict:
        """Generate platform drafts. In Phase 1, returns structured placeholders.

        Phase 2 replaces this with actual LLM calls.
        """
        from pr_stage.personalizer import DraftPersonalizer
        platforms = ["facebook", "linkedin", "instagram", "whatsapp_status"]
        drafts = {}

        for platform in platforms:
            placeholder = (
                f"[Auto-draft pending — occasion: {card.occasion}, platform: {platform}. "
                f"Run: /stage draft {card.id}]"
            )
            drafts[platform] = self._personalizer.build_platform_draft(
                placeholder, platform, card
            )

        return drafts
