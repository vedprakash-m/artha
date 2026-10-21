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
