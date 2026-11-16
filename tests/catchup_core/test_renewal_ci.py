"""CI input verification with no network, native authority or provider access."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from catchup_core.renewal_ci import Gate, Job, Run, verify_ci
from catchup_core.renewal_inputs import InputRejected

NOW = datetime(2026, 1, 2, tzinfo=UTC)
REPO = "example/project"
SHA = "a" * 40
WORKFLOW = ".github/workflows/ci.yml"
GATE = Gate(WORKFLOW, "b" * 64, ("tests-linux", "tests-windows"))


def run(**changes):
    return replace(Run(REPO, SHA, "refs/heads/main", "push", WORKFLOW, "b" * 64,
                       1, 1, NOW - timedelta(minutes=1), "completed", "success",
                       (Job("tests-linux", "completed", "success"),
                        Job("tests-windows", "completed", "success"))), **changes)


def check(runs):
    return verify_ci(repository=REPO, commit=SHA, gates=(GATE,), runs=runs, now=NOW)


def test_AR04_success_requires_the_actual_expected_jobs():
    assert check((run(),)).status == "passed"
    assert check((run(jobs=(Job("something-else", "completed", "success"),)),)).status == "held"


@pytest.mark.parametrize("changes", [
    {"repository": "foreign/project"}, {"commit": "c" * 40},
    {"ref": "refs/heads/feature"}, {"event": "pull_request"},
    {"definition_sha256": "c" * 64}, {"conclusion": "failure"},
    {"conclusion": "cancelled"}, {"created_at": NOW + timedelta(seconds=1)},
    {"created_at": NOW - timedelta(days=2)},
    {"jobs": (Job("tests-linux", "completed", "skipped"), Job("tests-windows", "completed", "success"))},
])
def test_AR04_foreign_or_incomplete_evidence_does_not_pass(changes):
    assert check((run(**changes),)).status == "held"


def test_AR08_running_or_missing_ci_is_resumable_not_green():
    assert check(()).status == "pending"
    assert check((run(status="in_progress", conclusion=""),)).status == "pending"


def test_AR22_latest_attempt_wins_and_known_failures_are_not_erased():
    older = run(attempt=1)
    newer = run(attempt=2, conclusion="failure")
    assert check((older, newer)).status == "held"
    prior_failure = run(attempt=1, conclusion="failure")
    successful_retry = run(attempt=2)
    assert check((prior_failure, successful_retry)).status == "held"


def test_AR22_later_run_is_not_replaced_with_an_older_success():
    newer = run(run_id=2, created_at=NOW, status="in_progress", conclusion="")
    assert check((run(), newer)).status == "pending"


def test_AR13_duplicate_runs_jobs_and_gates_refuse():
    assert check((run(), run())).status == "held"
    assert check((run(jobs=(Job("tests-linux", "completed", "success"),) * 2),)).status == "held"
    with pytest.raises(InputRejected):
        verify_ci(repository=REPO, commit=SHA, gates=(GATE, GATE), runs=(), now=NOW)


def test_AR13_malformed_status_does_not_escape_as_an_exception():
    assert check((run(status=[]),)).status == "held"


@pytest.mark.parametrize("kwargs", [
    {"repository": "CANARY\n"}, {"commit": "CANARY"},
    {"gates": ()}, {"max_age_seconds": True}, {"max_age_seconds": 0},
    {"max_age_seconds": 86401}, {"now": NOW.replace(tzinfo=None)},
])
def test_AR13_bad_inputs_have_bounded_errors(kwargs):
    options = {"repository": REPO, "commit": SHA, "gates": (GATE,), "runs": (), "now": NOW}
    options.update(kwargs)
    with pytest.raises(InputRejected, match="^invalid renewal CI metadata$"):
        verify_ci(**options)
