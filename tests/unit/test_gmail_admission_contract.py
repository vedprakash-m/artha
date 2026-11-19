from __future__ import annotations

import base64
import os
import sys
import time
import types
from unittest.mock import MagicMock

import pytest

from connectors import google_email


def _service(monkeypatch, message):
    service = MagicMock()
    service.users().messages().get().execute.return_value = message
    module = types.ModuleType("google_auth")
    module.build_service = lambda *args, **kwargs: service
    monkeypatch.setitem(sys.modules, "google_auth", module)
    service.users().messages().get.reset_mock()
    return service


def test_message_metadata_uses_metadata_only_and_preserves_provider_identity(monkeypatch):
    service = _service(monkeypatch, {
        "id": "msg-1",
        "threadId": "thread-1",
        "historyId": "7",
        "sizeEstimate": 12,
        "labelIds": ["INBOX"],
        "payload": {"headers": [
            {"name": "From", "value": "Sender <sender@example.invalid>"},
            {"name": "To", "value": "owner@example.invalid"},
        ]},
    })

    result = google_email.message_metadata({}, "msg-1")

    assert result["id"] == "msg-1"
    assert result["thread_id"] == "thread-1"
    assert result["from"] == "Sender <sender@example.invalid>"
    assert result["labels"] == ["INBOX"]
    service.users().messages().get.assert_called_once()
    assert service.users().messages().get.call_args.kwargs["format"] == "metadata"
    assert "raw" not in str(service.users().messages().get.call_args)


def test_raw_message_uses_raw_only_and_rejects_changed_identity(monkeypatch):
    encoded = base64.urlsafe_b64encode(b"synthetic MIME").decode().rstrip("=")
    service = _service(monkeypatch, {"id": "different", "raw": encoded})

    with pytest.raises(ValueError, match="identity changed"):
        google_email.raw_message({}, "msg-1")
    assert service.users().messages().get.call_args.kwargs["format"] == "raw"


def test_list_message_ids_rejects_repeated_continuation_token(monkeypatch):
    service = MagicMock()
    service.users().messages().list().execute.side_effect = [
        {"messages": [{"id": "one"}], "nextPageToken": "same"},
        {"messages": [{"id": "two"}], "nextPageToken": "same"},
    ]
    module = types.ModuleType("google_auth")
    module.build_service = lambda *args, **kwargs: service
    monkeypatch.setitem(sys.modules, "google_auth", module)

    with pytest.raises(ValueError, match="continuation token"):
        google_email.list_message_ids(
            {}, query="from:sender@example.invalid", since="2026-01-01", max_results=10
        )


def test_list_message_ids_stops_at_result_bound(monkeypatch):
    service = MagicMock()
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": "one"}, {"id": "two"}],
        "nextPageToken": "unused",
    }
    service.users().getProfile().execute.return_value = {"historyId": "99"}
    service.users().messages().list.reset_mock()
    service.users().getProfile.reset_mock()
    module = types.ModuleType("google_auth")
    module.build_service = lambda *args, **kwargs: service
    monkeypatch.setitem(sys.modules, "google_auth", module)

    listing = google_email.list_message_ids(
        {}, query="from:sender@example.invalid", since="2026-01-01", max_results=1
    )

    ids_, cursor = listing
    assert ids_ == ["one"]
    assert cursor is None
    assert listing.complete is False
    assert listing.truncation_reason == "max_results"
    assert service.users().messages().list.call_count == 1


def test_list_message_ids_at_result_bound_is_complete_without_continuation(monkeypatch):
    service = MagicMock()
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": "one"}],
    }
    service.users().getProfile().execute.return_value = {"historyId": "99"}
    module = types.ModuleType("google_auth")
    module.build_service = lambda *args, **kwargs: service
    monkeypatch.setitem(sys.modules, "google_auth", module)

    listing = google_email.list_message_ids(
        {}, query="from:sender@example.invalid", since="2026-01-01", max_results=1)

    assert listing.message_ids == ["one"]
    assert listing.complete is True
    assert listing.cursor == "99"
    assert listing.truncation_reason is None


def test_execute_request_terminates_worker_before_timeout_returns(monkeypatch):
    command = [
        sys.executable,
        "-c",
        "import os,sys,time; "
        "print(os.getpid(), file=sys.stderr, flush=True); "
        "time.sleep(10)",
    ]
    monkeypatch.setattr(
        google_email, "_request_worker_command", lambda: command, raising=False)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="Gmail admission") as raised:
        google_email.list_message_ids(
            {}, query="from:sender@example.invalid", since="2026-01-01",
            max_results=10, deadline=started + 1.5,
        )

    assert time.monotonic() - started < 4.0
    timeout = raised.value.__cause__
    assert timeout is not None
    worker_pid = int(bytes(timeout.stderr).strip())
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel.OpenProcess(0x00100000, False, worker_pid)
        if handle:
            running = kernel.WaitForSingleObject(handle, 0) == 0x00000102
            kernel.CloseHandle(handle)
            assert running is False
        else:
            assert ctypes.get_last_error() == 87
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
