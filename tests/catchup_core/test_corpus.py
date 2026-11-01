from __future__ import annotations

from datetime import UTC, datetime

import pytest
from test_coordinator import CIPHER, KEY

from catchup_core import corpus
from catchup_core import ledger as lg

NOW = datetime(2026, 10, 21, tzinfo=UTC)


def case(case_id="case_a", surfaced=(), ledger=None):
    signal_id = 'sig_a'
    if ledger is not None:
        signal_id, _ = ledger.upsert_signal(lg.SignalInput('school_email', 'acct', case_id, 'v1',
                                           NOW.isoformat(), case_id, 'blob'))
        ledger.set_signal_subjects(signal_id, ['owner'])
    return {"case_id": case_id, "domain": "kids_school", "signal_id": signal_id, "excerpt": "Exam Friday",
            "core_surfaced": list(surfaced)}


def test_a_positive_label_needs_severity_and_the_obligation_it_should_have_surfaced():
    with pytest.raises(corpus.CorpusError, match="severity"):
        corpus.validate({"needs_alert": "yes", "expected": ["k"]})
    with pytest.raises(corpus.CorpusError, match="obligation"):
        corpus.validate({"needs_alert": "yes", "severity": "P1"})
    with pytest.raises(corpus.CorpusError):
        corpus.validate({"needs_alert": "yes", "severity": "P1", "expected": ["k"], "case_type": "negative"})
    ok = corpus.validate({"needs_alert": "yes", "severity": "P2", "expected": ["b", "a"]})
    assert ok["expected"] == ["a", "b"] and ok["case_type"] == "positive"


def test_a_negative_label_expects_nothing_and_unknown_answers_are_refused():
    assert corpus.validate({"needs_alert": "no"})["case_type"] == "negative"
    with pytest.raises(corpus.CorpusError):
        corpus.validate({"needs_alert": "no", "expected": ["k"]})
    with pytest.raises(corpus.CorpusError):
        corpus.validate({"needs_alert": "maybe"})
    with pytest.raises(corpus.CorpusError):
        corpus.validate({"needs_alert": "no", "case_type": "weird"})


def test_labels_are_stored_encrypted_and_unsure_never_reaches_the_gate(tmp_path):
    with lg.Ledger(tmp_path / "runtime" / "ledger.db", id_key=KEY, cipher=CIPHER,
                   known_subjects=frozenset({"owner"})).open() as led:
        led.migrate()
        corpus.record_label(led, case("case_a", ledger=led), {"needs_alert": "no", "note": "weekly bulletin"}, NOW)
        corpus.record_label(led, case("case_b", ledger=led), {"needs_alert": "unsure"}, NOW)
        corpus.record_label(led, case("case_c", ["exam"], ledger=led), {"needs_alert": "yes", "severity": "P1", "expected": ["exam"]}, NOW)
        saved = (tmp_path / "runtime" / "corpus" / "case_a.ae1").read_bytes()
        assert b"weekly bulletin" not in saved and b"Exam Friday" not in saved
        labels = corpus.load_labels(led)
        assert [l["case_id"] for l in labels] == ["case_a", "case_b", "case_c"]
        assert corpus.evaluations(labels) == []  # labels alone never establish implementation outcomes
        assert corpus.labeled(led, "case_a") and not corpus.labeled(led, "case_z")
        (tmp_path / "runtime" / "corpus" / "case_a.ae1").write_bytes(b"torn")
        with pytest.raises(corpus.CorpusError):
            corpus.load_labels(led)


def test_gaps_name_exactly_what_the_release_gate_still_lacks():
    empty = corpus.gaps([])
    assert empty["missing_for_gate"] == ["reviewed positive P1 outcome", "reviewed positive P2 outcome",
        "reviewed source-negative outcome", "2 more cross-Domain outcome(s)", "correction-sensitive Goal outcome"]
    labels = [
        {"reviewed": True, "case_type": "negative", "source_negative_reviewed": True, "expected": []},
        {"reviewed": True, "case_type": "positive", "severity": "P1", "expected": ["a"]},
        {"reviewed": False, "case_type": "positive", "severity": "P2", "expected": ["b"]},
    ]
    got = corpus.gaps(labels)
    assert got["unsure"] == 1 and got["source_negative"] == 1
    assert "reviewed positive P2 outcome" in got["missing_for_gate"] and "reviewed positive P1 outcome" not in got["missing_for_gate"]


def test_an_excerpt_drops_links_comments_and_is_bounded():
    text = "Hello [click](https://x.example/a?token=1) <!--[if mso]-->" + "word " * 300
    out = corpus._excerpt(text)
    assert "http" not in out and "<!--" not in out and len(out) <= corpus.EXCERPT_CHARS


def test_prefill_lists_one_case_per_signal_with_what_the_core_surfaced(tmp_path):
    with lg.Ledger(tmp_path / "runtime" / "ledger.db", id_key=KEY, cipher=CIPHER,
                   known_subjects=frozenset({"owner"})).open() as led:
        led.migrate()
        assert corpus.prefill(led, lambda ref: "x", "school_email", "kids_school") == []


def test_prefill_shows_the_obligations_the_core_surfaced_from_each_signal(tmp_path):
    from catchup_core.contracts import FactStatus, Provenance  # noqa: PLC0415
    with lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open() as led:
        led.migrate()
        sid, _ = led.upsert_signal(lg.SignalInput("school_email", "acct", "u1", "v1", "t", "h1", "blob1"))
        quiet, _ = led.upsert_signal(lg.SignalInput("school_email", "acct", "u2", "v1", "t", "h2", "blob2"))
        led.set_signal_subjects(sid, ["kid1"])
        led.set_signal_subjects(quiet, ["kid1"])
        led.put_fact(lg.FactInput(domain="kids_school", predicate="test_date", value="2026-10-30",
                                  primary_subject_id="kid1", observed_at="t", status=FactStatus.CURRENT,
                                  provenance=Provenance.VERIFIED_SOURCE, evidence=((sid, 0, 4),)))
        cases = corpus.prefill(led, lambda ref: f"Subject {ref}\nbody [link](https://x.example)", "school_email",
                               "kids_school")
        by_signal = {c["signal_id"]: c for c in cases}
        assert by_signal[sid]["core_surfaced"] == []  # a Fact without returned Alert evidence is not surfaced
        assert by_signal[quiet]["core_surfaced"] == [] and "http" not in by_signal[quiet]["excerpt"]
        assert by_signal[sid]["labeled"] is False
        corpus.record_label(led, by_signal[sid], {"needs_alert": "no"}, NOW)
        assert [c["labeled"] for c in corpus.prefill(led, lambda r: "x", "school_email", "kids_school")
                if c["signal_id"] == sid] == [True]


def test_the_cli_parses_a_label_and_reports_errors_on_stderr(monkeypatch):
    import io  # noqa: PLC0415

    from catchup_core import corpus_cli  # noqa: PLC0415
    args = corpus_cli._parse(["label", "case_a", "--needs-alert", "yes", "--severity", "P1", "--expect", "k"])
    assert args.command == "label" and args.expect == ["k"] and args.source == "school_email"
    monkeypatch.setattr(corpus_cli, "_run", lambda a: (_ for _ in ()).throw(corpus.CorpusError("no such case")))
    out, err = io.StringIO(), io.StringIO()
    assert corpus_cli.main(["status"], out, err) == 1 and "no such case" in err.getvalue() and not out.getvalue()
    monkeypatch.setattr(corpus_cli, "_run", lambda a: {"ok": True})
    assert corpus_cli.main(["status"], out, err) == 0 and '"ok": true' in out.getvalue()


def test_the_cli_runs_prefill_label_and_status_against_a_ledger(tmp_path, monkeypatch):
    from types import SimpleNamespace  # noqa: PLC0415

    from catchup_core import corpus_cli, runtime  # noqa: PLC0415
    ledger = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    ledger.migrate()
    sid, _ = ledger.upsert_signal(lg.SignalInput("school_email", "acct", "u1", "v1", "t", "h1", "blob1"))
    ledger.set_signal_subjects(sid, ["owner"])
    from contextlib import nullcontext
    deps = SimpleNamespace(ledger=ledger, blobs=SimpleNamespace(get=lambda ref: "Subject\nbody"),
                           maintenance=nullcontext)
    seen = {}
    monkeypatch.setattr(runtime, "load_runtime_config", lambda path: SimpleNamespace(viewer="ved"))
    monkeypatch.setattr(runtime, "build_deps", lambda request, **kw: seen.update(kw) or deps)
    monkeypatch.setattr(corpus_cli, "_run", corpus_cli._run)            # the real function, with the fakes above
    ledger.close = lambda: None                                         # keep it open across the three commands
    listing = corpus_cli._run(corpus_cli._parse(["prefill", "--unlabeled"]))
    case_id = listing["cases"][0]["case_id"]
    done = corpus_cli._run(corpus_cli._parse(["label", case_id, "--needs-alert", "no"]))
    assert done == {"recorded": case_id, "needs_alert": "no", "reviewed": True} and seen["mutation"] is True
    assert corpus_cli._run(corpus_cli._parse(["prefill", "--unlabeled"]))["cases"] == []
    assert corpus_cli._run(corpus_cli._parse(["status"]))["source_negative"] == 1
    with pytest.raises(corpus.CorpusError, match="no such case"):
        corpus_cli._run(corpus_cli._parse(["label", "case_missing", "--needs-alert", "no"]))
    assert sid
