"""Wiring of finished library code into the runtime: usage receipts, retention config, Goal calibration lines."""
from __future__ import annotations

import dataclasses
from argparse import Namespace
from datetime import datetime, timezone

import pytest

from catchup_core import cli, commands
from catchup_core.ledger import LedgerError, SignalInput
from catchup_core.resources import AdmissionStore, ResourcePolicy
from test_release_regressions import NOW

world = pytest.importorskip("test_release_regressions").world

POLICY = ResourcePolicy.from_mapping({"max_requests_per_month": 50, "max_pending_signals": 4})


def with_admissions(deps, led, *keys):
    store = AdmissionStore(led, POLICY)
    sid = led.upsert_signal(SignalInput("mail", "one", "m1", "1", NOW.isoformat(), "h1", "synthetic-blob"))[0]
    for key in keys:
        store.admit("cu_a", sid, key, 1000, NOW)
    return dataclasses.replace(deps, admissions=store), store


def usage_args(**changes):
    base = {"command": "usage", "catchup_id": "cu_a", "request_key": "r1", "input_tokens": 120,
            "output_tokens": 30, "source": "provider-reported", "shadow": False}
    return Namespace(**{**base, **changes})


def test_given_a_flagged_usage_command_when_parsed_then_all_receipt_fields_are_required():
    got = cli.parse_args(["usage", "cu_a", "r1", "--input-tokens", "5", "--output-tokens", "6", "--source", "p"])
    assert (got.catchup_id, got.request_key, got.input_tokens, got.output_tokens, got.source) == ("cu_a", "r1", 5, 6, "p")
    for argv in (["usage", "cu_a", "r1"], ["usage", "cu_a", "r1", "--input-tokens", "5", "--source", "p"],
                 ["usage", "cu_a", "r1", "--input-tokens", "x", "--output-tokens", "1", "--source", "p"]):
        with pytest.raises(SystemExit):
            cli.parse_args(argv)


def test_given_an_admitted_request_when_the_owner_reports_usage_then_the_receipt_replaces_the_estimate(world):
    led, _, make, _ = world
    deps, store = with_admissions(make(), led, "r1", "r2")
    before = store.month_usage("2026-10")
    result = commands.execute(usage_args(), deps, led._path.parent)
    after = store.month_usage("2026-10")
    assert result["usage_recorded"] is True and result["unknown_requests"] == ["r2"]
    assert (before.actual_tokens, before.unknown_requests) == (0, 2)
    assert (after.actual_tokens, after.unknown_requests, after.reported_requests) == (150, 1, 1)


def test_given_no_usage_report_when_listed_then_the_request_stays_unknown_not_zero(world):
    led, _, make, _ = world
    deps, store = with_admissions(make(), led, "r1")
    assert store.uncertain_requests("cu_a") == ["r1"]
    assert store.month_usage("2026-10").counted_tokens > 0


def test_given_a_non_owner_when_reporting_usage_then_it_is_refused_and_nothing_is_stored(world):
    led, _, make, _ = world
    deps, store = with_admissions(make(), led, "r1")
    other = dataclasses.replace(deps, viewer="someone-else")
    with pytest.raises(LedgerError, match="owner"):
        commands.execute(usage_args(), other, led._path.parent)
    assert store.uncertain_requests("cu_a") == ["r1"]


def test_given_no_resource_policy_when_reporting_usage_then_it_is_refused(world):
    led, _, make, _ = world
    with pytest.raises(LedgerError, match="resource"):
        commands.execute(usage_args(), make(), led._path.parent)


def test_given_an_unadmitted_request_or_a_conflicting_receipt_when_reported_then_it_is_rejected(world):
    led, _, make, _ = world
    deps, _ = with_admissions(make(), led, "r1")
    with pytest.raises(ValueError, match="exact admission"):
        commands.execute(usage_args(request_key="nope"), deps, led._path.parent)
    commands.execute(usage_args(), deps, led._path.parent)
    commands.execute(usage_args(), deps, led._path.parent)         # same receipt is a no-op
    with pytest.raises(ValueError, match="different usage receipt"):
        commands.execute(usage_args(output_tokens=31), deps, led._path.parent)


def test_given_the_usage_command_when_run_through_the_cli_then_it_counts_as_a_mutation():
    assert cli._mutates(Namespace(command="usage", action=None, apply=None, label=None))


# ── retention command reads the configured periods through RetentionPolicy.from_config ──────────────────────────────

def config_file(root, **extra):
    import yaml
    (root / "config").mkdir(exist_ok=True)
    data = {"sources": {"school_email": {"connector": "gmail", "account_scope": "personal"}},
            "subjects": ["kid1"], "viewer": "owner", **extra}
    (root / "config" / "catchup_core.yaml").write_text(yaml.safe_dump(data))


def retention_args(**changes):
    return Namespace(**{"command": "retention", "at": None, "apply": None, "shadow": False, **changes})


def test_given_no_retention_config_when_previewed_then_every_class_needs_a_policy_and_nothing_applies(world, tmp_path):
    led, _, make, _ = world
    config_file(tmp_path)
    result = commands.execute(retention_args(), make(), tmp_path)
    assert result["application_allowed"] is False
    assert {e["status"] for e in result["entries"]} <= {"policy_needed"}


def test_given_a_configured_period_when_previewed_then_the_policy_is_read_from_the_config(world, tmp_path, monkeypatch):
    from catchup_core import retention
    led, _, make, _ = world
    config_file(tmp_path, retention={"replay": 30})
    seen = []
    original = retention.RetentionPolicy.from_config.__func__

    def spy(cls, policy):
        got = original(cls, policy)
        seen.append(got)
        return got
    monkeypatch.setattr(retention.RetentionPolicy, "from_config", classmethod(spy))
    result = commands.execute(retention_args(), make(), tmp_path)
    assert seen[0].days_for("replay") == 30 and result["application_allowed"] is False


def test_given_an_invalid_retention_config_when_previewed_then_it_is_refused(world, tmp_path):
    from catchup_core.retention import RetentionError
    config_file(tmp_path, retention={"source": 0})
    with pytest.raises(RetentionError):
        commands.execute(retention_args(), world[2](), tmp_path)


def test_given_apply_when_retention_runs_then_deletion_stays_held(world, tmp_path):
    config_file(tmp_path, retention={"replay": 30})
    with pytest.raises(LedgerError, match="held"):
        commands.execute(retention_args(apply="digest"), world[2](), tmp_path)


# ── weekly Goal lines: leading indicators, Sprint calibration (only with an authored tolerance), tradeoffs ───────────

from decimal import Decimal  # noqa: E402

from catchup_core import goals  # noqa: E402
from test_goal_metrics import coverage, evidence, metric  # noqa: E402

SPRINT = {"id": "s1", "goal_ref": "G1", "status": "active", "start": "2026-10-01", "end": "2026-10-20"}


@pytest.fixture
def gworld(world):
    led = world[0]
    coverage(led)
    evidence(led, "G1 2026-10-05 100.00 USD")
    evidence(led, "G1 2026-10-12 125.25 USD")          # 50.5% of the way; 60% of the Sprint has elapsed
    return led


def weekly(led, rows, sprints, *, weekly=True):
    return goals.observe_goals(led, rows, "owner", NOW, persist=False, weekly=weekly, sprints=sprints)


def test_given_a_sprint_without_an_authored_tolerance_when_weekly_then_no_calibration_is_shown(gworld):
    lines = weekly(gworld, [metric()], [SPRINT])
    assert not any("Sprint" in line for line in lines)


@pytest.mark.parametrize(("tolerance", "word"), [("0.05", "behind"), (0.10, "aligned")])
def test_given_a_per_sprint_tolerance_when_weekly_then_calibration_names_the_comparison(gworld, tolerance, word):
    lines = weekly(gworld, [metric()], [{**SPRINT, "tolerance": tolerance}])
    line = next(line for line in lines if "Sprint" in line)
    assert word in line and "of the target reached" in line and "Sprint elapsed" in line


def test_given_a_tolerance_when_not_weekly_then_the_briefing_lines_do_not_change(gworld):
    assert not any("Sprint" in line for line in weekly(gworld, [metric()], [{**SPRINT, "tolerance": "0.05"}], weekly=False))


@pytest.mark.parametrize("tolerance", [True, "abc", -0.1, 2, [0.1], float("nan")])
def test_given_an_unusable_tolerance_when_weekly_then_the_line_asks_for_review_and_shows_no_status(gworld, tolerance):
    lines = weekly(gworld, [metric()], [{**SPRINT, "tolerance": tolerance}])
    review = [line for line in lines if "tolerance" in line]
    assert len(review) == 1 and "review" in review[0]
    assert not any(w in line for line in lines for w in ("behind", "ahead", "aligned"))


def test_given_an_inactive_or_other_goals_sprint_when_weekly_then_it_is_not_used(gworld):
    other = [{**SPRINT, "status": "closed", "tolerance": "0.05"}, {**SPRINT, "goal_ref": "G9", "tolerance": "0.05"}]
    assert not any("Sprint" in line for line in weekly(gworld, [metric()], other))


def test_given_an_unknown_measurement_when_calibrated_then_the_line_says_why_not_a_status(world):
    led = world[0]                                          # no evidence at all
    lines = weekly(led, [metric()], [{**SPRINT, "tolerance": "0.05"}])
    line = next(line for line in lines if "Sprint" in line)
    assert "unknown" in line and "no verified measurement" in line


def test_given_milestones_when_weekly_then_leading_indicators_are_counted(world):
    led = world[0]
    row = {"id": "G1", "owner": "owner", "title": "Prepare", "status": "active",
           "metric": {"kind": "milestones", "target": 2},
           "milestones": [{"id": "m1", "title": "One"}, {"id": "m2", "title": "Two", "target_date": "2026-10-01"}]}
    lines = weekly(led, [row], [])
    line = next(line for line in lines if "leading" in line)
    assert "2 ready" in line and "1 overdue" in line
    assert not any("leading" in line for line in weekly(led, [row], [], weekly=False))


def two_goals():
    a = metric()
    a["competes_with"] = ["G2"]
    b = metric()
    b.update(id="G2", title="Other aim")
    b["metric"] = {**b["metric"], "baseline": "100.00", "target": "150.00"}
    return a, b


def test_given_authored_competing_goals_one_ahead_and_one_behind_when_weekly_then_a_proposal_only_line_appears(world):
    led = world[0]
    coverage(led)
    evidence(led, "G1 2026-10-05 100.00 USD")
    evidence(led, "G1 2026-10-12 105.00 USD")              # behind
    evidence(led, "G2 2026-10-05 100.00 USD")
    evidence(led, "G2 2026-10-12 145.00 USD")              # ahead
    a, b = two_goals()
    sprints = [{**SPRINT, "tolerance": "0.10"}, {**SPRINT, "id": "s2", "goal_ref": "G2", "tolerance": "0.10"}]
    lines = weekly(led, [a, b], sprints)
    trade = [line for line in lines if "tradeoff" in line.lower()]
    assert len(trade) == 1 and "proposal" in trade[0] and "nothing has been changed" in trade[0]
    assert "Practice or save" in trade[0] and "Other aim" in trade[0]
    # without the authored link the same numbers propose nothing
    a.pop("competes_with")
    assert not any("tradeoff" in line.lower() for line in weekly(led, [a, b], sprints))


def test_given_the_goals_weekly_command_when_a_sprint_has_a_tolerance_then_the_view_shows_calibration(gworld, tmp_path):
    import yaml
    deps = world_deps = None
    state = tmp_path / "state"
    state.mkdir()
    text = yaml.safe_dump({"goals": [metric()], "sprints": [{**SPRINT, "tolerance": "0.05"}]})
    (state / "goals.md").write_text(text)
    from catchup_core.runtime import _goal_lines
    lines = _goal_lines(state, reader=lambda _name: text, ledger=gworld, viewer="owner", now=NOW,
                        today=NOW.date(), persist=False, weekly=True)
    assert any("Sprint behind" in line for line in lines.lines)
    plain = _goal_lines(state, reader=lambda _name: text, ledger=gworld, viewer="owner", now=NOW,
                        today=NOW.date(), persist=False)
    assert not any("Sprint behind" in line for line in plain.lines)


def test_given_competing_goals_when_the_runtime_evaluates_goals_one_at_a_time_then_the_tradeoff_still_appears(world, tmp_path):
    import yaml
    from catchup_core.runtime import _goal_lines
    led = world[0]
    coverage(led)
    for text in ("G1 2026-10-05 100.00 USD", "G1 2026-10-12 105.00 USD",
                 "G2 2026-10-05 100.00 USD", "G2 2026-10-12 145.00 USD"):
        evidence(led, text)
    a, b = two_goals()
    text = yaml.safe_dump({"goals": [a, b], "sprints": [{**SPRINT, "tolerance": "0.10"},
                                                        {**SPRINT, "id": "s2", "goal_ref": "G2", "tolerance": "0.10"}]})
    lines = _goal_lines(tmp_path, reader=lambda _n: text, ledger=led, viewer="owner", now=NOW,
                        today=NOW.date(), persist=False, weekly=True)
    index = next(i for i, line in enumerate(lines.lines) if "Tradeoff" in line)
    assert len(lines.citations[index]) >= 2                      # cites the Facts behind both Goals
    assert not any("Tradeoff" in line for line in _goal_lines(
        tmp_path, reader=lambda _n: text, ledger=led, viewer="owner", now=NOW, today=NOW.date(), persist=False).lines)


def test_given_a_first_receipt_of_zero_tokens_without_a_reason_when_reported_then_it_is_refused(world):
    led, _, make, _ = world
    deps, store = with_admissions(make(), led, "r1")
    with pytest.raises(ValueError, match="zero tokens"):
        commands.execute(usage_args(input_tokens=0, output_tokens=0), deps, led._path.parent)
    assert store.uncertain_requests("cu_a") == ["r1"]               # still unknown, still counted at its estimate
    assert store.month_usage("2026-10").counted_tokens > 0


def test_given_a_zero_receipt_with_a_stated_reason_when_reported_then_it_is_kept_with_the_reason(world):
    led, _, make, _ = world
    deps, store = with_admissions(make(), led, "r1")
    commands.execute(usage_args(input_tokens=0, output_tokens=0, zero_reason="provider billed nothing"),
                     deps, led._path.parent)
    source = led.conn.execute("SELECT source FROM resource_usage").fetchone()[0]
    assert "provider billed nothing" in source and store.uncertain_requests("cu_a") == []


def test_given_shadow_mode_when_cancelling_extraction_then_it_is_refused_and_nothing_is_stored(world):
    led, _, make, _ = world
    with pytest.raises(LedgerError, match="shadow"):
        commands.execute(Namespace(command="cancel-extraction", catchup_id="cu_a", shadow=True),
                         make(), led._path.parent)
    assert not led.conn.execute("SELECT 1 FROM resource_cancellations").fetchone()


def test_given_the_usage_flags_when_parsed_then_a_zero_reason_is_optional():
    got = cli.parse_args(["usage", "cu_a", "r1", "--input-tokens", "0", "--output-tokens", "0",
                          "--source", "p", "--zero-reason", "cached"])
    assert got.zero_reason == "cached"
