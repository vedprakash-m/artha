from __future__ import annotations

from datetime import datetime, timezone

import pytest

import session_stop as ss  # noqa: E402
import stale_banner as sb  # noqa: E402

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def tree(tmp_path, *, draft, last):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "state").mkdir()
    if draft:
        (tmp_path / "tmp" / "briefing_incoming_claude.md").write_text("x")
    if last:
        (tmp_path / "state" / "health-check.md").write_text(f'---\nlast_catch_up: "{last}"\n---\n')
    return tmp_path


def test_no_draft_means_no_catch_up_and_no_complaint(tmp_path):
    root = tree(tmp_path, draft=False, last=None)
    assert ss.check(root, local=tmp_path / "L", now=NOW) == []


def test_catch_up_without_a_fresh_health_record_is_recorded_and_shown_once(tmp_path):
    root = tree(tmp_path, draft=True, last="2026-10-11T15:50:00Z")
    problems = ss.check(root, local=tmp_path / "L", now=NOW, archive=lambda: (root / "tmp" / "briefing_incoming_claude.md").unlink())
    assert problems == ["state/health-check.md was not updated by this Catch-up"]
    assert sb.take_stop_failures(tmp_path / "L")[0].endswith("was not updated by this Catch-up")
    assert sb.take_stop_failures(tmp_path / "L") == []          # cleared after being shown


def test_fresh_health_record_and_archive_pass(tmp_path):
    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    assert ss.check(root, local=tmp_path / "L", now=NOW, archive=lambda: (root / "tmp" / "briefing_incoming_claude.md").unlink()) == []


def test_archive_failure_and_leftover_draft_are_both_recorded(tmp_path):
    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    def boom():
        raise OSError("disk full")
    problems = ss.check(root, local=tmp_path / "L", now=NOW, archive=boom)
    assert "Briefing archive failed: OSError" in problems
    assert "Briefing draft is still in tmp/ after the archive step" in problems


def test_archive_runs_under_the_draft_publication_fence(tmp_path, monkeypatch):
    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    entered = []

    class Fence:
        def __enter__(self):
            entered.append("entered")

        def __exit__(self, *_args):
            entered.append("exited")

    monkeypatch.setattr("catchup_core.session_lock.publication_fence", lambda _path: Fence())
    problems = ss.check(
        root,
        local=tmp_path / "L",
        now=NOW,
        archive=lambda: (root / "tmp" / "briefing_incoming_claude.md").unlink(),
    )
    assert problems == []
    assert entered == ["entered", "exited"]


def test_archive_refuses_draft_replaced_before_publication(tmp_path, monkeypatch):
    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    calls = []

    class Fence:
        def __enter__(self):
            (root / "tmp" / "briefing_incoming_claude.md").write_bytes(b"replacement")

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("catchup_core.session_lock.publication_fence", lambda _path: Fence())
    problems = ss.check(
        root,
        local=tmp_path / "L",
        now=NOW,
        archive=lambda: calls.append("archived"),
    )
    assert calls == []
    assert problems == [
        "Briefing archive failed: RuntimeError",
        "Briefing draft is still in tmp/ after the archive step",
    ]


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
                        archive=lambda: (root / "tmp" / "briefing_incoming_claude.md").unlink())
    assert problems == ["state/health-check.md was not updated by this Catch-up"]
    assert ss.fallback_path(tmp_path).name == "stop-hook-failures.log"


def test_legacy_draft_is_held_instead_of_published(tmp_path):
    root = tree(tmp_path, draft=False, last="2026-10-20T11:00:00Z")
    legacy = root / "tmp" / "briefing_draft_claude.md"
    legacy.write_text("legacy", encoding="utf-8")

    problems = ss.check(root, local=tmp_path / "L", now=NOW)

    assert legacy.exists()
    assert problems == [
        "Legacy briefing draft is held: stage it as tmp/briefing_incoming_claude.md",
    ]


def test_default_archive_uses_canonical_owner_and_deletes_exact_draft(
    tmp_path, monkeypatch,
):
    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    draft = root / "tmp" / "briefing_incoming_claude.md"
    calls = []

    monkeypatch.setattr(
        "lib.briefing_archive.save",
        lambda text, **metadata: calls.append((text, metadata)) or {"status": "ok"},
    )

    assert ss.check(root, local=tmp_path / "L", now=NOW) == []
    assert calls == [
        (
            "x",
            {
                "source": "interactive_cli",
                "runtime": "claude",
            },
        )
    ]
    assert not draft.exists()


def test_failed_canonical_archive_keeps_staged_draft(tmp_path, monkeypatch):
    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    draft = root / "tmp" / "briefing_incoming_claude.md"
    monkeypatch.setattr(
        "lib.briefing_archive.save",
        lambda _text, **_metadata: {"status": "failed", "error": "injection_detected"},
    )

    problems = ss.check(root, local=tmp_path / "L", now=NOW)

    assert draft.exists()
    assert problems == [
        "Briefing archive failed: RuntimeError",
        "Briefing draft is still in tmp/ after the archive step",
    ]


def test_legacy_auto_archive_helper_delegates_to_canonical_owner(
    tmp_path, monkeypatch,
):
    import auto_archive_briefs as auto_archive

    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    draft = root / "tmp" / "briefing_incoming_claude.md"
    calls = []

    class Fence:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(auto_archive, "ROOT", root)
    monkeypatch.setattr(auto_archive, "DRAFT", draft)
    monkeypatch.setattr(
        "catchup_core.session_lock.publication_fence",
        lambda _path: Fence(),
    )
    monkeypatch.setattr(
        "session_stop.archive_staged_briefing",
        lambda candidate_root, candidate_draft, expected:
        calls.append((candidate_root, candidate_draft, expected)),
    )

    auto_archive.main()

    assert calls == [(root, draft, b"x")]


def test_legacy_auto_archive_helper_is_noop_without_staged_draft(
    tmp_path, monkeypatch,
):
    import auto_archive_briefs as auto_archive

    monkeypatch.setattr(auto_archive, "DRAFT", tmp_path / "missing.md")

    assert auto_archive.main() is None


def test_unreadable_staged_draft_is_explicitly_held(tmp_path, monkeypatch):
    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    draft = root / "tmp" / "briefing_incoming_claude.md"
    original = type(draft).read_bytes

    def read_bytes(path):
        if path == draft:
            raise OSError("access denied")
        return original(path)

    monkeypatch.setattr(type(draft), "read_bytes", read_bytes)

    assert ss.check(root, local=tmp_path / "L", now=NOW) == [
        "Briefing draft could not be read; publication is held",
    ]


def test_archive_binding_rejects_unexpected_or_changed_draft(tmp_path, monkeypatch):
    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    draft = root / "tmp" / "briefing_incoming_claude.md"
    wrong = root / "tmp" / "other.md"
    wrong.write_text("other", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unexpected briefing draft"):
        ss.archive_staged_briefing(root, wrong, b"other")
    with pytest.raises(RuntimeError, match="changed before archive"):
        ss.archive_staged_briefing(root, draft, b"stale")


def test_archive_refuses_draft_changed_after_canonical_save(tmp_path, monkeypatch):
    root = tree(tmp_path, draft=True, last="2026-10-20T11:00:00Z")
    draft = root / "tmp" / "briefing_incoming_claude.md"

    def save_and_replace(_text, **_metadata):
        draft.write_bytes(b"replacement")
        return {"status": "ok"}

    monkeypatch.setattr("lib.briefing_archive.save", save_and_replace)

    with pytest.raises(RuntimeError, match="changed after archive"):
        ss.archive_staged_briefing(root, draft, b"x")
