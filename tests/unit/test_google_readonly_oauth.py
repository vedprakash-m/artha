"""Read-only setup uses exact scopes without production credentials or requests."""
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from google.oauth2.credentials import Credentials

import google_auth as google
import setup_google_oauth as wizard


READ_SCOPES = {
    "gmail": ["https://www.googleapis.com/auth/gmail.readonly"],
    "calendar": ["https://www.googleapis.com/auth/calendar.readonly"],
}


def credentials(scopes, granted_scopes=None):
    creds = Credentials(
        token="synthetic-access-token",
        scopes=scopes,
        granted_scopes=granted_scopes,
    )
    creds.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
    return creds


@pytest.fixture
def consent(monkeypatch):
    flow = Mock()
    factory = Mock(return_value=flow)
    save = Mock()
    monkeypatch.setattr(google, "_get_client_config", lambda: {"installed": {}})
    monkeypatch.setattr(google.InstalledAppFlow, "from_client_config", factory)
    monkeypatch.setattr(google, "_save_token", save)
    return flow, factory, save


@pytest.mark.parametrize("api", READ_SCOPES)
@pytest.mark.parametrize("read_only", [False, True])
def test_explicit_consent_requests_selected_scopes(consent, api, read_only):
    flow, factory, save = consent
    scopes = READ_SCOPES[api] if read_only else (
        google.GMAIL_SCOPES if api == "gmail" else google.CALENDAR_SCOPES
    )
    creds = credentials(scopes)
    flow.run_local_server.return_value = creds
    getter = google.get_gmail_credentials if api == "gmail" else google.get_calendar_credentials
    assert getter(force_reauth=True, read_only=read_only) is creds
    factory.assert_called_once_with({"installed": {}}, scopes)
    flow.run_local_server.assert_called_once_with(port=0, open_browser=True)
    save.assert_called_once_with(creds, f"{'gmail' if api == 'gmail' else 'gcal'}-oauth-token")


@pytest.mark.parametrize("granted", [False, True])
def test_saved_scope_metadata_records_effective_grant(monkeypatch, granted):
    scopes = READ_SCOPES["gmail"]
    creds = credentials(google.GMAIL_SCOPES if granted else scopes, scopes if granted else None)
    store = Mock()
    monkeypatch.setattr(google, "_credential_store", lambda: store)
    monkeypatch.setattr(google, "_status_set", lambda *args: None)
    google._save_token(creds, "gmail-oauth-token")
    assert store.write.call_args.args[1]["scopes"] == scopes


@pytest.mark.parametrize("api", READ_SCOPES)
def test_missing_readonly_credentials_are_refused_without_refresh(monkeypatch, api):
    refresh = Mock()
    monkeypatch.setattr(google, "_load_token", lambda _: None)
    monkeypatch.setattr(google, "_refresh_existing", refresh)
    with pytest.raises(RuntimeError, match="read-only"):
        google.build_service(api, "v1", read_only=True)
    refresh.assert_not_called()


def test_unfresh_consent_credentials_are_not_saved(consent):
    flow, _, save = consent
    creds = credentials(READ_SCOPES["gmail"])
    creds.expiry = None
    flow.run_local_server.return_value = creds
    with pytest.raises(RuntimeError, match="fresh token"):
        google.get_gmail_credentials(force_reauth=True, read_only=True)
    save.assert_not_called()


@pytest.mark.parametrize("api", READ_SCOPES)
@pytest.mark.parametrize("scope_kind", ["empty", "broad", "other-service"])
@pytest.mark.parametrize("granted", [False, True])
def test_unexpected_new_scopes_are_not_saved(consent, api, scope_kind, granted):
    flow, _, save = consent
    unexpected = {
        "empty": [],
        "broad": READ_SCOPES[api] + ["https://www.googleapis.com/auth/drive.file"],
        "other-service": READ_SCOPES["calendar" if api == "gmail" else "gmail"],
    }[scope_kind]
    flow.run_local_server.return_value = credentials(
        READ_SCOPES[api] if granted else unexpected,
        unexpected if granted else None,
    )
    getter = google.get_gmail_credentials if api == "gmail" else google.get_calendar_credentials
    with pytest.raises(RuntimeError, match="read-only"):
        getter(force_reauth=True, read_only=True)
    save.assert_not_called()


@pytest.mark.parametrize("api", READ_SCOPES)
def test_readonly_cached_broad_token_is_refused_before_refresh(monkeypatch, api):
    refresh = Mock()
    monkeypatch.setattr(google, "_load_token", lambda _: credentials(READ_SCOPES[api] + google.DRIVE_SCOPES))
    monkeypatch.setattr(google, "_refresh_existing", refresh)
    with pytest.raises(RuntimeError, match="read-only"):
        google.build_service(api, "v1", read_only=True)
    refresh.assert_not_called()


@pytest.mark.parametrize("api", READ_SCOPES)
@pytest.mark.parametrize("changed", [False, True])
def test_readonly_cached_token_and_refresh_are_checked(monkeypatch, api, changed):
    creds = credentials(READ_SCOPES[api])
    refreshed = credentials(READ_SCOPES[api] + google.DRIVE_SCOPES) if changed else creds
    monkeypatch.setattr(google, "_load_token", lambda _: creds)
    monkeypatch.setattr(google, "_refresh_existing", lambda _: (refreshed, False))
    build = Mock()
    monkeypatch.setattr(google, "build", build)
    if changed:
        with pytest.raises(RuntimeError, match="read-only"):
            google.build_service(api, "v1", read_only=True)
        build.assert_not_called()
    else:
        assert google.build_service(api, "v1", read_only=True) is build.return_value
        build.assert_called_once_with(api, "v1", credentials=creds, cache_discovery=False)


@pytest.mark.parametrize("api", ["drive", "unknown"])
def test_readonly_unsupported_api_never_reads_credentials(monkeypatch, api):
    read = Mock()
    monkeypatch.setattr(google, "_load_token", read)
    with pytest.raises(ValueError):
        google.build_service(api, "v1", read_only=True)
    read.assert_not_called()


@pytest.mark.parametrize("api", ["gmail", "calendar", "drive"])
def test_default_service_keeps_existing_scopes_and_noninteractive_refresh(monkeypatch, api):
    creds = credentials(google.ALL_SCOPES)
    refresh = Mock(return_value=(creds, False))
    monkeypatch.setattr(google, "_refresh_existing", refresh)
    monkeypatch.setattr(google, "_load_token", lambda _: pytest.fail("extra credential read"))
    build = Mock()
    monkeypatch.setattr(google, "build", build)
    assert google.build_service(api, "v1") is build.return_value
    refresh.assert_called_once()


@pytest.mark.parametrize("read_only", [False, True])
def test_wizard_forwards_scope_selection_to_consent(monkeypatch, read_only):
    build = Mock()
    monkeypatch.setattr(google, "build_service", build)
    wizard._run_oauth_flow("calendar", read_only=read_only)
    build.assert_called_once_with("calendar", "v3", force_reauth=True, read_only=read_only)


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("stored", [False, True])
def test_setup_does_not_skip_readonly_consent_and_preserves_default_skip(monkeypatch, read_only, stored):
    monkeypatch.setattr(google, "check_stored_credentials", lambda: {
        "client_id_stored": True, "client_secret_stored": True,
        "gmail_token_stored": stored, "gcal_token_stored": stored,
    })
    flow = Mock()
    test = Mock(return_value=True)
    monkeypatch.setattr(wizard, "_run_oauth_flow", flow)
    monkeypatch.setattr(wizard, "_test_connections", test)
    wizard.run_setup(read_only=read_only)
    assert flow.call_count == (0 if stored and not read_only else 2)
    if flow.call_count:
        assert [call.args for call in flow.call_args_list] == [("gmail",), ("calendar",)]
        assert all(call.kwargs == {"read_only": read_only} for call in flow.call_args_list)
    test.assert_called_once_with(read_only=read_only)


def test_failed_readonly_connection_never_reports_setup_complete(monkeypatch, capsys):
    monkeypatch.setattr(google, "check_stored_credentials", lambda: {
        "client_id_stored": True, "client_secret_stored": True,
    })
    monkeypatch.setattr(wizard, "_run_oauth_flow", lambda *args, **kwargs: None)
    monkeypatch.setattr(wizard, "_test_connections", lambda **kwargs: False)
    with pytest.raises(SystemExit) as error:
        wizard.run_setup(read_only=True)
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "Setup complete" not in output.out
    assert "failed" in output.err.lower()


@pytest.mark.parametrize("failure", [None, "gmail", "calendar"])
def test_connection_checks_use_readonly_services_and_report_failures(monkeypatch, failure):
    gmail, calendar = Mock(), Mock()
    gmail.users.return_value.getProfile.return_value.execute.return_value = {
        "emailAddress": "synthetic@example.invalid", "messagesTotal": 2,
    }
    calendar.calendarList.return_value.list.return_value.execute.return_value = {"items": []}
    if failure == "gmail":
        gmail.users.return_value.getProfile.return_value.execute.side_effect = RuntimeError("synthetic failure")
    elif failure == "calendar":
        calendar.calendarList.return_value.list.return_value.execute.side_effect = RuntimeError("synthetic failure")
    build = Mock(side_effect=[gmail, calendar])
    monkeypatch.setattr(google, "build_service", build)
    assert wizard._test_connections(read_only=True) is (failure is None)
    assert all(call.kwargs == {"read_only": True} for call in build.call_args_list)


@pytest.mark.parametrize("flags,read_only", [([], False), (["--read-only"], True)])
def test_cli_routes_readonly_mode(monkeypatch, flags, read_only):
    monkeypatch.setattr("sys.argv", ["setup_google_oauth.py", *flags])
    setup = Mock()
    monkeypatch.setattr(wizard, "run_setup", setup)
    wizard.main()
    setup.assert_called_once_with(read_only=read_only)


@pytest.mark.parametrize("flag,helper", [("--status", "show_status"), ("--revoke", "revoke_tokens")])
def test_existing_cli_inspection_and_revoke_routes_are_unchanged(monkeypatch, flag, helper):
    monkeypatch.setattr("sys.argv", ["setup_google_oauth.py", flag])
    action = Mock()
    monkeypatch.setattr(wizard, helper, action)
    monkeypatch.setattr(wizard, "run_setup", lambda **kwargs: pytest.fail("unexpected setup"))
    wizard.main()
    action.assert_called_once_with()
