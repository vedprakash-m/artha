"""CORE-ITEMS (specs/features/core-open-items.md): the core writes its own Open Items file for released Domains."""
from __future__ import annotations

import sys
from datetime import date

import pytest

from catchup_core import coordinator as co
from catchup_core import core_items
from catchup_core.contracts import ExecutionMode, Severity
from catchup_core.triage import AlertDraft

from test_coordinator import cal, email, req, world  # noqa: F401


def draft(key="k1", deadline=date(2026, 11, 4), severity=Severity.P2, **kw):
    fields = dict(obligation_key=key, domain="kids_school", subject_id="kid1", predicate="signup_deadline", fact_id="f1",
                  severity=severity, deadline=deadline, latest_useful_action=date(2026, 11, 3), days_until=4,
                  state="actionable")
    return AlertDraft(**{**fields, **kw})


def test_render_has_a_core_owned_header_and_one_row_per_obligation():                                  # CI-03
    text = core_items.render([draft(), draft("k2", deadline=None, severity=None, domain="finance", subject_id="owner",
                                             predicate="bill_due_date", days_until=None, state="no_deadline")],
                             {"kid1": "Synthetic Kid", "owner": "Synthetic Owner"})
    assert "core-owned" in text and "regenerated" in text
    assert "| kids_school | Synthetic Kid | signup deadline | 2026-11-04 | 4 | P2 | actionable | k1 |" in text
    assert "| finance | Synthetic Owner | bill due date |  |  | unscored | no_deadline | k2 |" in text


def test_render_with_nothing_open_says_none():                                                         # CI-06
    assert "none" in core_items.render([], {}).lower()


def test_rows_are_ordered_by_due_date_then_key():
    text = core_items.render([draft("b", date(2026, 11, 18)), draft("a", date(2026, 11, 4)), draft("c", date(2026, 11, 4))], {})
    assert [text.index(f"| {k} |") for k in ("a", "c", "b")] == sorted(text.index(f"| {k} |") for k in ("a", "c", "b"))


def test_an_obligation_listed_twice_appears_once():
    text = core_items.render([draft("k1"), draft("k1"), draft("k2", date(2026, 11, 18))], {})
    assert text.count("| k1 |") == 1 and text.count("| k2 |") == 1


def test_publish_is_atomic_private_and_idempotent(tmp_path):                                           # CI-04, CI-05
    path = tmp_path / "state" / "core_open_items.md"
    path.parent.mkdir()
    assert core_items.publish(path, "one\n") is True
    if sys.platform == "win32":
        from catchup_core.quarantine_fs_windows import open_owner_private_path
        with open_owner_private_path(path) as opened:
            assert opened.read(100) == b"one\n"
    else:
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    before = path.stat().st_mtime_ns
    assert core_items.publish(path, "one\n") is False and path.stat().st_mtime_ns == before
    assert core_items.publish(path, "two\n") is True and path.read_text() == "two\n"
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows private DACL")
def test_publish_replaces_a_legacy_non_private_file_on_windows(tmp_path):                            # CI-04
    """Given a file written before the private-DACL change (inherited permissions), when the core publishes,
    then it regenerates the file privately instead of failing every later authoritative run."""
    from catchup_core.backup import atomic_bytes
    from catchup_core.quarantine_fs_windows import open_owner_private_path
    path = tmp_path / "core_open_items.md"
    atomic_bytes(path, b"inherited permissions\n")
    assert core_items.publish(path, "one\n") is True
    with open_owner_private_path(path) as opened:
        assert opened.read(100) == b"one\n"
    assert list(tmp_path.iterdir()) == [path]
    assert core_items.publish(path, "two\n") is True and path.read_text() == "two\n"


def test_authoritative_run_publishes_surfaced_obligations_once(world):  # noqa: F811  CI-01
    _ledger, _blobs, make, _published = world
    seen = []
    result = co.run_catchup(req(), make({"school_email": email(), "calendar": cal()},
                                        items_publisher=lambda alerts: seen.append([a.obligation_key for a in alerts])))
    assert result.exit_code() == 0 and len(seen) == 1 and seen[0]


def test_shadow_run_never_publishes(world):  # noqa: F811  CI-02
    _ledger, _blobs, make, _published = world
    seen = []
    co.run_catchup(req(mode=ExecutionMode.SHADOW), make({"school_email": email(), "calendar": cal()},
                                                        items_publisher=lambda alerts: seen.append(alerts)))
    assert seen == []
