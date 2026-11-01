"""Scoped interchange, custodian view and reviewed configuration over synthetic encrypted storage."""
from __future__ import annotations

import json
from argparse import Namespace

import pytest

from catchup_core import interchange, ledger as lg, user_config
from test_user_surfaces import CIPHER, KEY

T = "2026-10-12T00:00:00Z"


@pytest.fixture
def store(tmp_path):
    led = lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER,
                    consent_subjects=frozenset({"adult"})).open()
    led.migrate()
    yield led
    led.close()


def put(store, subject, domain, value, **kw):
    return store.put_fact(lg.FactInput(domain=domain, predicate="note", value=value, observed_at=T,
                                       primary_subject_id=subject, **kw))


def test_interchange_contains_only_exportable_facts_and_counts_the_rest(store, tmp_path):
    open_fact = put(store, "kid1", "kids_school", "OPEN-VALUE")
    put(store, "adult", "health", "VIEW-ONLY-SECRET")
    put(store, "adult", "finance", "NO-CONSENT-SECRET")
    store.grant_consent("adult", "adult", "health", ["view"], "owner")
    target = tmp_path / "out.ae1"
    summary = interchange.write_interchange(target, store, "owner")
    assert summary == {"exported_facts": 1, "omitted_facts": 2, "format": "artha.interchange.v1",
                       "dispatchable": False}
    assert b"OPEN-VALUE" not in target.read_bytes()
    document = interchange.read_interchange(target, CIPHER)
    assert [f["fact_id"] for f in document["facts"]] == [open_fact]
    assert "SECRET" not in json.dumps(document) and document["omitted_facts"] == 2


def test_adding_export_consent_widens_only_that_scope_and_revocation_removes_it(store, tmp_path):
    put(store, "adult", "health", "H"), put(store, "adult", "finance", "F")
    store.grant_consent("adult", "adult", "health", ["view"], "owner")
    store.grant_consent("adult", "adult", "finance", ["view"], "owner")
    grant = store.grant_consent("adult", "adult", "health", ["export"], "owner")
    first = interchange.build_interchange(store, "owner")
    assert [f["domain"] for f in first["facts"]] == ["health"]
    store.revoke_consent(grant)
    assert interchange.build_interchange(store, "owner")["facts"] == []


def test_interchange_carries_corrections_and_unresolved_states_but_no_grants_or_actions(store, tmp_path):
    fid = put(store, "kid1", "kids_school", "2026-10-14")
    old = store.get_fact(fid, "owner")
    from catchup_core import views
    views.correct(store, "owner", fid, "2026-10-16", old.recorded_at)
    unknown = put(store, "kid1", "kids_school", "maybe", status=lg.FactStatus.UNKNOWN)
    store.grant_consent("adult", "adult", "kids_school", ["view", "export"], "owner")
    document = interchange.build_interchange(store, "owner")
    by_id = {f["fact_id"]: f for f in document["facts"]}
    assert by_id[fid]["value"] == "2026-10-16" and by_id[fid]["provenance"] == "user_confirmed"
    assert [v["value"] for v in by_id[fid]["versions"]] == ["2026-10-14", "2026-10-16"]
    assert by_id[unknown]["status"] == "unknown"
    assert {"consent", "actions", "credentials", "keys"} <= set(document["excluded"])
    assert not {"consent", "actions", "credentials", "keys"} & set(document)


@pytest.mark.parametrize("mutation", ["schema", "grant", "action", "dispatchable", "garbage", "wrong_key"])
def test_import_reads_but_never_accepts_authority_or_foreign_envelopes(store, tmp_path, mutation):
    put(store, "kid1", "kids_school", "v")
    target = tmp_path / "x.ae1"
    interchange.write_interchange(target, store, "owner")
    document = interchange.read_interchange(target, CIPHER)
    if mutation == "garbage":
        target.write_bytes(b"not ciphertext")
    elif mutation == "wrong_key":
        from catchup_core import crypto
        other = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"9" * 32}))
        with pytest.raises(Exception):
            interchange.read_interchange(target, other)
        return
    else:
        change = {"schema": {"schema": "artha.other.v9"}, "grant": {"consent": [{"audience": "*"}]},
                  "action": {"actions": [{"id": "a"}]}, "dispatchable": {"dispatchable": True}}[mutation]
        target.write_bytes(CIPHER.encrypt(json.dumps({**document, **change}).encode(), aad=b"artha.interchange.v1")[0])
    with pytest.raises(ValueError):
        interchange.read_interchange(target, CIPHER)


def test_reading_an_interchange_file_writes_nothing_to_the_ledger(store, tmp_path):
    put(store, "kid1", "kids_school", "v")
    target = tmp_path / "x.ae1"
    interchange.write_interchange(target, store, "owner")
    before = store.conn.execute("SELECT count(*) FROM facts").fetchone()[0], store.conn.execute(
        "SELECT count(*) FROM consent").fetchone()[0]
    result = interchange.read_interchange(target, CIPHER)
    assert result["facts"] and (store.conn.execute("SELECT count(*) FROM facts").fetchone()[0],
                                store.conn.execute("SELECT count(*) FROM consent").fetchone()[0]) == before


def test_custodian_sees_only_emergency_consented_facts_and_loses_them_on_revocation(store):
    visible = put(store, "adult", "health", "Allergy: penicillin", viewers=("sibling",))
    put(store, "adult", "finance", "hidden bank detail", viewers=("sibling",))
    put(store, "adult", "health", "not shared with custodian")
    assert interchange.emergency_view(store, "sibling")["facts"] == []
    store.grant_consent("adult", "adult", "health", ["view"], "sibling")
    assert interchange.emergency_view(store, "sibling")["facts"] == []        # view alone is not emergency access
    grant = store.grant_consent("adult", "adult", "health", ["emergency"], "sibling")
    view = interchange.emergency_view(store, "sibling")
    assert [f["fact_id"] for f in view["facts"]] == [visible]
    assert view["facts"][0]["value"] == "Allergy: penicillin" and view["dispatchable"] is False
    assert set(view["facts"][0]) == {"fact_id", "domain", "subject", "predicate", "value", "status", "observed_at"}
    store.revoke_consent(grant)
    assert interchange.emergency_view(store, "sibling")["facts"] == []


def test_emergency_view_expires_with_its_consent(store):
    put(store, "adult", "health", "Allergy", viewers=("sibling",))
    store.grant_consent("adult", "adult", "health", ["view"], "sibling")
    store.grant_consent("adult", "adult", "health", ["emergency"], "sibling", expires_at="2000-01-01T00:00:00Z")
    assert interchange.emergency_view(store, "sibling")["facts"] == []


CONFIG = "# owner notes stay\nsources:\n  s:\n    connector: gmail\n    account_scope: a\nsubjects: [owner]\nbootstrap_days: 30\ncutover: false\n"


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "catchup_core.yaml"
    path.write_text(CONFIG)
    return path


@pytest.mark.parametrize('newline', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_configuration_change_is_previewed_and_not_applied_until_the_reviewed_digest_returns(config, newline):
    config.write_bytes(CONFIG.replace('\n', newline).encode())
    preview = user_config.propose(config, "bootstrap_days", "60")
    assert preview["before"] == 30 and preview["after"] == 60 and preview["applied"] is False
    assert config.read_text() == CONFIG
    result = user_config.apply(config, "bootstrap_days", "60", preview["apply_digest"])
    assert result["applied"] is True
    assert config.read_text() == CONFIG.replace("bootstrap_days: 30", "bootstrap_days: 60")
    assert config.read_bytes() == CONFIG.replace("bootstrap_days: 30", "bootstrap_days: 60").replace(
        '\n', newline).encode()


def test_configuration_apply_refuses_wrong_digest_changed_file_and_changed_value(config):
    preview = user_config.propose(config, "bootstrap_days", "60")
    with pytest.raises(ValueError, match="review"):
        user_config.apply(config, "bootstrap_days", "60", "0" * 64)
    with pytest.raises(ValueError, match="review"):
        user_config.apply(config, "bootstrap_days", "61", preview["apply_digest"])
    config.write_text(CONFIG + "extra: 1\n")
    with pytest.raises(ValueError, match="changed"):
        user_config.apply(config, "bootstrap_days", "60", preview["apply_digest"])


@pytest.mark.parametrize("key", ["cutover", "viewer", "consent_required", "sources", "extractor_cmd", "release",
                                 "subjects", "unknown_key"])
def test_configuration_never_changes_held_or_consent_bearing_settings(config, key):
    with pytest.raises(ValueError, match="held|unknown"):
        user_config.propose(config, key, "true")
    assert config.read_text() == CONFIG


@pytest.mark.parametrize("value", ["0", "-1", "3661", "abc", "1.5", "True", ""])
def test_configuration_values_are_validated_before_preview(config, value):
    with pytest.raises(ValueError):
        user_config.propose(config, "bootstrap_days", value)


def test_missing_setting_is_added_on_its_own_line(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("subjects: [owner]\n")
    preview = user_config.propose(path, "bootstrap_days", "14")
    assert preview["before"] is None
    user_config.apply(path, "bootstrap_days", "14", preview["apply_digest"])
    assert path.read_text() == "subjects: [owner]\nbootstrap_days: 14\n"


def test_configuration_applies_with_commands_router(tmp_path, store):
    from catchup_core import commands
    path = tmp_path / "config"
    path.mkdir()
    (path / "catchup_core.yaml").write_text(CONFIG)
    deps = Namespace(ledger=store, viewer="owner", validate_inputs=lambda: None, mode=None)
    shown = commands.execute(Namespace(command="configure", key="bootstrap_days", value="45", apply=None),
                             deps, tmp_path)
    assert shown["applied"] is False
    done = commands.execute(Namespace(command="configure", key="bootstrap_days", value="45",
                                      apply=shown["apply_digest"]), deps, tmp_path)
    assert done["applied"] is True and "bootstrap_days: 45" in (path / "catchup_core.yaml").read_text()
    assert commands.execute(Namespace(command="emergency"), deps, tmp_path)["facts"] == []
    out = tmp_path / "i.ae1"
    assert commands.execute(Namespace(command="interchange", path=str(out), read=False), deps,
                            tmp_path)["format"] == "artha.interchange.v1"
    assert commands.execute(Namespace(command="interchange", path=str(out), read=True), deps,
                            tmp_path)["executes_nothing"] is True


def test_interchange_needs_the_encryption_key(tmp_path):
    bare = lg.Ledger(tmp_path / "bare.db", id_key=KEY).open()
    bare.migrate()
    with pytest.raises(lg.LedgerError, match="encryption"):
        interchange.write_interchange(tmp_path / "x.ae1", bare, "owner")
    bare.close()


def test_configuration_file_must_be_a_mapping(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("- not\n- a mapping\n")
    with pytest.raises(ValueError, match="mapping"):
        user_config.propose(path, "bootstrap_days", "10")
