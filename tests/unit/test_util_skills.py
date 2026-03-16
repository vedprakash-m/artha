"""
tests/unit/test_util_skills.py — Tests for the 5 new utilization skills (U-9)

Tests:
  TestRelationshipPulse    (6 tests) — circle loading, stale detection, cadence thresholds
  TestOccasionTracker      (7 tests) — birthday/festival/anniversary detection, windows
  TestBillDueTracker       (5 tests) — monthly/one-time bill date parsing, alert thresholds
  TestCreditMonitor        (4 tests) — signal detection, fraud priority, dedup
  TestSchoolCalendar       (5 tests) — school event detection, grade alerts

Ref: specs/util.md §U-9
"""
from __future__ import annotations

import textwrap
from datetime import date, timedelta
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# TestRelationshipPulse
# ─────────────────────────────────────────────────────────────────────────────

class TestRelationshipPulse:

    def _make_contacts(self, tmp_path: Path, last_contact_date: str, circle_cadence: str = "monthly") -> Path:
        contacts = f"""\
---
schema_version: "1.1"
circles:
  friends:
    label: "Friends"
    members: ["Alice Smith"]
    cadence: "{circle_cadence}"
    nudge: true
---
# Contacts
## Friends
| Name | Phone | Last WA | Notes |
|------|-------|---------|-------|
| Alice Smith | +1 555 0100 | {last_contact_date} | |
"""
        p = tmp_path / "state" / "contacts.md"
        _write(p, contacts)
        return tmp_path

    def test_stale_contact_surfaced(self, tmp_path):
        from scripts.skills.relationship_pulse import RelationshipPulseSkill
        forty_days_ago = (date.today() - timedelta(days=40)).isoformat()
        artha_dir = self._make_contacts(tmp_path, forty_days_ago)
        skill = RelationshipPulseSkill(artha_dir=artha_dir)
        result = skill.execute()
        assert result["status"] == "success"
        stale = result["data"]["stale_contacts"]
        assert any(c["name"] == "Alice Smith" for c in stale)

    def test_fresh_contact_not_stale(self, tmp_path):
        from scripts.skills.relationship_pulse import RelationshipPulseSkill
        two_days_ago = (date.today() - timedelta(days=2)).isoformat()
        artha_dir = self._make_contacts(tmp_path, two_days_ago)
        skill = RelationshipPulseSkill(artha_dir=artha_dir)
        result = skill.execute()
        assert result["status"] == "success"
        stale = result["data"]["stale_contacts"]
        assert not any(c["name"] == "Alice Smith" for c in stale)

    def test_no_nudge_circle_skipped(self, tmp_path):
        contacts = """\
---
schema_version: "1.1"
circles:
  core:
    label: "Core Family"
    members: ["Bob"]
    cadence: "daily_passive"
    nudge: false
---
# Contacts
| Name | Phone | Last WA |
|Bob | +1 555 | 2020-01-01 |
"""
        p = tmp_path / "state" / "contacts.md"
        _write(p, contacts)
        from scripts.skills.relationship_pulse import RelationshipPulseSkill
        skill = RelationshipPulseSkill(artha_dir=tmp_path)
        result = skill.execute()
        assert result["status"] == "success"
        assert result["data"]["total_stale"] == 0

    def test_never_contacted_flagged(self, tmp_path):
        from scripts.skills.relationship_pulse import RelationshipPulseSkill
        artha_dir = self._make_contacts(tmp_path, "", circle_cadence="monthly")
        (artha_dir / "state" / "contacts.md").write_text("""\
---
schema_version: "1.1"
circles:
  friends:
    label: "Friends"
    members: ["Unknown Person"]
    cadence: "monthly"
    nudge: true
---
# Contacts
| Unknown Person | +1 | — | |
""", encoding="utf-8")
        skill = RelationshipPulseSkill(artha_dir=artha_dir)
        result = skill.execute()
        # never-contacted member should appear as stale (last_contact=None)
        stale = result["data"]["stale_contacts"]
        assert any(c["name"] == "Unknown Person" for c in stale)

    def test_missing_contacts_file_returns_empty(self, tmp_path):
        from scripts.skills.relationship_pulse import RelationshipPulseSkill
        skill = RelationshipPulseSkill(artha_dir=tmp_path)
        result = skill.execute()
        assert result["status"] == "success"
        assert result["data"]["total_stale"] == 0

    def test_to_dict_returns_summary_string(self, tmp_path):
        from scripts.skills.relationship_pulse import RelationshipPulseSkill
        skill = RelationshipPulseSkill(artha_dir=tmp_path)
        d = skill.to_dict()
        assert "summary" in d
        assert isinstance(d["summary"], str)


# ─────────────────────────────────────────────────────────────────────────────
# TestOccasionTracker
# ─────────────────────────────────────────────────────────────────────────────

class TestOccasionTracker:

    def _make_occasions(self, tmp_path: Path, extra_content: str = "") -> Path:
        p = tmp_path / "state" / "occasions.md"
        _write(p, extra_content)
        return tmp_path

    def test_upcoming_birthday_detected(self, tmp_path):
        from scripts.skills.occasion_tracker import OccasionTrackerSkill
        future = (date.today() + timedelta(days=5)).isoformat()
        dob_year = date.today().year - 30  # 30 years old
        dob = f"{dob_year}-{future[5:]}"  # same month-day, 30 years ago
        content = f"""\
---
schema_version: "1.1"
---
# Occasions
### Extended Family — India Birthdays
| Person | DOB | Age in 2026 | Relationship | Contact |
|--------|-----|-------------|--------------|---------|
| Test Person | {dob} | 30 | Friend | |
"""
        artha_dir = self._make_occasions(tmp_path, content)
        skill = OccasionTrackerSkill(artha_dir=artha_dir)
        result = skill.execute()
        assert result["status"] == "success"
        upcoming = result["data"]["upcoming"]
        birthdays = [u for u in upcoming if u["type"] == "birthday"]
        assert any(u["name"] == "Test Person" for u in birthdays)

    def test_past_birthday_not_surfaced_if_next_year_far(self, tmp_path):
        from scripts.skills.occasion_tracker import OccasionTrackerSkill
        # Birthday was 200 days ago → next occurrence is 165 days away (>30d window)
        past = date.today() - timedelta(days=200)
        dob = f"1990-{past.strftime('%m-%d')}"
        content = f"""\
---
schema_version: "1.1"
---
### Extended Family — India Birthdays
| Name | DOB | Age | Rel | Phone |
| Old Person | {dob} | 35 | | |
"""
        artha_dir = self._make_occasions(tmp_path, content)
        skill = OccasionTrackerSkill(artha_dir=artha_dir)
        result = skill.execute()
        upcoming = result["data"]["upcoming"]
        assert not any(u["name"] == "Old Person" for u in upcoming)

    def test_festival_within_window_detected(self, tmp_path):
        from scripts.skills.occasion_tracker import OccasionTrackerSkill
        tomorrow = (date.today() + timedelta(days=2)).isoformat()
        content = f"""\
---
schema_version: "1.1"
---
## Cultural & Religious Occasions
| Festival | 2026 Date | Significance | Action |
| Test Festival | {tomorrow} | Test | Wish family |
"""
        artha_dir = self._make_occasions(tmp_path, content)
        skill = OccasionTrackerSkill(artha_dir=artha_dir)
        result = skill.execute()
        upcoming = result["data"]["upcoming"]
        festivals = [u for u in upcoming if u["type"] == "festival"]
        assert any(u["name"] == "Test Festival" for u in festivals)

    def test_anniversary_within_30_days(self, tmp_path):
        from scripts.skills.occasion_tracker import OccasionTrackerSkill
        # Anniversary that falls 10 days from now this year
        ann_date = date.today() + timedelta(days=10)
        ann_str = ann_date.strftime("%B %d, 2007")
        content = f"""\
---
schema_version: "1.1"
---
### Wedding Anniversary
| Event | Date | Milestone |
| Couple Anniversary | {ann_str} | 17 years |
"""
        artha_dir = self._make_occasions(tmp_path, content)
        skill = OccasionTrackerSkill(artha_dir=artha_dir)
        result = skill.execute()
        upcoming = result["data"]["upcoming"]
        anniversaries = [u for u in upcoming if u["type"] == "anniversary"]
        assert any(u["name"] == "Couple Anniversary" for u in anniversaries)

    def test_empty_occasions_returns_ok(self, tmp_path):
        from scripts.skills.occasion_tracker import OccasionTrackerSkill
        skill = OccasionTrackerSkill(artha_dir=tmp_path)
        result = skill.execute()
        assert result["status"] == "success"
        assert result["data"]["total"] == 0

    def test_severity_red_within_3_days(self, tmp_path):
        from scripts.skills.occasion_tracker import OccasionTrackerSkill
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        dob_year = date.today().year - 25
        dob = f"{dob_year}-{tomorrow[5:]}"
        content = f"""\
---
schema_version: "1.1"
---
### Extended Family — India Birthdays
| Person | DOB | Age | Rel | Phone |
| Urgent Person | {dob} | 25 | | |
"""
        artha_dir = self._make_occasions(tmp_path, content)
        skill = OccasionTrackerSkill(artha_dir=artha_dir)
        result = skill.execute()
        upcoming = result["data"]["upcoming"]
        match = next((u for u in upcoming if u["name"] == "Urgent Person"), None)
        assert match is not None
        assert match["severity"] == "🔴"

    def test_to_dict_includes_summary(self, tmp_path):
        from scripts.skills.occasion_tracker import OccasionTrackerSkill
        skill = OccasionTrackerSkill(artha_dir=tmp_path)
        d = skill.to_dict()
        assert "summary" in d


# ─────────────────────────────────────────────────────────────────────────────
# TestBillDueTracker
# ─────────────────────────────────────────────────────────────────────────────

class TestBillDueTracker:

    def _make_occasions(self, tmp_path: Path, bill_date: str, item: str = "Test Bill") -> Path:
        content = f"""\
---
schema_version: "1.1"
---
# Occasions
## Financial Deadlines
### Upcoming Bills
| Item | Due Date | Notes |
| {item} | {bill_date} | Some notes |
"""
        p = tmp_path / "state" / "occasions.md"
        _write(p, content)
        return tmp_path

    def test_bill_due_in_3_days_orange(self, tmp_path):
        from scripts.skills.bill_due_tracker import BillDueTrackerSkill
        due = (date.today() + timedelta(days=3)).isoformat()
        artha_dir = self._make_occasions(tmp_path, due)
        skill = BillDueTrackerSkill(artha_dir=artha_dir)
        result = skill.execute()
        assert result["status"] == "success"
        upcoming = result["data"]["upcoming"]
        assert any(b["item"] == "Test Bill" for b in upcoming)
        match = next(b for b in upcoming if b["item"] == "Test Bill")
        assert match["severity"] == "🟠"

    def test_bill_due_tomorrow_red(self, tmp_path):
        from scripts.skills.bill_due_tracker import BillDueTrackerSkill
        due = (date.today() + timedelta(days=1)).isoformat()
        artha_dir = self._make_occasions(tmp_path, due)
        skill = BillDueTrackerSkill(artha_dir=artha_dir)
        result = skill.execute()
        upcoming = result["data"]["upcoming"]
        match = next((b for b in upcoming if b["item"] == "Test Bill"), None)
        assert match is not None
        assert match["severity"] == "🔴"

    def test_bill_not_due_soon_not_surfaced(self, tmp_path):
        from scripts.skills.bill_due_tracker import BillDueTrackerSkill
        due = (date.today() + timedelta(days=30)).isoformat()
        artha_dir = self._make_occasions(tmp_path, due)
        skill = BillDueTrackerSkill(artha_dir=artha_dir)
        result = skill.execute()
        upcoming = result["data"]["upcoming"]
        assert not any(b["item"] == "Test Bill" for b in upcoming)

    def test_monthly_bill_parses_day(self, tmp_path):
        from scripts.skills.bill_due_tracker import _parse_bill_date
        today = date.today()
        d = _parse_bill_date("Monthly (23rd)", today)
        assert d is not None
        assert d.day == 23

    def test_missing_occasions_returns_empty(self, tmp_path):
        from scripts.skills.bill_due_tracker import BillDueTrackerSkill
        skill = BillDueTrackerSkill(artha_dir=tmp_path)
        result = skill.execute()
        assert result["status"] == "success"
        assert result["data"]["upcoming"] == []


# ─────────────────────────────────────────────────────────────────────────────
# TestCreditMonitor
# ─────────────────────────────────────────────────────────────────────────────

class TestCreditMonitor:

    def test_hard_inquiry_detected(self, tmp_path):
        from scripts.skills.credit_monitor import CreditMonitorSkill
        p = tmp_path / "state" / "digital.md"
        _write(p, "- Hard inquiry from Capital One on 2026-03-10\n")
        skill = CreditMonitorSkill(artha_dir=tmp_path)
        result = skill.execute()
        assert result["status"] == "success"
        alerts = result["data"]["alerts"]
        assert any(a["type"] == "hard_inquiry" for a in alerts)

    def test_fraud_alert_detected_and_prioritized(self, tmp_path):
        from scripts.skills.credit_monitor import CreditMonitorSkill
        p = tmp_path / "state" / "digital.md"
        _write(p, "- Fraud alert: suspicious activity detected on account\n- Hard inquiry Capital One\n")
        skill = CreditMonitorSkill(artha_dir=tmp_path)
        result = skill.execute()
        assert result["data"]["has_fraud"] is True
        alerts = result["data"]["alerts"]
        assert alerts[0]["type"] == "fraud_alert"

    def test_normal_content_no_alerts(self, tmp_path):
        from scripts.skills.credit_monitor import CreditMonitorSkill
        p = tmp_path / "state" / "digital.md"
        _write(p, "- Netflix subscription $15.99/month\n- Spotify $10/month\n")
        skill = CreditMonitorSkill(artha_dir=tmp_path)
        result = skill.execute()
        assert result["data"]["total"] == 0

    def test_missing_state_files_returns_empty(self, tmp_path):
        from scripts.skills.credit_monitor import CreditMonitorSkill
        skill = CreditMonitorSkill(artha_dir=tmp_path)
        result = skill.execute()
        assert result["status"] == "success"
        assert result["data"]["total"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# TestSchoolCalendar
# ─────────────────────────────────────────────────────────────────────────────

class TestSchoolCalendar:

    def test_school_event_detected_in_calendar(self, tmp_path):
        from scripts.skills.school_calendar import SchoolCalendarSkill
        tomorrow = (date.today() + timedelta(days=2)).isoformat()
        p = tmp_path / "state" / "calendar.md"
        _write(p, f"- LWSD Parent-Teacher Conference {tomorrow} 5:00 PM\n")
        skill = SchoolCalendarSkill(artha_dir=tmp_path)
        result = skill.execute()
        assert result["status"] == "success"
        events = result["data"]["upcoming_events"]
        assert len(events) > 0

    def test_non_school_events_not_surfaced(self, tmp_path):
        from scripts.skills.school_calendar import SchoolCalendarSkill
        tomorrow = (date.today() + timedelta(days=2)).isoformat()
        p = tmp_path / "state" / "calendar.md"
        _write(p, f"- Dentist appointment {tomorrow} 2:00 PM\n- Grocery shopping\n")
        skill = SchoolCalendarSkill(artha_dir=tmp_path)
        result = skill.execute()
        # Dentist is not a school event
        events = result["data"]["upcoming_events"]
        assert not any("dentist" in e["event"].lower() for e in events)

    def test_grade_alert_in_kids_md(self, tmp_path):
        from scripts.skills.school_calendar import SchoolCalendarSkill
        p = tmp_path / "state" / "kids.md"
        _write(p, "- Missing assignment: Parth — Math HW (overdue 3 days)\n")
        skill = SchoolCalendarSkill(artha_dir=tmp_path)
        result = skill.execute()
        assert result["data"]["total_grade_alerts"] >= 1

    def test_missing_files_returns_empty(self, tmp_path):
        from scripts.skills.school_calendar import SchoolCalendarSkill
        skill = SchoolCalendarSkill(artha_dir=tmp_path)
        result = skill.execute()
        assert result["status"] == "success"
        assert result["data"]["total_events"] == 0

    def test_to_dict_has_summary(self, tmp_path):
        from scripts.skills.school_calendar import SchoolCalendarSkill
        skill = SchoolCalendarSkill(artha_dir=tmp_path)
        d = skill.to_dict()
        assert "summary" in d
        assert isinstance(d["summary"], str)
