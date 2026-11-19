"""
tests/unit/test_email_send_handler.py — Tests for email_send ActionHandler.

Tests validate(), dry_run(), execute(), build_reverse_proposal(), health_check().
Gmail API is mocked throughout — no real network calls.

Ref: specs/act.md §8.1
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest


import actions.email_send as email_send_handler
from actions.base import ActionProposal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_proposal(**kwargs) -> ActionProposal:
    params = {
        "to": "recipient@example.com",
        "subject": "Test Subject",
        "body": "Test body content",
        "draft_first": True,
    }
    params.update(kwargs.get("parameters", {}))
    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type="email_send",
        domain="test",
        title="Send test email",
        description="Test description",
        parameters=params,
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=True,
        undo_window_sec=30,
        expires_at=None,
        source_step=None,
        source_skill=None,
        linked_oi=None,
    )


# ---------------------------------------------------------------------------
# validate() tests
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_proposal_passes(self):
        p = _make_proposal()
        ok, reason = email_send_handler.validate(p)
        assert ok is True
        assert reason == ""

    def test_missing_to_fails(self):
        p = _make_proposal(parameters={"to": "", "subject": "Hi", "body": "Hello"})
        ok, reason = email_send_handler.validate(p)
        assert ok is False
        assert "'to'" in reason

    def test_missing_subject_fails(self):
        p = _make_proposal(parameters={"to": "a@b.com", "subject": "", "body": "Hello"})
        ok, reason = email_send_handler.validate(p)
        assert ok is False
        assert "'subject'" in reason

    def test_missing_body_fails(self):
        p = _make_proposal(parameters={"to": "a@b.com", "subject": "Hi", "body": ""})
        ok, reason = email_send_handler.validate(p)
        assert ok is False
        assert "'body'" in reason

    def test_invalid_email_no_at_fails(self):
        p = _make_proposal(parameters={"to": "notanemail", "subject": "Hi", "body": "Hello"})
        ok, reason = email_send_handler.validate(p)
        assert ok is False
        assert "@" in reason or "invalid" in reason.lower()

    def test_multiple_recipients_valid(self):
        p = _make_proposal(parameters={
            "to": "a@example.com, b@example.com",
            "subject": "Hi",
            "body": "Hello",
        })
        ok, reason = email_send_handler.validate(p)
        assert ok is True

    def test_subject_over_limit_fails(self):
        p = _make_proposal(parameters={
            "to": "a@example.com",
            "subject": "X" * 999,
            "body": "Hello",
        })
        ok, reason = email_send_handler.validate(p)
        assert ok is False
        assert "long" in reason.lower() or "998" in reason


# ---------------------------------------------------------------------------
# dry_run() tests
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_is_local_and_never_creates_a_draft(self):
        p = _make_proposal()
        with patch("actions.email_send.build_service", side_effect=AssertionError("provider touched")):
            result = email_send_handler.dry_run(p)

        assert result.status == "success"
        assert result.data.get("preview_mode") is True
        assert "draft_id" not in result.data

    def test_dry_run_no_draft_first_returns_preview(self):
        p = _make_proposal(parameters={
            "to": "a@b.com",
            "subject": "Hi",
            "body": "Hello",
            "draft_first": False,
        })
        result = email_send_handler.dry_run(p)
        assert result.status == "success"
        assert result.data.get("preview_mode") is True

    def test_reply_dry_run_is_local_and_never_creates_a_draft(self):
        from actions import email_reply
        p = email_reply.ActionProposal(
            id="reply-preview",
            action_type="email_reply",
            domain="comms",
            title="Reply",
            description="",
            parameters={"thread_id": "thread-1", "body": "Synthetic reply"},
            friction="standard",
            min_trust=1,
            sensitivity="standard",
            reversible=False,
            undo_window_sec=None,
            expires_at=None,
        )
        with patch("actions.email_reply._extract_reply_headers", side_effect=AssertionError("provider read")):
            result = email_reply.dry_run(p)
        assert result.status == "success"
        assert result.data == {
            "preview_mode": True,
            "thread_id": "thread-1",
            "body_length": len("Synthetic reply"),
            "reply_all": False,
        }


# ---------------------------------------------------------------------------
# execute() tests
# ---------------------------------------------------------------------------

class TestExecute:
    def test_execute_sends_message(self):
        p = _make_proposal(parameters={
            "to": "a@b.com",
            "subject": "Hi",
            "body": "Hello",
            "draft_first": False,
        })
        mock_service = MagicMock()
        mock_service.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": "sender@example.com"
        }
        mock_service.users.return_value.messages.return_value.send.return_value.execute.return_value = {
            "id": "msg123",
            "threadId": "thread456",
        }

        with patch("actions.email_send.build_service", return_value=mock_service):
            result = email_send_handler.execute(p)

        assert result.status == "success"
        assert result.data.get("message_id") == "msg123"

    def test_execute_with_draft_id_sends_draft(self):
        p = _make_proposal(parameters={
            "to": "a@b.com",
            "subject": "Hi",
            "body": "Hello",
            "draft_id": "draft123",
        })
        mock_service = MagicMock()
        mock_service.users.return_value.drafts.return_value.send.return_value.execute.return_value = {
            "id": "msg456",
            "threadId": "thread789",
        }

        with patch("actions.email_send.build_service", return_value=mock_service):
            result = email_send_handler.execute(p)

        assert result.status == "success"
        assert result.data.get("message_id") == "msg456"

    def test_execute_api_error_returns_failure(self):
        p = _make_proposal(parameters={
            "to": "a@b.com",
            "subject": "Hi",
            "body": "Hello",
        })
        with patch("actions.email_send.build_service", side_effect=Exception("quota exceeded")):
            result = email_send_handler.execute(p)
        assert result.status == "failure"
        assert "could not start" in result.message

    def test_execute_timeout_after_dispatch_returns_unknown(self):
        p = _make_proposal(parameters={
            "to": "a@b.com",
            "subject": "Hi",
            "body": "Hello",
            "draft_first": False,
        })
        mock_service = MagicMock()
        mock_service.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": "sender@example.com"
        }
        mock_service.users.return_value.messages.return_value.send.return_value.execute.side_effect = TimeoutError(
            "synthetic timeout"
        )

        with patch("actions.email_send.build_service", return_value=mock_service):
            result = email_send_handler.execute(p)

        assert result.status == "unknown"
        assert "reconcile" in result.message.lower()
        assert "synthetic timeout" not in result.message

    def test_execute_missing_provider_receipt_returns_unknown(self):
        p = _make_proposal(parameters={
            "to": "a@b.com",
            "subject": "Hi",
            "body": "Hello",
            "draft_first": False,
        })
        mock_service = MagicMock()
        mock_service.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": "sender@example.com"
        }
        mock_service.users.return_value.messages.return_value.send.return_value.execute.return_value = {}

        with patch("actions.email_send.build_service", return_value=mock_service):
            result = email_send_handler.execute(p)

        assert result.status == "unknown"


# ---------------------------------------------------------------------------
# build_reverse_proposal() tests
# ---------------------------------------------------------------------------

class TestBuildReverseProposal:
    def test_build_reverse_proposal_creates_trash_action(self):
        p = _make_proposal()
        result_data = {"message_id": "msg123", "to": "a@b.com"}
        reverse = email_send_handler.build_reverse_proposal(p, result_data)
        assert reverse.action_type == "email_send_undo"
        assert reverse.parameters.get("message_id") == "msg123"
        assert reverse.parameters.get("action") == "trash"

    def test_build_reverse_proposal_missing_message_id_raises(self):
        p = _make_proposal()
        with pytest.raises(ValueError, match="message_id"):
            email_send_handler.build_reverse_proposal(p, {})


# ---------------------------------------------------------------------------
# health_check() tests
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_check_returns_true_when_credentials_available(self):
        with patch(
            "actions.email_send.check_stored_credentials",
            return_value={"gmail_token_stored": True},
        ):
            assert email_send_handler.health_check() is True

    def test_health_check_returns_false_when_no_token(self):
        with patch(
            "actions.email_send.check_stored_credentials",
            return_value={"gmail_token_stored": False},
        ):
            assert email_send_handler.health_check() is False

    def test_health_check_returns_false_on_import_error(self):
        with patch("actions.email_send.check_stored_credentials", side_effect=ImportError):
            assert email_send_handler.health_check() is False
