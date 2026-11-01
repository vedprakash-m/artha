"""RECALL-PHRASES: a pack may declare wording that grounds an enum value (VA-01..VA-06)."""
from __future__ import annotations

import pytest
import yaml

from catchup_core import domain_pack as dp
from catchup_core.extract import supports_value

PACKS = dp.__file__.replace("domain_pack.py", "packs/")


def pack_with(aliases):
    data = yaml.safe_load(open(PACKS + "vehicle.yaml"))
    for pred in data["predicates"]:
        if pred["name"] == "recall_status":
            if aliases is None:
                pred.pop("aliases", None)
            else:
                pred["aliases"] = aliases
    return dp.load_pack(data)


def test_aliases_load_as_value_to_words_pairs():
    pred = pack_with({"open": ["issued", "new recall"]}).predicates["recall_status"]
    assert pred.aliases == (("open", ("issued", "new recall")),)                                  # VA-01


def test_no_aliases_by_default():
    assert pack_with(None).predicates["recall_status"].aliases == ()                              # VA-02


@pytest.mark.parametrize("aliases", [{"nope": ["issued"]}, {"open": "issued"}, {"open": []}, {"open": [3]}, ["open"]])
def test_malformed_aliases_are_rejected(aliases):
    with pytest.raises(dp.PackError, match="aliases"):
        pack_with(aliases)                                                                         # VA-03


def test_an_alias_grounds_its_value_only_when_declared():
    quote = "Recall Issued for your vehicle"
    assert supports_value(pack_with({"open": ["issued"]}).predicates["recall_status"], "open", quote)
    assert not supports_value(pack_with(None).predicates["recall_status"], "open", quote)         # VA-04


def test_an_alias_never_grounds_another_value():
    pred = pack_with({"open": ["issued"]}).predicates["recall_status"]
    assert not supports_value(pred, "remedied", "Recall Issued for your vehicle")                 # VA-05


def test_negated_hedged_or_questioned_alias_wording_is_still_rejected():
    pred = pack_with({"open": ["issued"]}).predicates["recall_status"]
    for quote in ("No recall was issued", "A recall may be issued", "Was a recall issued?", "recall not issued"):
        assert not supports_value(pred, "open", quote)                                             # VA-06


def test_hints_ask_for_a_short_stable_obligation_name():                                           # VA-07
    """A name every mail of the bill contains keeps one obligation from splitting into several (REF-STABILITY)."""
    finance = dp.load_pack_file(PACKS + "finance.yaml").predicates["bill_due_date"]
    vehicle = dp.load_pack_file(PACKS + "vehicle.yaml").predicates["recall_status"]
    assert "biller name only" in finance.hint and "make only" in vehicle.hint
