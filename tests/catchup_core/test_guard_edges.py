"""Error paths on the write, restore, scorecard and encrypted-read boundaries."""
from __future__ import annotations

import io
import json
import sys
import types
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from catchup_core import adapters as ad
from catchup_core import backup, cli, commands, crypto, curated, ids, ledger as lg, views
from catchup_core.contracts import Confidentiality
from catchup_core.scorecard import Case, score
from catchup_core.blobs import EncryptedBlobStore
from lib.connector_coverage import report_scope  # noqa: F401  (import path parity with other tests)

KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"7" * 32}))
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def test_scorecard_explains_every_failure():
    card = score([])
    assert not card.passes and card.failures() == ["no evaluated cases"]
    bad = score([Case("c", frozenset({"a"}), frozenset(), quiet_claim=True, citations_resolve=False,
                      incumbent_surfaced=frozenset({"a"}))])
    text = " ".join(bad.failures())
    assert "false quiet" in text and "unresolved citations" in text and "incumbent" in text and "recall" in text


def test_encrypted_state_is_read_in_memory_through_the_vault(tmp_path, monkeypatch):
    (tmp_path / "goals.md.age").write_bytes(b"cipher")
    seen = []
    fake = types.SimpleNamespace(get_age_vault=lambda: types.SimpleNamespace(read=lambda n: seen.append(n) or "---\ngoals: []\n---\n"))
    monkeypatch.setitem(sys.modules, "foundation", fake)
    assert curated.read_state(tmp_path, "goals").startswith("---") and seen == ["goals"]
    assert curated.records("[1, 2]", "goals") == []


@pytest.fixture
def led(tmp_path):
    ledger = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER).open()
    ledger.migrate()
    yield ledger
    ledger.close()


def test_feedback_refuses_unknown_and_evidence_free_alerts(led):
    with pytest.raises(ValueError, match="unknown Alert"):
        views.feedback(led, "owner", "nope", "useful", "x", NOW)
    led.upsert_alert("k", domain="kids_school", primary_subject_id="kid1", severity="P2")
    with pytest.raises(lg.PermissionDenied):
        views.feedback(led, "owner", "k", "useful", "x", NOW)


def test_acknowledge_marks_the_alert(led):
    fid = led.put_fact(lg.FactInput(domain="kids_school", predicate="test_date", value="2026-10-14",
                                    observed_at="t", primary_subject_id="kid1"))
    led.upsert_alert("k", domain="kids_school", primary_subject_id="kid1", severity="P2", fact_ids=[fid])
    views.feedback(led, "owner", "k", "acknowledge", "seen", NOW)
    assert led.get_alert("k")["status"] == "acknowledged"


def test_export_and_capture_need_the_encryption_key(tmp_path):
    keyless = lg.Ledger(tmp_path / "k.db", id_key=KEY).open()
    keyless.migrate()
    try:
        deps = types.SimpleNamespace(ledger=keyless, viewer="owner", blobs=types.SimpleNamespace(_root=tmp_path),
                                     now=lambda: NOW)
        for name, extra in (("export", {"path": str(tmp_path / "x")}), ("capture", {"text": "t"})):
            with pytest.raises(lg.LedgerError, match="encryption key"):
                commands.execute(Namespace(command=name, **extra), deps, tmp_path)
        fid = keyless.put_fact(lg.FactInput(domain="kids_school", predicate="test_date", value="2026-10-14",
                                            observed_at="t", primary_subject_id="kid1",
                                            confidentiality=Confidentiality.ORDINARY))
        keyless.upsert_alert("k", domain="kids_school", primary_subject_id="kid1", severity="P2", fact_ids=[fid])
        with pytest.raises(ValueError, match="requires encryption"):
            views.feedback(keyless, "owner", "k", "useful", "x", NOW)
    finally:
        keyless.close()


def test_replay_case_requires_encryption(tmp_path):
    keyless = lg.Ledger(tmp_path / "k.db", id_key=KEY).open()
    keyless.migrate()
    try:
        with pytest.raises(ValueError, match="require encryption"):
            views.replay_case(keyless, EncryptedBlobStore(tmp_path / "b", CIPHER, KEY), "owner", "k", "useful", "x",
                              "fbk_1", NOW, tmp_path / "r")
    finally:
        keyless.close()


def test_restore_refuses_a_repository_destination_and_a_mismatched_actor(tmp_path, monkeypatch):
    root = Path(cli.__file__).resolve().parents[2]
    monkeypatch.setattr("catchup_core.runtime.load_runtime_config", lambda _p: types.SimpleNamespace(viewer="owner"))
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["restore", str(tmp_path / "s.ae1"), str(root / "restored")], out=out, err=err) == 20
    assert "outside the repository" in err.getvalue()
    err = io.StringIO()
    assert cli.main(["restore", "--actor", "intruder", str(tmp_path / "s.ae1"), str(tmp_path / "d")], out=out, err=err) == 20
    assert "actor" in err.getvalue()


def test_atomic_bytes_leaves_no_temp_file_when_replace_fails(tmp_path, monkeypatch):
    def refuse(_a, _b):
        raise OSError("cross-device")
    monkeypatch.setattr(backup.os, "replace", refuse)
    with pytest.raises(OSError):
        backup.atomic_bytes(tmp_path / "x.ae1", b"cipher")
    assert list(tmp_path.iterdir()) == []


def test_calendar_attachment_marks_the_record_incomplete():
    raw = {"id": "m1", "source": "gmail", "date_iso": "2026-10-11", "subject": "s", "body": "b",
           "attachments": [{"name": "form.pdf"}]}
    got = ad.fetch_via_handler("school_email", "acct", lambda **_: iter([raw]), since="2026-10-01")
    assert got.records[0].incomplete and any("attachment" in b for b in got.blind_spots)


def test_default_cutover_flag_follows_environment_and_authoritative_runs_stay_disabled(monkeypatch):
    from catchup_core.contracts import CatchupRequest, ExecutionMode
    monkeypatch.setenv("ARTHA_CUTOVER", "0")
    with pytest.raises(cli.NotConfigured, match="disabled until the pilot is cut over"):
        cli._default_factory(CatchupRequest("cu_x", "owner", "cli", mode=ExecutionMode.AUTHORITATIVE, host="h"))


def test_bundle_enroll_command_prints_the_secret_once_and_export_demands_configuration(monkeypatch):
    store = {}
    fake = types.SimpleNamespace(get_password=lambda s, n: store.get((s, n)),
                                 set_password=lambda s, n, v: store.__setitem__((s, n), v))
    monkeypatch.setitem(sys.modules, "keyring", fake)
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["bundle-enroll", "win-host"], out=out, err=err) == 0
    shown = json.loads(out.getvalue())
    assert shown["enrolled"] and len(shown["secret_to_copy_once"]) >= 40 and "--secret" in shown["next"]
    out2 = io.StringIO()
    assert cli.main(["bundle-enroll", "win-host", "--secret", shown["secret_to_copy_once"]], out=out2, err=err) == 0
    assert "secret_to_copy_once" not in json.loads(out2.getvalue())
    monkeypatch.setattr("catchup_core.runtime.load_runtime_config",
                        lambda _p: types.SimpleNamespace(policy={}, sources={}))
    err3 = io.StringIO()
    assert cli.main(["bundle-export", "school_email"], out=io.StringIO(), err=err3) == 20
    assert "needs both a 'sources' entry and a 'bundles' entry" in err3.getvalue()
    bad = io.StringIO()
    assert cli.main(["bundle-enroll", "x", "--secret", "c2hvcnQ="], out=io.StringIO(), err=bad) == 20


def test_reconcile_and_resolve_commands_delegate_to_the_executor_with_the_local_viewer(tmp_path, monkeypatch):
    calls = []

    class FakeExecutor:
        def __init__(self, root):
            calls.append(("init", root))

        def reconcile(self, action_id):
            calls.append(("reconcile", action_id))
            return types.SimpleNamespace(status="unknown", message="Still unknown")

        def resolve_unknown(self, action_id, outcome, by):
            calls.append(("resolve", action_id, outcome, by))
            return types.SimpleNamespace(status="success", message="Resolved")

    monkeypatch.setitem(sys.modules, "action_executor", types.SimpleNamespace(ActionExecutor=FakeExecutor))
    deps = types.SimpleNamespace(ledger=None, viewer="owner")
    shown = commands.execute(Namespace(command="reconcile", action_id="a1"), deps, tmp_path)
    assert shown == {"action": "a1", "status": "unknown", "message": "Still unknown"}
    commands.execute(Namespace(command="resolve", action_id="a1", outcome="failed"), deps, tmp_path)
    assert ("resolve", "a1", "failed", "owner") in calls


def test_bundle_export_publishes_only_after_authorising_the_source_and_closes_storage(tmp_path, monkeypatch):
    from catchup_core.bundles import ProducerKey, import_bundles
    from catchup_core.contracts import CoverageStatus
    from catchup_core.coordinator import ConnectorFetch, ConnectorRecord
    key = ProducerKey("win-host", b"w" * 32)
    kr = types.SimpleNamespace(get_password=lambda s, n: __import__("base64").b64encode(b"w" * 32).decode())
    monkeypatch.setitem(sys.modules, "keyring", kr)
    closed, authorised = [], []
    ledger = types.SimpleNamespace(_consent_subjects=frozenset({"adult"}), close=lambda: closed.append(True))
    config = types.SimpleNamespace(
        viewer="owner", sources={"school_email": {"connector": "gmail", "account_scope": "school"}},
        policy={"bundles": {"school_email": {"producer": "win-host", "dir": str(tmp_path), "recipient": "mac",
                                             "account_scope": "school"}}}, subjects=[], consent_required=frozenset())
    monkeypatch.setattr("catchup_core.runtime.load_runtime_config", lambda _p: config)
    monkeypatch.setattr("catchup_core.runtime.build_deps",
                        lambda *_a, **_k: types.SimpleNamespace(ledger=ledger, subjects=["owner", "adult"]))
    monkeypatch.setattr("catchup_core.runtime._default_loaders", lambda: (None, None))
    fetch = ConnectorFetch("school_email", "school", (ConnectorRecord("m1", "hello"),), CoverageStatus.COMPLETE)
    monkeypatch.setattr("catchup_core.runtime.build_fetchers", lambda *_a, **_k: {"school_email": lambda: fetch})
    monkeypatch.setattr("catchup_core.policy.authorize_source", lambda *a, **k: authorised.append((a[3], k)))
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["bundle-export", "school_email"], out=out, err=err) == 0, err.getvalue()
    assert json.loads(out.getvalue())["records"] == 1 and closed == [True]
    assert authorised[0][0] == "school_email:school" and authorised[0][1]["operations"] == ("ingest", "export")
    imported = import_bundles(tmp_path, {"win-host": key}, name="school_email", namespace="personal", recipient="mac",
                              account_scope="school", authority_generation=1, now=datetime.now(timezone.utc))
    assert [r.text for r in imported.fetch.records] == ["hello"]


def test_everything_flag_means_the_deep_format_and_wins_over_an_explicit_format():
    args = cli.parse_args(["catchup", "--shadow", "--everything", "--format", "flash"])
    assert args.everything is True
    seen = []

    def factory(request):
        seen.append(request.briefing_format.value)
        raise cli.NotConfigured("stop here")
    cli.main(["catchup", "--shadow", "--everything", "--format", "flash"], deps_factory=factory,
             out=io.StringIO(), err=io.StringIO())
    assert seen == ["deep"]
