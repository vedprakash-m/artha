"""
tests/integration/test_slack_e2e.py — Slack integration end-to-end test skeleton.

These tests verify the full Slack data path:
  connector (fetch) → pipeline → routing → state + briefing.
  action (slack_send) → Slack API.

Tests are SKIPPED in CI unless ARTHA_SLACK_INTEGRATION_TEST=1 and a valid
ARTHA_SLACK_BOT_TOKEN are set. This prevents accidental live API calls.

To run against a real Slack workspace:
    ARTHA_SLACK_INTEGRATION_TEST=1 \\
    ARTHA_SLACK_BOT_TOKEN=xoxb-your-token \\
    ARTHA_SLACK_TEST_CHANNEL=C123456 \\
    pytest tests/integration/test_slack_e2e.py -v

Ref: specs/connect.md §9 (Testing), §4 (E2E validation gates)
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Marker: skip unless explicitly enabled
_LIVE = os.environ.get("ARTHA_SLACK_INTEGRATION_TEST") == "1"
_SKIP_LIVE = pytest.mark.skipif(not _LIVE, reason="Set ARTHA_SLACK_INTEGRATION_TEST=1 to run")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slack_ok(**data: Any) -> bytes:
    return json.dumps({"ok": True, **data}).encode("utf-8")


def _mock_resp(body: bytes):
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_proposal(action_type: str = "slack_send", **params: Any):
    from actions.base import ActionProposal
    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type=action_type,
        domain="work",
        title="E2E test",
        description="Integration test",
        parameters={"channel": "C_TEST", "text": "E2E test", **params},
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=None,
    )


# ---------------------------------------------------------------------------
# Connector mock-mode tests (no live API)
# ---------------------------------------------------------------------------

class TestSlackConnectorMocked:
    """Connector fetch() pipeline with full Slack API mocked."""

    def test_fetch_yields_correct_schema(self):
        from connectors.slack import fetch

        ch_body = _slack_ok(
            channels=[{"id": "C1", "name": "general"}],
            response_metadata={"next_cursor": ""},
        )
        msg = {
            "ts": "1700000001.000000",
            "text": "Deploy done",
            "user": "U1",
        }
        hist_body = _slack_ok(
            messages=[msg],
            has_more=False,
            response_metadata={"next_cursor": ""},
        )

        call_count = [0]

        def _side_effect(req, **_):
            call_count[0] += 1
            if call_count[0] == 1:
                return _mock_resp(ch_body)
            return _mock_resp(hist_body)

        with patch("connectors.slack.urllib.request.urlopen", side_effect=_side_effect):
            with patch("connectors.slack._resolve_user", return_value="Alice"):
                with patch("connectors.slack.time.sleep"):
                    records = list(fetch(auth_context={"token": "xoxb-test"}))

        assert len(records) >= 1
        r = records[0]
        required = {"id", "title", "body", "author", "ts", "url",
                    "source", "channel", "thread_ts", "reactions"}
        assert required.issubset(r.keys())

    def test_fetch_source_tag_applied(self):
        from connectors.slack import fetch

        ch_body = _slack_ok(
            channels=[{"id": "C1", "name": "eng"}],
            response_metadata={"next_cursor": ""},
        )
        hist_body = _slack_ok(
            messages=[{"ts": "1.0", "text": "hi", "user": "U1"}],
            has_more=False,
            response_metadata={"next_cursor": ""},
        )
        call_count = [0]

        def _se(req, **_):
            call_count[0] += 1
            return _mock_resp(ch_body if call_count[0] == 1 else hist_body)

        with patch("connectors.slack.urllib.request.urlopen", side_effect=_se):
            with patch("connectors.slack._resolve_user", return_value="X"):
                with patch("connectors.slack.time.sleep"):
                    records = list(fetch(
                        auth_context={"token": "xoxb-test"},
                        source_tag="my_slack",
                    ))
        assert all(r["source"] == "my_slack" for r in records)

    def test_health_check_calls_auth_test(self):
        from connectors.slack import health_check

        body = _slack_ok(team="T1", user="bot")
        with patch("connectors.slack.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_resp(body)
            result = health_check(auth_context={"token": "xoxb-test"})
        assert result is True


class TestSlackActionMocked:
    """slack_send action handler with Slack API mocked."""

    def test_validate_passes_on_valid_proposal(self):
        from actions.slack_send import validate
        p = _make_proposal()
        ok, _ = validate(p)
        assert ok is True

    def test_execute_sends_to_correct_channel(self):
        from actions.slack_send import execute
        captured: dict = {}

        def _cap(req, **_):
            if hasattr(req, "data"):
                captured.update(json.loads(req.data.decode()))
            resp = MagicMock()
            resp.read.return_value = _slack_ok(ts="99.0")
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("actions.slack_send._load_token", return_value="xoxb-test"):
            with patch("actions.slack_send.urllib.request.urlopen", side_effect=_cap):
                result = execute(_make_proposal(channel="C_TARGET", text="Hello"))
        assert result.status == "success"
        assert captured.get("channel") == "C_TARGET"


# ---------------------------------------------------------------------------
# Live API tests (skipped unless ARTHA_SLACK_INTEGRATION_TEST=1)
# ---------------------------------------------------------------------------

@_SKIP_LIVE
class TestSlackConnectorLive:
    """Tests that hit the real Slack API. Requires ARTHA_SLACK_BOT_TOKEN."""

    def test_health_check_live(self):
        from connectors.slack import health_check
        result = health_check()
        assert result is True, "auth.test failed — check ARTHA_SLACK_BOT_TOKEN"

    def test_fetch_nonzero_records(self):
        from connectors.slack import fetch
        records = list(fetch(since="1h", max_results=5))
        # We don't require records (channel might be empty) but no exception
        assert isinstance(records, list)
        for r in records:
            assert "id" in r
            assert "body" in r

    def test_fetch_record_timestamps_are_iso8601(self):
        from connectors.slack import fetch
        from datetime import datetime
        records = list(fetch(since="24h", max_results=10))
        for r in records:
            if r.get("ts"):
                datetime.fromisoformat(r["ts"])  # should not raise


@_SKIP_LIVE
class TestSlackSendLive:
    """Tests that post real messages. Requires ARTHA_SLACK_BOT_TOKEN + test channel."""

    def test_send_message_live(self):
        from actions.slack_send import execute
        channel = os.environ.get("ARTHA_SLACK_TEST_CHANNEL", "")
        if not channel:
            pytest.skip("Set ARTHA_SLACK_TEST_CHANNEL to enable this test")

        from actions.base import ActionProposal
        p = ActionProposal(
            id=str(uuid.uuid4()),
            action_type="slack_send",
            domain="test",
            title="E2E live send",
            description="Automated integration test",
            parameters={"channel": channel, "text": "[Artha E2E test] This message is automated."},
            friction="low",
            min_trust=0,
            sensitivity="standard",
            reversible=False,
            undo_window_sec=None,
            expires_at=None,
        )
        result = execute(p)
        assert result.status == "success", f"Live send failed: {result.message}"
        assert result.data.get("ts"), "Expected non-empty ts in result data"
