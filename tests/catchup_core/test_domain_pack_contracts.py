"""Pack contract evaluation: effective interval, jurisdiction, quantity, permission and version moves (RA-14)."""
from __future__ import annotations

from datetime import date

import pytest

from catchup_core import domain_pack as dp


def pred(**extra):
    base = {"name": "p", "type": "date", "authority": ["a"]}
    return dp.load_pack({"domain": "d", "version": "1.0.0", "confidentiality": "ordinary", "impact": "advisory",
                         "sources": [], "predicates": [{**base, **extra}]}).predicates["p"]


def test_given_no_declarations_when_checked_then_the_predicate_applies_everywhere():
    assert pred().applies(date(2026, 1, 1), None) is True


@pytest.mark.parametrize(("day", "expected"), [(date(2026, 1, 1), True), (date(2026, 12, 31), True),
                                               (date(2027, 1, 1), False), (date(2025, 12, 31), False)])
def test_given_an_interval_when_checked_then_start_is_inclusive_and_end_is_exclusive(day, expected):
    p = pred(effective_interval={"start": "2026-01-01", "end": "2027-01-01"})
    assert p.applies(day, None) is expected


def test_given_a_jurisdiction_when_none_is_supplied_then_the_answer_is_unknown_not_true():
    assert pred(jurisdiction=["US-WA"]).applies(date(2026, 1, 1), None) is None


@pytest.mark.parametrize(("declared", "given", "expected"), [
    (["US-WA"], "US-WA", True), (["US-WA"], "US-OR", False), (["US"], "US-WA", True),
    (["US-WA"], "US", False), (["US-WA", "CA-BC"], "CA-BC", True)])
def test_given_jurisdictions_when_compared_then_a_declared_parent_covers_its_subdivisions(declared, given, expected):
    assert pred(jurisdiction=declared).applies(date(2026, 1, 1), given) is expected


def test_given_both_declarations_when_the_interval_fails_then_false_wins_over_unknown():
    p = pred(jurisdiction=["US-WA"], effective_interval={"start": "2026-01-01", "end": "2027-01-01"})
    assert p.applies(date(2028, 1, 1), None) is False
    assert p.applies(date(2026, 6, 1), None) is None


def test_given_a_quantity_when_unit_or_currency_differs_then_it_is_not_accepted():
    p = pred(quantity={"unit": "dollars", "currency": "USD"})
    assert p.accepts_quantity("dollars", "USD") is True
    assert p.accepts_quantity("dollars", "EUR") is False
    assert p.accepts_quantity("dollars", None) is False
    assert p.accepts_quantity("miles", "USD") is False
    assert pred().accepts_quantity("dollars", None) is False        # nothing declared: nothing is accepted


def with_permissions(rules):
    return dp.load_pack({"domain": "d", "version": "1.0.0", "confidentiality": "ordinary", "impact": "advisory",
                         "sources": [], "permissions": rules,
                         "predicates": [{"name": "p", "type": "date", "authority": ["a"]},
                                        {"name": "q", "type": "date", "authority": ["a"]}]})


def test_given_allow_and_deny_rules_when_resolved_then_deny_wins_and_unmatched_is_unknown():
    pack = with_permissions([{"scope": "viewer", "predicates": ["p", "q"], "effect": "allow"},
                             {"scope": "viewer", "predicates": ["q"], "effect": "deny"}])
    assert pack.permission("viewer", "p") == "allow"
    assert pack.permission("viewer", "q") == "deny"
    assert pack.permission("other", "p") is None
    with pytest.raises(dp.PackError):
        pack.permission("viewer", "missing")


def test_given_no_permissions_declared_when_resolved_then_nothing_is_allowed():
    pack = dp.load_pack_file(dp.__file__.replace("domain_pack.py", "packs/vehicle.yaml"))
    assert pack.permissions is None and pack.permission("viewer", "recall_status") is None


def moving(**extra):
    return dp.load_pack({"domain": "d", "version": "2.0.0", "confidentiality": "ordinary", "impact": "advisory",
                         "sources": [], "predicates": [{"name": "p", "type": "date", "authority": ["a"]}], **extra})


def test_given_a_declared_migration_and_rollback_when_asked_then_only_those_moves_are_answered():
    pack = moving(migration={"from_version": "1.0.0", "to_version": "2.0.0", "compatible": True},
                  rollback={"from_version": "2.0.0", "to_version": "1.0.0", "compatible": False})
    assert pack.can_move("1.0.0", "2.0.0") is True
    assert pack.can_move("2.0.0", "1.0.0") is False        # declared as incompatible
    assert pack.can_move("0.9.0", "2.0.0") is None         # never declared: unknown
    assert moving().can_move("1.0.0", "2.0.0") is None
    assert pack.can_move("2.0.0", "2.0.0") is True         # staying on the same version needs nothing
