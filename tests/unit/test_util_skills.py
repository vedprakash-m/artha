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


# ─────────────────────────────────────────────────────────────────────────────
# TestWhatsAppLastContact (U-9.6)
# ─────────────────────────────────────────────────────────────────────────────

class TestWhatsAppLastContact:
    """
    Tests for WhatsAppLastContact skill.

    All tests use an in-memory SQLite DB that mimics the ChatStorage.sqlite
    schema so no real WhatsApp installation is needed.
    """

    # ── Fixtures ─────────────────────────────────────────────────────────────

    def _make_wa_db(self, tmp_path: Path, rows: list[dict]) -> Path:
        """Create a minimal fake ChatStorage.sqlite with the given chat rows."""
        import sqlite3
        db_path = tmp_path / "wa_test.sqlite"
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        # Minimal schema matching what the skill queries
        cur.executescript("""
            CREATE TABLE ZWACHATSESSION (
                Z_PK INTEGER PRIMARY KEY,
                ZPARTNERNAME TEXT,
                ZCONTACTJID TEXT,
                ZSESSIONTYPE INTEGER,
                ZLASTMESSAGEDATE REAL
            );
            CREATE TABLE ZWAMESSAGE (
                Z_PK INTEGER PRIMARY KEY,
                ZCHATSESSION INTEGER,
                ZTEXT TEXT,
                ZISFROMME INTEGER,
                ZMESSAGEDATE REAL
            );
            CREATE TABLE ZWAGROUPMEMBER (
                Z_PK INTEGER PRIMARY KEY,
                ZCHATSESSION INTEGER,
                ZMEMBERJID TEXT
            );
        """)
        for i, row in enumerate(rows, 1):
            cur.execute(
                "INSERT INTO ZWACHATSESSION VALUES (?,?,?,?,?)",
                (i, row["name"], row.get("jid", f"{i}@s.whatsapp.net"),
                 row.get("type", 0), row.get("last_ts", 0.0))
            )
            for j, msg in enumerate(row.get("messages", []), 1):
                cur.execute(
                    "INSERT INTO ZWAMESSAGE VALUES (?,?,?,?,?)",
                    (i * 1000 + j, i, msg.get("text", ""), msg.get("from_me", 0), msg.get("ts", 0.0))
                )
        con.commit()
        con.close()
        return db_path

    def _make_contacts(self, tmp_path: Path, members: list[str], cadence: str = "monthly") -> Path:
        content = f"""\
---
schema_version: "1.1"
circles:
  us_friends:
    label: "US Friends"
    members: {members!r}
    cadence: "{cadence}"
    nudge: true
---
# Contacts
| Name | Team | Phone | Email | Location | Notes |
|------|------|-------|-------|----------|-------|
"""
        p = tmp_path / "state" / "contacts.md"
        _write(p, content)
        return tmp_path

    # ── Apple epoch helpers ───────────────────────────────────────────────────

    @staticmethod
    def _days_ago_ts(n: int) -> float:
        """Return an Apple CoreData timestamp for n days ago."""
        from datetime import timezone
        d = date.today() - timedelta(days=n)
        unix_ts = d.toordinal() - date(1970, 1, 1).toordinal()
        return float(unix_ts * 86400 - 978307200)

    # ── Tests ─────────────────────────────────────────────────────────────────

    def test_db_unavailable_raises_file_not_found(self, tmp_path, monkeypatch):
        """Skill raises FileNotFoundError when WA DB is absent (non-macOS / no app)."""
        from scripts.skills.whatsapp_last_contact import WhatsAppLastContact, _WA_DB_SOURCE
        monkeypatch.setattr(
            "scripts.skills.whatsapp_last_contact._WA_DB_SOURCE",
            tmp_path / "nonexistent.sqlite",
        )
        skill = WhatsAppLastContact(artha_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            skill.pull()

    def test_phone_normalisation(self):
        """_normalize_phone strips JID suffixes correctly."""
        from scripts.skills.whatsapp_last_contact import _normalize_phone
        assert _normalize_phone("14251234567@s.whatsapp.net") == "14251234567"
        assert _normalize_phone("148863767855175@lid") == "148863767855175"
        assert _normalize_phone("918800123456@s.whatsapp.net") == "918800123456"

    def test_apple_ts_conversion(self):
        """_ts_to_date converts Apple CoreData timestamp to correct date."""
        from scripts.skills.whatsapp_last_contact import _ts_to_date
        # 2024-01-01 = 86400 * (date(2024,1,1) - date(2001,1,1)).days
        d = date(2024, 1, 1)
        ts = (d.toordinal() - date(2001, 1, 1).toordinal()) * 86400.0
        assert _ts_to_date(ts) == d

    def test_stale_contact_surfaced(self, tmp_path, monkeypatch):
        """Contact with last WA > cadence threshold appears in nudges."""
        from scripts.skills.whatsapp_last_contact import WhatsAppLastContact, _WA_DB_COPY

        db = self._make_wa_db(tmp_path, [
            {"name": "Alice S", "jid": "15550110000@s.whatsapp.net",
             "last_ts": self._days_ago_ts(45)},
        ])
        monkeypatch.setattr("scripts.skills.whatsapp_last_contact._WA_DB_SOURCE", db)
        monkeypatch.setattr("scripts.skills.whatsapp_last_contact._WA_DB_COPY",
                            tmp_path / "copy.sqlite")

        self._make_contacts(tmp_path, ["Alice S"], cadence="monthly")
        skill = WhatsAppLastContact(artha_dir=tmp_path)
        result = skill.execute()

        assert result["status"] == "success"
        assert result["data"]["nudge_count"] >= 1

    def test_fresh_contact_not_in_nudges(self, tmp_path, monkeypatch):
        """Contact within cadence window does NOT appear in nudges."""
        from scripts.skills.whatsapp_last_contact import WhatsAppLastContact

        db = self._make_wa_db(tmp_path, [
            {"name": "Bob F", "jid": "15550220000@s.whatsapp.net",
             "last_ts": self._days_ago_ts(5)},
        ])
        monkeypatch.setattr("scripts.skills.whatsapp_last_contact._WA_DB_SOURCE", db)
        monkeypatch.setattr("scripts.skills.whatsapp_last_contact._WA_DB_COPY",
                            tmp_path / "copy.sqlite")

        self._make_contacts(tmp_path, ["Bob F"], cadence="monthly")
        skill = WhatsAppLastContact(artha_dir=tmp_path)
        result = skill.execute()

        nudges = result["data"]["nudges"]
        assert not any(n["contact"] == "Bob F" for n in nudges)

    def test_lid_contact_does_not_crash(self, tmp_path, monkeypatch):
        """LID-type contacts (privacy mode) are handled gracefully — no exception."""
        from scripts.skills.whatsapp_last_contact import WhatsAppLastContact

        db = self._make_wa_db(tmp_path, [
            {"name": "Mukul M", "jid": "148863767855175@lid",
             "last_ts": self._days_ago_ts(20)},
        ])
        monkeypatch.setattr("scripts.skills.whatsapp_last_contact._WA_DB_SOURCE", db)
        monkeypatch.setattr("scripts.skills.whatsapp_last_contact._WA_DB_COPY",
                            tmp_path / "copy.sqlite")

        self._make_contacts(tmp_path, ["Mukul M"], cadence="biweekly")
        skill = WhatsAppLastContact(artha_dir=tmp_path)
        result = skill.execute()  # must not raise
        assert result["status"] == "success"

    def test_birthday_wish_inferred(self, tmp_path, monkeypatch):
        """Birthday wishes I sent are returned in inferred_dobs list."""
        from scripts.skills.whatsapp_last_contact import WhatsAppLastContact

        # ts for Apr 17 of current year (or last year)
        today = date.today()
        wish_date = today.replace(month=4, day=17) if today.month > 4 or (today.month == 4 and today.day >= 17) \
            else today.replace(year=today.year - 1, month=4, day=17)
        wish_ts = (wish_date.toordinal() - date(2001, 1, 1).toordinal()) * 86400.0

        db = self._make_wa_db(tmp_path, [
            {
                "name": "Vishnu T",
                "jid": "14259613223@s.whatsapp.net",
                "last_ts": wish_ts,
                "messages": [{"text": "Happy Birthday Vishnu! 🎂", "from_me": 1, "ts": wish_ts}],
            }
        ])
        monkeypatch.setattr("scripts.skills.whatsapp_last_contact._WA_DB_SOURCE", db)
        monkeypatch.setattr("scripts.skills.whatsapp_last_contact._WA_DB_COPY",
                            tmp_path / "copy.sqlite")

        self._make_contacts(tmp_path, ["Vishnu T"])
        skill = WhatsAppLastContact(artha_dir=tmp_path)
        result = skill.execute()

        dobs = result["data"]["inferred_dobs"]
        assert any(d["name"] == "Vishnu T" and d["probable_dob_month"] == 4 for d in dobs)

    def test_empty_contacts_no_crash(self, tmp_path, monkeypatch):
        """Skill runs cleanly even when contacts.md has no circles."""
        from scripts.skills.whatsapp_last_contact import WhatsAppLastContact

        db = self._make_wa_db(tmp_path, [])
        monkeypatch.setattr("scripts.skills.whatsapp_last_contact._WA_DB_SOURCE", db)
        monkeypatch.setattr("scripts.skills.whatsapp_last_contact._WA_DB_COPY",
                            tmp_path / "copy.sqlite")

        # Write a minimal contacts.md without circles
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)
        (tmp_path / "state" / "contacts.md").write_text("# Contacts\n", encoding="utf-8")

        skill = WhatsAppLastContact(artha_dir=tmp_path)
        result = skill.execute()
        assert result["status"] == "success"
        assert result["data"]["nudge_count"] == 0
