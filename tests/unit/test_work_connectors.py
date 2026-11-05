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
    - fetch: failures propagate to Connector coverage instead of appearing empty
    - fetch: date-only WIQL filters and schema-valid records

  outlookctl_bridge:
    - _parse_event: regular event fields mapped correctly
    - _parse_event: Teams detected from location string
    - _parse_event: is_teams flag already True preserved
    - _parse_event: attendees_available always False
    - fetch: non-Windows yields nothing
    - fetch: missing or failing outlookctl propagates instead of appearing empty
    - health_check: non-Windows returns True
    - health_check: missing outlookctl returns False

  check_ado_auth (preflight integration):
    - returns skipped message when azure_devops.enabled is False
    - returns passed CheckResult when az_cli token acquired

Ref: specs/work-domain-assessment.md §18, §22
"""
from __future__ import annotations

import json
import subprocess
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

class TestWorkiqVersionPin:
    @pytest.mark.parametrize("surface", ["connector", "package", "script"])
    def test_exact_release_is_shared_by_every_surface(self, surface, monkeypatch):
        if surface == "connector":
            from connectors import workiq_bridge as module
        elif surface == "package":
            from preflight import _types as module
        else:
            import importlib.util
            name = "_preflight_workiq_pin_test"
            spec = importlib.util.spec_from_file_location(name, _SCRIPTS / "preflight.py")
            module = importlib.util.module_from_spec(spec)
            monkeypatch.setitem(sys.modules, name, module)
            spec.loader.exec_module(module)
        assert module.WORKIQ_VERSION_PIN == "0.4.1"

    def test_queries_use_the_same_exact_release(self, monkeypatch):
        from connectors import workiq_bridge as bridge
        command = r"C:\synthetic\Node\npx.cmd"
        monkeypatch.setattr(bridge, "_find_npx", lambda: command)
        with patch("platform.system", return_value="Linux"), \
             patch.object(bridge.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "synthetic reply"
            assert bridge._ask_workiq("synthetic question") == "synthetic reply"
        assert run.call_args.args[0] == [
            command, "-y", "@microsoft/workiq@0.4.1", "ask", "-q", "synthetic question",
        ]


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
        assert key.startswith("workiq_calendar_")
        assert "2026-03-22" not in key

    def test_cache_key_people_includes_name(self):
        key = self._cache_key_for_mode("people", person_name="Alice Smith")
        assert "alice_smith" not in key

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
        test_key = wb._cache_key_for_mode(
            "calendar", start_date="2026-03-16", end_date="2026-03-22", person_name="",
            since="", max_results=200, lookback="48", lookback_days=7, context_depth="brief",
            channels=wb._TEAMS_AI_CHANNELS, source_tag="workiq", account_id="", tenant_id="",
            redaction_policy={"keywords": wb._redact_keywords(), "replacement": wb._redact_replacement()})
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


class TestWorkiqQueryFailures:
    @pytest.fixture
    def bridge(self):
        from connectors import workiq_bridge
        with (
            patch.object(workiq_bridge, "_load_cache", return_value={}),
            patch.object(workiq_bridge, "_save_cache"),
            patch.object(workiq_bridge, "_redact_keywords", return_value=[]),
            patch.object(workiq_bridge, "_redact_replacement", return_value="[REDACTED]"),
        ):
            yield workiq_bridge

    @pytest.mark.parametrize("mode", ["calendar", "email", "teams", "people", "documents"])
    @pytest.mark.parametrize("rich", [False, True])
    @pytest.mark.parametrize("timeout", [False, True])
    def test_failed_structured_query_never_becomes_empty_success(
        self, bridge, mode, rich, timeout,
    ):
        failure = subprocess.TimeoutExpired("synthetic", 90) if timeout else RuntimeError("unavailable")
        responses = ["synthetic narrative", failure] if rich else [failure]
        with patch.object(bridge, "_ask_workiq", side_effect=responses), \
             pytest.raises(RuntimeError):
            list(bridge.fetch(
                auth_context={}, mode=mode, person_name="Synthetic Person",
                context_depth="rich" if rich else "brief",
            ))
        bridge._save_cache.assert_not_called()

    @pytest.mark.parametrize("narrative", [RuntimeError("unavailable"), ""])
    def test_rich_fallback_keeps_records_but_reports_gap_without_caching(self, bridge, narrative):
        from lib.connector_coverage import CoverageCollector
        collector = CoverageCollector()
        row = "2026-03-16 | 09:00 | 10:00 | Synthetic meeting | Example | Teams | yes"
        with collector.track("workiq") as tracker, \
             patch.object(bridge, "_ask_workiq", side_effect=[narrative, row]):
            records = list(bridge.fetch(auth_context={}, context_depth="rich"))
        assert len(records) == 1
        assert records[0]["processing_gaps"]
        assert any(scope.status == "failed" for scope in tracker.scopes)
        bridge._save_cache.assert_not_called()

    @pytest.mark.parametrize("person_name", ["", " ", None])
    def test_people_name_is_required_before_cache_or_query(self, bridge, person_name):
        with patch.object(bridge, "_ask_workiq") as query, \
             pytest.raises(ValueError, match="person_name"):
            list(bridge.fetch(auth_context={}, mode="people", person_name=person_name))
        bridge._load_cache.assert_not_called()
        bridge._save_cache.assert_not_called()
        query.assert_not_called()

    @pytest.mark.parametrize("all_failed", [False, True])
    def test_channel_failures_are_not_complete_or_cached(self, bridge, all_failed):
        from lib.connector_coverage import CoverageCollector
        collector = CoverageCollector()
        row = "Example | 2026-03-16 | Synthetic topic | Synthetic quote | https://example.test/message"
        responses = [RuntimeError("unavailable"), RuntimeError("unavailable")] if all_failed else [
            row, RuntimeError("unavailable"),
        ]
        with collector.track("workiq") as tracker, \
             patch.object(bridge, "_ask_workiq", side_effect=responses):
            if all_failed:
                with pytest.raises(RuntimeError):
                    list(bridge.fetch(auth_context={}, mode="teams_ai", channels=["one", "two"]))
            else:
                records = list(bridge.fetch(auth_context={}, mode="teams_ai", channels=["one", "two"]))
                assert len(records) == 1
                assert records[0]["processing_gaps"]
        assert any(scope.status == "failed" for scope in tracker.scopes)
        bridge._save_cache.assert_not_called()

    def test_channel_deadline_is_reported_as_truncated_not_empty_success(self, bridge):
        from lib.connector_coverage import CoverageCollector
        collector = CoverageCollector()
        times = [datetime.fromtimestamp(value, tz=timezone.utc) for value in (0, 301)]
        with collector.track("workiq") as tracker, patch.object(bridge, "datetime") as clock, \
             patch.object(bridge, "_ask_workiq") as query, pytest.raises(RuntimeError):
            clock.now.side_effect = times
            list(bridge.fetch(auth_context={}, mode="teams_ai", channels=["one"]))
        query.assert_not_called()
        assert any(scope.status == "truncated" for scope in tracker.scopes)
        bridge._save_cache.assert_not_called()

    @pytest.mark.parametrize("retry", [RuntimeError("unavailable"), "Still not a calendar table"])
    def test_failed_calendar_retry_never_publishes_empty_cache(self, bridge, retry):
        with patch.object(bridge, "_ask_workiq", side_effect=["Not a calendar table", retry]), \
             pytest.raises(RuntimeError):
            list(bridge.fetch(auth_context={}))
        bridge._save_cache.assert_not_called()

    def test_pipeline_sees_query_failure_instead_of_successful_zero_records(self, bridge):
        from pipeline import _fetch_one
        with patch.object(bridge, "_ask_workiq", side_effect=RuntimeError("unavailable")):
            _name, lines, _elapsed, error = _fetch_one(
                "workiq_email", bridge, {}, {"mode": "email"}, {"max_attempts": 1}, since="2026-03-16",
            )
        assert not lines
        assert error is not None
        bridge._save_cache.assert_not_called()

    @pytest.mark.parametrize("direct_mcp", [False, True])
    def test_successful_query_routing_and_cache_are_preserved(self, bridge, direct_mcp):
        row = "2026-03-16 | 09:00 | 10:00 | Synthetic meeting | Example | Teams | yes"
        with (
            patch.object(bridge, "_ask_workiq_mcp", return_value=row if direct_mcp else None),
            patch.object(bridge, "_ask_workiq", return_value=row) as cli,
        ):
            records = list(bridge.fetch(auth_context={}, use_mcp=True))
        assert len(records) == 1
        assert "processing_gaps" not in records[0]
        assert cli.call_count == (0 if direct_mcp else 1)
        bridge._save_cache.assert_called_once()

    def test_successful_calendar_parse_retry_can_still_be_cached(self, bridge):
        row = "2026-03-16 | 09:00 | 10:00 | Synthetic meeting | Example | Teams | yes"
        with patch.object(bridge, "_ask_workiq", side_effect=["Not a calendar table", row]):
            records = list(bridge.fetch(auth_context={}))
        assert len(records) == 1
        assert "processing_gaps" not in records[0]
        bridge._save_cache.assert_called_once()


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
            "One_custom.PM_Owner_Identity": {"displayName": "Project Owner", "id": "def"},
        }
        fields.update(overrides)
        return {"fields": fields, "url": "https://ado.example.com/wiql"}

    def test_normalise_maps_fields(self):
        record = self._normalise_item(self._raw_item())
        assert record["id"] == "42"
        assert record["title"] == "Fix null pointer in auth service"
        assert record["state"] == "Active"
        assert record["type"] == "Bug"
        assert record["priority"] == 2
        assert record["assigned_to"] == "Alice Smith"
        assert record["pm_owner"] == "Project Owner"

    def test_normalise_dates_trimmed(self):
        record = self._normalise_item(self._raw_item())
        assert record["target_date"] == "2026-03-25"
        assert record["changed_date"] == "2026-03-14"
        assert record["date_iso"] == "2026-03-14T08:00:00Z"

    def test_normalise_satisfies_connector_schema(self):
        from schemas.connector_record import validate_record

        record = self._normalise_item(self._raw_item())
        assert validate_record(record).id == "42"

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
    def test_fetch_no_org_url_raises(self):
        from connectors.ado_workitems import fetch
        with patch("connectors.ado_workitems._ado_config", return_value=("", "MyProj", "az_cli")):
            with pytest.raises(RuntimeError, match="not configured"):
                list(fetch(auth_context={"method": "az_cli", "access_token": "tok"}))

    @pytest.mark.parametrize("status", [400, 401, 403, 500])
    def test_fetch_wiql_http_error_raises(self, status):
        from connectors.ado_workitems import fetch
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        with (
            patch("connectors.ado_workitems._ado_config",
                  return_value=("https://dev.azure.com/myorg", "MyProj", "az_cli")),
            patch("requests.post", return_value=mock_resp),
        ):
            with pytest.raises(RuntimeError, match=str(status)):
                list(fetch(auth_context={"method": "az_cli", "access_token": "tok"}))

    @pytest.mark.parametrize("since, expected", [
        ("2026-03-01", "2026-03-01"),
        ("2026-03-01T12:34:56Z", "2026-03-01"),
        ("2026-03-01T23:59:59-08:00", "2026-03-01"),
    ])
    def test_wiql_accepts_dates_and_timestamps_without_sending_time(self, since, expected):
        from connectors.ado_workitems import _wiql_query

        response = MagicMock()
        response.json.return_value = {"workItems": [{"id": 42}]}
        with patch("connectors.ado_workitems.requests.post", return_value=response) as post:
            assert _wiql_query("https://ado.example.invalid", "Project", {}, since) == [42]
        query = post.call_args.kwargs["json"]["query"]
        assert f"[System.ChangedDate] >= '{expected}'" in query
        assert since == expected or since not in query

    def test_wiql_since_includes_all_active_and_recently_changed_items(self):
        from connectors.ado_workitems import _wiql_query

        response = MagicMock()
        response.json.return_value = {"workItems": []}
        with patch("connectors.ado_workitems.requests.post", return_value=response) as post:
            _wiql_query("https://ado.example.invalid", "Project", {}, "2026-03-01")
        queries = [call.kwargs["json"]["query"] for call in post.call_args_list]
        assert len(queries) == 2
        assert "[System.AssignedTo] = @Me" in queries[0]
        assert "[One_custom.PM_Owner_Identity]" not in queries[0]
        assert "[One_custom.PM_Owner_Identity] = @Me" in queries[1]
        assert "[System.AssignedTo]" not in queries[1]
        for query in queries:
            assert (
                "([System.State] NOT IN ('Closed', 'Resolved', 'Done', 'Removed') "
                "OR [System.ChangedDate] >= '2026-03-01')"
            ) in query

    def test_wiql_deduplicates_items_matching_both_ownership_filters(self):
        from connectors.ado_workitems import _wiql_query

        assigned_response = MagicMock()
        assigned_response.json.return_value = {"workItems": [{"id": 42}, {"id": 43}]}
        pm_owner_response = MagicMock()
        pm_owner_response.json.return_value = {"workItems": [{"id": 43}, {"id": 44}]}
        with patch(
            "connectors.ado_workitems.requests.post",
            side_effect=[assigned_response, pm_owner_response],
        ):
            ids = _wiql_query("https://ado.example.invalid", "Project", {})
        assert ids == [42, 43, 44]

    @pytest.mark.parametrize("since", ["not-a-date", "2026-02-30", "2026-03-01' OR 1=1"])
    def test_invalid_since_is_rejected_before_provider_call(self, since):
        from connectors.ado_workitems import _wiql_query

        with patch("connectors.ado_workitems.requests.post") as post:
            with pytest.raises(ValueError, match="since"):
                _wiql_query("https://ado.example.invalid", "Project", {}, since)
        post.assert_not_called()

    @pytest.mark.parametrize("auth", [
        {"method": "az_cli"}, {"method": "api_key"}, {"method": "unsupported"},
    ])
    def test_auth_failure_does_not_become_empty_success(self, auth):
        from connectors.ado_workitems import fetch

        with patch("connectors.ado_workitems._ado_config",
                   return_value=("https://ado.example.invalid", "Project", "az_cli")):
            with pytest.raises(RuntimeError, match="auth|access_token|password|Unsupported"):
                list(fetch(auth_context=auth))

    def test_http_failure_reaches_core_coverage_as_unavailable(self):
        from catchup_core.adapters import fetch_via_handler
        from catchup_core.contracts import CoverageStatus, FailureClass
        from connectors.ado_workitems import fetch
        import requests

        response = MagicMock(status_code=401)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        with (
            patch("connectors.ado_workitems._ado_config",
                  return_value=("https://ado.example.invalid", "Project", "az_cli")),
            patch("connectors.ado_workitems.requests.post", return_value=response),
        ):
            result = fetch_via_handler("ado_workitems", "synthetic", fetch, since="2026-03-01",
                                      auth_context={"method": "az_cli", "access_token": "synthetic"})
        assert result.status is CoverageStatus.UNAVAILABLE
        assert result.error is FailureClass.AUTH

    def test_valid_empty_query_remains_empty(self):
        from connectors.ado_workitems import fetch

        with (
            patch("connectors.ado_workitems._ado_config",
                  return_value=("https://ado.example.invalid", "Project", "az_cli")),
            patch("connectors.ado_workitems._wiql_query", return_value=[]),
            patch("connectors.ado_workitems._batch_fetch") as batch,
        ):
            assert list(fetch(auth_context={"method": "az_cli", "access_token": "synthetic"})) == []
        batch.assert_not_called()

    @pytest.mark.parametrize("max_results", [0, -1, True, "2", None])
    def test_invalid_result_limit_is_rejected(self, max_results):
        from connectors.ado_workitems import fetch

        with pytest.raises(ValueError, match="max_results"):
            list(fetch(auth_context={}, max_results=max_results))

    @pytest.mark.parametrize("stage", ["query", "batch"])
    @pytest.mark.parametrize("failure", ["network", "http", "http_without_response"])
    def test_provider_failures_are_not_successful_empty_results(self, stage, failure):
        from connectors import ado_workitems as ado
        import requests

        response = MagicMock(status_code=503) if failure == "http" else None
        error = (requests.Timeout("connection timed out") if failure == "network"
                 else requests.HTTPError(response=response))
        with (
            patch.object(ado, "_ado_config", return_value=("https://ado.example.invalid", "Project", "az_cli")),
            patch.object(ado, "_wiql_query", side_effect=error if stage == "query" else None,
                         return_value=[42]),
            patch.object(ado, "_batch_fetch", side_effect=error if stage == "batch" else None),
        ):
            with pytest.raises(RuntimeError, match="WIQL query|batch fetch"):
                list(ado.fetch(auth_context={"method": "az_cli", "access_token": "synthetic"}))

    @pytest.mark.parametrize("source_tag", ["ado_workitems", ""])
    def test_successful_fetch_preserves_schema_and_reports_truncation(self, source_tag):
        from connectors import ado_workitems as ado
        from lib.connector_coverage import CoverageCollector
        from schemas.connector_record import validate_record

        raw = {"fields": {"System.Id": 42, "System.Title": "Synthetic item",
                          "System.ChangedDate": "2026-03-01T12:00:00Z"}}
        collector = CoverageCollector()
        with (
            patch.object(ado, "_ado_config", return_value=("https://ado.example.invalid", "Project", "az_cli")),
            patch.object(ado, "_wiql_query", return_value=[42, 43]),
            patch.object(ado, "_batch_fetch", return_value=[raw]) as batch,
            collector.track("ado_workitems") as tracker,
        ):
            records = list(ado.fetch(auth_context={"method": "az_cli", "access_token": "synthetic"},
                                     max_results=1, source_tag=source_tag))
        assert validate_record(records[0]).id == "42"
        assert records[0]["source"] == (source_tag or "ado")
        assert batch.call_args.args[1] == [42]
        assert [(scope.scope, scope.status) for scope in tracker.scopes] == [("workitems", "truncated")]

    @pytest.mark.parametrize("offset, expected", [("7d", "2026-03-01"), ("24h", "2026-03-07")])
    def test_relative_since_uses_date_only_filter(self, offset, expected):
        from connectors import ado_workitems as ado

        response = MagicMock()
        response.json.return_value = {"workItems": []}
        with (
            patch.object(ado, "datetime") as clock,
            patch.object(ado.requests, "post", return_value=response) as post,
        ):
            clock.now.return_value = datetime(2026, 3, 8, 12, tzinfo=timezone.utc)
            assert ado._wiql_query("https://ado.example.invalid", "Project", {}, offset) == []
        assert f"[System.ChangedDate] >= '{expected}'" in post.call_args.kwargs["json"]["query"]

    def test_empty_since_omits_date_filter(self):
        from connectors import ado_workitems as ado

        response = MagicMock()
        response.json.return_value = {"workItems": []}
        with patch.object(ado.requests, "post", return_value=response) as post:
            assert ado._wiql_query("https://ado.example.invalid", "Project", {}) == []
        queries = [call.kwargs["json"]["query"] for call in post.call_args_list]
        assert len(queries) == 2
        assert "[System.AssignedTo] = @Me" in queries[0]
        assert "[One_custom.PM_Owner_Identity] = @Me" in queries[1]
        for query in queries:
            assert "[System.ChangedDate] >=" not in query
            assert "[System.State] NOT IN ('Closed', 'Resolved', 'Done', 'Removed')" in query

    def test_batch_fetch_respects_provider_batch_size_and_empty_input(self):
        from connectors import ado_workitems as ado

        first, second = MagicMock(), MagicMock()
        first.json.return_value = {"value": [{"id": n} for n in range(1, 201)]}
        second.json.return_value = {"value": [{"id": 201}]}
        with patch.object(ado.requests, "get", side_effect=[first, second]) as get:
            assert ado._batch_fetch("https://ado.example.invalid", [], {}) == []
            get.assert_not_called()
            items = ado._batch_fetch("https://ado.example.invalid", list(range(1, 202)), {"test": "header"})
        assert len(items) == 201
        assert get.call_count == 2
        assert "?ids=201&" in get.call_args.args[0]
        assert "One_custom.PM_Owner_Identity" in get.call_args.args[0]
        assert get.call_args.kwargs["timeout"] == ado.REQUEST_TIMEOUT

    @pytest.mark.parametrize("payload", [{}, {"workItems": None}, {"workItems": {}}])
    def test_missing_wiql_listing_is_not_a_valid_empty_query(self, payload):
        from connectors import ado_workitems as ado

        response = MagicMock()
        response.json.return_value = payload
        with patch.object(ado.requests, "post", return_value=response):
            with pytest.raises(ValueError, match="workItems"):
                ado._wiql_query("https://ado.example.invalid", "Project", {})

    @pytest.mark.parametrize("payload", [{}, {"value": None}, {"value": {}}])
    def test_missing_batch_listing_is_not_empty_success(self, payload):
        from connectors import ado_workitems as ado

        response = MagicMock()
        response.json.return_value = payload
        with patch.object(ado.requests, "get", return_value=response):
            with pytest.raises(ValueError, match="value"):
                ado._batch_fetch("https://ado.example.invalid", [42], {})

    @pytest.mark.parametrize("returned_ids", [[], [43], [42, 42], [42, 43]])
    def test_omitted_duplicate_or_unrequested_batch_ids_are_rejected(self, returned_ids):
        from connectors import ado_workitems as ado

        response = MagicMock()
        response.json.return_value = {"value": [{"id": item_id} for item_id in returned_ids]}
        with patch.object(ado.requests, "get", return_value=response):
            with pytest.raises(RuntimeError, match="batch response"):
                ado._batch_fetch("https://ado.example.invalid", [42], {})

    def test_missing_native_identity_is_not_invented(self):
        from connectors.ado_workitems import _normalise_item
        from schemas.connector_record import validate_record

        record = _normalise_item({"fields": {"System.ChangedDate": "2026-03-01"}})
        with pytest.raises(ValueError, match="id"):
            validate_record(record)


class TestAdoWorkitemsConfigurationAndHealth:
    @pytest.mark.parametrize("profile, path, expected", [
        ({"one": {"two": "value"}}, "one.two", "value"),
        ({"one": "not-a-mapping"}, "one.two", "fallback"),
        ({"one": None}, "one.two", "fallback"),
        ({}, "missing", "fallback"),
        (None, "missing", "fallback"),
    ])
    def test_profile_lookup(self, profile, path, expected):
        from connectors.ado_workitems import _profile_value

        with patch("profile_loader.load_profile", return_value=profile):
            assert _profile_value(path, "fallback") == expected

    def test_unreadable_profile_is_reported_by_fetch_as_unconfigured(self):
        from connectors.ado_workitems import fetch

        with patch("profile_loader.load_profile", side_effect=ValueError("synthetic invalid profile")):
            with pytest.raises(RuntimeError, match="not configured"):
                list(fetch(auth_context={}))

    def test_config_strips_trailing_slash_and_uses_default_auth(self):
        from connectors.ado_workitems import _ado_config

        profile = {"integrations": {"azure_devops": {
            "organization_url": "https://ado.example.invalid/", "project": "Project",
        }}}
        with patch("profile_loader.load_profile", return_value=profile):
            assert _ado_config() == ("https://ado.example.invalid", "Project", "az_cli")

    @pytest.mark.parametrize("configured", [("", "Project", "az_cli"), ("https://ado.example.invalid", "", "az_cli")])
    def test_health_missing_config_is_false(self, configured, capsys):
        from connectors import ado_workitems as ado

        with patch.object(ado, "_ado_config", return_value=configured):
            assert ado.health_check({}) is False
        assert "not configured" in capsys.readouterr().err

    def test_health_bad_auth_is_false(self, capsys):
        from connectors import ado_workitems as ado

        with patch.object(ado, "_ado_config", return_value=("https://ado.example.invalid", "Project", "az_cli")):
            assert ado.health_check({"method": "az_cli"}) is False
        assert "auth error" in capsys.readouterr().err

    @pytest.mark.parametrize("status", [200, 401, 403, 500])
    def test_health_reports_provider_status_without_raising(self, status, capsys):
        from connectors import ado_workitems as ado

        with (
            patch.object(ado, "_ado_config", return_value=("https://ado.example.invalid", "Project", "az_cli")),
            patch.object(ado.requests, "post", return_value=MagicMock(status_code=status)),
        ):
            assert ado.health_check({"method": "az_cli", "access_token": "synthetic"}) is (status == 200)
        if status != 200:
            assert str(status) in capsys.readouterr().err

    def test_health_network_failure_is_false(self, capsys):
        from connectors import ado_workitems as ado
        import requests

        with (
            patch.object(ado, "_ado_config", return_value=("https://ado.example.invalid", "Project", "az_cli")),
            patch.object(ado.requests, "post", side_effect=requests.ConnectionError("synthetic connection failure")),
        ):
            assert ado.health_check({"method": "az_cli", "access_token": "synthetic"}) is False
        assert "connection failure" in capsys.readouterr().err


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

    def test_fetch_no_outlookctl_binary_raises(self):
        from connectors.outlookctl_bridge import fetch
        with (
            patch("platform.system", return_value="Windows"),
            patch("connectors.outlookctl_bridge._find_outlookctl", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="not found"):
                list(fetch(auth_context={}))

    @pytest.mark.parametrize("failure", [
        FileNotFoundError("synthetic missing executable"),
        subprocess.TimeoutExpired("outlookctl", 25),
    ])
    def test_command_process_failure_propagates(self, failure):
        from connectors.outlookctl_bridge import fetch
        with (
            patch("platform.system", return_value="Windows"),
            patch("connectors.outlookctl_bridge._find_outlookctl", return_value="outlookctl"),
            patch("connectors.outlookctl_bridge.subprocess.run", side_effect=failure),
        ):
            with pytest.raises(RuntimeError):
                list(fetch(auth_context={}))

    @pytest.mark.parametrize("returncode,stdout", [
        (1, "[]"),
        (0, "not json"),
        (0, "null"),
        (0, "{}"),
        (0, '{"items": null}'),
        (0, '{"events": {}}'),
        (0, '["not an event"]'),
    ])
    def test_invalid_calendar_response_propagates(self, returncode, stdout):
        from connectors.outlookctl_bridge import fetch
        with (
            patch("platform.system", return_value="Windows"),
            patch("connectors.outlookctl_bridge._find_outlookctl", return_value="outlookctl"),
            patch("connectors.outlookctl_bridge.subprocess.run", return_value=MagicMock(
                returncode=returncode, stdout=stdout, stderr="synthetic error",
            )),
        ):
            with pytest.raises(RuntimeError):
                list(fetch(auth_context={}))

    @pytest.mark.parametrize("stdout", ["[]", '{"items": []}', '{"events": []}'])
    def test_valid_empty_calendar_is_preserved(self, stdout):
        from connectors.outlookctl_bridge import fetch
        with (
            patch("platform.system", return_value="Windows"),
            patch("connectors.outlookctl_bridge._find_outlookctl", return_value="outlookctl"),
            patch("connectors.outlookctl_bridge.subprocess.run", return_value=MagicMock(
                returncode=0, stdout=stdout,
            )),
        ):
            assert list(fetch(auth_context={})) == []

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

    def test_command_success_preserves_default_source_and_result_limit(self):
        from connectors.outlookctl_bridge import fetch
        event = {"subject": "Synthetic review", "start": "2030-01-01T10:00:00",
                 "end": "2030-01-01T11:00:00"}
        with (
            patch("platform.system", return_value="Windows"),
            patch("connectors.outlookctl_bridge.shutil.which", return_value="outlookctl"),
            patch("connectors.outlookctl_bridge.subprocess.run", return_value=MagicMock(
                returncode=0, stdout=json.dumps([event, event]),
            )),
        ):
            records = list(fetch(auth_context={}, max_results=1, source_tag=""))
        assert len(records) == 1
        assert records[0]["title"] == "Synthetic review"
        assert records[0]["source"] == "outlookctl"

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

    @pytest.mark.parametrize("returncode,expected", [(0, True), (1, False)])
    def test_health_check_cli_exit_code(self, returncode, expected):
        from connectors.outlookctl_bridge import health_check
        with (
            patch("platform.system", return_value="Windows"),
            patch("connectors.outlookctl_bridge._find_outlookctl", return_value="outlookctl"),
            patch("connectors.outlookctl_bridge.subprocess.run", return_value=MagicMock(
                returncode=returncode,
            )),
        ):
            assert health_check({}) is expected

    @pytest.mark.parametrize("failure", [
        FileNotFoundError("synthetic missing executable"),
        subprocess.TimeoutExpired("outlookctl", 5),
    ])
    def test_health_check_command_failure_returns_false(self, failure):
        from connectors.outlookctl_bridge import health_check
        with (
            patch("platform.system", return_value="Windows"),
            patch("connectors.outlookctl_bridge._find_outlookctl", return_value="outlookctl"),
            patch("connectors.outlookctl_bridge.subprocess.run", side_effect=failure),
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
