from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from catchup_core import domain_pack as dp
from catchup_core.contracts import Severity

PACK = Path(dp.__file__).parent / "packs" / "kids_school.yaml"


@pytest.fixture(scope="module")
def pack():
    return dp.load_pack_file(PACK)


def raw():
    import yaml
    return yaml.safe_load(PACK.read_text())


def NOW(y=2026, m=10, d=11, h=12):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


# ── load ──────────────────────────────────────────────────────────────────

def test_shipped_pack_loads(pack):
    assert pack.domain == "kids_school" and pack.version == "0.3.0"
    assert pack.timezone_name == "America/Los_Angeles"
    assert pack.predicates["accommodation_status"].confidentiality.value == "restricted"
    assert {s.connector for s in pack.sources if s.required} == {"school_email", "calendar"}
    assert pack.predicates["exam_registration_deadline"].high_stakes


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d.update(surprise=1), "unknown key"),
    (lambda d: d.pop("domain"), "missing 'domain'"),
    (lambda d: d.update(version="1.0"), "semver"),
    (lambda d: d.update(confidentiality="top"), "confidentiality"),
    (lambda d: d["predicates"].append(copy.deepcopy(d["predicates"][0])), "duplicate predicate"),
    (lambda d: d["predicates"][0].update(type="blob"), "type must be"),
    (lambda d: d["predicates"][2].pop("values"), "enum needs"),
    (lambda d: d["predicates"][0].update(values=["a"]), "only enum"),
    (lambda d: d["predicates"][0].update(authority=[]), "authority per predicate"),
    (lambda d: d["alert_rules"].append({"predicate": "nope", "when": "True", "severity": "P1"}), "unknown predicate"),
    (lambda d: d["alert_rules"].append({"predicate": "grade_alert", "when": "value == 'x'", "severity": "P9"}), "severity"),
    (lambda d: d["alert_rules"].append({"predicate": "grade_alert", "when": "value ==", "severity": "P1"}), "cannot parse"),
    (lambda d: d["alert_rules"].append({"predicate": "grade_alert", "when": "mystery > 1", "severity": "P1"}), "unknown rule variable"),
    (lambda d: d["alert_rules"].append({"predicate": "grade_alert", "when": "__import__('os')", "severity": "P1"}), "unsupported|unknown rule variable"),
    (lambda d: d["alert_rules"].append({"predicate": "grade_alert", "when": "value == 'terrible'", "severity": "P1"}), "not a declared value"),
    (lambda d: d["deadline_arithmetic"].update(timezone="nowhere_local"), "unbound"),
    (lambda d: d["deadline_arithmetic"].update(business_days="unbound_calendar"), "unbound"),
    (lambda d: d["deadline_arithmetic"].update(end_dates="sometimes"), "exclusive or inclusive"),
    (lambda d: d["deadline_arithmetic"].update(bogus=1), "unknown key"),
    (lambda d: d["sources"][0].update(oops=1), "unknown key"),
    (lambda d: d["sources"].append({"required": True}), "missing 'connector'"),
])
def test_invalid_packs_fail_at_load(mutate, match):
    data = raw()
    mutate(data)
    with pytest.raises(dp.PackError, match=match):
        dp.load_pack(data)


def test_non_mapping_and_unreadable_file(tmp_path):
    with pytest.raises(dp.PackError):
        dp.load_pack([])  # type: ignore[arg-type]
    with pytest.raises(dp.PackError):
        dp.load_pack_file(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [unclosed")
    with pytest.raises(dp.PackError):
        dp.load_pack_file(bad)


def test_bindings_override_and_holiday_validation():
    data = raw()
    pack = dp.load_pack(data, {"school_local": "America/New_York",
                               "school_calendar": {"holidays": ["2026-11-26"]}})
    assert pack.timezone_name == "America/New_York"
    assert date(2026, 11, 26) in pack.holidays
    with pytest.raises(dp.PackError, match="bad holiday"):
        dp.load_pack(data, {"school_calendar": {"holidays": ["not-a-date"]}})


# ── rules ─────────────────────────────────────────────────────────────────

def test_deadline_rule_precedence_most_severe_wins(pack):
    r = pack.evaluate("exam_registration_deadline", {"days_until": 2})
    assert r.severity is Severity.P1 and len(r.matched) == 2
    assert pack.evaluate("exam_registration_deadline", {"days_until": 5}).severity is Severity.P2
    assert pack.evaluate("exam_registration_deadline", {"days_until": 20}).severity is None


def test_overdue_uses_obligation_state(pack):
    outstanding = pack.evaluate("exam_registration_deadline",
                                {"days_until": -3, "obligation_state": "outstanding"})
    assert outstanding.severity is Severity.P1
    resolved = pack.evaluate("exam_registration_deadline",
                             {"days_until": -3, "obligation_state": "resolved"})
    assert resolved.severity is None


def test_missing_variable_is_uncertain_not_guessed(pack):
    r = pack.evaluate("exam_registration_deadline", {})
    assert r.severity is None and r.uncertain
    r = pack.evaluate("exam_registration_deadline", {"days_until": -3})   # obligation_state unknown
    assert r.severity is None and r.uncertain


def test_enum_rule(pack):
    assert pack.evaluate("grade_alert", {"value": "below_threshold"}).severity is Severity.P3
    assert pack.evaluate("grade_alert", {"value": "ok"}).severity is None


def test_evaluate_rejects_unknown_predicate_and_variable(pack):
    with pytest.raises(dp.PackError):
        pack.evaluate("nope", {})
    with pytest.raises(dp.PackError):
        pack.evaluate("grade_alert", {"sneaky": 1})


def test_expression_features():
    data = raw()
    data["alert_rules"] = [
        {"predicate": "grade_alert", "when": "not value == 'ok'", "severity": "P4"},
        {"predicate": "grade_alert", "when": "value in ['below_threshold', 'ok'] or days_until > 3", "severity": "P3"},
        {"predicate": "grade_alert", "when": "-days_until > 1", "severity": "P2"},
    ]
    pack = dp.load_pack(data)
    assert pack.evaluate("grade_alert", {"value": "below_threshold"}).severity is Severity.P3
    assert pack.evaluate("grade_alert", {"value": "ok", "days_until": -5}).severity is Severity.P2
    assert pack.evaluate("grade_alert", {"value": "ok"}).uncertain  # -days_until unknown


def test_type_mismatch_in_comparison_is_unknown_not_crash():
    data = raw()
    data["alert_rules"] = [{"predicate": "grade_alert", "when": "days_until > 3", "severity": "P2"}]
    pack = dp.load_pack(data)
    assert pack.evaluate("grade_alert", {"days_until": "soon"}).uncertain


# ── typed values ──────────────────────────────────────────────────────────

def test_predicate_value_validation(pack):
    pack.predicates["exam_registration_deadline"].validate_value(date(2026, 10, 19))
    pack.predicates["grade_alert"].validate_value("ok")
    pack.predicates["appointment_time"].validate_value(NOW())
    pack.predicates["missing_assignment"].validate_value("Essay 2")
    for pred, bad in [("exam_registration_deadline", "2026-10-19"), ("exam_registration_deadline", NOW()),
                      ("grade_alert", "meh"), ("appointment_time", date(2026, 1, 1)),
                      ("missing_assignment", 3)]:
        with pytest.raises(dp.PackError):
            pack.predicates[pred].validate_value(bad)


# ── deadline arithmetic ───────────────────────────────────────────────────

def test_days_until_uses_pack_timezone(pack):
    # 2026-10-12 03:00 UTC is still Oct 11 in Los Angeles
    now = datetime(2026, 10, 12, 3, tzinfo=timezone.utc)
    assert pack.days_until(date(2026, 10, 19), now) == 8
    assert pack.days_until(date(2026, 10, 11), now) == 0


def test_days_until_datetime_deadline_and_naive_guard(pack):
    now = NOW()
    assert pack.days_until(datetime(2026, 10, 14, 9, 0), now) == 3          # naive -> pack tz
    assert pack.days_until(datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc), now) == 3
    with pytest.raises(dp.PackError):
        pack.days_until(date(2026, 10, 14), datetime(2026, 10, 11))


def test_exclusive_end_dates(pack):
    assert pack.last_inclusive_day(date(2026, 10, 20)) == date(2026, 10, 19)


def test_latest_useful_action_counts_school_business_days():
    data = raw()
    data["bindings"]["school_calendar"] = {"holidays": ["2026-10-19"]}
    pack = dp.load_pack(data)
    # due Tue 2026-10-20, lead 2 school days: Mon 19 is a holiday, so Fri 16 then Thu 15
    assert pack.latest_useful_action("exam_registration_deadline", date(2026, 10, 20)) == date(2026, 10, 15)
    assert pack.latest_useful_action("test_date", date(2026, 10, 20)) == date(2026, 10, 20)   # lead 0


@pytest.mark.parametrize('mutate', [
    lambda d: d['sources'][0].update(required='false'),
    lambda d: d['sources'][0].update(freshness_hours=True),
    lambda d: d['sources'][0].update(freshness_hours=-1),
    lambda d: d['sources'][0].update(freshness_hours='24'),
    lambda d: d['predicates'][0].update(event='false'),
    lambda d: d['predicates'][0].update(high_stakes='false'),
    lambda d: d['predicates'][0].update(lead_days=-1),
    lambda d: d['predicates'][0].update(lead_days=True),
    lambda d: d['predicates'][0].update(authority='school_email'),
    lambda d: d['predicates'][2].update(values='current'),
    lambda d: d['predicates'][0].update(hint=7),
    lambda d: d['sources'].append(dict(d['sources'][0])),
    lambda d: d.update(impact='anything'),
    lambda d: d.update(domain=7),
    lambda d: d.update(bindings=['wrong']),
    lambda d: d.update(sources=[None]),
    lambda d: d.update(predicates='wrong'),
    lambda d: d.update(alert_rules=[True]),
])
def test_pack_policy_fields_are_typed_instead_of_coerced(mutate):
    data = raw()
    mutate(data)
    with pytest.raises(dp.PackError):
        dp.load_pack(data)


@pytest.mark.parametrize('mutate', [
    lambda d: d['sources'][0].update(freshness_hours=8761),
    lambda d: d['sources'][0].update(connector='invalid source'),
    lambda d: d['predicates'][0].update(lead_days=3661),
    lambda d: d['predicates'][0].update(name=7),
    lambda d: d['predicates'][0].update(confidentiality=False),
    lambda d: d['predicates'][0].update(hint='x' * 161),
    lambda d: d['predicates'][0].update(authority=['email', 'email']),
    lambda d: d['predicates'][2].update(values=[4]),
    lambda d: d['alert_rules'][0].update(when=True),
    lambda d: d.update(impact={}),
    lambda d: d.update(fixtures_dir=None),
    lambda d: d.update(version='1.0.0\n'),
])
def test_malformed_pack_limits_and_optional_fields_fail_closed(mutate):
    data = raw()
    mutate(data)
    with pytest.raises(dp.PackError):
        dp.load_pack(data)


def test_unknown_predicate_cannot_select_an_unrelated_domain_policy(pack):
    with pytest.raises(dp.PackError, match='unknown predicate'):
        pack.for_predicate('unknown')


def extension_raw():
    return {
        'domain': 'extraDomain', 'version': '2.0.0', 'confidentiality': 'ordinary', 'impact': 'advisory',
        'sources': [{'connector': 'fixture_source', 'class': 'document'}],
        'predicates': [{'name': 'fixture_status', 'type': 'enum', 'values': ['open', 'closed'],
                        'authority': ['document'], 'jurisdiction': ['US-WA'],
                        'effective_interval': {'start': '2026-01-01', 'end': '2027-01-01'}}],
        'alert_rules': [{'predicate': 'fixture_status', 'when': "value == 'open'", 'severity': 'P4'}],
        'retention': {'max_days': 30, 'audit_days': 90},
        'permissions': [{'scope': 'read', 'predicates': ['fixture_status'], 'effect': 'deny'}],
        'compatibility': {'core_contract': '0.6.0'},
        'migration': {'from_version': '1.0.0', 'to_version': '2.0.0', 'compatible': False},
        'rollback': {'from_version': '2.0.0', 'to_version': '1.0.0', 'compatible': True},
        'policy_bindings': {name: {'policy_id': 'reviewed-fixture', 'version': '1.0.0'}
                            for name in ('retention', 'permissions', 'jurisdiction', 'effective_interval',
                                         'compatibility', 'migration', 'rollback', 'source_authority')},
    }


def test_extension_declarations_are_typed_and_legacy_policy_stays_unset(pack):
    assert pack.retention is None and pack.permissions is None
    assert pack.compatibility is None and pack.migration is None and pack.rollback is None
    assert not pack.extension_ready
    loaded = dp.load_pack(extension_raw())
    assert loaded.retention.max_days == 30
    assert loaded.permissions[0].effect == 'deny'
    assert loaded.predicates['fixture_status'].jurisdiction == ('US-WA',)
    assert loaded.predicates['fixture_status'].effective_interval.start == date(2026, 1, 1)
    assert loaded.migration.from_version == '1.0.0'
    assert loaded.rollback.to_version == '1.0.0'
    assert loaded.extension_ready
    assert loaded.runtime_guards_required


@pytest.mark.parametrize('mutate', [
    lambda d: d['retention'].update(max_days=True),
    lambda d: d['retention'].update(max_days='30'),
    lambda d: d['retention'].update(audit_days=-1),
    lambda d: d['retention'].update(unbounded=True),
    lambda d: d.update(retention=None),
    lambda d: d.update(permissions=[]),
    lambda d: d['permissions'][0].update(effect='allow_if_known'),
    lambda d: d['permissions'][0].update(scope='*'),
    lambda d: d['permissions'][0].update(predicates=['unknown']),
    lambda d: d['permissions'][0].update(predicates=[]),
    lambda d: d['permissions'][0].update(when='True'),
    lambda d: d['predicates'][0].update(jurisdiction=[]),
    lambda d: d['predicates'][0].update(jurisdiction=['*']),
    lambda d: d['predicates'][0].update(jurisdiction='US-WA'),
    lambda d: d['predicates'][0].update(effective_interval={'start': '2027-01-01', 'end': '2026-01-01'}),
    lambda d: d['predicates'][0].update(effective_interval={'start': '2026-01-01'}),
    lambda d: d['predicates'][0].update(effective_interval={'start': '20260101', 'end': '2027-01-01'}),
    lambda d: d['predicates'][0].update(effective_interval={'start': True, 'end': '2027-01-01'}),
    lambda d: d['migration'].update(compatible='false'),
    lambda d: d['migration'].update(from_version='1.0'),
    lambda d: d['migration'].update(to_version='3.0.0'),
    lambda d: d['rollback'].update(from_version='1.0.0'),
    lambda d: d['rollback'].update(to_version='0.1.0'),
    lambda d: d['compatibility'].update(core_contract=6),
    lambda d: d['compatibility'].update(core_contract='01.0.0'),
    lambda d: d['compatibility'].update(core_contract='\u0661.0.0'),
    lambda d: d['policy_bindings']['retention'].update(version='latest'),
    lambda d: d['policy_bindings']['retention'].update(policy_id='*'),
    lambda d: d['policy_bindings'].update(unknown={'policy_id': 'x', 'version': '1.0.0'}),
])
def test_extension_literals_fail_closed(mutate):
    data = extension_raw()
    mutate(data)
    with pytest.raises(dp.PackError):
        dp.load_pack(data)


def test_extension_readiness_requires_bindings_and_high_stakes_policy():
    data = extension_raw()
    data.pop('policy_bindings')
    loaded = dp.load_pack(data)
    assert not loaded.extension_ready
    assert 'policy_binding:retention' in loaded.extension_requirements
    assert loaded.policy_bindings == {}
    data = extension_raw()
    data['policy_bindings'].pop('source_authority')
    loaded = dp.load_pack(data)
    assert not loaded.extension_ready
    assert 'policy_binding:source_authority' in loaded.extension_requirements
    data = extension_raw()
    data['predicates'][0]['high_stakes'] = True
    data['predicates'][0].pop('jurisdiction')
    loaded = dp.load_pack(data)
    assert not loaded.extension_ready
    assert 'jurisdiction:fixture_status' in loaded.extension_requirements


def test_quantity_is_metadata_not_a_new_executable_type():
    data = extension_raw()
    pred = data['predicates'][0]
    pred.update(type='text', quantity={'unit': 'USD', 'currency': 'USD'})
    pred.pop('values')
    data['alert_rules'] = []
    loaded = dp.load_pack(data)
    assert loaded.predicates['fixture_status'].quantity.unit == 'USD'
    assert not loaded.predicates['fixture_status'].execution_supported
    assert not loaded.extension_ready
    loaded.predicates['fixture_status'].validate_value('12.50')
    with pytest.raises(dp.PackError, match='quantity.*consumer'):
        loaded.evaluate('fixture_status', {'value': '12.50'})
    for quantity in ({'unit': '*'}, {'unit': 'USD', 'currency': 'usd'}, {'unit': 3},
                     {'unit': 'USD', 'currency': True}, {'unit': 'USD', 'rounding': 'auto'}):
        pred['quantity'] = quantity
        with pytest.raises(dp.PackError):
            dp.load_pack(data)
    pred.update(type='quantity', quantity={'unit': 'kg'})
    with pytest.raises(dp.PackError, match='type must be'):
        dp.load_pack(data)


@pytest.mark.parametrize('contract_case', ['normal', 'partial', 'replay', 'revoked', 'corrected'])
def test_extra_domain_dynamic_fixture_contract_only(tmp_path, contract_case):
    """Case labels are contract fixtures, not measured source-lifecycle capabilities."""
    import yaml
    data = extension_raw()
    if contract_case != 'revoked':
        data['permissions'][0]['effect'] = 'allow'
    path = tmp_path / f'{contract_case}.yaml'
    path.write_text(yaml.safe_dump(data), encoding='utf-8')
    loaded = dp.load_pack_file(path)
    assert loaded.domain == 'extraDomain'
    variables = {} if contract_case == 'partial' else {'value': 'closed' if contract_case == 'corrected' else 'open'}
    result = loaded.evaluate('fixture_status', variables)
    assert result.uncertain is (contract_case == 'partial')
    assert result.severity is (None if contract_case in {'partial', 'corrected'} else Severity.P4)
    if contract_case == 'replay':
        assert dp.load_pack_file(path).evaluate('fixture_status', variables) == result
    assert loaded.permissions[0].effect == ('deny' if contract_case == 'revoked' else 'allow')
    assert 'permissions' in loaded.runtime_guards_required
