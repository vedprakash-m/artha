from __future__ import annotations

import io
import json
from datetime import date, datetime, timezone

import pytest

from catchup_core import cli
from catchup_core import goals as gl
from catchup_core.contracts import CatchupRequest, CatchupResult, CoverageEntry, CoverageStatus, ExecutionMode, RunStatus
from catchup_core.ledger import FactView
from catchup_core.scorecard import Case, score

NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)
GOAL = gl.AuthoredGoal("G-1", "ved", "Register on time", "exam_registration_deadline", target=2, period_days=30)


def fv(fid, value):
    return FactView(fid, "kids_school", "exam_registration_deadline", value, "kid1", "ordinary",
                    "verified_source", "current", None, None, False, "t", "t")


# ── goals ─────────────────────────────────────────────────────────────────

def test_observation_counts_only_obligations_met_on_time():
    facts = [fv("a", "2026-10-05"), fv("b", "2026-10-08"), fv("c", "2026-09-01"),
             fv("d", {"value": "2026-10-10", "lineage": "legacy_state:x@y"}), fv("e", "garbage")]
    resolved = {"a": date(2026, 10, 4), "b": date(2026, 10, 9)}      # b was late
    obs = gl.observe_on_time(GOAL, facts, resolved, NOW, last_verified=NOW)
    assert obs.value == 1 and set(obs.source_fact_ids) == {"a", "b", "d"}      # c is outside the period
    assert gl.assess(GOAL, obs).status == "at_risk"


def test_target_met_is_on_track():
    facts = [fv("a", "2026-10-05"), fv("b", "2026-10-08")]
    obs = gl.observe_on_time(GOAL, facts, {"a": date(2026, 10, 1), "b": date(2026, 10, 8)}, NOW, last_verified=NOW)
    assert gl.assess(GOAL, obs).status == "on_track"


def test_no_verified_source_means_unknown_not_zero():
    obs = gl.observe_on_time(GOAL, [fv("a", "2026-10-05")], {}, NOW, last_verified=None)
    assert obs.value is None and gl.assess(GOAL, obs).status == "unknown"


def test_stale_evidence_is_unknown():
    old = datetime(2026, 10, 1, tzinfo=timezone.utc)
    obs = gl.observe_on_time(GOAL, [], {}, NOW, last_verified=old)
    a = gl.assess(GOAL, obs)
    assert a.status == "unknown" and "h old" in a.reason


def test_authored_goal_is_immutable():
    with pytest.raises(Exception):
        GOAL.target = 99   # type: ignore[misc]


# ── scorecard ─────────────────────────────────────────────────────────────

def c(**kw):
    base = dict(case_id="c", expected=frozenset({"k1"}), surfaced=frozenset({"k1"}))
    base.update(kw)
    return Case(**base)


def test_clean_run_passes():
    s = score([c(), c(case_id="d", expected=frozenset(), surfaced=frozenset(), quiet_claim=True)])
    assert s.passes and s.recall == 1.0 and s.precision == 1.0 and s.failures() == []


def test_false_quiet_claim_fails_even_with_high_recall():
    cases = [c(case_id=str(i)) for i in range(20)] + [
        c(case_id="q", expected=frozenset({"k9"}), surfaced=frozenset(), quiet_claim=True)]
    s = score(cases)
    assert not s.passes and s.false_quiet == 1 and s.recall < 1.0


def test_quiet_over_incomplete_coverage_is_false_quiet():
    s = score([c(expected=frozenset(), surfaced=frozenset(), quiet_claim=True, coverage_complete=False)])
    assert s.false_quiet == 1 and not s.passes


def test_regression_vs_incumbent_and_citations():
    s = score([c(expected=frozenset({"k1", "k2"}), surfaced=frozenset({"k1"}),
                 incumbent_surfaced=frozenset({"k2"}), citations_resolve=False)])
    assert s.regressions_vs_incumbent == 1 and s.unresolved_citations == 1
    assert len(s.failures()) == 4 - 0 or len(s.failures()) >= 3


def test_empty_scorecard_is_vacuously_clean():
    s = score([])
    assert s.passes and s.cases == 0


# ── cli ───────────────────────────────────────────────────────────────────

def test_authoritative_default_factory_is_disabled_until_cutover(monkeypatch):
    monkeypatch.setenv("ARTHA_CUTOVER", "0")
    err = io.StringIO()
    assert cli.main(["catchup", "--json", "--host", "mac"], out=io.StringIO(), err=err) == 30
    assert "cut over" in err.getvalue()


def test_shadow_default_factory_reports_missing_configuration(monkeypatch):
    from catchup_core import runtime
    monkeypatch.setattr(runtime, "build_deps", lambda request: (_ for _ in ()).throw(
        runtime.RuntimeConfigError("config/catchup_core.yaml missing")))
    err = io.StringIO()
    assert cli.main(["catchup", "--json", "--shadow"], out=io.StringIO(), err=err) == 30
    assert "catchup_core.yaml" in err.getvalue()


def test_authoritative_without_host_is_a_usage_error():
    err = io.StringIO()
    assert cli.main(["catchup"], out=io.StringIO(), err=err) == 30
    assert "host" in err.getvalue()


def test_bad_arguments_exit_30():
    assert cli.main(["catchup", "--format", "nonsense"], out=io.StringIO(), err=io.StringIO()) == 30
    assert cli.main([], out=io.StringIO(), err=io.StringIO()) == 30


def test_json_output_and_exit_code_follow_the_result(monkeypatch):
    def fake_run(request, deps):
        return CatchupResult(request.catchup_id, request.mode, RunStatus.DEGRADED,
                             coverage=(CoverageEntry("calendar", "a", CoverageStatus.UNAVAILABLE),),
                             reason_unpublished="shadow")
    monkeypatch.setattr(cli, "run_catchup", fake_run)
    out = io.StringIO()
    code = cli.main(["catchup", "--json", "--shadow", "--id", "cu_x"], deps_factory=lambda r: object(),
                    out=out, err=io.StringIO())
    assert code == 10
    data = json.loads(out.getvalue())
    assert data["catchup_id"] == "cu_x" and data["status"] == "degraded" and data["mode"] == "shadow"
    text = io.StringIO()
    assert cli.main(["catchup", "--shadow"], deps_factory=lambda r: object(),
                    out=text, err=io.StringIO()) == 10
    assert "degraded" in text.getvalue()
