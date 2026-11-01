"""Goal leading indicators, Sprint calibrations and evidence-backed tradeoffs (re-artha RA-11)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from catchup_core import crypto, ids, ledger as lg
from catchup_core.contracts import ProcessingState, Provenance
from catchup_core.curated import completion_key, milestone_record
from catchup_core.goals import (
    AuthoredGoal,
    GoalObservation,
    calibrate_sprint,
    goal_tradeoffs,
    leading_indicators,
    observe_goals,
    tradeoff_lines,
)
from catchup_core.plans import MilestoneState

KEY = ids.HmacKey(1, b"k" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"c" * 32}))
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


@pytest.fixture
def world(tmp_path: Any):
    ledger = lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER, owner_id="owner").open()
    ledger.migrate()
    yield ledger
    ledger.close()


def test_outcome_goal_with_milestones_evaluates_leading_indicators(world: lg.Ledger):
    # Outcome goal tracking weight or savings with leading milestone steps
    row = {
        "id": "G_savings",
        "title": "Emergency Fund",
        "owner": "owner",
        "status": "active",
        "metric": {
            "kind": "outcome",
            "domain": "finance",
            "predicate": "savings_balance",
            "target": 10000,
            "baseline": 5000,
            "unit": "USD",
            "comparison": "at_least",
            "sources": ["bank"],
            "period_days": 30,
            "freshness_hours": 72,
        },
        "milestones": [
            {"id": "m1", "title": "Open high-yield savings account", "status": "done"},
            {"id": "m2", "title": "Set up monthly auto-transfer", "prerequisites": ["m1"], "target_date": "2026-10-18"},
            {"id": "m3", "title": "Audit recurring subscriptions", "prerequisites": ["m2"], "target_date": "2026-10-30"},
        ],
    }

    world.set_coverage("bank", "default", "complete", completed_at=NOW.isoformat())
    sid, _ = world.upsert_signal(lg.SignalInput("bank", "default", "s1", "v1", NOW.isoformat(), "hmac", "blob_1"))
    world.set_processing_state(sid, ProcessingState.RECONCILED)
    world.put_fact(
        lg.FactInput(
            domain="finance",
            predicate="savings_balance",
            value=f"G_savings {TODAY.isoformat()} 7500 USD",
            primary_subject_id="owner",
            observed_at=NOW.isoformat(),
            evidence=((sid, 0, 4),),
        )
    )

    # Put completion evidence for m1
    m1_sid, _ = world.upsert_signal(lg.SignalInput("bank", "default", "s2", "v1", NOW.isoformat(), "hmac2", "blob_2"))
    world.set_processing_state(m1_sid, ProcessingState.RECONCILED)
    key = completion_key("milestone", milestone_record(row, row["milestones"][0]))
    world.put_fact(
        lg.FactInput(
            domain="finance",
            predicate="commitment_completed",
            value={"record_key": key, "completed": True, "actor": "owner"},
            primary_subject_id="owner",
            observed_at=NOW.isoformat(),
            obligation_key=key,
            provenance=Provenance.USER_CONFIRMED,
            evidence=((m1_sid, 0, 4),),
        )
    )

    sprints = [
        {
            "id": "S1",
            "goal_ref": "G_savings",
            "status": "active",
            "start": "2026-10-01",
            "end": "2026-10-31",
            "tolerance": "0.10",
        }
    ]

    calibrations: dict[str, Any] = {}
    observed: dict[str, Any] = {}
    lines = observe_goals(
        world,
        [row],
        "owner",
        NOW,
        weekly=True,
        sprints=sprints,
        calibrations=calibrations,
        observed=observed,
    )

    # Verify leading indicators are counted
    obs = observed["G_savings"]
    assert obs.indicators is not None
    assert obs.indicators.known is True
    # m1 is done; m2 is ready and due soon (Oct 18 is within 7 days of Oct 15); m3 is blocked
    assert obs.indicators.ready == 1
    assert obs.indicators.due_soon == 1
    assert obs.indicators.blocked == 1

    # Verify sprint calibration is computed
    # Progress: (7500 - 5000) / (10000 - 5000) = 2500 / 5000 = 0.50 (50%)
    # Elapsed: 15 / 31 = 0.4839 (48%)
    # Gap: ~0.016 <= tolerance 0.10 -> aligned
    cal = calibrations["G_savings"]
    assert cal.status == "aligned"
    assert "50% of the target reached" in cal.reason

    # Verify formatted weekly lines include leading indicators and calibration
    assert any("leading indicators — 1 ready, 1 blocked, 0 overdue, 1 due within 7 days" in line for line in lines)
    assert any("Sprint aligned" in line for line in lines)


def test_on_time_obligation_with_milestones_evaluates_leading_indicators(world: lg.Ledger):
    row = {
        "id": "G_bills",
        "title": "On-Time Bills",
        "owner": "owner",
        "status": "active",
        "metric": {
            "kind": "on_time_obligations",
            "domain": "finance",
            "predicate": "bill_due_date",
            "target": 2,
            "period_days": 30,
            "sources": ["mail"],
            "freshness_hours": 72,
        },
        "milestones": [
            {"id": "m1", "title": "Check invoices", "status": "done"},
            {"id": "m2", "title": "Pay utility", "prerequisites": ["m1"], "target_date": "2026-10-10"},
        ],
    }

    world.set_coverage("mail", "default", "complete", completed_at=NOW.isoformat())
    sid, _ = world.upsert_signal(lg.SignalInput("mail", "default", "s1", "v1", NOW.isoformat(), "hmac", "blob_1"))
    world.set_processing_state(sid, ProcessingState.RECONCILED)
    # Put an on-time bill fact
    world.put_fact(
        lg.FactInput(
            domain="finance",
            predicate="bill_due_date",
            value="2026-10-20",
            primary_subject_id="owner",
            observed_at=NOW.isoformat(),
            evidence=((sid, 0, 4),),
        )
    )

    observed: dict[str, Any] = {}
    lines = observe_goals(
        world,
        [row],
        "owner",
        NOW,
        weekly=True,
        observed=observed,
    )

    obs = observed["G_bills"]
    assert obs.indicators is not None
    assert obs.indicators.known is True
    # m2 has target_date Oct 10, today is Oct 15 -> overdue!
    assert obs.indicators.overdue == 1
    assert any("leading indicators" in line and "1 overdue" in line for line in lines)


def test_competing_goals_tradeoff_proposals_with_calibrations(world: lg.Ledger):
    # Two competing goals: G1 is ahead, G2 is behind
    g1 = {
        "id": "G1",
        "title": "Fitness Mileage",
        "owner": "owner",
        "status": "active",
        "competes_with": ["G2"],
        "metric": {
            "kind": "outcome",
            "domain": "wellness",
            "predicate": "distance_miles",
            "target": 100,
            "baseline": 0,
            "unit": "miles",
            "comparison": "at_least",
            "sources": ["strava"],
            "period_days": 30,
            "freshness_hours": 72,
        },
    }
    g2 = {
        "id": "G2",
        "title": "Study Hours",
        "owner": "owner",
        "status": "active",
        "competes_with": ["G1"],
        "metric": {
            "kind": "outcome",
            "domain": "learning",
            "predicate": "study_hours",
            "target": 50,
            "baseline": 0,
            "unit": "hours",
            "comparison": "at_least",
            "sources": ["tracker"],
            "period_days": 30,
            "freshness_hours": 72,
        },
    }

    world.set_coverage("strava", "default", "complete", completed_at=NOW.isoformat())
    world.set_coverage("tracker", "default", "complete", completed_at=NOW.isoformat())
    s1, _ = world.upsert_signal(lg.SignalInput("strava", "default", "sig1", "v1", NOW.isoformat(), "hm1", "b1"))
    s2, _ = world.upsert_signal(lg.SignalInput("tracker", "default", "sig2", "v1", NOW.isoformat(), "hm2", "b2"))
    world.set_processing_state(s1, ProcessingState.RECONCILED)
    world.set_processing_state(s2, ProcessingState.RECONCILED)

    f1 = world.put_fact(
        lg.FactInput(
            domain="wellness",
            predicate="distance_miles",
            value=f"G1 {TODAY.isoformat()} 85 miles",  # 85% reached at day 15/31 (48%) -> ahead!
            primary_subject_id="owner",
            observed_at=NOW.isoformat(),
            evidence=((s1, 0, 2),),
        )
    )
    f2 = world.put_fact(
        lg.FactInput(
            domain="learning",
            predicate="study_hours",
            value=f"G2 {TODAY.isoformat()} 10 hours",  # 20% reached at day 15/31 (48%) -> behind!
            primary_subject_id="owner",
            observed_at=NOW.isoformat(),
            evidence=((s2, 0, 2),),
        )
    )

    sprints = [
        {"id": "S1", "goal_ref": "G1", "status": "active", "start": "2026-10-01", "end": "2026-10-31", "tolerance": "0.10"},
        {"id": "S2", "goal_ref": "G2", "status": "active", "start": "2026-10-01", "end": "2026-10-31", "tolerance": "0.10"},
    ]

    calibrations: dict[str, Any] = {}
    observed: dict[str, Any] = {}
    lines = observe_goals(
        world,
        [g1, g2],
        "owner",
        NOW,
        weekly=True,
        sprints=sprints,
        calibrations=calibrations,
        observed=observed,
    )

    assert calibrations["G1"].status == "ahead"
    assert calibrations["G2"].status == "behind"

    # Verify tradeoff lines
    trades = tradeoff_lines([g1, g2], calibrations, observed)
    assert len(trades) == 1
    line_text, fact_ids = trades[0]
    assert "Study Hours is behind its Sprint while Fitness Mileage is ahead" in line_text
    assert f1 in fact_ids and f2 in fact_ids
    assert any("Tradeoff to consider (proposal only" in line for line, _ in trades)
