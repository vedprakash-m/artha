from __future__ import annotations

from pathlib import Path

import pytest

from catchup_core import domain_pack as dp
from catchup_core.contracts import Confidentiality

PACKS_DIR = Path(dp.__file__).parent / "packs"


def test_composite_pack_single():
    kids = dp.load_pack_file(PACKS_DIR / "kids_school.yaml")
    comp = dp.composite_pack([kids])
    assert comp is kids


def test_composite_pack_multiple():
    kids = dp.load_pack_file(PACKS_DIR / "kids_school.yaml")
    finance = dp.load_pack_file(PACKS_DIR / "finance.yaml")
    immigration = dp.load_pack_file(PACKS_DIR / "immigration.yaml")
    home = dp.load_pack_file(PACKS_DIR / "home.yaml")
    health = dp.load_pack_file(PACKS_DIR / "health.yaml")

    comp = dp.composite_pack([kids, finance, immigration, home, health])
    assert comp.domain == "kids_school+finance+immigration+home+health"
    assert comp.confidentiality == Confidentiality.RESTRICTED
    assert comp.impact == "critical"

    # All predicates merged
    for expected in ["exam_registration_deadline", "bill_due_date", "case_status", "mortgage_due_date", "doctor_appointment"]:
        assert expected in comp.predicates

    # All rules combined
    assert len(comp.rules) == len(kids.rules) + len(finance.rules) + len(immigration.rules) + len(home.rules) + len(health.rules)

    # Sources merged by connector
    connectors = {s.connector for s in comp.sources}
    assert {"school_email", "finance_email", "immigration_email", "home_email", "health_email"} <= connectors
    assert "calendar" in connectors


def test_composite_pack_empty_raises():
    with pytest.raises(dp.PackError, match="empty sequence"):
        dp.composite_pack([])


def test_composite_preserves_strictest_source_freshness_and_all_versions():
    from dataclasses import replace
    kids = dp.load_pack_file(PACKS_DIR / 'kids_school.yaml')
    home = dp.load_pack_file(PACKS_DIR / 'home.yaml')
    kids = replace(kids, sources=(dp.Source('shared', True, 48),))
    home = replace(home, sources=(dp.Source('shared', False, 2),))
    combined = dp.composite_pack([kids, home])
    assert combined.sources == (dp.Source('shared', True, 2),)
    assert combined.version != dp.composite_pack([kids, replace(home, version='9.9.9')]).version
    with pytest.raises(dp.PackError):
        dp.composite_pack([kids, replace(home, sources=(dp.Source('shared', False, 2, 'different'),))])


def test_composite_retains_each_predicates_business_calendar_and_timezone():
    from dataclasses import replace
    from datetime import date, datetime, timezone
    from zoneinfo import ZoneInfo
    kids = dp.load_pack_file(PACKS_DIR / 'kids_school.yaml')
    home = dp.load_pack_file(PACKS_DIR / 'home.yaml')
    school_holiday = date(2026, 10, 13)
    kids = replace(kids, holidays=frozenset({school_holiday}))
    name = next(iter(home.predicates))
    home = replace(home, holidays=frozenset(), timezone=ZoneInfo('Asia/Tokyo'), timezone_name='Asia/Tokyo',
                   predicates={k: replace(p, lead_days=1) for k, p in home.predicates.items()})
    combined = dp.composite_pack([kids, home])
    assert combined.latest_useful_action(name, date(2026, 10, 14)) == school_holiday
    assert combined.for_predicate(name).timezone_name == 'Asia/Tokyo'
    assert combined.for_predicate(name).days_until(date(2026, 10, 14),
        datetime(2026, 10, 13, 18, tzinfo=timezone.utc)) == 0
    assert combined.for_predicate('exam_registration_deadline').holidays == kids.holidays


def test_composite_refuses_ambiguous_domain_and_predicate_ownership():
    from dataclasses import replace
    kids = dp.load_pack_file(PACKS_DIR / 'kids_school.yaml')
    with pytest.raises(dp.PackError):
        dp.composite_pack([kids, kids])
    with pytest.raises(dp.PackError):
        dp.composite_pack([kids, replace(kids, domain='different', predicate_domains={})])


def policy_pack(domain, *, retention_days=30, effect='allow', unit=None, currency=None, version='2.0.0'):
    predicate = {'name': domain + '_status', 'type': 'text', 'authority': ['document'],
                 'jurisdiction': ['US-WA'],
                 'effective_interval': {'start': '2026-01-01', 'end': '2027-01-01'}}
    if unit is not None:
        predicate['quantity'] = {'unit': unit}
        if currency is not None:
            predicate['quantity']['currency'] = currency
    return dp.load_pack({
        'domain': domain, 'version': version, 'confidentiality': 'ordinary', 'impact': 'advisory',
        'sources': [], 'predicates': [predicate],
        'retention': {'max_days': retention_days, 'audit_days': retention_days},
        'permissions': [{'scope': 'read', 'predicates': [predicate['name']], 'effect': effect}],
        'compatibility': {'core_contract': '0.6.0'},
        'migration': {'from_version': '1.0.0', 'to_version': version, 'compatible': False},
        'rollback': {'from_version': version, 'to_version': '1.0.0', 'compatible': True},
    })


def test_composite_policy_is_not_weakened_or_filled_in():
    from dataclasses import replace
    first = policy_pack('first', retention_days=90)
    second = policy_pack('second', retention_days=7, effect='deny')
    combined = dp.composite_pack([first, second])
    assert combined.retention == dp.RetentionPolicy(7, 7)
    assert combined.permissions == first.permissions + second.permissions
    assert combined.compatibility == first.compatibility
    assert combined.migration == first.migration and combined.rollback == first.rollback
    assert combined.for_predicate('second_status') is second
    assert combined.predicates['second_status'].effective_interval == second.predicates['second_status'].effective_interval
    assert combined.predicates['second_status'].jurisdiction == ('US-WA',)
    assert not combined.extension_ready
    legacy = replace(first, domain='legacy', predicates={}, retention=None, permissions=None)
    mixed = dp.composite_pack([legacy, second])
    assert not mixed.extension_ready
    assert any('legacy:retention' == item for item in mixed.extension_requirements)
    assert legacy.retention is None
    nested = dp.composite_pack([combined, policy_pack('third')])
    assert nested.retention == dp.RetentionPolicy(7, 7)
    assert nested.for_predicate('first_status') is first


@pytest.mark.parametrize('other', [
    {'unit': 'EUR', 'currency': 'EUR'},
    {'unit': 'USD', 'currency': 'EUR'},
    {'unit': 'kg'},
    {'version': '3.0.0', 'unit': 'USD', 'currency': 'USD'},
])
def test_composite_rejects_incompatible_quantity_and_migration_declarations(other):
    first = policy_pack('first', unit='USD', currency='USD')
    second = policy_pack('second', **other)
    with pytest.raises(dp.PackError, match='conflicting'):
        dp.composite_pack([first, second])


def test_composite_rejects_contract_version_or_rollback_compatibility_conflict():
    from dataclasses import replace
    first, second = policy_pack('first'), policy_pack('second')
    for changed in (replace(second, compatibility=dp.PackCompatibility('0.7.0')),
                    replace(second, rollback=dp.PackTransition('2.0.0', '1.0.0', False))):
        with pytest.raises(dp.PackError, match='conflicting'):
            dp.composite_pack([first, changed])
