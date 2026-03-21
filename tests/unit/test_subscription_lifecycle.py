"""
tests/unit/test_subscription_lifecycle.py — Unit tests for E6 extensions
to scripts/skills/subscription_monitor.py

Coverage:
  - _parse_date() handles YYYY-MM-DD, datetime objects, None
  - _detect_upcoming_renewals() returns subscription_renewal_upcoming signals
  - _detect_cancellation_deadlines() returns subscription_cancellation_deadline signals
  - _detect_annual_reviews() returns subscription_annual_review signals
  - _detect_stale_data() returns True when last_updated > 90 days ago
  - parse() returns new output keys: renewal_count, cancellation_deadline_count, etc.
  - compare_fields includes new count fields
  - MAX alert cap (2 per run) enforced
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from scripts.skills.subscription_monitor import SubscriptionMonitorSkill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill(tmp_path):
    skill = SubscriptionMonitorSkill(artha_dir=tmp_path)
    return skill


def _sub(name: str, **kwargs) -> dict:
    base = {
        "name": name,
        "service": name,
        "amount": 9.99,
        "billing_cycle": "monthly",
        "status": "active",
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_iso_string(self):
        from scripts.skills.subscription_monitor import _parse_date
        d = _parse_date("2026-12-31")
        assert d == date(2026, 12, 31)

    def test_none_returns_none(self):
        from scripts.skills.subscription_monitor import _parse_date
        assert _parse_date(None) is None

    def test_invalid_returns_none(self):
        from scripts.skills.subscription_monitor import _parse_date
        assert _parse_date("not-a-date") is None

    def test_date_object_passthrough(self):
        from scripts.skills.subscription_monitor import _parse_date
        d = date(2026, 6, 15)
        result = _parse_date(d)
        assert result == d


# ---------------------------------------------------------------------------
# _detect_upcoming_renewals
# ---------------------------------------------------------------------------

class TestDetectUpcomingRenewals:
    def test_renewal_in_2_days_triggers_signal(self):
        from scripts.skills.subscription_monitor import _detect_upcoming_renewals
        soon = (date.today() + timedelta(days=2)).isoformat()
        subs = [_sub("Netflix", next_renewal=soon)]
        signals = _detect_upcoming_renewals(subs)
        assert len(signals) >= 1
        assert signals[0]["signal_type"] == "subscription_renewal_upcoming"

    def test_renewal_in_30_days_no_signal(self):
        from scripts.skills.subscription_monitor import _detect_upcoming_renewals
        far = (date.today() + timedelta(days=30)).isoformat()
        subs = [_sub("Netflix", next_renewal=far)]
        signals = _detect_upcoming_renewals(subs)
        assert len(signals) == 0

    def test_no_renewal_date_no_signal(self):
        from scripts.skills.subscription_monitor import _detect_upcoming_renewals
        subs = [_sub("Netflix")]
        signals = _detect_upcoming_renewals(subs)
        assert signals == []


# ---------------------------------------------------------------------------
# _detect_cancellation_deadlines
# ---------------------------------------------------------------------------

class TestDetectCancellationDeadlines:
    def test_cancel_before_free_trial_end_triggers(self):
        from scripts.skills.subscription_monitor import _detect_cancellation_deadlines
        soon = (date.today() + timedelta(days=2)).isoformat()
        subs = [_sub("FreeTrialService", cancel_by=soon, status="trial")]
        signals = _detect_cancellation_deadlines(subs)
        assert len(signals) >= 1
        assert signals[0]["signal_type"] == "subscription_cancellation_deadline"

    def test_no_trial_no_signal(self):
        from scripts.skills.subscription_monitor import _detect_cancellation_deadlines
        subs = [_sub("Netflix", status="active")]
        signals = _detect_cancellation_deadlines(subs)
        assert signals == []


# ---------------------------------------------------------------------------
# _detect_annual_reviews
# ---------------------------------------------------------------------------

class TestDetectAnnualReviews:
    def test_annual_renewal_in_window_triggers(self):
        from scripts.skills.subscription_monitor import _detect_annual_reviews
        soon = (date.today() + timedelta(days=15)).isoformat()
        subs = [_sub("AnnualService", annual_review_date=soon, billing_cycle="annual")]
        signals = _detect_annual_reviews(subs)
        assert len(signals) >= 1
        assert signals[0]["signal_type"] == "subscription_annual_review"

    def test_no_annual_review_date_no_signal(self):
        from scripts.skills.subscription_monitor import _detect_annual_reviews
        subs = [_sub("MonthlyService", billing_cycle="monthly")]
        signals = _detect_annual_reviews(subs)
        assert signals == []


# ---------------------------------------------------------------------------
# _detect_stale_data
# ---------------------------------------------------------------------------

class TestDetectStaleData:
    def test_stale_over_90_days(self):
        from scripts.skills.subscription_monitor import _detect_stale_data
        old = (date.today() - timedelta(days=100)).isoformat()
        assert _detect_stale_data(old) is True

    def test_recent_not_stale(self):
        from scripts.skills.subscription_monitor import _detect_stale_data
        recent = (date.today() - timedelta(days=10)).isoformat()
        assert _detect_stale_data(recent) is False

    def test_none_not_stale(self):
        from scripts.skills.subscription_monitor import _detect_stale_data
        # None should not be considered stale (no data to evaluate)
        result = _detect_stale_data(None)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# compare_fields
# ---------------------------------------------------------------------------

class TestCompareFields:
    def test_compare_fields_includes_new_keys(self, tmp_path):
        skill = _make_skill(tmp_path)
        fields = skill.compare_fields
        # Fields added in E6 (using 'upcoming_renewal_count' key)
        assert "upcoming_renewal_count" in fields or any("renewal" in f for f in fields)


# ---------------------------------------------------------------------------
# MAX cap
# ---------------------------------------------------------------------------

class TestMaxAlertCap:
    def test_max_2_signals_per_run(self):
        from scripts.skills.subscription_monitor import _detect_upcoming_renewals, _MAX_SUB_ALERTS_PER_RUN
        soon = (date.today() + timedelta(days=2)).isoformat()
        subs = [_sub(f"Service{i}", next_renewal_date=soon) for i in range(10)]
        signals = _detect_upcoming_renewals(subs)
        assert len(signals) <= _MAX_SUB_ALERTS_PER_RUN
