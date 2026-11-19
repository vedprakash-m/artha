"""passport_expiry must not report "ok" when it found no passport expiry dates at all."""
from __future__ import annotations

from pathlib import Path

from skills.passport_expiry import PassportExpirySkill


def test_no_recognized_dates_is_unknown_not_ok():
    out = PassportExpirySkill(Path(".")).parse("- **Passport**: [REDACTED]\nnothing with a date\n")
    assert out["highest_alert"] == "unknown" and out["passports"] == [] and "message" in out


def test_a_recognized_far_future_date_is_still_ok():
    out = PassportExpirySkill(Path(".")).parse("passport_expiry: 2099-01-01\npassport_holder: Test\n")
    assert out["highest_alert"] == "ok" and len(out["passports"]) == 1


def test_empty_state_keeps_the_vault_hint():
    assert PassportExpirySkill(Path(".")).parse("")["vault_required"] is True
