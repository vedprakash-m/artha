from __future__ import annotations

from catchup_core import shadow as sh
from catchup_core.scorecard import score

BRIEFING = """ARTHA · Sunday
━━━━━━━━━━━━━━━━━━━━
🔴 CRITICAL
- AP registration closes 10/19 for Parth
- Passport renewal window opens
🟠 URGENT
• Archana MyChart results unread
1. Capital One migration
🟡 FYI
- Newsletter from school
"""


def test_extracts_only_critical_and_urgent_bullets():
    assert sh.incumbent_items(BRIEFING) == [
        "AP registration closes 10/19 for Parth", "Passport renewal window opens",
        "Archana MyChart results unread", "Capital One migration"]


def test_no_sections_no_items():
    assert sh.incumbent_items("Nothing urgent.\n- a bullet") == []


def test_unlabeled_items_need_review_and_are_not_matched():
    items = sh.incumbent_items(BRIEFING)
    labels = {"AP registration closes 10/19 for Parth": "kids:parth:ap_registration"}
    c = sh.compare(["kids:parth:ap_registration", "kids:trisha:essay"], items, labels)
    assert c.matched == ("kids:parth:ap_registration",)
    assert c.core_only == ("kids:trisha:essay",)
    assert len(c.needs_review) == 3 and not c.clean


def test_labeled_item_the_core_missed_is_a_regression():
    items = ["Passport renewal window opens"]
    c = sh.compare([], items, {items[0]: "immigration:passport"})
    assert c.incumbent_only == ("immigration:passport",) and not c.clean
    case = sh.as_case("c1", c, quiet_claim=False, coverage_complete=True, citations_resolve=True)
    s = score([case])
    assert not s.passes and s.regressions_vs_incumbent == 1


def test_parity_scores_clean():
    items = ["AP registration closes 10/19 for Parth"]
    c = sh.compare(["kids:parth:ap"], items, {items[0]: "kids:parth:ap"})
    assert c.clean
    assert score([sh.as_case("c", c, quiet_claim=False, coverage_complete=True, citations_resolve=True)]).passes
