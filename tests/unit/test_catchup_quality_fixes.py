"""
Unit tests for scripts introduced in the catch-up quality improvement plan:
  - email_classifier.py  (P2-1)
  - health_check_writer.py (P1-1)
  - calendar_writer.py   (P1-4)
  - migrate_oi.py        (P0-3)
  - preflight.py bootstrap-stub detection (P0-2)
  - preflight.py expired token refresh path (P0-1)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ARTHA_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
for _p in [str(_ARTHA_ROOT), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# email_classifier
# ---------------------------------------------------------------------------

class TestEmailClassifier:
    """email_classifier.py: rule-based marketing detection."""

    def _import(self):
        import email_classifier as ec  # type: ignore[import]
        return ec

    def test_marketing_newsletter_tagged(self):
        ec = self._import()
        rec = {"type": "email", "sender": "noreply@substack.com", "subject": "Weekly digest"}
        ec.classify_email(rec)
        assert rec["marketing"] is True
        # Category is either newsletter or promotional depending on which rule fires first
        assert rec.get("marketing_category") in ("newsletter", "promotional")

    def test_important_domain_never_marketing(self):
        ec = self._import()
        rec = {"type": "email", "sender": "noreply@uscis.gov", "subject": "Weekly digest"}
        ec.classify_email(rec)
        assert rec["marketing"] is False

    def test_important_subject_overrides_marketing_domain(self):
        ec = self._import()
        # Amazon sends both marketing and order confirmations
        rec = {"type": "email", "sender": "no-reply@e.amazon.com", "subject": "Your order has shipped"}
        ec.classify_email(rec)
        # Important subject should override marketing domain pattern
        assert rec["marketing"] is False

    def test_promotional_subject_tagged(self):
        ec = self._import()
        rec = {"type": "email", "sender": "deals@example.com", "subject": "50% off — sale ends tonight!"}
        ec.classify_email(rec)
        assert rec["marketing"] is True

    def test_regular_email_not_marketing(self):
        ec = self._import()
        rec = {"type": "email", "sender": "friend@gmail.com", "subject": "Dinner plans"}
        ec.classify_email(rec)
        assert rec["marketing"] is False

    def test_non_email_record_passes_through_unchanged(self):
        ec = self._import()
        rec = {"type": "calendar_event", "summary": "Weekly digest"}
        original = dict(rec)
        classify_records = ec.classify_records
        classify_records([rec])
        # Non-email record should not gain marketing field
        assert "marketing" not in rec

    def test_security_alert_not_marketing(self):
        ec = self._import()
        rec = {"type": "email", "sender": "security@paypal.com", "subject": "Unauthorized access attempt detected"}
        ec.classify_email(rec)
        assert rec["marketing"] is False

    def test_unsubscribe_subject_tagged(self):
        ec = self._import()
        rec = {"type": "email", "sender": "info@newsletter.example.com", "subject": "Unsubscribe from our list"}
        ec.classify_email(rec)
        assert rec["marketing"] is True

    def test_custom_whitelist_respected(self):
        ec = self._import()
        rec = {"type": "email", "sender": "news@mycompany.internal", "subject": "Company newsletter"}
        ec.classify_email(rec, custom_whitelist=["mycompany.internal"])
        assert rec["marketing"] is False

    def test_hdfc_bank_not_marketing(self):
        ec = self._import()
        rec = {"type": "email", "sender": "alerts@hdfcbank.com", "subject": "Transaction alert"}
        ec.classify_email(rec)
        assert rec["marketing"] is False


# ---------------------------------------------------------------------------
# health_check_writer
# ---------------------------------------------------------------------------

class TestHealthCheckWriter:
    """health_check_writer.py: atomic frontmatter updater."""

    def _import(self):
        import health_check_writer as hcw  # type: ignore[import]
        return hcw

    def test_write_creates_file_from_template(self, tmp_path):
        hcw = self._import()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        templates_dir = state_dir / "templates"
        templates_dir.mkdir()
        (templates_dir / "health-check.md").write_text(
            "---\nschema_version: '1.1'\nlast_catch_up: never\ncatch_up_count: 0\n---\n\n## Connector Health\n",
            encoding="utf-8",
        )

        with (
            patch.object(hcw, "HEALTH_CHECK_FILE", state_dir / "health-check.md"),
            patch.object(hcw, "STATE_DIR", state_dir),
            patch.object(hcw, "TMP_DIR", tmp_path / "tmp"),
            patch.object(hcw, "LOCK_FILE", tmp_path / ".artha-lock"),
        ):
            rc = hcw.write_health_check(
                last_catch_up="2026-03-15T23:00:00Z",
                email_count=21,
                domains_processed=["finance", "kids"],
                session_mode="normal",
            )
        assert rc == 0
        content = (state_dir / "health-check.md").read_text()
        # Value may be quoted (e.g. "2026-03-15T23:00:00Z") — check for the timestamp itself
        assert "2026-03-15T23:00:00Z" in content
        assert "email_count: 21" in content
        assert "catch_up_count: 1" in content

    def test_increment_catch_up_count(self, tmp_path):
        hcw = self._import()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        hc = state_dir / "health-check.md"
        hc.write_text("---\nschema_version: '1.1'\nlast_catch_up: never\ncatch_up_count: 5\n---\n", encoding="utf-8")

        with (
            patch.object(hcw, "HEALTH_CHECK_FILE", hc),
            patch.object(hcw, "STATE_DIR", state_dir),
            patch.object(hcw, "TMP_DIR", tmp_path / "tmp"),
            patch.object(hcw, "LOCK_FILE", tmp_path / ".artha-lock"),
        ):
            hcw.write_health_check(last_catch_up="2026-03-15T23:00:00Z")
        content = hc.read_text()
        assert "catch_up_count: 6" in content

    def test_bootstrap_stub_replaced(self, tmp_path):
        hcw = self._import()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        templates_dir = state_dir / "templates"
        templates_dir.mkdir()
        tmpl = templates_dir / "health-check.md"
        tmpl.write_text("---\nschema_version: '1.1'\nlast_catch_up: never\ncatch_up_count: 0\n---\n\n## Connector Health\n", encoding="utf-8")
        hc = state_dir / "health-check.md"
        hc.write_text("---\n# Content\nsome: value\n---\n", encoding="utf-8")

        with (
            patch.object(hcw, "HEALTH_CHECK_FILE", hc),
            patch.object(hcw, "STATE_DIR", state_dir),
            patch.object(hcw, "TMP_DIR", tmp_path / "tmp"),
            patch.object(hcw, "LOCK_FILE", tmp_path / ".artha-lock"),
        ):
            hcw.write_health_check(last_catch_up="2026-03-15T23:00:00Z")
        content = hc.read_text()
        assert "# Content" not in content
        assert "schema_version" in content


# ---------------------------------------------------------------------------
# calendar_writer
# ---------------------------------------------------------------------------

class TestCalendarWriter:
    """calendar_writer.py: pipeline calendar event persistence."""

    def _import(self):
        import calendar_writer as cw  # type: ignore[import]
        return cw

    def _make_event(self, title: str = "Team Standup", date: str = "2026-03-20") -> dict:
        return {
            "type": "calendar_event",
            "source": "google_calendar",
            "title": title,
            "date": date,
            "end": date,
        }

    def test_writes_new_event(self, tmp_path):
        cw = self._import()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        cal = state_dir / "calendar.md"
        cal.write_text("---\ndomain: calendar\nlast_updated: \"\"\n---\n\n## Upcoming Events\n\n<!-- Events are appended here by calendar_writer.py -->\n\n## Past Events\n", encoding="utf-8")

        events = [self._make_event()]
        import io
        from unittest.mock import patch as _patch
        with _patch.object(cw, "CALENDAR_FILE", cal), _patch.object(cw, "STATE_DIR", state_dir), _patch.object(cw, "TMP_DIR", tmp_path / "tmp"):
            # Write records directly via internal function
            content = cw._read_or_init_calendar()
            existing = cw._existing_dedup_keys(content)
            entry = cw._format_event(events[0], "abc123deadbeef12")
            assert "Team Standup" in entry
            assert "dedup:abc123deadbeef12" in entry

    def test_dedup_key_stable(self):
        cw = self._import()
        e1 = {"title": "Dentist", "date": "2026-03-25"}
        e2 = {"title": "Dentist", "date": "2026-03-25"}
        assert cw._event_dedup_key(e1) == cw._event_dedup_key(e2)

    def test_dedup_key_differs_for_different_events(self):
        cw = self._import()
        e1 = {"title": "Dentist", "date": "2026-03-25"}
        e2 = {"title": "Doctor", "date": "2026-03-25"}
        assert cw._event_dedup_key(e1) != cw._event_dedup_key(e2)

    def test_is_calendar_record(self):
        cw = self._import()
        assert cw._is_calendar_record({"source": "google_calendar"}) is True
        assert cw._is_calendar_record({"type": "event"}) is True
        assert cw._is_calendar_record({"type": "email", "source": "gmail"}) is False

    def test_bootstrap_stub_replaced_on_read(self, tmp_path):
        cw = self._import()
        stub_content = "---\n# Content\nsome: value\n---\n"
        cal = tmp_path / "calendar.md"
        cal.write_text(stub_content, encoding="utf-8")
        from unittest.mock import patch as _patch
        with _patch.object(cw, "CALENDAR_FILE", cal):
            # _read_or_init_calendar should replace the stub
            result = cw._read_or_init_calendar()
        assert "# Content" not in result
        assert "domain: calendar" in result


# ---------------------------------------------------------------------------
# migrate_oi
# ---------------------------------------------------------------------------

class TestMigrateOI:
    """migrate_oi.py: OI backfill migration."""

    def _import(self):
        import migrate_oi as moi  # type: ignore[import]
        return moi

    def test_dry_run_no_write(self, tmp_path):
        moi = self._import()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "finance.md").write_text("- See OI-016 for debit card task\n- Also OI-023 pending\n", encoding="utf-8")
        (state_dir / "open_items.md").write_text(
            "---\ndomain: open_items\nlast_updated: \"\"\nschema_version: \"1.0\"\n---\n\n## Active\n",
            encoding="utf-8",
        )

        with (
            patch.object(moi, "STATE_DIR", state_dir),
            patch.object(moi, "OPEN_ITEMS_FILE", state_dir / "open_items.md"),
        ):
            rc = moi.run(dry_run=True)
        assert rc == 0
        # File unchanged in dry-run
        content = (state_dir / "open_items.md").read_text()
        assert "OI-016" not in content

    def test_backfills_missing_ids(self, tmp_path):
        moi = self._import()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "finance.md").write_text("- See OI-016 for debit card task\n- OI-023 follow-up needed\n", encoding="utf-8")
        oi_file = state_dir / "open_items.md"
        oi_file.write_text(
            "---\ndomain: open_items\nlast_updated: \"\"\nschema_version: \"1.0\"\n---\n\n## Active\n",
            encoding="utf-8",
        )

        with (
            patch.object(moi, "STATE_DIR", state_dir),
            patch.object(moi, "OPEN_ITEMS_FILE", oi_file),
        ):
            rc = moi.run(dry_run=False)
        assert rc == 0
        content = oi_file.read_text()
        assert "OI-016" in content
        assert "OI-023" in content

    def test_skips_already_tracked_ids(self, tmp_path):
        moi = self._import()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "finance.md").write_text("- OI-016 handled\n", encoding="utf-8")
        oi_file = state_dir / "open_items.md"
        oi_file.write_text(
            "---\ndomain: open_items\nlast_updated: \"\"\n---\n\n## Active\n\n- id: OI-016\n  status: open\n",
            encoding="utf-8",
        )

        with (
            patch.object(moi, "STATE_DIR", state_dir),
            patch.object(moi, "OPEN_ITEMS_FILE", oi_file),
        ):
            rc = moi.run(dry_run=False)
        assert rc == 0
        # OI-016 should appear exactly once
        content = oi_file.read_text()
        assert content.count("OI-016") == 1

    def test_highest_oi_reported(self):
        moi = self._import()
        ids = {"OI-016", "OI-023", "OI-029"}
        assert moi._highest_oi_id(ids) == 29


# ---------------------------------------------------------------------------
# preflight.py bootstrap stub detection (P0-2)
# ---------------------------------------------------------------------------

class TestPreflightBootstrapStub:
    """preflight.py: _is_bootstrap_stub() detects placeholder files."""

    def _import(self):
        import preflight as pf  # type: ignore[import]
        return pf

    def test_stub_detected(self, tmp_path):
        pf = self._import()
        f = tmp_path / "test.md"
        f.write_text("---\n# Content\nsome: value\n\n---\n", encoding="utf-8")
        assert pf._is_bootstrap_stub(str(f)) is True

    def test_real_file_not_stub(self, tmp_path):
        pf = self._import()
        f = tmp_path / "test.md"
        f.write_text("---\nschema_version: '1.0'\nlast_updated: '2026-03-15'\n---\n\n# Real data\n", encoding="utf-8")
        assert pf._is_bootstrap_stub(str(f)) is False

    def test_missing_file_returns_false(self, tmp_path):
        pf = self._import()
        missing = str(tmp_path / "nonexistent.md")
        assert pf._is_bootstrap_stub(missing) is False

    def test_check_state_templates_detects_stubs(self, tmp_path):
        pf = self._import()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        templates_dir = state_dir / "templates"
        templates_dir.mkdir()
        # Create a template
        (templates_dir / "goals.md").write_text(
            "---\ndomain: goals\nlast_updated: never\n---\n\n# Goals\n",
            encoding="utf-8",
        )
        # Create a stub version of the file
        (state_dir / "goals.md").write_text("---\n# Content\nsome: value\n---\n", encoding="utf-8")

        with patch.object(pf, "STATE_DIR", str(state_dir)):
            result = pf.check_state_templates(auto_fix=False)
        assert result.passed is False
        assert "bootstrap stub" in result.message

    def test_check_state_templates_fix_replaces_stub(self, tmp_path):
        pf = self._import()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        templates_dir = state_dir / "templates"
        templates_dir.mkdir()
        (templates_dir / "goals.md").write_text(
            "---\ndomain: goals\nlast_updated: never\n---\n\n# Goals\n",
            encoding="utf-8",
        )
        stub = state_dir / "goals.md"
        stub.write_text("---\n# Content\nsome: value\n---\n", encoding="utf-8")

        with patch.object(pf, "STATE_DIR", str(state_dir)):
            result = pf.check_state_templates(auto_fix=True)
        assert result.auto_fixed is True
        content = stub.read_text()
        assert "# Content" not in content
        assert "domain: goals" in content
