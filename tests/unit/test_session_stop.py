from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import session_stop as ss  # noqa: E402
import stale_banner as sb  # noqa: E402

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def tree(tmp_path, *, draft, last):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "state").mkdir()
    if draft:
        (tmp_path / "tmp" / "briefing_draft_claude.md").write_text("x")
    if last:
        (tmp_path / "state" / "health-check.md").write_text(f'---\nlast_catch_up: "{last}"\n---\n')
    return tmp_path


def test_no_draft_means_no_catch_up_and_no_complaint(tmp_path):
    root = tree(tmp_path, draft=False, last=None)
    assert ss.check(root, local=tmp_path / "L", now=NOW) == []


def test_catch_up_without_a_fresh_health_record_is_recorded_and_shown_once(tmp_path):
    root = tree(tmp_path, draft=True, last="2026-10-11T15:50:00Z")
    problems = ss.check(root, local=tmp_path / "L", now=NOW, archive=lambda: (root / "tmp" / "briefing_draft_claude.md").unlink())
    assert problems == ["state/health-check.md was not updated by this Catch-up"]
    assert sb.take_stop_failures(tmp_path / "L")[0].endswith("was not updated by this Catch-up")
    assert sb.take_stop_failures(tmp_path / "L") == []          # cleared after being shown


def test_fresh_health_record_and_archive_pass(tmp_path):
    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    assert ss.check(root, local=tmp_path / "L", now=NOW, archive=lambda: (root / "tmp" / "briefing_draft_claude.md").unlink()) == []


def test_archive_failure_and_leftover_draft_are_both_recorded(tmp_path):
    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    def boom():
        raise OSError("disk full")
    problems = ss.check(root, local=tmp_path / "L", now=NOW, archive=boom)
    assert "Briefing archive failed: OSError" in problems
    assert "Briefing draft is still in tmp/ after the archive step" in problems


def test_main_never_raises(monkeypatch):
    monkeypatch.setattr(ss, "check", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert ss.main() == 0


def test_main_runs_the_real_check_and_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    monkeypatch.setattr(ss, "check", lambda: [])
    assert ss.main() == 0


def test_unreadable_health_timestamp_counts_as_not_updated(tmp_path):
    root = tree(tmp_path, draft=True, last="not-a-date")
    problems = ss.check(root, local=tmp_path / "L", now=NOW,
                        archive=lambda: (root / "tmp" / "briefing_draft_claude.md").unlink())
    assert problems == ["state/health-check.md was not updated by this Catch-up"]
    assert ss.fallback_path(tmp_path).name == "stop-hook-failures.log"
