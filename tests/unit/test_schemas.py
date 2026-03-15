"""
tests/unit/test_schemas.py — Unit tests for scripts/schemas/ package

Phase 5 verification suite.

Coverage:
  - BriefingOutput validates a known-good briefing
  - BriefingOutput rejects missing one_thing
  - BriefingOutput rejects invalid severity enum
  - Validation failure is graceful (non-blocking pattern tested at module level)
  - Structured JSON can be serialized
  - DomainIndexCard factory works correctly
  - SessionSummarySchema validates and rejects appropriately
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from schemas.briefing import AlertItem, BriefingOutput, DomainSummary, FlashBriefingOutput
from schemas.domain_index import DomainIndexCard, DomainIndexEntry
from schemas.session import SessionSummarySchema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_alert(
    severity: str = "urgent",
    domain: str = "finance",
    description: str = "Bill due in 7 days",
    score: int | None = 18,
) -> AlertItem:
    return AlertItem(
        severity=severity,
        domain=domain,
        description=description,
        score=score,
    )


def _make_briefing(**kwargs) -> BriefingOutput:
    defaults = dict(
        one_thing="File your tax return before April 15",
        critical_alerts=[],
        urgent_alerts=[_make_alert()],
        domain_summaries=[
            DomainSummary(
                domain="finance",
                bullet_points=["Bill due", "Tax deadline"],
                alert_count=1,
            )
        ],
        open_items_added=2,
        open_items_closed=1,
        pii_footer="🔒 PII: 10 scanned · 0 redacted · 0 patterns",
        briefing_format="standard",
    )
    defaults.update(kwargs)
    return BriefingOutput(**defaults)


# ---------------------------------------------------------------------------
# AlertItem
# ---------------------------------------------------------------------------

class TestAlertItem:
    def test_valid_alert(self):
        alert = _make_alert()
        assert alert.severity == "urgent"
        assert alert.domain == "finance"

    def test_domain_normalized_to_lowercase(self):
        alert = _make_alert(domain="Finance")
        assert alert.domain == "finance"

    def test_invalid_severity_raises(self):
        with pytest.raises(ValidationError):
            AlertItem(severity="extreme", domain="test", description="x")

    def test_description_too_long_raises(self):
        with pytest.raises(ValidationError):
            AlertItem(
                severity="urgent",
                domain="test",
                description="x" * 201,
            )

    def test_score_above_27_raises(self):
        with pytest.raises(ValidationError):
            AlertItem(
                severity="critical",
                domain="test",
                description="test",
                score=28,
            )

    def test_score_is_optional(self):
        alert = AlertItem(severity="info", domain="test", description="test")
        assert alert.score is None


# ---------------------------------------------------------------------------
# DomainSummary
# ---------------------------------------------------------------------------

class TestDomainSummary:
    def test_valid_domain_summary(self):
        ds = DomainSummary(
            domain="health",
            bullet_points=["Appointment next week", "Insurance renewal due"],
            alert_count=1,
        )
        assert ds.domain == "health"
        assert len(ds.bullet_points) == 2

    def test_domain_lowercase_normalized(self):
        ds = DomainSummary(domain="HEALTH", alert_count=0)
        assert ds.domain == "health"

    def test_alert_count_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            DomainSummary(domain="test", alert_count=-1)

    def test_too_many_bullets_rejected(self):
        with pytest.raises(ValidationError):
            DomainSummary(
                domain="test",
                bullet_points=["B"] * 6,  # max is 5
                alert_count=0,
            )


# ---------------------------------------------------------------------------
# BriefingOutput
# ---------------------------------------------------------------------------

class TestBriefingOutput:
    def test_valid_briefing_passes(self):
        briefing = _make_briefing()
        assert briefing.one_thing.startswith("File")
        assert briefing.briefing_format == "standard"

    def test_rejects_missing_one_thing(self):
        with pytest.raises(ValidationError):
            _make_briefing(one_thing="")

    def test_rejects_one_thing_too_long(self):
        with pytest.raises(ValidationError):
            _make_briefing(one_thing="x" * 301)

    def test_rejects_invalid_severity_in_alert(self):
        with pytest.raises(ValidationError):
            _make_briefing(
                critical_alerts=[
                    AlertItem(severity="WRONG", domain="test", description="x")
                ]
            )

    def test_all_alerts_sorted_by_severity(self):
        briefing = _make_briefing(
            critical_alerts=[_make_alert(severity="critical")],
            urgent_alerts=[_make_alert(severity="urgent")],
        )
        all_alerts = briefing.all_alerts()
        assert all_alerts[0].severity == "critical"
        assert all_alerts[1].severity == "urgent"

    def test_structured_json_serializable(self):
        briefing = _make_briefing()
        data = briefing.model_dump()
        json_str = json.dumps(data)
        assert '"one_thing"' in json_str

    def test_open_items_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            _make_briefing(open_items_added=-1)

    def test_rejects_invalid_briefing_format(self):
        with pytest.raises(ValidationError):
            _make_briefing(briefing_format="unknown_format")

    def test_valid_formats_accepted(self):
        for fmt in ["flash", "standard", "digest", "deep"]:
            b = _make_briefing(briefing_format=fmt)
            assert b.briefing_format == fmt


# ---------------------------------------------------------------------------
# FlashBriefingOutput
# ---------------------------------------------------------------------------

class TestFlashBriefingOutput:
    def test_valid_flash_briefing(self):
        flash = FlashBriefingOutput(
            one_thing="Pay the rent",
            top_alert=_make_alert(severity="urgent"),
            open_items_added=1,
            pii_footer="🔒 PII: 5 scanned · 0 redacted",
        )
        assert flash.briefing_format == "flash"
        assert flash.top_alert is not None

    def test_flash_one_thing_too_long_raises(self):
        with pytest.raises(ValidationError):
            FlashBriefingOutput(one_thing="x" * 201)

    def test_top_alert_optional(self):
        flash = FlashBriefingOutput(one_thing="All clear today")
        assert flash.top_alert is None


# ---------------------------------------------------------------------------
# Graceful validation degradation pattern
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    """Verify the non-blocking validation pattern described in the spec.

    Validation failures should be catchable without blocking output.
    The schema never raises on partial data if callers use model_validate
    with appropriate error handling.
    """

    def test_validation_error_is_catchable(self):
        try:
            BriefingOutput(one_thing="")
            assert False, "Should have raised ValidationError"
        except ValidationError as exc:
            # Error is inspectable — caller can log and proceed
            assert len(exc.errors()) > 0

    def test_partial_briefing_can_be_logged(self):
        """A failing validation should not prevent logging the briefing text."""
        failed = False
        try:
            BriefingOutput(one_thing="")
        except ValidationError:
            failed = True

        # In production, we'd still render the briefing Markdown
        # and just log the schema failure. Test the flag.
        assert failed is True  # schema caught the problem

    def test_validation_errors_are_descriptive(self):
        try:
            AlertItem(severity="INVALID", domain="test", description="x")
        except ValidationError as exc:
            error_json = exc.errors()
            assert any("severity" in str(e) for e in error_json)


# ---------------------------------------------------------------------------
# DomainIndexCard
# ---------------------------------------------------------------------------

class TestDomainIndexCard:
    def _sample_index_data(self) -> dict:
        return {
            "immigration": {
                "status": "ACTIVE",
                "last_activity_days": 1,
                "alerts": 2,
                "src": "state/immigration.md.age",
                "last_updated": "2026-03-14",
            },
            "travel": {
                "status": "STALE",
                "last_activity_days": 65,
                "alerts": 0,
                "src": "state/travel.md",
                "last_updated": "2026-01-09",
            },
            "estate": {
                "status": "ARCHIVE",
                "last_activity_days": 600,
                "alerts": 0,
                "src": "state/estate.md.age",
                "last_updated": "2024-07-20",
            },
        }

    def test_from_index_data_creates_card(self):
        data = self._sample_index_data()
        card = DomainIndexCard.from_index_data("INDEX CARD TEXT", data)
        assert card.total_domains == 3
        assert card.active_count == 1
        assert card.stale_count == 1
        assert card.archive_count == 1

    def test_entries_keyed_by_domain(self):
        data = self._sample_index_data()
        card = DomainIndexCard.from_index_data("card", data)
        assert "immigration" in card.entries
        assert card.entries["immigration"].alerts == 2

    def test_card_text_preserved(self):
        data = self._sample_index_data()
        card = DomainIndexCard.from_index_data("MARKER_TEXT_12345", data)
        assert card.card_text == "MARKER_TEXT_12345"

    def test_entry_status_validated(self):
        with pytest.raises(ValidationError):
            DomainIndexEntry(domain="test", status="INVALID_STATUS")


# ---------------------------------------------------------------------------
# SessionSummarySchema
# ---------------------------------------------------------------------------

class TestSessionSummarySchema:
    def _make_schema_valid(self, **kwargs) -> SessionSummarySchema:
        defaults = dict(
            session_intent="Morning catch-up",
            command_executed="/catch-up",
            key_findings=["Finding 1", "Finding 2"],
            state_mutations=["state/finance.md"],
            open_threads=["Review IRS notice"],
            next_suggested="/domain finance",
            timestamp="2026-03-15T10:00:00Z",
            context_before_pct=82.0,
            context_after_pct=22.0,
            trigger_reason="post_command",
        )
        defaults.update(kwargs)
        return SessionSummarySchema(**defaults)

    def test_valid_schema_passes(self):
        s = self._make_schema_valid()
        assert s.session_intent == "Morning catch-up"

    def test_findings_capped_at_5(self):
        s = self._make_schema_valid(
            key_findings=["F1", "F2", "F3", "F4", "F5", "F6", "F7"]
        )
        assert len(s.key_findings) <= 5

    def test_invalid_trigger_reason_raises(self):
        with pytest.raises(ValidationError):
            self._make_schema_valid(trigger_reason="random_reason")

    def test_invalid_timestamp_raises(self):
        with pytest.raises(ValidationError):
            self._make_schema_valid(timestamp="not-a-date")

    def test_context_pct_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            self._make_schema_valid(context_before_pct=150.0)

    def test_missing_session_intent_raises(self):
        with pytest.raises(ValidationError):
            self._make_schema_valid(session_intent="")
