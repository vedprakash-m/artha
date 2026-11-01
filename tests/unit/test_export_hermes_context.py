"""Tests for scripts/export_hermes_context.py — Artha × Hermes integration.

Covers all test requirements from specs/h-int.md §16.5:
  - Work open items (source_domain: employment) excluded
  - WorkIQ calendar events excluded
  - state/work/*.md files never read by exporter
  - _validate_no_work_data() fires on each work signal keyword
  - Export succeeds cleanly with only personal domain items
  - No account numbers / policy numbers / case numbers in payload
  - Calendar source tags in exporter match _CALENDAR_SOURCE_TAGS from calendar_writer.py
  - --dry-run builds payload but does not POST to HA
  - --standalone skips when .pipeline_running sentinel exists and is <1h old
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — import from scripts/ without installing the package
# ---------------------------------------------------------------------------
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from export_hermes_context import (  # noqa: E402
    _BLOCKED_CALENDAR_SOURCES,
    _PERSONAL_CALENDAR_SOURCES,
    _WORK_SIGNALS,
    _load_allowed_domains,
    _read_goals,
    _read_open_items,
    _read_upcoming_events,
    _validate_no_work_data,
    export_hermes_context,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_artha(tmp_path: Path) -> Path:
    """Minimal Artha directory tree for unit tests."""
    (tmp_path / "config").mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "tmp").mkdir()
    (tmp_path / "state" / "work").mkdir()

    # Minimal connectors.yaml
    (tmp_path / "config" / "connectors.yaml").write_text(
        "connectors:\n"
        "  homeassistant:\n"
        "    fetch:\n"
        "      ha_url: http://homeassistant.local:8123\n",
        encoding="utf-8",
    )

    # Minimal allowlist
    (tmp_path / "config" / "hermes_context_allowlist.yaml").write_text(
        "allowed_domains:\n"
        "  - finance\n"
        "  - home\n"
        "  - health\n"
        "  - kids\n"
        "  - insurance\n"
        "  - digital\n"
        "  - travel\n"
        "  - learning\n"
        "  - shopping\n"
        "  - wellness\n"
        "  - social\n"
        "  - calendar\n",
        encoding="utf-8",
    )

    return tmp_path


def _make_goals_md(goals: list[dict]) -> str:
    """Build a minimal state/goals.md string.

    Goals are placed inside the YAML frontmatter (between the two --- markers),
    matching the real state/goals.md format where goals: lives in the frontmatter
    block, not in the markdown body that follows the closing ---. This matches
    what _read_goals() expects when it parses parts[1] after splitting on ---.
    """
    lines = ["---\ndomain: goals\ngoals:\n"]
    for g in goals:
        lines.append(f"- id: {g['id']}\n")
        lines.append(f"  title: \"{g['title']}\"\n")
        lines.append(f"  status: {g['status']}\n")
        lines.append(f"  category: {g['category']}\n")
    lines.append("---\n")
    return "".join(lines)


def _make_open_items_md(items: list[dict]) -> str:
    """Build a minimal state/open_items.md string."""
    lines = ["---\ndomain: open_items\n---\n# Open Items\n\n## Active\n\n"]
    for item in items:
        lines.append(f"- id: {item['id']}\n")
        lines.append(f"  date_added: 2026-01-01\n")
        lines.append(f"  source_domain: {item['source_domain']}\n")
        lines.append(f"  description: \"{item['description']}\"\n")
        lines.append(f"  priority: {item['priority']}\n")
        lines.append(f"  status: {item['status']}\n")
        lines.append(f"  todo_id: \"\"\n")
    return "".join(lines)


def _make_calendar_md(events: list[dict], today: date) -> str:
    """Build a minimal state/calendar.md string."""
    lines = ["---\ndomain: calendar\n---\n\n## Upcoming Events\n\n"]
    for ev in events:
        date_str = ev.get("date", str(today))
        source = ev.get("source", "google_calendar")
        lines.append(f"- **{ev['title']}**  <!-- dedup:abc123 -->\n")
        lines.append(f"  - Date: {date_str}\n")
        lines.append(f"  - Source: {source}\n")
    return "".join(lines)


# ---------------------------------------------------------------------------
# §16.5 Test 1: Work open items are excluded from payload
# ---------------------------------------------------------------------------

class TestWorkDomainExclusion:
    def test_employment_items_excluded(self, tmp_artha: Path) -> None:
        """Items with source_domain: employment must never appear in payload."""
        content = _make_open_items_md([
            {"id": "OI-W01", "source_domain": "employment", "description": "Sprint review prep",
             "priority": "P1", "status": "open"},
            {"id": "OI-P01", "source_domain": "home", "description": "Fix leaky faucet",
             "priority": "P1", "status": "open"},
        ])
        (tmp_artha / "state" / "open_items.md").write_text(content, encoding="utf-8")
        allowed = _load_allowed_domains(tmp_artha)

        p1 = _read_open_items(tmp_artha / "state" / "open_items.md", "P1", allowed)

        assert any("Fix leaky faucet" in item for item in p1), "Personal home item must be included"
        assert not any("Sprint review" in item for item in p1), "Employment item must be excluded"

    def test_career_items_excluded(self, tmp_artha: Path) -> None:
        """Items with source_domain: career must never appear in payload."""
        content = _make_open_items_md([
            {"id": "OI-C01", "source_domain": "career", "description": "Apply to FAANG job",
             "priority": "P2", "status": "open"},
        ])
        (tmp_artha / "state" / "open_items.md").write_text(content, encoding="utf-8")
        allowed = _load_allowed_domains(tmp_artha)

        p2 = _read_open_items(tmp_artha / "state" / "open_items.md", "P2", allowed)

        assert not any("FAANG" in item for item in p2)

    def test_immigration_items_excluded(self, tmp_artha: Path) -> None:
        """Items with source_domain: immigration must never appear in payload."""
        content = _make_open_items_md([
            {"id": "OI-I01", "source_domain": "immigration", "description": "File I-485",
             "priority": "P1", "status": "open"},
        ])
        (tmp_artha / "state" / "open_items.md").write_text(content, encoding="utf-8")
        allowed = _load_allowed_domains(tmp_artha)

        p1 = _read_open_items(tmp_artha / "state" / "open_items.md", "P1", allowed)

        assert not any("I-485" in item for item in p1)

    def test_only_open_status_included(self, tmp_artha: Path) -> None:
        """Closed items must never appear regardless of domain."""
        content = _make_open_items_md([
            {"id": "OI-D01", "source_domain": "home", "description": "Done task",
             "priority": "P1", "status": "done"},
            {"id": "OI-O01", "source_domain": "home", "description": "Open task",
             "priority": "P1", "status": "open"},
        ])
        (tmp_artha / "state" / "open_items.md").write_text(content, encoding="utf-8")
        allowed = _load_allowed_domains(tmp_artha)

        p1 = _read_open_items(tmp_artha / "state" / "open_items.md", "P1", allowed)

        assert any("Open task" in item for item in p1)
        assert not any("Done task" in item for item in p1)


# ---------------------------------------------------------------------------
# §16.5 Test 2: WorkIQ calendar events excluded
# ---------------------------------------------------------------------------

class TestCalendarSourceExclusion:
    def test_workiq_calendar_excluded(self, tmp_artha: Path) -> None:
        """Events with source: workiq_calendar must not appear in output."""
        today = date.today()
        tomorrow = today + timedelta(days=1)
        content = _make_calendar_md([
            {"title": "WorkIQ Standup", "date": str(tomorrow), "source": "workiq_calendar"},
            {"title": "Family Dentist", "date": str(tomorrow), "source": "google_calendar"},
        ], today)
        (tmp_artha / "state" / "calendar.md").write_text(content, encoding="utf-8")

        events = _read_upcoming_events(tmp_artha, days=7)
        titles = [e["title"] for e in events]

        assert "Family Dentist" in titles
        assert "WorkIQ Standup" not in titles

    def test_msgraph_calendar_excluded(self, tmp_artha: Path) -> None:
        """Events with source: msgraph_calendar (M365) must not appear in output."""
        today = date.today()
        tomorrow = today + timedelta(days=1)
        content = _make_calendar_md([
            {"title": "1:1 with Manager", "date": str(tomorrow), "source": "msgraph_calendar"},
            {"title": "School pickup", "date": str(tomorrow), "source": "icloud_calendar"},
        ], today)
        (tmp_artha / "state" / "calendar.md").write_text(content, encoding="utf-8")

        events = _read_upcoming_events(tmp_artha, days=7)
        titles = [e["title"] for e in events]

        assert "School pickup" in titles
        assert "1:1 with Manager" not in titles

    def test_google_calendar_included(self, tmp_artha: Path) -> None:
        """Events from allowed personal sources must appear."""
        today = date.today()
        tomorrow = today + timedelta(days=1)
        for source in ("google_calendar", "gcal", "icloud_calendar", "caldav_calendar",
                       "outlook_calendar"):
            content = _make_calendar_md([
                {"title": f"Personal Event via {source}",
                 "date": str(tomorrow), "source": source},
            ], today)
            (tmp_artha / "state" / "calendar.md").write_text(content, encoding="utf-8")

            events = _read_upcoming_events(tmp_artha, days=7)
            titles = [e["title"] for e in events]
            assert any(source in t for t in titles), f"Event from {source} should be included"

    def test_past_events_excluded(self, tmp_artha: Path) -> None:
        """Past events must not appear in the output."""
        today = date.today()
        yesterday = today - timedelta(days=1)
        content = _make_calendar_md([
            {"title": "Yesterday's event", "date": str(yesterday), "source": "google_calendar"},
        ], today)
        (tmp_artha / "state" / "calendar.md").write_text(content, encoding="utf-8")

        events = _read_upcoming_events(tmp_artha, days=7)
        assert not any("Yesterday" in e["title"] for e in events)

    def test_today_flag(self, tmp_artha: Path) -> None:
        """Events on today's date must have today=True."""
        today = date.today()
        content = _make_calendar_md([
            {"title": "Today's appointment", "date": str(today), "source": "google_calendar"},
        ], today)
        (tmp_artha / "state" / "calendar.md").write_text(content, encoding="utf-8")

        events = _read_upcoming_events(tmp_artha, days=7)
        today_events = [e for e in events if e["today"]]

        assert any("Today's appointment" in e["title"] for e in today_events)

    def test_no_source_event_excluded(self, tmp_artha: Path) -> None:
        """Events with no Source tag are excluded by default (§16.3 exclude-by-default)."""
        today = date.today()
        tomorrow = today + timedelta(days=1)
        # Build calendar.md manually with a missing Source line
        content = f"# Calendar\n\n- **No-source event**\n  - Date: {tomorrow}\n\n"
        (tmp_artha / "state" / "calendar.md").write_text(content, encoding="utf-8")

        events = _read_upcoming_events(tmp_artha, days=7)
        assert not any("No-source event" in e["title"] for e in events)

    def test_source_with_annotation_included(self, tmp_artha: Path) -> None:
        """Source tags like 'manual (email exchange ...)' should match 'manual' allowlist."""
        today = date.today()
        tomorrow = today + timedelta(days=1)
        # Build manually to exercise the annotation stripping
        content = (
            f"# Calendar\n\n"
            f"- **Meeting with Prof**\n"
            f"  - Date: {tomorrow}\n"
            f"  - Source: manual (email exchange 2026-03-30)\n\n"
        )
        (tmp_artha / "state" / "calendar.md").write_text(content, encoding="utf-8")

        events = _read_upcoming_events(tmp_artha, days=7)
        assert any("Meeting with Prof" in e["title"] for e in events)


# ---------------------------------------------------------------------------
# goals_parked attribute test
# ---------------------------------------------------------------------------

class TestGoalsParked:
    def test_parked_goals_read_separately(self, tmp_artha: Path) -> None:
        """_read_goals with status_filter='parked' returns only parked goals."""
        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([
                {"id": "G-001", "title": "Run 5K", "status": "active", "category": "fitness"},
                {"id": "G-002", "title": "AI-103 cert", "status": "parked", "category": "learning"},
                {"id": "G-003", "title": "Read 12 books", "status": "parked", "category": "learning"},
            ]),
            encoding="utf-8",
        )
        active = _read_goals(tmp_artha / "state" / "goals.md", "active")
        parked = _read_goals(tmp_artha / "state" / "goals.md", "parked")
        assert active == ["Run 5K"]
        assert "AI-103 cert" in parked
        assert "Read 12 books" in parked
        assert "Run 5K" not in parked

    def test_goals_parked_in_payload(self, tmp_artha: Path) -> None:
        """export_hermes_context must include goals_parked in the HA payload."""
        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([
                {"id": "G-001", "title": "Run 5K", "status": "active", "category": "fitness"},
                {"id": "G-002", "title": "AI-103 cert", "status": "parked", "category": "learning"},
            ]),
            encoding="utf-8",
        )
        (tmp_artha / "state" / "open_items.md").write_text(
            _make_open_items_md([]), encoding="utf-8"
        )

        posted_payload: dict = {}

        def _mock_post(url, *, headers, json, timeout):
            posted_payload.update(json)
            return SimpleNamespace(status_code=201)

        with (
            patch("platform.system", return_value="Darwin"),
            patch("keyring.get_password", return_value="fake-token"),
            patch("export_hermes_context._is_reachable", return_value=True),
            patch("requests.post", side_effect=_mock_post),
        ):
            export_hermes_context(tmp_artha)

        assert "goals_parked" in posted_payload.get("attributes", {}), \
            "goals_parked missing from HA payload"
        assert "AI-103 cert" in posted_payload["attributes"]["goals_parked"]


# ---------------------------------------------------------------------------
# §16.5 Test 3: state/work/*.md files are never read
# ---------------------------------------------------------------------------

class TestWorkDirectoryNeverRead:
    def test_work_state_files_not_accessed(self, tmp_artha: Path) -> None:
        """The exporter must not open any file under state/work/."""
        # Write a work state file with sensitive content
        (tmp_artha / "state" / "work").mkdir(exist_ok=True)
        (tmp_artha / "state" / "work" / "projects.md").write_text(
            "Sprint: CONFIDENTIAL_PROJECT_X\nICM: 12345678\n",
            encoding="utf-8",
        )

        # Write benign personal state files
        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([
                {"id": "G-001", "title": "Run 5K", "status": "active", "category": "fitness"}
            ]),
            encoding="utf-8",
        )
        (tmp_artha / "state" / "open_items.md").write_text(
            _make_open_items_md([]), encoding="utf-8"
        )

        # Track all file opens
        opened_paths: list[str] = []
        _real_open = Path.open

        def _tracking_open(self: Path, *args, **kwargs):  # type: ignore[override]
            opened_paths.append(str(self))
            return _real_open(self, *args, **kwargs)

        with patch.object(Path, "open", _tracking_open):
            _read_goals(tmp_artha / "state" / "goals.md", "active")
            allowed = _load_allowed_domains(tmp_artha)
            _read_open_items(tmp_artha / "state" / "open_items.md", "P1", allowed)
            _read_upcoming_events(tmp_artha, days=7)

        work_accesses = [p for p in opened_paths if "/state/work/" in p]
        assert work_accesses == [], f"Work state files accessed: {work_accesses}"


# ---------------------------------------------------------------------------
# §16.5 Test 4: _validate_no_work_data() fires on each work signal
# ---------------------------------------------------------------------------

class TestWorkSignalScan:
    @pytest.mark.parametrize("signal,sample_text", [
        ("workiq", "workiq meeting notes"),
        (r"\bado\b", "see ado item 12345"),
        (r"\bsprint\b", "sprint planning done"),
        ("incident", "incident response"),
        (r"\bicm\b", "icm ticket 789"),
        (r"teams\.microsoft", "teams.microsoft.com link"),
        ("work_os", "work_os flag set"),
        (r"\bemployment\b", "employment status"),
        ("performance", "performance review"),
        ("calibration", "calibration cycle"),
    ])
    def test_signal_detected(self, signal: str, sample_text: str) -> None:
        """Each work signal must be detected in a payload containing it."""
        payload = {"attributes": {"goals_active": [sample_text]}}
        matches = _validate_no_work_data(payload)
        assert matches, f"Signal '{signal}' not detected in '{sample_text}'"

    def test_clean_payload_returns_empty(self) -> None:
        """A clean personal payload must return an empty list."""
        payload = {
            "attributes": {
                "goals_active": ["Run 5K", "Finish online course"],
                "open_items_p1": ["Fix leaky faucet"],
                "today_events": ["Dentist 9am"],
            }
        }
        assert _validate_no_work_data(payload) == []

    def test_does_not_raise(self) -> None:
        """_validate_no_work_data must never raise, even on unusual input."""
        _validate_no_work_data({})
        _validate_no_work_data({"attributes": None})  # type: ignore[arg-type]
        _validate_no_work_data({"state": 42})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# §16.5 Test 5: Export succeeds cleanly with only personal domain items
# ---------------------------------------------------------------------------

class TestCleanExportSuccess:
    def test_personal_only_export_succeeds(self, tmp_artha: Path) -> None:
        """Full export flow with personal data returns True and sends correct payload."""
        today = date.today()
        tomorrow = today + timedelta(days=1)

        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([
                {"id": "G-001", "title": "Run 5K", "status": "active", "category": "fitness"},
                {"id": "G-002", "title": "Read 12 books", "status": "parked", "category": "learning"},
            ]),
            encoding="utf-8",
        )
        (tmp_artha / "state" / "open_items.md").write_text(
            _make_open_items_md([
                {"id": "OI-001", "source_domain": "home", "description": "Fix fence",
                 "priority": "P1", "status": "open"},
                {"id": "OI-002", "source_domain": "health", "description": "Schedule dentist",
                 "priority": "P2", "status": "open"},
            ]),
            encoding="utf-8",
        )
        (tmp_artha / "state" / "calendar.md").write_text(
            _make_calendar_md([
                {"title": "Dentist", "date": str(tomorrow), "source": "google_calendar"},
            ], today),
            encoding="utf-8",
        )

        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value="fake-ha-token"), \
             patch("export_hermes_context._is_reachable", return_value=True), \
             patch("requests.post", return_value=mock_response) as mock_post:
            result = export_hermes_context(tmp_artha)

        assert result is True
        assert mock_post.called

        call_kwargs = mock_post.call_args
        sent_payload = call_kwargs.kwargs.get("json", call_kwargs.args[0] if call_kwargs.args else {})
        attrs = sent_payload.get("attributes", {})

        assert "Run 5K" in attrs.get("goals_active", [])
        assert "Read 12 books" not in attrs.get("goals_active", [])  # parked excluded from active
        assert "Read 12 books" in attrs.get("goals_parked", [])       # but present in parked (§6.1)
        assert "goals_parked" in attrs, "goals_parked key must be present in HA payload"
        assert any("Fix fence" in item for item in attrs.get("open_items_p1", []))
        assert any("Schedule dentist" in item for item in attrs.get("open_items_p2", []))
        assert attrs.get("schema_version") == "1.0"
        assert "generated" in attrs

    def test_missing_state_files_returns_false_or_skips(self, tmp_artha: Path) -> None:
        """Export must not crash if state files are absent."""
        # No state files created — goals.md, open_items.md, calendar.md all absent.
        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value="fake-ha-token"), \
             patch("export_hermes_context._is_reachable", return_value=True), \
             patch("requests.post", return_value=mock_response):
            result = export_hermes_context(tmp_artha)

        # Must complete without raising (may succeed with empty payload)
        assert isinstance(result, bool)

    def test_non_darwin_returns_false(self, tmp_artha: Path) -> None:
        """Export must skip immediately on non-macOS platforms."""
        with patch("platform.system", return_value="Windows"):
            result = export_hermes_context(tmp_artha)
        assert result is False

    def test_no_token_returns_false(self, tmp_artha: Path) -> None:
        """Export must skip if HA token is not in keyring."""
        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value=None):
            result = export_hermes_context(tmp_artha)
        assert result is False

    def test_lan_unreachable_returns_false(self, tmp_artha: Path) -> None:
        """Export must skip without error if HA is not reachable."""
        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value="fake-token"), \
             patch("export_hermes_context._is_reachable", return_value=False):
            result = export_hermes_context(tmp_artha)
        assert result is False


# ---------------------------------------------------------------------------
# §16.5 Test 6: No sensitive data in payload (account numbers, case numbers)
# ---------------------------------------------------------------------------

class TestSensitiveDataAbsent:
    _SENSITIVE_PATTERNS = [
        r"\b\d{9,}\b",            # Long numeric IDs (account numbers, case numbers)
        r"[A-Z]{3}\d{7,}",        # Pattern like ABC1234567 (immigration case numbers)
        r"\$\d+",                 # Dollar amounts
        r"account.*\d{4,}",       # Account + digits
        r"policy.*\d{4,}",        # Policy + digits
    ]

    def test_no_account_numbers_in_payload(self, tmp_artha: Path) -> None:
        """Payload must not contain account numbers or policy numbers."""
        import re as _re

        (tmp_artha / "state" / "open_items.md").write_text(
            _make_open_items_md([
                # Domain not in allowlist — should be excluded
                {"id": "OI-FIN", "source_domain": "finance_detailed",
                 "description": "HDFC account 123456789 renewal",
                 "priority": "P1", "status": "open"},
                # Domain in allowlist — safe description
                {"id": "OI-HOME", "source_domain": "home",
                 "description": "Fix fence gate",
                 "priority": "P1", "status": "open"},
            ]),
            encoding="utf-8",
        )
        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([]), encoding="utf-8"
        )

        mock_response = MagicMock()
        mock_response.status_code = 201

        captured_payload: list[dict] = []

        def _capture_post(url, *, headers, json, timeout):  # noqa: ARG001
            captured_payload.append(json)
            return mock_response

        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value="tok"), \
             patch("export_hermes_context._is_reachable", return_value=True), \
             patch("requests.post", side_effect=_capture_post):
            export_hermes_context(tmp_artha)

        assert captured_payload, "POST should have been called"
        payload_str = json.dumps(captured_payload[0])

        assert "123456789" not in payload_str, "Account number leaked into payload"
        assert "HDFC" not in payload_str, "Financial institution name leaked"


# ---------------------------------------------------------------------------
# §16.5 Test 7: Calendar source tags match calendar_writer.py
# ---------------------------------------------------------------------------

class TestCalendarSourceTagSync:
    def test_blocked_sources_are_subset_of_calendar_source_tags(self) -> None:
        """Blocked calendar sources must all be in calendar_writer._CALENDAR_SOURCE_TAGS."""
        try:
            from calendar_writer import _CALENDAR_SOURCE_TAGS  # type: ignore[import]
        except ImportError:
            pytest.skip("calendar_writer not importable in this environment")

        missing = _BLOCKED_CALENDAR_SOURCES - _CALENDAR_SOURCE_TAGS
        assert not missing, (
            f"Blocked sources {missing} are not in calendar_writer._CALENDAR_SOURCE_TAGS. "
            "Source tag drift detected — update export_hermes_context.py."
        )

    def test_personal_sources_not_in_blocked(self) -> None:
        """Personal calendar sources must not appear in the blocked set."""
        overlap = _PERSONAL_CALENDAR_SOURCES & _BLOCKED_CALENDAR_SOURCES
        assert not overlap, f"Personal sources incorrectly in blocked set: {overlap}"


# ---------------------------------------------------------------------------
# §16.5 Test 8: --dry-run builds payload but does not POST
# ---------------------------------------------------------------------------

class TestDryRunFlag:
    def test_dry_run_no_post(self, tmp_artha: Path) -> None:
        """In dry-run mode, export_hermes_context must not call requests.post."""
        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([
                {"id": "G-001", "title": "Run 5K", "status": "active", "category": "fitness"}
            ]),
            encoding="utf-8",
        )
        (tmp_artha / "state" / "open_items.md").write_text(
            _make_open_items_md([]), encoding="utf-8"
        )

        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value="tok"), \
             patch("export_hermes_context._is_reachable", return_value=True), \
             patch("requests.post") as mock_post:
            result = export_hermes_context(tmp_artha, dry_run=True)

        assert result is True, "dry-run should return True (success)"
        mock_post.assert_not_called()

    def test_dry_run_builds_valid_payload(self, tmp_artha: Path, caplog) -> None:
        """dry-run must log the payload (verifiable via logging output)."""
        import logging
        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([
                {"id": "G-001", "title": "Summit Mailbox Peak", "status": "active", "category": "fitness"}
            ]),
            encoding="utf-8",
        )
        (tmp_artha / "state" / "open_items.md").write_text(
            _make_open_items_md([]), encoding="utf-8"
        )

        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value="tok"), \
             patch("export_hermes_context._is_reachable", return_value=True), \
             patch("requests.post"), \
             caplog.at_level(logging.INFO, logger="export_hermes_context"):
            export_hermes_context(tmp_artha, dry_run=True)

        assert any("dry-run" in record.message for record in caplog.records)
        assert any("Summit Mailbox Peak" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# §16.5 Test 9: --standalone skips when pipeline sentinel exists and is <1h old
# ---------------------------------------------------------------------------

class TestStandaloneMode:
    def test_standalone_skips_when_sentinel_recent(self, tmp_artha: Path) -> None:
        """Standalone mode must skip export if .pipeline_running is <1h old."""
        sentinel = tmp_artha / "tmp" / ".pipeline_running"
        sentinel.touch()
        # Sentinel is brand new (age ≈ 0 seconds < 3600)

        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value="tok"), \
             patch("requests.post") as mock_post:
            result = export_hermes_context(tmp_artha, standalone=True)

        assert result is False, "Should skip when sentinel is recent"
        mock_post.assert_not_called()

    def test_standalone_proceeds_when_sentinel_old(self, tmp_artha: Path) -> None:
        """Standalone mode must proceed if .pipeline_running is >1h old."""
        sentinel = tmp_artha / "tmp" / ".pipeline_running"
        sentinel.touch()

        # Age the sentinel to 2 hours ago
        old_mtime = time.time() - 7201
        import os
        os.utime(sentinel, (old_mtime, old_mtime))

        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([]), encoding="utf-8"
        )
        (tmp_artha / "state" / "open_items.md").write_text(
            _make_open_items_md([]), encoding="utf-8"
        )

        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value="tok"), \
             patch("export_hermes_context._is_reachable", return_value=True), \
             patch("requests.post", return_value=mock_response):
            result = export_hermes_context(tmp_artha, standalone=True)

        # Should attempt export (old sentinel is ignored)
        assert result is True

    def test_standalone_proceeds_when_no_sentinel(self, tmp_artha: Path) -> None:
        """Standalone mode must proceed normally if no sentinel file exists."""
        assert not (tmp_artha / "tmp" / ".pipeline_running").exists()

        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([]), encoding="utf-8"
        )
        (tmp_artha / "state" / "open_items.md").write_text(
            _make_open_items_md([]), encoding="utf-8"
        )

        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value="tok"), \
             patch("export_hermes_context._is_reachable", return_value=True), \
             patch("requests.post", return_value=mock_response):
            result = export_hermes_context(tmp_artha, standalone=True)

        assert result is True

    def test_standalone_skips_audit_write(self, tmp_artha: Path) -> None:
        """Standalone mode must not write to audit.md."""
        audit_path = tmp_artha / "state" / "audit.md"
        audit_path.write_text("", encoding="utf-8")

        # Trigger work signal to force audit write attempt
        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([
                {"id": "G-001", "title": "sprint training run", "status": "active",
                 "category": "fitness"}
            ]),
            encoding="utf-8",
        )
        (tmp_artha / "state" / "open_items.md").write_text(
            _make_open_items_md([]), encoding="utf-8"
        )

        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value="tok"), \
             patch("export_hermes_context._is_reachable", return_value=True), \
             patch("requests.post", return_value=mock_response):
            export_hermes_context(tmp_artha, standalone=True)

        # audit.md must remain empty in standalone mode
        assert audit_path.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# Additional: payload size guard
# ---------------------------------------------------------------------------

class TestPayloadSizeGuard:
    def test_oversized_payload_truncated(self, tmp_artha: Path) -> None:
        """week_events must be truncated when payload exceeds 4096 bytes."""
        today = date.today()
        # Create many week events to force oversized payload
        events = [
            {
                "title": "A" * 100 + f" Event {i}",
                "date": str(today + timedelta(days=i + 1)),
                "source": "google_calendar",
            }
            for i in range(7)
        ]
        (tmp_artha / "state" / "calendar.md").write_text(
            _make_calendar_md(events, today), encoding="utf-8"
        )
        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([]), encoding="utf-8"
        )
        (tmp_artha / "state" / "open_items.md").write_text(
            _make_open_items_md([]), encoding="utf-8"
        )

        captured: list[dict] = []
        mock_response = MagicMock()
        mock_response.status_code = 201

        def _capture(url, *, headers, json, timeout):  # noqa: ARG001
            captured.append(json)
            return mock_response

        with patch("platform.system", return_value="Darwin"), \
             patch("keyring.get_password", return_value="tok"), \
             patch("export_hermes_context._is_reachable", return_value=True), \
             patch("requests.post", side_effect=_capture):
            export_hermes_context(tmp_artha)

        if captured:
            payload_size = len(json.dumps(captured[0]))
            assert payload_size <= 4096, f"Payload too large: {payload_size} bytes"


# ---------------------------------------------------------------------------
# §6.1: week_events hard-capped at 5 items
# ---------------------------------------------------------------------------

class TestWeekEventsCap:
    def test_week_events_capped_at_five(self, tmp_artha: Path) -> None:
        """week_events must never exceed 5 items regardless of calendar entries (§6.1)."""
        today = date.today()
        # 7 non-today personal events — only 5 should appear in week_events
        events = [
            {
                "title": f"Event {i}",
                "date": str(today + timedelta(days=i + 1)),
                "source": "google_calendar",
            }
            for i in range(7)
        ]
        (tmp_artha / "state" / "calendar.md").write_text(
            _make_calendar_md(events, today), encoding="utf-8"
        )
        (tmp_artha / "state" / "goals.md").write_text(
            _make_goals_md([]), encoding="utf-8"
        )
        (tmp_artha / "state" / "open_items.md").write_text(
            _make_open_items_md([]), encoding="utf-8"
        )

        captured: list[dict] = []
        mock_response = MagicMock()
        mock_response.status_code = 201

        def _capture(url, *, headers, json, timeout):  # noqa: ARG001
            captured.append(json)
            return mock_response

        with (
            patch("platform.system", return_value="Darwin"),
            patch("keyring.get_password", return_value="tok"),
            patch("export_hermes_context._is_reachable", return_value=True),
            patch("requests.post", side_effect=_capture),
        ):
            export_hermes_context(tmp_artha)

        assert captured, "POST must have been called"
        week_events = captured[0]["attributes"]["week_events"]
        assert len(week_events) <= 5, (
            f"week_events has {len(week_events)} items — spec §6.1 mandates max 5"
        )
