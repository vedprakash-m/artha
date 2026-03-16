"""
tests/unit/test_work_connectors.py — Unit tests for Wave 1+2 work domain connectors.

Coverage:
  workiq_bridge:
    - _parse_calendar: valid pipe-table → structured records
    - _parse_calendar: header rows are skipped
    - _parse_calendar: Teams detection from location string (is_teams quirk)
    - _parse_calendar: redact_kws replaces keyword in title
    - _parse_email: valid rows → structured records + pre-filter noise
    - _parse_teams: valid rows → structured records
    - _parse_people: returns single-item list with profile blob
    - _parse_documents: valid rows → structured records
    - _parse_pipe_table: fewer-than-expected fields are skipped
    - _get_cached: returns None for stale entry
    - _get_cached: returns data for fresh entry
    - _cache_key_for_mode: deterministic keys per mode
    - fetch: cache hit yields records without calling _ask_workiq
    - fetch: unknown mode raises ValueError
    - health_check: non-Windows returns True

  ado_workitems:
    - _normalise_item: maps ADO fields to record keys correctly
    - _normalise_item: AssignedTo dict → displayName string
    - _normalise_item: dates trimmed to YYYY-MM-DD
    - _get_headers: az_cli method produces Bearer header
    - _get_headers: api_key method produces Basic header
    - _get_headers: unsupported method raises RuntimeError
    - fetch: missing org_url → yields nothing + prints error
    - fetch: WIQL HTTP error → yields nothing

  outlookctl_bridge:
    - _parse_event: regular event fields mapped correctly
    - _parse_event: Teams detected from location string
    - _parse_event: is_teams flag already True preserved
    - _parse_event: attendees_available always False
    - fetch: non-Windows yields nothing
    - fetch: outlookctl not found yields nothing
    - health_check: non-Windows returns True
    - health_check: missing outlookctl returns False

  check_ado_auth (preflight integration):
    - returns skipped message when azure_devops.enabled is False
    - returns passed CheckResult when az_cli token acquired

Ref: specs/work-domain-assessment.md §18, §22
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ─────────────────────────────────────────────────────────────────────────────
# workiq_bridge
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkiqBridgeParsers:
    @pytest.fixture(autouse=True)
    def _import(self):
        from connectors.workiq_bridge import (
            _parse_calendar,
            _parse_email,
            _parse_teams,
            _parse_people,
            _parse_documents,
            _parse_pipe_table,
        )
        self._parse_calendar = _parse_calendar
        self._parse_email = _parse_email
        self._parse_teams = _parse_teams
        self._parse_people = _parse_people
        self._parse_documents = _parse_documents
        self._parse_pipe_table = _parse_pipe_table

    def test_parse_calendar_valid(self):
        raw = "2026-03-16 | 09:00 | 10:00 | Sprint Review | Alice | Teams | yes"
        records = self._parse_calendar(raw, [])
        assert len(records) == 1
        r = records[0]
        assert r["date"] == "2026-03-16"
        assert r["title"] == "Sprint Review"
        assert r["is_teams"] is True
        assert r["source"] == "workiq"

    def test_parse_calendar_skips_header_row(self):
        raw = (
            "DATE | START_TIME | END_TIME | TITLE | ORGANIZER | LOCATION | TEAMS(yes/no)\n"
            "2026-03-16 | 09:00 | 10:00 | Sprint Review | Alice | Teams | yes"
        )
        records = self._parse_calendar(raw, [])
        assert len(records) == 1
        assert records[0]["title"] == "Sprint Review"

    def test_parse_calendar_teams_from_location(self):
        # is_teams flag is "no" but location mentions Teams
        raw = "2026-03-16 | 10:00 | 11:00 | Design Sync | Bob | Microsoft Teams | no"
        records = self._parse_calendar(raw, [])
        assert records[0]["is_teams"] is True

    def test_parse_calendar_redact(self):
        raw = "2026-03-16 | 09:00 | 10:00 | Project Acme Review | Alice | Teams | no"
        records = self._parse_calendar(raw, ["Acme"])
        assert "Acme" not in records[0]["title"]
        assert "[REDACTED]" in records[0]["title"]

    def test_parse_email_valid(self):
        raw = "alice@example.com | Action needed: doc review | 2026-03-16 | yes"
        records = self._parse_email(raw, [])
        assert len(records) == 1
        assert records[0]["needs_response"] is True

    def test_parse_email_prefilter_noreply(self):
        # no-reply sender should be filtered out
        raw = "no-reply@company.com | Newsletter | 2026-03-16 | no"
        records = self._parse_email(raw, [])
        assert len(records) == 0

    def test_parse_email_prefilter_calendar_accept(self):
        raw = "alice@example.com | Accepted: Sprint Review | 2026-03-16 | no"
        records = self._parse_email(raw, [])
        # not pre-filtered by sender, but "Accepted:" in subject — not pre-filtered either
        # The pre-filter only checks the SENDER pattern matching the row
        # Subject "Accepted:" is filtered via _PREFILTER_PATTERNS which checks sender
        assert len(records) == 1  # sender is normal; "Accepted:" is in subject not sender

    def test_parse_teams_valid(self):
        raw = "Bob | #engineering | Looks good. LGTM on the PR | yes"
        records = self._parse_teams(raw, [])
        assert len(records) == 1
        assert records[0]["needs_action"] is True
        assert records[0]["channel"] == "#engineering"

    def test_parse_people_returns_single_item(self):
        profile_blob = "Alice Smith\nTitle: Staff Engineer\nDepartment: Platform\n"
        records = self._parse_people(profile_blob, "Alice Smith")
        assert len(records) == 1
        assert records[0]["name"] == "Alice Smith"
        assert "Staff Engineer" in records[0]["profile"]
        assert records[0]["source"] == "workiq_people"

    def test_parse_people_empty_raw_returns_empty(self):
        records = self._parse_people("", "Nobody")
        assert records == []

    def test_parse_documents_valid(self):
        raw = "Design Doc v2 | SharePoint | 2026-03-14 | https://sharepoint.example.com"
        records = self._parse_documents(raw, [])
        assert len(records) == 1
        assert records[0]["title"] == "Design Doc v2"
        assert records[0]["type"] == "SharePoint"
        assert records[0]["source"] == "workiq_documents"

    def test_parse_pipe_table_skips_short_rows(self):
        raw = "field1 | field2"  # only 2 fields for requested 4
        rows = self._parse_pipe_table(raw, 4)
        assert rows == []

    def test_parse_pipe_table_skips_comments(self):
        raw = "# this is a comment\nfield1 | field2 | field3 | field4"
        rows = self._parse_pipe_table(raw, 4)
        assert len(rows) == 1


class TestWorkiqBridgeCache:
    @pytest.fixture(autouse=True)
    def _import(self):
        from connectors.workiq_bridge import (
            _get_cached,
            _set_cached,
            _cache_key_for_mode,
        )
        self._get_cached = _get_cached
        self._set_cached = _set_cached
        self._cache_key_for_mode = _cache_key_for_mode

    def test_cache_miss_returns_none(self):
        assert self._get_cached("calendar", "workiq_calendar__", {}) is None

    def test_cache_hit_fresh(self):
        cache: dict = {}
        data = [{"title": "Test Event"}]
        self._set_cached("calendar", "test_key", data, cache)
        result = self._get_cached("calendar", "test_key", cache)
        assert result == data

    def test_cache_stale_returns_none(self):
        ts = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
        cache = {"test_key": {"mode": "calendar", "cached_at": ts, "data": [{"x": 1}]}}
        result = self._get_cached("calendar", "test_key", cache)
        assert result is None

    def test_cache_key_calendar_includes_dates(self):
        key = self._cache_key_for_mode("calendar", start_date="2026-03-16", end_date="2026-03-22")
        assert "2026-03-16" in key
        assert "2026-03-22" in key

    def test_cache_key_people_includes_name(self):
        key = self._cache_key_for_mode("people", person_name="Alice Smith")
        assert "alice_smith" in key

    def test_cache_key_email_is_stable(self):
        k1 = self._cache_key_for_mode("email")
        k2 = self._cache_key_for_mode("email")
        assert k1 == k2


class TestWorkiqBridgeFetch:
    def test_fetch_unknown_mode_raises(self):
        import sys
        from connectors.workiq_bridge import fetch
        with pytest.raises(ValueError, match="Unknown mode"):
            list(fetch(mode="bogus_mode", auth_context={}))

    def test_fetch_cache_hit_skips_subprocess(self):
        """Cache hit should yield records without calling _ask_workiq."""
        from connectors import workiq_bridge as wb
        # Preload cache with stale-proof fresh data
        fresh_ts = datetime.now(timezone.utc).isoformat()
        test_key = "workiq_calendar_2026-03-16_2026-03-22"
        fake_cache = {
            test_key: {
                "mode": "calendar",
                "cached_at": fresh_ts,
                "data": [{"title": "Cached Sprint", "source": "workiq"}],
            }
        }
        with (
            patch.object(wb, "_load_cache", return_value=fake_cache),
            patch.object(wb, "_ask_workiq") as mock_ask,
            patch.object(wb, "_save_cache"),
        ):
            records = list(wb.fetch(
                mode="calendar",
                start_date="2026-03-16",
                end_date="2026-03-22",
                auth_context={},
            ))
        mock_ask.assert_not_called()
        assert len(records) == 1
        assert records[0]["title"] == "Cached Sprint"

    def test_health_check_non_windows_returns_true(self):
        from connectors.workiq_bridge import health_check
        with patch("platform.system", return_value="Darwin"):
            assert health_check({}) is True

    def test_health_check_no_npx_returns_false(self):
        from connectors.workiq_bridge import health_check
        with (
            patch("platform.system", return_value="Windows"),
            patch("connectors.workiq_bridge._find_npx", return_value=None),
        ):
            assert health_check({}) is False


# ─────────────────────────────────────────────────────────────────────────────
# ado_workitems
# ─────────────────────────────────────────────────────────────────────────────

class TestAdoWorkitemsNormalise:
    @pytest.fixture(autouse=True)
    def _import(self):
        from connectors.ado_workitems import _normalise_item, _get_headers
        self._normalise_item = _normalise_item
        self._get_headers = _get_headers

    def _raw_item(self, **overrides) -> dict:
        fields = {
            "System.Id": 42,
            "System.Title": "Fix null pointer in auth service",
            "System.State": "Active",
            "System.WorkItemType": "Bug",
            "Microsoft.VSTS.Common.Priority": 2,
            "Microsoft.VSTS.Scheduling.TargetDate": "2026-03-25T00:00:00Z",
            "System.ChangedDate": "2026-03-14T08:00:00Z",
            "System.IterationPath": "MyProject\\Sprint 12",
            "System.AreaPath": "MyProject\\Backend",
            "System.AssignedTo": {"displayName": "Alice Smith", "id": "abc"},
        }
        fields.update(overrides)
        return {"fields": fields, "url": "https://ado.example.com/wiql"}

    def test_normalise_maps_fields(self):
        record = self._normalise_item(self._raw_item())
        assert record["id"] == 42
        assert record["title"] == "Fix null pointer in auth service"
        assert record["state"] == "Active"
        assert record["type"] == "Bug"
        assert record["priority"] == 2
        assert record["assigned_to"] == "Alice Smith"

    def test_normalise_dates_trimmed(self):
        record = self._normalise_item(self._raw_item())
        assert record["target_date"] == "2026-03-25"
        assert record["changed_date"] == "2026-03-14"

    def test_normalise_assigned_to_string_passthrough(self):
        # Some ADO responses return AssignedTo as plain string
        record = self._normalise_item(self._raw_item(**{
            "System.AssignedTo": "Bob Jones"
        }))
        assert record["assigned_to"] == "Bob Jones"

    def test_normalise_source_tag(self):
        record = self._normalise_item(self._raw_item())
        assert record["source"] == "ado"

    def test_get_headers_az_cli(self):
        ctx = {"method": "az_cli", "access_token": "eyJtoken"}
        headers = self._get_headers(ctx)
        assert headers["Authorization"].startswith("Bearer ")

    def test_get_headers_api_key_pat(self):
        import base64
        ctx = {"method": "api_key", "password": "mypat123"}
        headers = self._get_headers(ctx)
        assert headers["Authorization"].startswith("Basic ")
        # Verify the encoded content is ":mypat123"
        encoded = headers["Authorization"].split(" ", 1)[1]
        decoded = base64.b64decode(encoded).decode()
        assert decoded == ":mypat123"

    def test_get_headers_unsupported_raises(self):
        with pytest.raises(RuntimeError, match="Unsupported auth method"):
            self._get_headers({"method": "oauth2"})


class TestAdoWorkitemsFetch:
    def test_fetch_no_org_url_yields_nothing(self, capsys):
        from connectors.ado_workitems import fetch
        with patch("connectors.ado_workitems._ado_config", return_value=("", "MyProj", "az_cli")):
            records = list(fetch(auth_context={"method": "az_cli", "access_token": "tok"}))
        assert records == []
        captured = capsys.readouterr()
        assert "not configured" in captured.err

    def test_fetch_wiql_http_error_yields_nothing(self, capsys):
        from connectors.ado_workitems import fetch
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        with (
            patch("connectors.ado_workitems._ado_config",
                  return_value=("https://dev.azure.com/myorg", "MyProj", "az_cli")),
            patch("requests.post", return_value=mock_resp),
        ):
            records = list(fetch(auth_context={"method": "az_cli", "access_token": "tok"}))
        assert records == []
        captured = capsys.readouterr()
        assert "401" in captured.err


# ─────────────────────────────────────────────────────────────────────────────
# outlookctl_bridge
# ─────────────────────────────────────────────────────────────────────────────

class TestOutlookctlBridgeParseEvent:
    @pytest.fixture(autouse=True)
    def _import(self):
        from connectors.outlookctl_bridge import _parse_event
        self._parse_event = _parse_event

    def test_basic_event(self):
        raw = {
            "subject": "1:1 with Manager",
            "start": "2026-03-16T09:00:00",
            "end": "2026-03-16T09:30:00",
            "organizer": "Alice",
            "location": "Building A / Room 102",
            "is_teams": False,
        }
        record = self._parse_event(raw)
        assert record["title"] == "1:1 with Manager"
        assert record["organizer"] == "Alice"
        assert record["is_teams"] is False
        assert record["attendees_available"] is False

    def test_teams_detection_from_location(self):
        raw = {
            "subject": "Sprint Planning",
            "start": "2026-03-16T10:00:00",
            "end": "2026-03-16T11:00:00",
            "organizer": "Bob",
            "location": "Microsoft Teams Meeting",
            "is_teams": False,  # outlookctl 0.1.0 quirk
        }
        record = self._parse_event(raw)
        assert record["is_teams"] is True

    def test_is_teams_true_preserved(self):
        raw = {
            "subject": "All-Hands",
            "start": "2026-03-16T12:00:00",
            "end": "2026-03-16T13:00:00",
            "organizer": "HR",
            "location": "",
            "is_teams": True,
        }
        record = self._parse_event(raw)
        assert record["is_teams"] is True

    def test_attendees_available_always_false(self):
        record = self._parse_event({
            "subject": "Test", "start": "2026-03-16T08:00:00",
            "end": "2026-03-16T09:00:00", "organizer": "", "location": "",
        })
        assert record["attendees_available"] is False


class TestOutlookctlBridgeFetch:
    def test_fetch_non_windows_yields_nothing(self):
        from connectors.outlookctl_bridge import fetch
        with patch("platform.system", return_value="Darwin"):
            records = list(fetch(auth_context={}))
        assert records == []

    def test_fetch_no_outlookctl_binary_yields_nothing(self):
        from connectors.outlookctl_bridge import fetch
        with (
            patch("platform.system", return_value="Windows"),
            patch("connectors.outlookctl_bridge._find_outlookctl", return_value=None),
        ):
            records = list(fetch(auth_context={}))
        assert records == []

    def test_fetch_yields_parsed_events(self):
        from connectors.outlookctl_bridge import fetch
        fake_output = [
            {"subject": "Sprint Review", "start": "2026-03-16T10:00:00",
             "end": "2026-03-16T11:00:00", "organizer": "Alice", "location": "", "is_teams": False},
        ]
        with (
            patch("platform.system", return_value="Windows"),
            patch("connectors.outlookctl_bridge._run_outlookctl", return_value=fake_output),
        ):
            records = list(fetch(auth_context={}))
        assert len(records) == 1
        assert records[0]["title"] == "Sprint Review"

    def test_health_check_non_windows_returns_true(self):
        from connectors.outlookctl_bridge import health_check
        with patch("platform.system", return_value="Darwin"):
            assert health_check({}) is True

    def test_health_check_outlookctl_missing_returns_false(self):
        from connectors.outlookctl_bridge import health_check
        with (
            patch("platform.system", return_value="Windows"),
            patch("connectors.outlookctl_bridge._find_outlookctl", return_value=None),
        ):
            assert health_check({}) is False


# ─────────────────────────────────────────────────────────────────────────────
# check_ado_auth in preflight.py
# ─────────────────────────────────────────────────────────────────────────────

class TestPreflight_CheckAdoAuth:
    @pytest.fixture(autouse=True)
    def _import(self):
        import preflight
        self.pf = preflight

    def test_ado_disabled_returns_skipped(self, tmp_path):
        """When azure_devops.enabled is false, check should pass (skipped)."""
        import yaml as _yaml
        profile = {"integrations": {"azure_devops": {"enabled": False}}}
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "user_profile.yaml").write_text(_yaml.dump(profile), encoding="utf-8")

        with patch("preflight.ARTHA_DIR", str(tmp_path)):
            result = self.pf.check_ado_auth()
        assert result.passed is True
        assert "skipped" in result.message.lower() or "not enabled" in result.message.lower()

    def test_ado_enabled_az_cli_success(self, tmp_path):
        """When enabled and az_cli returns a token, check should pass."""
        import yaml as _yaml
        profile = {"integrations": {"azure_devops": {
            "enabled": True,
            "organization_url": "https://dev.azure.com/myorg",
        }}}
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "user_profile.yaml").write_text(_yaml.dump(profile), encoding="utf-8")

        fake_token = json.dumps({"accessToken": "eyJfake", "expiresOn": "2026-03-17 22:00:00"})
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = fake_token

        with (
            patch("preflight.ARTHA_DIR", str(tmp_path)),
            patch("subprocess.run", return_value=mock_proc),
        ):
            result = self.pf.check_ado_auth()
        assert result.passed is True

    def test_ado_enabled_az_cli_failure(self, tmp_path):
        """When enabled but az_cli returns non-zero, check should fail."""
        import yaml as _yaml
        profile = {"integrations": {"azure_devops": {
            "enabled": True,
            "organization_url": "https://dev.azure.com/myorg",
        }}}
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "user_profile.yaml").write_text(_yaml.dump(profile), encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "Please run az login"

        with (
            patch("preflight.ARTHA_DIR", str(tmp_path)),
            patch("subprocess.run", return_value=mock_proc),
        ):
            result = self.pf.check_ado_auth()
        assert result.passed is False
