"""Runtime helpers that guard archives, backups, replay bundles and the disclosure log."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from catchup_core import crypto, ids
from catchup_core import runtime as rt
from catchup_core.curated import read_state, records

CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"7" * 32}))
NOW = lambda: datetime(2026, 10, 20, 12, 0, tzinfo=UTC)  # noqa: E731


def test_model_log_appends_private_monthly_jsonl_without_content(tmp_path):
    write = rt._model_log_writer(tmp_path, NOW)
    write({"purpose": "extract", "request_id": "evd_1"})
    write({"purpose": "extract", "request_id": "evd_2"})
    path = tmp_path / "model-requests" / "2026-10.jsonl"
    assert [json.loads(line)["request_id"] for line in path.read_text().splitlines()] == ["evd_1", "evd_2"]
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_replay_purge_only_touches_valid_ids(tmp_path):
    (tmp_path / "replay").mkdir()
    keep = tmp_path / "replay" / "keep.ae1"
    gone = tmp_path / "replay" / "fbk_abc123.ae1"
    keep.write_bytes(b"x")
    gone.write_bytes(b"x")
    rt._purge_replay(tmp_path, ["fbk_abc123", "../keep", "keep"])
    assert keep.exists() and not gone.exists()


def test_publisher_is_idempotent_encrypted_and_refuses_conflicts(tmp_path):
    publish = rt.make_file_publisher(tmp_path, NOW, cipher=CIPHER)
    publish("cu_1", "text", 1)
    publish("cu_1", "text", 1)                                         # same text: no-op
    archive = next((tmp_path / "briefings").glob("*-cu_1.v1.ae1"))
    assert b"text" not in archive.read_bytes()
    with pytest.raises(rt.RuntimeConfigError, match="different text"):
        publish("cu_1", "other", 1)
    with pytest.raises(rt.RuntimeConfigError, match="invalid Catch-up archive identity"):
        publish("../evil", "t", 1)
    (tmp_path / "briefings" / "2026-10-19-cu_1.v1.ae1").write_bytes(b"dup")
    with pytest.raises(rt.RuntimeConfigError, match="multiple archives"):
        publish("cu_1", "text", 1)


def test_plaintext_publisher_path_for_unencrypted_deployments(tmp_path):
    publish = rt.make_file_publisher(tmp_path, NOW)
    publish("cu_p", "hello", 1)
    publish("cu_p", "hello", 1)
    assert (tmp_path / "briefings" / "2026-10-20-cu_p.v1.md").read_text() == "hello"
    with pytest.raises(rt.RuntimeConfigError):
        publish("cu_p", "different", 1)


def _db(path: Path) -> Path:
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE t(x)")
        con.execute("INSERT INTO t VALUES (1)")
    return path


def test_migration_backup_is_verified_both_encrypted_and_plain(tmp_path):
    db = _db(tmp_path / "ledger.db")
    encrypted = rt._backup_for(tmp_path, cipher=CIPHER)(db)
    assert encrypted.suffix == ".ae1" and b"SQLite format" not in encrypted.read_bytes()
    plain = rt._backup_for(tmp_path)(db)
    with sqlite3.connect(plain) as con:
        assert con.execute("SELECT x FROM t").fetchone() == (1,)


def test_goals_unavailable_is_stated_not_hidden(tmp_path):
    def broken(_name):
        raise OSError("locked")
    assert rt._goal_lines(tmp_path, reader=broken) == ["Goals unavailable; progress is unknown."]


def test_goal_lines_skip_inactive_and_say_not_reverified(tmp_path):
    text = ("---\ngoals:\n  - {id: G1, title: Save, status: active, next_action: deposit}\n"
            "  - {id: G2, title: Old, status: done}\n---\n")
    lines = rt._goal_lines(tmp_path, reader=lambda _n: text)
    assert len(lines) == 1 and "G1" in lines[0] and "not re-verified" in lines[0]


def test_unknown_pack_in_a_domain_list_fails_closed():
    with pytest.raises(rt.RuntimeConfigError):
        rt._load_packs(("kids_school", "no_such_domain"))
    with pytest.raises(rt.RuntimeConfigError):
        rt._load_packs(("no_such_domain",))


def test_household_binding_replaces_subjects_and_consent(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "user_profile.yaml").write_text(
        "family:\n  primary_user: {name: Pat Owner}\n  spouse: {name: Sam Owner}\n"
        "  children:\n    - {name: Kid One, dob: '2015-05-05'}\n")
    cfg = rt.RuntimeConfig({"s": {"connector": "x", "account_scope": "y"}}, ["owner"], "owner")
    rt._bind_household(cfg, tmp_path, ids.HmacKey(1, b"k" * 32), datetime(2026, 10, 20).date())
    assert cfg.subjects[0] == "owner" and len(cfg.subjects) == 3
    assert len(cfg.consent_required) == 1                           # spouse only; the child is a minor
    (tmp_path / "config" / "user_profile.yaml").write_text("family: {}\n")
    with pytest.raises(rt.RuntimeConfigError, match="household identity"):
        rt._bind_household(cfg, tmp_path, ids.HmacKey(1, b"k" * 32), datetime(2026, 10, 20).date())


def test_curated_reader_rejects_bad_names_and_parses_records(tmp_path):
    with pytest.raises(ValueError):
        read_state(tmp_path, "../etc")
    assert read_state(tmp_path, "goals") == ""
    assert records("", "goals") == []
    (tmp_path / "goals.md").write_text("---\ngoals:\n  g1: {title: A}\n---\n")
    assert records(read_state(tmp_path, "goals"), "goals") == [{"id": "g1", "title": "A"}]
    assert records("---\nplain: 1\n---\n", "goals") == []
    items = "- id: OI-1\n  status: open\n- id: OI-2\n  status: done\n"
    assert [i["id"] for i in records(items, "open_items")] == ["OI-1"]


GOALS = ("---\ngoals:\n"
         "  - {id: G1, title: Save, status: active, category: finance, next_action: deposit, next_action_date: '2026-10-18'}\n"
         "  - {id: G2, title: Fit, status: active, next_action: run, next_action_date: '2026-10-25'}\n"
         "  - {id: G3, title: Old, status: done, category: health}\n---\n")


def test_goal_lines_say_when_the_next_action_is_due_or_overdue(tmp_path):
    from datetime import date
    lines = rt._goal_lines(tmp_path, reader=lambda _n: GOALS, today=date(2026, 10, 20))
    assert "overdue by 2 days" in lines[0] and "due in 5 days" in lines[1]
    assert rt._goal_lines(tmp_path, reader=lambda _n: GOALS, today=date(2026, 10, 18))[0].count("due today") == 1
    bad = GOALS.replace("2026-10-25", "soon")
    assert "date unreadable" in rt._goal_lines(tmp_path, reader=lambda _n: bad, today=date(2026, 10, 20))[1]


def test_goal_domains_are_active_categories_only_and_failure_means_none(tmp_path):
    assert rt._goal_domains(tmp_path, reader=lambda _n: GOALS) == {"finance"}

    def broken(_n):
        raise OSError("locked")
    assert rt._goal_domains(tmp_path, reader=broken) == set()


def test_bundle_sources_import_through_the_runtime_and_unenrolled_producers_are_unavailable(tmp_path):
    from catchup_core.bundles import ProducerKey, export_bundle
    from catchup_core.contracts import CoverageStatus
    from catchup_core.coordinator import ConnectorRecord
    key = ProducerKey("win-host", b"w" * 32)
    export_bundle(tmp_path, key, namespace="personal", recipient="mac", account_scope="school", authority_generation=1,
                  sequence=1, records=[ConnectorRecord("m1", "text")], requested_since="2026-10-01T00:00:00Z",
                  completed_until="2026-10-20T10:00:00+00:00", created_at=NOW())

    class Keyring:
        def __init__(self, secret):
            self.secret = secret

        def get_password(self, _s, _n):
            import base64
            return base64.b64encode(self.secret).decode() if self.secret else None

    class Ledger:
        class conn:                                           # noqa: N801 - minimal stand-in
            @staticmethod
            def execute(_sql):
                return types_namespace(fetchone=lambda: (None,))

    def types_namespace(**kw):
        import types
        return types.SimpleNamespace(**kw)

    cfg = types_namespace(policy={"bundles": {"school_email": {
        "producer": "win-host", "dir": str(tmp_path), "recipient": "mac", "account_scope": "school"}}})
    fetchers = rt._bundle_fetchers(cfg, Ledger(), Keyring(b"w" * 32), {"school_email": True}, NOW)
    got = fetchers["school_email"]()
    assert got.status is CoverageStatus.COMPLETE and [r.text for r in got.records] == ["text"]
    missing = rt._bundle_fetchers(cfg, Ledger(), Keyring(None), {"school_email": True}, NOW)["school_email"]()
    assert missing.status is CoverageStatus.UNAVAILABLE and "not enrolled" in missing.blind_spots[0]
    with pytest.raises(rt.RuntimeConfigError):
        rt._bundle_fetchers(types_namespace(policy={"bundles": ["x"]}), Ledger(), Keyring(None), {}, NOW)
    assert rt._bundle_fetchers(types_namespace(policy={}), Ledger(), Keyring(None), {}, NOW) == {}
