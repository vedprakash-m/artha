from __future__ import annotations

import json

import pytest

from catchup_core import contracts as c


def _req(**kw):
    base = dict(catchup_id="cu_1", actor="user", channel="cli", host="mac-1")
    base.update(kw)
    return c.CatchupRequest(**base)


# ── state machine ─────────────────────────────────────────────────────────

def test_stage_order_matches_spec():
    assert [s.value for s in c.Stage] == [
        "CREATED", "PREFLIGHT", "FETCH", "NORMALIZE", "EXTRACT", "RECONCILE", "TRIAGE",
        "COMPOSE", "VALIDATE", "COMMIT", "PRESENTED", "FINALIZED"]


def test_forward_one_step_only():
    assert c.can_transition(c.Stage.CREATED, c.Stage.PREFLIGHT)
    assert not c.can_transition(c.Stage.CREATED, c.Stage.FETCH)
    assert not c.can_transition(c.Stage.FETCH, c.Stage.PREFLIGHT)


def test_finalized_reachable_from_any_stage_and_terminal():
    for stage in c.Stage:
        if stage is c.Stage.FINALIZED:
            continue
        assert c.can_transition(stage, c.Stage.FINALIZED)
    assert not c.can_transition(c.Stage.FINALIZED, c.Stage.FINALIZED)
    assert c.next_stage(c.Stage.FINALIZED) is None


# ── request ───────────────────────────────────────────────────────────────

def test_request_round_trip():
    r = _req(connector_scopes=("gmail:a",), pilot_domains=("kids_school",),
             briefing_format=c.BriefingFormat.DIGEST)
    assert c.CatchupRequest.from_dict(r.to_dict()) == r


@pytest.mark.parametrize("kw", [{"catchup_id": " "}, {"actor": ""}, {"channel": ""}, {"schema_version": 0},
                                {"host": ""}])
def test_request_validation(kw):
    with pytest.raises(c.ContractError):
        _req(**kw)


def test_shadow_request_needs_no_host():
    assert _req(mode=c.ExecutionMode.SHADOW, host="").mode is c.ExecutionMode.SHADOW


def test_request_from_dict_missing_field_and_bad_enum():
    with pytest.raises(c.ContractError):
        c.CatchupRequest.from_dict({"actor": "u"})
    with pytest.raises(c.ContractError):
        c.CatchupRequest.from_dict({"catchup_id": "x", "actor": "u", "channel": "c", "mode": "nope"})


# ── checkpoint ────────────────────────────────────────────────────────────

def test_failed_checkpoint_requires_class():
    with pytest.raises(c.ContractError):
        c.StageCheckpoint("cu_1", c.Stage.FETCH, "failed")
    with pytest.raises(c.ContractError):
        c.StageCheckpoint("cu_1", c.Stage.FETCH, "committed", failure_class=c.FailureClass.AUTH)
    assert c.StageCheckpoint("cu_1", c.Stage.FETCH, "failed", failure_class=c.FailureClass.AUTH)
    with pytest.raises(c.ContractError):
        c.StageCheckpoint("cu_1", c.Stage.FETCH, "weird")


# ── result ────────────────────────────────────────────────────────────────

def _cov(status=c.CoverageStatus.COMPLETE, required=True, name="calendar"):
    return c.CoverageEntry(name, "acct", status, required=required)


def test_exit_codes_match_contract():
    assert c.RunStatus.COMPLETE.exit_code() == 0
    assert c.RunStatus.DEGRADED.exit_code() == 10
    assert c.RunStatus.BLOCKED.exit_code() == 20
    assert c.ExitCode.USAGE_ERROR == 30


def test_complete_requires_complete_required_coverage():
    with pytest.raises(c.ContractError):
        c.CatchupResult("cu_1", c.ExecutionMode.AUTHORITATIVE, c.RunStatus.COMPLETE,
                        coverage=(_cov(c.CoverageStatus.DEGRADED),), briefing_id="b1", briefing_version=1)


def test_optional_gap_does_not_prevent_complete():
    r = c.CatchupResult("cu_1", c.ExecutionMode.AUTHORITATIVE, c.RunStatus.COMPLETE,
                        coverage=(_cov(), _cov(c.CoverageStatus.NOTIFIED_NOT_FETCHED, required=False, name="portal")),
                        briefing_id="b1", briefing_version=1)
    assert r.exit_code() == 0


def test_shadow_result_never_publishes():
    with pytest.raises(c.ContractError):
        c.CatchupResult("cu_1", c.ExecutionMode.SHADOW, c.RunStatus.COMPLETE, briefing_id="b1", briefing_version=1)
    assert c.CatchupResult("cu_1", c.ExecutionMode.SHADOW, c.RunStatus.COMPLETE)


def test_published_briefing_needs_version_and_not_blocked():
    with pytest.raises(c.ContractError):
        c.CatchupResult("cu_1", c.ExecutionMode.AUTHORITATIVE, c.RunStatus.DEGRADED, briefing_id="b1")
    with pytest.raises(c.ContractError):
        c.CatchupResult("cu_1", c.ExecutionMode.AUTHORITATIVE, c.RunStatus.BLOCKED,
                        briefing_id="b1", briefing_version=1, failures=(c.FailureClass.STORAGE,))


def test_unpublished_authoritative_result_states_reason():
    with pytest.raises(c.ContractError):
        c.CatchupResult("cu_1", c.ExecutionMode.AUTHORITATIVE, c.RunStatus.DEGRADED)
    r = c.CatchupResult("cu_1", c.ExecutionMode.AUTHORITATIVE, c.RunStatus.BLOCKED,
                        reason_unpublished="vault locked", failures=(c.FailureClass.STORAGE,))
    assert r.exit_code() == 20


def test_blocked_needs_failure_or_reason():
    with pytest.raises(c.ContractError):
        c.CatchupResult("cu_1", c.ExecutionMode.SHADOW, c.RunStatus.BLOCKED)


def test_derive_status():
    assert c.CatchupResult.derive_status((_cov(),), ()) is c.RunStatus.COMPLETE
    assert c.CatchupResult.derive_status((_cov(c.CoverageStatus.UNAVAILABLE),), ()) is c.RunStatus.DEGRADED
    assert c.CatchupResult.derive_status((_cov(),), (c.FailureClass.AUTH,)) is c.RunStatus.DEGRADED
    assert c.CatchupResult.derive_status((_cov(c.CoverageStatus.ANOMALY, required=False),), ()) is c.RunStatus.COMPLETE


def test_result_json_round_trip():
    r = c.CatchupResult("cu_1", c.ExecutionMode.AUTHORITATIVE, c.RunStatus.DEGRADED,
                        coverage=(_cov(c.CoverageStatus.DEGRADED),), briefing_id="b1", briefing_version=2,
                        failures=(c.FailureClass.SCHEMA_REJECTED,), artifact_ids=("a1",), pending_refs=("p1",))
    data = json.loads(r.to_json())
    assert data["contract_version"]
    assert c.CatchupResult.from_dict(data) == r


def test_result_from_dict_rejects_unknown_major_and_missing_fields():
    r = c.CatchupResult("cu_1", c.ExecutionMode.SHADOW, c.RunStatus.COMPLETE).to_dict()
    r["contract_version"] = "9.0.0"
    with pytest.raises(c.ContractError):
        c.CatchupResult.from_dict(r)
    with pytest.raises(c.ContractError):
        c.CatchupResult.from_dict({"contract_version": "0.1.0"})
    bad = c.CatchupResult("cu_1", c.ExecutionMode.SHADOW, c.RunStatus.COMPLETE).to_dict()
    bad["status"] = "weird"
    with pytest.raises(c.ContractError):
        c.CatchupResult.from_dict(bad)


def test_severity_rank_and_coverage_quiet_rule():
    assert c.Severity.P1.rank < c.Severity.P3.rank
    assert c.CoverageStatus.COMPLETE.quiet_claim_allowed
    assert not c.CoverageStatus.NOTIFIED_NOT_FETCHED.quiet_claim_allowed
