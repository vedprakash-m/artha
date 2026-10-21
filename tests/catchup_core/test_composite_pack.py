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
    assert "gmail" in connectors
    assert "calendar" in connectors


def test_composite_pack_empty_raises():
    with pytest.raises(dp.PackError, match="empty sequence"):
        dp.composite_pack([])
