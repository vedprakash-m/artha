"""Direct unit tests for scripts/items_view.py."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from unittest.mock import patch

import items_view as iv

ARTHA_TEST_OWNER = {
    "requirements": ["RA-03", "INFRA-UX"],
    "modules": ["scripts/items_view.py"],
}


_SAMPLE_OPEN_ITEMS = f"""---
domain: open_items
schema_version: '1.0'
---

## Active

- id: OI-001
  status: open
  priority: P0
  source_domain: finance
  deadline: "{(date.today() - timedelta(days=2)).isoformat()}"
  description: "Call bank to confirm wire transfer"

- id: OI-002
  status: open
  priority: P1
  source_domain: health
  deadline: "{(date.today() + timedelta(days=2)).isoformat()}"
  description: "Call clinic to schedule annual physical"

- id: OI-003
  status: done
  priority: P2
  source_domain: home
  deadline: "{(date.today() + timedelta(days=10)).isoformat()}"
  description: "Replace air filter"
"""


def test_parse_items_and_deadline_helpers():
    items = iv._parse_items(_SAMPLE_OPEN_ITEMS)
    assert [it["id"] for it in items] == ["OI-001", "OI-002", "OI-003"]
    assert iv._is_quick_task(items[0]) is False  # overdue days=-2 outside 0..7 window
    assert iv._is_quick_task(items[1]) is True   # 2d <= 7d and starts with "Call"
    assert iv._days_until("not-a-date") is None
    assert "overdue" in iv._deadline_label(-2)
    assert "due today" in iv._deadline_label(0)
    assert "2d" in iv._deadline_label(2)


def test_formats_flash_standard_quick_and_digest():
    items = iv._parse_items(_SAMPLE_OPEN_ITEMS)
    flash = iv._format_flash(items, filter_status="open")
    assert "OI-001" in flash and "OI-003" not in flash

    standard = iv._format_standard(items, filter_domain="finance", filter_status="open")
    assert "OI-001" in standard and "OI-002" not in standard

    quick = iv._format_quick(items)
    assert "OI-002" in quick and "OI-001" not in quick

    digest = iv._format_digest(items)
    assert "Finance" in digest and "Home" in digest and "Total tracked: **3**" in digest


def test_main_cli_modes_and_missing_file(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "missing.md"
    monkeypatch.setattr(iv, "_ITEMS_FILE", missing)
    monkeypatch.setattr(sys, "argv", ["items_view.py"])
    assert iv.main() == 1
    assert "not found" in capsys.readouterr().err

    oi = tmp_path / "open_items.md"
    oi.write_text(_SAMPLE_OPEN_ITEMS, encoding="utf-8")
    monkeypatch.setattr(iv, "_ITEMS_FILE", oi)

    for argv, expected in [
        (["items_view.py", "--format", "flash"], "OI-001"),
        (["items_view.py", "--format", "digest"], "All Items by Domain"),
        (["items_view.py", "--quick"], "Quick Wins"),
        (["items_view.py", "--format", "standard", "--domain", "health"], "OI-002"),
    ]:
        with patch.object(sys, "argv", argv):
            assert iv.main() == 0
        assert expected in capsys.readouterr().out
