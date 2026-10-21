"""RA-24: refused by code, not by a model's judgement."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from lib.action_policy import check_policy  # noqa: E402

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def proposal(action="email_send", sensitivity="standard", expires=None, **params):
    expires = expires or (NOW + timedelta(hours=1)).isoformat()
    return SimpleNamespace(action_type=action, sensitivity=sensitivity, parameters=params, expires_at=expires)


def test_ordinary_approved_action_passes():
    assert check_policy(proposal(to="a@b.com"), "owner", NOW) == (True, "")


def test_hand_off_tier_is_never_executed_even_when_approved():
    for kind in ("payment_send", "bank_transfer", "password_reset", "delete_permanent_file", "legal_filing", "submit_tax"):
        ok, why = check_policy(proposal(kind), "owner", NOW)
        assert not ok and "hand-off only" in why, kind
    ok, why = check_policy(proposal("custom_thing"), "owner", NOW, handoff_types=frozenset({"custom_thing"}))
    assert not ok and "instruction sheet" in why


def test_expired_unreadable_or_stale_proposals_are_refused():
    assert not check_policy(proposal(expires="2026-10-20T11:00:00Z"), "owner", NOW)[0]
    assert "cannot be read" in check_policy(proposal(expires="soon"), "owner", NOW)[1]
    assert check_policy(proposal(expires="2026-10-20T13:00:00"), "owner", NOW)[0]            # naive means UTC


def test_standing_and_automatic_approvals_only_prepare():
    for who in ("standing:weekly", "system:executor", "auto"):
        ok, why = check_policy(proposal(to="a@b.com"), who, NOW)
        assert not ok and "preparation only" in why


def test_sensitive_content_needs_exactly_one_named_recipient():
    assert check_policy(proposal(sensitivity="high", to="doc@clinic.org"), "owner", NOW)[0]
    for to in ("a@b.com, c@d.com", ["a@b.com", "c@d.com"], "all@school.org", "team@x.org", "", None):
        params = {"to": to} if to is not None else {}
        assert not check_policy(proposal(sensitivity="critical", **params), "owner", NOW)[0], to
    assert check_policy(proposal("reminder_create", sensitivity="critical"), "owner", NOW)[0]   # not outbound
    assert check_policy(proposal(sensitivity="standard", to="a@b.com, c@d.com"), "owner", NOW)[0]
