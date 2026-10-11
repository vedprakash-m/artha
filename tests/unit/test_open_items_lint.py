"""WP-03 / §12.2 item 8: open_items.md linter and deterministic repair."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import open_items_lint as oi  # noqa: E402

GOOD = '''---
domain: open_items
schema_version: "1.0"
---

# Open Items

## Active

- id: OI-001
  date_added: 2026-10-04
  source_domain: kids
  description: "Registration closes"
  deadline: 2026-10-19
  priority: P1
  status: open
  origin: pipeline
  todo_id: ""

## Closed

- id: OI-002
  date_added: 2026-05-16
  source_domain: finance
  description: "Paid"
  deadline: ""
  priority: P2
  status: done
  closed_date: 2026-05-20
'''


def codes(text):
    return sorted({i.code for i in oi.lint(text)})


def test_clean_file_has_no_issues():
    assert oi.lint(GOOD) == []


def test_unindented_field_detected_and_repaired():
    bad = GOOD.replace("  date_added: 2026-10-04", "date_added: 2026-10-04")
    assert "unindented-field" in codes(bad)
    fixed, changes = oi.repair(bad)
    assert fixed == GOOD
    assert len(changes) == 1 and changes[0].line == 11
    assert oi.lint(fixed) == []


def test_repair_is_idempotent_and_touches_nothing_else():
    bad = GOOD.replace("  date_added: 2026-10-04", "date_added: 2026-10-04")
    once, _ = oi.repair(bad)
    twice, changes = oi.repair(once)
    assert once == twice and changes == []


def test_only_known_item_fields_are_reindented():
    text = GOOD + "\nnotes: stray top-level prose that is not an item field\n"
    fixed, changes = oi.repair(text)
    assert changes == []
    assert fixed == text


def test_missing_required_field_reported_not_invented():
    bad = GOOD.replace('  description: "Registration closes"\n', "")
    assert "missing-required" in codes(bad)
    fixed, changes = oi.repair(bad)
    assert fixed == bad, "the repair never fabricates a value"


@pytest.mark.parametrize("old,new,code", [
    ("OI-001", "OI-1x", "bad-id"),
    ("priority: P1", "priority: urgent", "bad-priority"),
    ("status: open", "status: maybe", "bad-status"),
    ("date_added: 2026-10-04", "date_added: 10/04/2026", "bad-date"),
    ("deadline: 2026-10-19", "deadline: soon", "bad-date"),
])
def test_field_value_rules(old, new, code):
    assert code in codes(GOOD.replace(old, new, 1))


def test_duplicate_id_detected():
    assert "duplicate-id" in codes(GOOD.replace("OI-002", "OI-001"))


def test_closed_status_in_active_section_is_a_warning_not_an_error(tmp_path):
    bad = GOOD.replace("status: open", "status: done", 1)
    assert "section-mismatch" in codes(bad)
    assert all(not i.is_error for i in oi.lint(bad))
    path = tmp_path / "open_items.md"
    path.write_text(bad)
    assert oi.main(["--path", str(path), "--check"]) == 0


def test_cli_check_fails_then_fix_passes(tmp_path, monkeypatch, capsys):
    path = tmp_path / "open_items.md"
    path.write_text(GOOD.replace("  date_added: 2026-10-04", "date_added: 2026-10-04"))
    assert oi.main(["--path", str(path), "--check"]) == 1
    assert oi.main(["--path", str(path), "--fix"]) == 0
    assert oi.main(["--path", str(path), "--check"]) == 0
    assert path.read_text() == GOOD


def test_check_reports_line_numbers(tmp_path, capsys):
    path = tmp_path / "open_items.md"
    path.write_text(GOOD.replace("  date_added: 2026-10-04", "date_added: 2026-10-04"))
    oi.main(["--path", str(path), "--check"])
    out = capsys.readouterr().out
    assert ":11:" in out and "unindented-field" in out
