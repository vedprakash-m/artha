"""Model-neutral extraction exchange: pause, answer from any assistant, resume (D-28)."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import test_coordinator as tc  # noqa: E402

from catchup_core import cli as cli_mod  # noqa: E402
from catchup_core import coordinator as co  # noqa: E402
from catchup_core import exchange as xc  # noqa: E402
from catchup_core.contracts import ExitCode, RunStatus  # noqa: E402

world = tc.world        # share the end-to-end fixture
EXTRACT, cal, email, req = tc.EXTRACT, tc.cal, tc.email, tc.req


def test_request_id_is_a_stable_function_of_the_prompt():
    assert xc.request_id("a") == xc.request_id("a") != xc.request_id("b")
    assert len(xc.request_id("a")) == 16


def test_extractor_answers_only_the_exact_prompt():
    ex = xc.ExchangeExtractor({xc.request_id("p1"): "{}"})
    assert ex("p1") == "{}"
    with pytest.raises(xc.ExtractionPending) as pending:
        ex("p1 altered")
    assert pending.value.request.prompt == "p1 altered"


def test_parse_responses_accepts_both_shapes_and_rejects_the_rest():
    assert xc.parse_responses('{"responses": {"a": "x"}}') == {"a": "x"}
    assert xc.parse_responses('{"a": "x"}') == {"a": "x"}
    for bad in ("not json", "[1]", '{"a": 1}', '{"responses": {"a": {"nested": true}}}'):
        with pytest.raises(ValueError):
            xc.parse_responses(bad)
    with pytest.raises(ValueError):
        xc.parse_responses(json.dumps({str(i): "x" for i in range(xc._MAX_RESPONSES + 1)}))


def test_response_identity_and_cohort_are_checked_separately_from_answer_text():
    import hashlib
    identity = {'provider': 'synthetic', 'model': 'offline', 'revision': '1', 'retention_policy': 'test'}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    expected = {'implementation': 'current', 'model': fingerprint}
    batch = xc.parse_response_document(json.dumps({'responses': {'a': '{}'}, 'model_identity': identity,
                                                   'cohort': expected}))
    assert xc.verify_response_identity(batch, expected, required=True)
    with pytest.raises(ValueError, match='cohort'):
        xc.verify_response_identity(batch, {**expected, 'implementation': 'changed'}, required=True)
    old = xc.parse_response_document('{"responses":{"a":"{}"}}')
    assert xc.verify_response_identity(old, expected, required=False) is False
    with pytest.raises(ValueError, match='identity'):
        xc.verify_response_identity(old, expected, required=True)


def test_response_document_has_a_byte_bound_before_json_parsing(monkeypatch):
    monkeypatch.setattr(xc, '_MAX_DOCUMENT_BYTES', 8)
    with pytest.raises(ValueError, match='size limit'):
        xc.parse_response_document('x' * 9)


def test_run_pauses_then_resumes_with_answers_from_any_model(world):
    ledger, _blobs, make, published = world
    request = req("cu_x")
    fetchers = {"school_email": email(), "calendar": cal()}

    # phase 1: no answers -> the run pauses, nothing is published, no failure is recorded
    with pytest.raises(xc.AwaitingExtraction) as paused:
        co.run_catchup(request, make(fetchers, extractor=xc.ExchangeExtractor()))
    assert published == [] and paused.value.catchup_id == "cu_x"
    assert len(paused.value.requests) == 1 and "<untrusted>" in paused.value.requests[0].prompt
    assert "extract" not in ledger.committed_stages("cu_x")

    # the assistant (any model) answers the printed prompt
    pending = paused.value.requests[0]
    answer = EXTRACT(pending.prompt)

    # phase 2: resume with the same id and the answers -> a normal complete run
    result = co.run_catchup(request, make(fetchers, extractor=xc.ExchangeExtractor({pending.request_id: answer})))
    assert result.status is RunStatus.COMPLETE and len(result.artifact_ids) == 1
    assert published == [("cu_x", 1)]


def test_hostile_answer_is_rejected_by_the_core_not_trusted(world):
    _ledger, _blobs, make, _published = world
    with pytest.raises(xc.AwaitingExtraction) as paused:
        co.run_catchup(req("cu_h"), make({"school_email": email(), "calendar": cal()},
                                         extractor=xc.ExchangeExtractor()))
    pending = paused.value.requests[0]
    forged = json.dumps({"complete": True, "candidates": [{"predicate": "exam_registration_deadline", "value": "2026-10-01",
                                         "subject_id": "kid1", "quote": "this text is not in the message",
                                         "span": [0, 10]}]})
    result = co.run_catchup(req("cu_h"), make({"school_email": email(), "calendar": cal()},
                                              extractor=xc.ExchangeExtractor({pending.request_id: forged})))
    assert not result.artifact_ids                          # fabricated evidence never becomes an Alert
    assert result.status is not RunStatus.COMPLETE          # a rejected answer keeps the run non-quiet


def _factory_for(make, fetchers):
    return lambda request: make(fetchers, extractor=xc.ExchangeExtractor())


def test_cli_prints_the_bundle_and_exits_40_then_resumes_from_stdin(world):
    _ledger, _blobs, make, published = world
    fetchers = {"school_email": email(), "calendar": cal()}
    out, err = io.StringIO(), io.StringIO()
    code = cli_mod.main(["catchup", "--json", "--shadow", "--id", "cu_cli"],
                        deps_factory=_factory_for(make, fetchers), out=out, err=err)
    assert code == int(ExitCode.AWAITING_EXTRACTION) == 40
    bundle = json.loads(out.getvalue())
    assert bundle["protocol"] == xc.PROTOCOL and bundle["catchup_id"] == "cu_cli"
    request = bundle["requests"][0]
    assert xc.request_id(request["prompt"]) == request["request_id"]

    answers = json.dumps({"responses": {request["request_id"]: EXTRACT(request["prompt"])}})
    out2 = io.StringIO()
    code2 = cli_mod.main(["catchup", "--json", "--shadow", "--id", "cu_cli", "--responses", "-"],
                         deps_factory=_factory_for(make, fetchers), out=out2, err=io.StringIO(),
                         stdin=io.StringIO(answers))
    assert code2 in (int(ExitCode.COMPLETE), int(ExitCode.DEGRADED))
    assert json.loads(out2.getvalue())["catchup_id"] == "cu_cli"
    assert published == []                                  # shadow runs never publish


def test_cli_rejects_malformed_responses_as_usage_error(world):
    _l, _b, make, _p = world
    err = io.StringIO()
    code = cli_mod.main(["catchup", "--shadow", "--responses", "-"],
                        deps_factory=_factory_for(make, {"school_email": email(), "calendar": cal()}),
                        out=io.StringIO(), err=err, stdin=io.StringIO("not json"))
    assert code == int(ExitCode.USAGE_ERROR) and "responses" in err.getvalue()
    code = cli_mod.main(["catchup", "--shadow", "--responses", "/no/such/file.json"],
                        deps_factory=_factory_for(make, {"school_email": email(), "calendar": cal()}),
                        out=io.StringIO(), err=io.StringIO())
    assert code == int(ExitCode.USAGE_ERROR)


def test_authoritative_cli_refuses_anonymous_answers_before_any_catchup_write(world):
    ledger, _, make, published = world
    out, err = io.StringIO(), io.StringIO()
    factory = _factory_for(make, {})
    code = cli_mod.main(['catchup', '--host', 'synthetic', '--json', '--responses', '-'], deps_factory=factory,
                        out=out, err=err, stdin=io.StringIO('{"responses":{"a":"{}"}}'))
    assert code == int(ExitCode.BLOCKED) and 'model identity' in err.getvalue()
    assert not published and ledger.conn.execute('SELECT count(*) FROM stage_journal').fetchone()[0] == 0


@pytest.mark.parametrize('reported_identity', [False, True])
def test_returned_shadow_output_records_whether_model_identity_was_reported(world, reported_identity):
    import hashlib
    from catchup_core import corpus
    ledger, _, make, _ = world
    identity = {'provider': 'synthetic', 'model': 'offline', 'revision': '1', 'retention_policy': 'test'}
    cohort = {'implementation': 'fixture', 'model': hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    factory = lambda _request: make({'school_email': email(), 'calendar': cal()},
        extractor=xc.ExchangeExtractor(), evaluation_cohort=cohort)
    out = io.StringIO()
    arguments = ['catchup', '--shadow', '--json', '--id', 'cu_identity']
    assert cli_mod.main(arguments, deps_factory=factory, out=out, err=io.StringIO()) == 40
    bundle = json.loads(out.getvalue())
    assert bundle['cohort'] == cohort
    payload = {'responses': {row['request_id']: EXTRACT(row['prompt']) for row in bundle['requests']}}
    if reported_identity:
        payload.update(model_identity=identity, cohort=cohort)
    code = cli_mod.main([*arguments, '--responses', '-'], deps_factory=factory, out=io.StringIO(),
                        err=io.StringIO(), stdin=io.StringIO(json.dumps(payload)))
    assert code == 0
    assert corpus.latest_outcome(ledger, 'cu_identity')['cohort']['model'] == (
        cohort['model'] if reported_identity else 'unverified')
