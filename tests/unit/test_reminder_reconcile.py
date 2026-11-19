"""reminder_create.reconcile: only the provider's presence evidence closes an unknown outcome."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from actions import reminder_create as rc  # noqa: E402

SINCE = "2026-10-20T10:00:00Z"


def proposal(title="Call school"):
    return SimpleNamespace(parameters={"title": title, "list_name": "Artha"})


@pytest.fixture
def provider(monkeypatch):
    state = {"tasks": []}
    monkeypatch.setattr(rc, "_get_token", lambda: "tok")
    monkeypatch.setattr(rc, "_resolve_list_id", lambda _t, _n: "L1")
    def fake(url, **_kw):
        state["url"] = url
        return {"value": state["tasks"]}
    monkeypatch.setattr(rc, "_graph_request", fake)
    return state


def test_exactly_one_task_created_after_approval_confirms_the_effect(provider):
    provider["tasks"] = [{"id": "t1", "createdDateTime": "2026-10-20T10:05:00Z"},
                         {"id": "old", "createdDateTime": "2026-09-01T00:00:00Z"}]
    result = rc.reconcile(proposal("It's due"), since=SINCE)
    assert result.status == "success" and result.data["task_id"] == "t1"
    assert "title eq 'It''s due'" in provider["url"]                      # quotes are escaped in the OData filter


def test_none_or_several_matches_stay_unknown(provider):
    assert rc.reconcile(proposal(), since=SINCE).status == "unknown"
    provider["tasks"] = [{"id": "a", "createdDateTime": "2026-10-20T11:00:00Z"},
                         {"id": "b", "createdDateTime": "2026-10-20T11:01:00Z"}]
    result = rc.reconcile(proposal(), since=SINCE)
    assert result.status == "unknown" and "several" in result.message


def test_a_failed_lookup_is_not_evidence(monkeypatch):
    def boom():
        raise RuntimeError("token expired")
    monkeypatch.setattr(rc, "_get_token", boom)
    result = rc.reconcile(proposal(), since=SINCE)
    assert result.status == "unknown" and "RuntimeError" in result.message


def test_token_comes_from_the_refreshing_loader_and_fails_loudly(monkeypatch):
    import types
    fake_auth = types.SimpleNamespace(load_msgraph_token=lambda: {"access_token": "fresh"})
    monkeypatch.setitem(sys.modules, "lib.auth", fake_auth)
    assert rc._get_token() == "fresh"
    monkeypatch.setitem(sys.modules, "lib.auth", types.SimpleNamespace(load_msgraph_token=lambda: {}))
    with pytest.raises(RuntimeError, match="setup_msgraph_oauth"):
        rc._get_token()


@pytest.mark.parametrize("destination", [
    "https://evil.example/v1.0/me/todo/lists",
    "http://graph.microsoft.com/v1.0/me/todo/lists",
    "https://graph.microsoft.com.evil.example/v1.0/me/todo/lists",
    "https://graph.microsoft.com/v1.0/me/drive",
])
def test_graph_credentials_never_attach_to_unapproved_destination(monkeypatch, destination):
    opener = SimpleNamespace(open=lambda *_args, **_kwargs: pytest.fail("request reached opener"))
    monkeypatch.setattr(rc.urllib.request, "build_opener", lambda *_args: opener)
    with pytest.raises(ValueError, match="Graph destination"):
        rc._graph_request(destination, access_token="synthetic-token")


def test_graph_request_refuses_redirects_before_reusing_authorization():
    handler = rc._NoGraphRedirect()
    assert handler.redirect_request(
        SimpleNamespace(), SimpleNamespace(), 302, "Found", {}, "https://evil.example/receive",
    ) is None


def test_approved_graph_url_uses_one_bounded_nonredirecting_opener(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"value": []}'

    class Opener:
        def open(self, request, *, timeout):
            assert request.full_url == f"{rc._GRAPH_TODO_BASE}?$top=10"
            assert request.get_header("Authorization") == "Bearer synthetic-token"
            assert timeout == 15
            return Response()

    monkeypatch.setattr(rc.urllib.request, "build_opener",
                        lambda handler: Opener() if isinstance(handler, rc._NoGraphRedirect) else None)
    assert rc._graph_request(f"{rc._GRAPH_TODO_BASE}?$top=10",
                             access_token="synthetic-token") == {"value": []}
