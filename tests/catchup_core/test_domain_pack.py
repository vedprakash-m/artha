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
    assert pack.domain == "kids_school" and pack.version == "0.1.0"
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
