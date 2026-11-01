from __future__ import annotations

import pytest

from catchup_core.rule_of_two import Broker, CallDeclaration, RuleOfTwoViolation, lint_configs


def D(name, u=False, p=False, c=False):
    return CallDeclaration(name, u, p, c)


def test_lint_flags_only_three_way_combinations():
    bad = D("bad", True, True, True)
    ok = [D("extractor", u=True), D("composer", p=True), D("answerer", p=True, c=True), D("x", u=True, p=True)]
    assert lint_configs(ok) == []
    msgs = lint_configs([*ok, bad])
    assert len(msgs) == 1 and msgs[0].startswith("bad:")


def test_broker_passes_valid_declared_roles():
    broker = Broker({"extractor": D("extractor", u=True)})
    eff = broker.check("extractor", untrusted_data=True)
    assert eff.untrusted and not eff.private and not eff.effect


def test_broker_catches_understated_declaration_via_tools():
    broker = Broker({"extractor": D("extractor", u=True, p=True)})
    with pytest.raises(RuleOfTwoViolation):
        broker.check("extractor", tools=["http_request"])


def test_broker_catches_private_data_attached_to_untrusted_effect_call():
    broker = Broker({"answerer": D("answerer", u=True, c=True)})
    with pytest.raises(RuleOfTwoViolation):
        broker.check("answerer", private_data=True)


def test_broker_unknown_call_uses_actual_attachments_only():
    broker = Broker()
    broker.check("adhoc", tools=["read_fact"], private_data=True)
    with pytest.raises(RuleOfTwoViolation):
        broker.check("adhoc", tools=["send_email"], private_data=True, untrusted_data=True)


def test_declaration_count():
    assert D("a", True, True, False).count == 2
    assert not D("a", True, True, False).violates
