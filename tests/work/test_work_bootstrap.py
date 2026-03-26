"""
tests/work/test_work_bootstrap.py — Unit tests for Work OS guided setup.

Validates scripts/work_bootstrap.py:
  - QUESTIONS list             — complete, all required fields present
  - _load_profile()            — reads existing yaml
  - _save_profile()            — writes atomically
  - _set_nested()              — dot-notation key setter
  - _get_nested()              — dot-notation key getter
  - _pending_questions()       — skips already-answered questions
  - _deep_merge()              — merges dicts recursively
  - bootstrap_from_answers()   — writes all answers, marks complete
  - bootstrap_dry_run()        — prints questions without writing
  - bootstrap_from_import()    — merges archive JSON into profile
  - main() --answers           — non-interactive JSON round-trip
  - main() --dry-run           — no writes, prints questions
  - main() --import-file       — imports an archive
  - Bootstrap complete flag    — work.bootstrap_status.completed = True

Run: pytest tests/work/test_work_bootstrap.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import work_bootstrap  # type: ignore
from work_bootstrap import (  # type: ignore
    QUESTIONS,
    _load_profile,
    _save_profile,
    _set_nested,
    _get_nested,
    _pending_questions,
    _deep_merge,
    _mark_bootstrap_complete,
    bootstrap_from_answers,
    bootstrap_dry_run,
    bootstrap_from_import,
    main,
    QUESTIONS,
    _write_org_calendar,
    _ORG_CAL_VALID_TYPES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_profile(tmp_path: Path, monkeypatch):
    """Redirect all profile I/O to a temp directory."""
    profile = tmp_path / "user_profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile)
    monkeypatch.setattr(work_bootstrap, "_CONFIG_DIR", tmp_path)
    return profile


# ---------------------------------------------------------------------------
# QUESTIONS sanity checks
# ---------------------------------------------------------------------------

def test_questions_exist():
    assert len(QUESTIONS) >= 10


def test_questions_have_required_fields():
    for q in QUESTIONS:
        assert "id" in q, f"missing id: {q}"
        assert "key" in q, f"missing key: {q}"
        assert "prompt" in q, f"missing prompt: {q}"
        assert "type" in q, f"missing type: {q}"


def test_questions_ids_unique():
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids)), "Duplicate question IDs detected"


def test_questions_keys_unique():
    keys = [q["key"] for q in QUESTIONS]
    assert len(keys) == len(set(keys)), "Duplicate question keys detected"


def test_question_types_valid():
    valid_types = {"str", "int", "multiline_list", "org_calendar_list"}
    for q in QUESTIONS:
        assert q["type"] in valid_types, f"Unknown type '{q['type']}' in question {q['id']}"


def test_questions_cover_essential_topics():
    ids = {q["id"] for q in QUESTIONS}
    essential = {"org", "role", "core_hours", "connect_goals", "redact_keywords"}
    missing = essential - ids
    assert not missing, f"Missing essential questions: {missing}"


# ---------------------------------------------------------------------------
# _set_nested / _get_nested
# ---------------------------------------------------------------------------

def test_set_nested_simple():
    d: dict = {}
    _set_nested(d, "foo", "bar")
    assert d["foo"] == "bar"


def test_set_nested_deep():
    d: dict = {}
    _set_nested(d, "work.employer.name", "Microsoft")
    assert d["work"]["employer"]["name"] == "Microsoft"


def test_set_nested_overwrites():
    d = {"work": {"employer": {"name": "Old Corp"}}}
    _set_nested(d, "work.employer.name", "New Corp")
    assert d["work"]["employer"]["name"] == "New Corp"


def test_set_nested_does_not_wipe_sibling():
    d: dict = {"work": {"employer": {"name": "Corp", "size": "large"}}}
    _set_nested(d, "work.employer.name", "New Corp")
    assert d["work"]["employer"]["size"] == "large"


def test_get_nested_simple():
    d = {"foo": "bar"}
    assert _get_nested(d, "foo") == "bar"


def test_get_nested_deep():
    d = {"work": {"role": {"title": "PM"}}}
    assert _get_nested(d, "work.role.title") == "PM"


def test_get_nested_missing():
    assert _get_nested({}, "work.role.title") is None


def test_get_nested_partial():
    d = {"work": {"role": {}}}
    assert _get_nested(d, "work.role.title") is None


# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------

def test_deep_merge_basic():
    base = {"a": 1, "b": 2}
    _deep_merge(base, {"b": 99, "c": 3})
    assert base == {"a": 1, "b": 99, "c": 3}


def test_deep_merge_nested():
    base = {"work": {"employer": {"name": "Acme"}, "role": {"title": "IC"}}}
    _deep_merge(base, {"work": {"employer": {"size": "SMB"}, "boundary": {}}})
    assert base["work"]["employer"]["name"] == "Acme"
    assert base["work"]["employer"]["size"] == "SMB"
    assert base["work"]["role"]["title"] == "IC"


def test_deep_merge_list_overwrite():
    base = {"goals": ["goal1"]}
    _deep_merge(base, {"goals": ["goal2", "goal3"]})
    assert base["goals"] == ["goal2", "goal3"]


# ---------------------------------------------------------------------------
# _pending_questions
# ---------------------------------------------------------------------------

def test_pending_all_unanswered():
    profile = {}
    pending = _pending_questions(profile)
    assert len(pending) == len(QUESTIONS)


def test_pending_some_answered():
    profile: dict = {}
    _set_nested(profile, QUESTIONS[0]["key"], "somevalue")
    pending = _pending_questions(profile)
    assert len(pending) == len(QUESTIONS) - 1


def test_pending_all_answered():
    profile: dict = {}
    for q in QUESTIONS:
        _set_nested(profile, q["key"], "fakevalue")
    pending = _pending_questions(profile)
    assert pending == []


def test_pending_empty_string_still_pending():
    profile: dict = {}
    _set_nested(profile, QUESTIONS[0]["key"], "")
    pending = _pending_questions(profile)
    # Empty string counts as unanswered
    assert any(q["id"] == QUESTIONS[0]["id"] for q in pending)


# ---------------------------------------------------------------------------
# _save_profile / _load_profile
# ---------------------------------------------------------------------------

def test_save_and_load(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)
    data = {"work": {"employer": {"name": "TestCorp"}}}
    _save_profile(data)
    loaded = _load_profile()
    assert loaded["work"]["employer"]["name"] == "TestCorp"


def test_load_missing_returns_empty(tmp_path, monkeypatch):
    profile_path = tmp_path / "nonexistent.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)
    assert _load_profile() == {}


def test_save_atomic(tmp_path, monkeypatch):
    """No .tmp file left behind after successful save."""
    profile_path = tmp_path / "profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)
    _save_profile({"key": "value"})
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Stale .tmp file: {tmp_files}"


# ---------------------------------------------------------------------------
# _mark_bootstrap_complete
# ---------------------------------------------------------------------------

def test_mark_bootstrap_complete():
    data: dict = {}
    _mark_bootstrap_complete(data)
    assert data["work"]["bootstrap_status"]["completed"] is True
    assert "completed_at" in data["work"]["bootstrap_status"]


# ---------------------------------------------------------------------------
# bootstrap_from_answers
# ---------------------------------------------------------------------------

def test_bootstrap_from_answers_writes_all(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "user_profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)

    answers = {
        "org": "Contoso",
        "role": "Senior PM",
        "core_hours": "09:00-17:00",
        "timezone": "America/Chicago",
    }
    rc = bootstrap_from_answers(answers)
    assert rc == 0
    loaded = _load_profile()
    assert loaded["work"]["employer"]["name"] == "Contoso"
    assert loaded["work"]["role"]["title"] == "Senior PM"
    assert loaded["work"]["bootstrap_status"]["completed"] is True


def test_bootstrap_from_answers_direct_key_path(tmp_path, monkeypatch):
    profile_path = tmp_path / "user_profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)

    rc = bootstrap_from_answers({"work.team.name": "Copilot"})
    assert rc == 0
    loaded = _load_profile()
    assert loaded["work"]["team"]["name"] == "Copilot"


def test_bootstrap_from_answers_prints_summary(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "user_profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)
    bootstrap_from_answers({"org": "Corp"})
    captured = capsys.readouterr()
    assert "written" in captured.out.lower() or "bootstrap" in captured.out.lower()


# ---------------------------------------------------------------------------
# bootstrap_dry_run
# ---------------------------------------------------------------------------

def test_bootstrap_dry_run_prints_questions(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "user_profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)
    rc = bootstrap_dry_run()
    captured = capsys.readouterr()
    assert rc == 0
    assert "BOOTSTRAP" in captured.out
    assert "org" in captured.out or "employer" in captured.out.lower()


def test_bootstrap_dry_run_does_not_write(tmp_path, monkeypatch):
    profile_path = tmp_path / "user_profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)
    bootstrap_dry_run()
    # Profile file should NOT be created by dry run
    assert not profile_path.exists()


def test_bootstrap_dry_run_shows_answered_status(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "user_profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)
    # Pre-answer one question
    d: dict = {}
    _set_nested(d, QUESTIONS[0]["key"], "SomeValue")
    _save_profile(d)
    bootstrap_dry_run()
    captured = capsys.readouterr()
    assert "✅" in captured.out  # answered question marked done


# ---------------------------------------------------------------------------
# bootstrap_from_import
# ---------------------------------------------------------------------------

def test_bootstrap_from_import_json(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "user_profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)

    archive = {"work": {"employer": {"name": "ImportedCorp"}, "role": {"title": "Staff PM"}}}
    import_file = tmp_path / "archive.json"
    import_file.write_text(json.dumps(archive), encoding="utf-8")

    rc = bootstrap_from_import(import_file)
    assert rc == 0
    loaded = _load_profile()
    assert loaded["work"]["employer"]["name"] == "ImportedCorp"
    assert loaded["work"]["role"]["title"] == "Staff PM"
    assert loaded["work"]["bootstrap_status"]["completed"] is True


def test_bootstrap_from_import_missing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "profile.yaml")
    rc = bootstrap_from_import(tmp_path / "nonexistent.json")
    assert rc == 2


def test_bootstrap_from_import_invalid_json(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json!}", encoding="utf-8")
    rc = bootstrap_from_import(bad_file)
    assert rc == 2


def test_bootstrap_from_import_merges_existing(tmp_path, monkeypatch):
    profile_path = tmp_path / "user_profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)

    # Existing profile has a privacy section
    existing: dict = {"privacy": {"redact_keywords": ["ConfidentialProject"]}}
    _save_profile(existing)

    # Import only touches work section
    archive = {"work": {"employer": {"name": "NewCorp"}}}
    import_file = tmp_path / "archive.json"
    import_file.write_text(json.dumps(archive), encoding="utf-8")

    bootstrap_from_import(import_file)
    loaded = _load_profile()
    assert loaded["privacy"]["redact_keywords"] == ["ConfidentialProject"]
    assert loaded["work"]["employer"]["name"] == "NewCorp"


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------

def test_main_dry_run(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "user_profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)
    rc = main(["--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "BOOTSTRAP" in captured.out
    assert not profile_path.exists()


def test_main_answers_json(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "user_profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)
    answers_json = json.dumps({"org": "FutureCorp", "role": "Director"})
    rc = main(["--answers", answers_json])
    captured = capsys.readouterr()
    assert rc == 0
    loaded = _load_profile()
    assert loaded["work"]["employer"]["name"] == "FutureCorp"


def test_main_answers_invalid_json(capsys):
    rc = main(["--answers", "{bad json}"])
    assert rc == 2


def test_main_import(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "profile.yaml"
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", profile_path)
    archive_data = {"work": {"employer": {"name": "CliImport"}}}
    archive_file = tmp_path / "archive.json"
    archive_file.write_text(json.dumps(archive_data), encoding="utf-8")
    rc = main(["--import-file", str(archive_file)])
    assert rc == 0
    loaded = _load_profile()
    assert loaded["work"]["employer"]["name"] == "CliImport"


def test_main_import_missing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
    rc = main(["--import-file", str(tmp_path / "does-not-exist.json")])
    assert rc == 2


# ===========================================================================
# Q13: org_calendar bootstrap question (§8.8 v2.3.0)
# ===========================================================================

class TestBootstrapQ13:
    """Q13 — org_calendar question and _write_org_calendar() helper."""

    def test_q13_present_in_questions_list(self):
        ids = [q["id"] for q in QUESTIONS]
        assert "org_calendar" in ids

    def test_q13_is_optional(self):
        q = next(q for q in QUESTIONS if q["id"] == "org_calendar")
        assert q.get("optional") is True

    def test_q13_type_is_org_calendar_list(self):
        q = next(q for q in QUESTIONS if q["id"] == "org_calendar")
        assert q["type"] == "org_calendar_list"

    def test_write_org_calendar_valid_row(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        cal_path = tmp_path / "state" / "work" / "work-org-calendar.md"
        count = _write_org_calendar(["connect_submission:2026-06-15:H1 Connect deadline"])
        assert count == 1
        content = cal_path.read_text(encoding="utf-8")
        assert "connect_submission" in content
        assert "2026-06-15" in content

    def test_write_org_calendar_skips_invalid_type(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        count = _write_org_calendar(["unknown_type:2026-06-15:notes"])
        assert count == 0

    def test_write_org_calendar_skips_bad_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        count = _write_org_calendar(["connect_submission:not-a-date:notes"])
        assert count == 0

    def test_write_org_calendar_empty_lines_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        count = _write_org_calendar([])
        assert count == 0

    def test_valid_types_frozenset(self):
        assert "connect_submission" in _ORG_CAL_VALID_TYPES
        assert "rewards_season" in _ORG_CAL_VALID_TYPES
        assert "promo_nomination" in _ORG_CAL_VALID_TYPES

    def test_bootstrap_from_answers_org_calendar_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        rc = bootstrap_from_answers(
            {"org_calendar": ["fiscal_close:2026-09-30:FY26 fiscal close"]}
        )
        assert rc == 0
        cal_path = tmp_path / "state" / "work" / "work-org-calendar.md"
        assert cal_path.exists()
        assert "fiscal_close" in cal_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 3 item 7: bootstrap_from_import — Markdown narrative mode
# ---------------------------------------------------------------------------

class TestBootstrapMarkdownImport:
    """Tests for Markdown narrative import mode (Phase 3 item 7, ss15.2-ss15.3)."""

    def _make_narrative_md(self, tmp_path, name="test-narrative"):
        content = (
            "---\ndomain: " + name + "\n---\n\n# Test Narrative\n\n"
            "## Timeline\n\n"
            "| Date | Milestone | Evidence | Impact |\n"
            "|------|-----------|----------|--------|\n"
            "| **2025-02-01** | PilotX delivered | Connect May 2025 | 12 team actions |\n"
            "| **2025-03-15** | Newsletter Issue 12 sent | Email evidence | Well received |\n\n"
            "## Visibility Events\n\n"
            "| Date | Stakeholder | Event Type | Context |\n"
            "|------|------------|------------|------|"
            "\n| 2025-03-01 | Jane Kim (CVP) | Newsletter review | Issue 12 well received |\n\n"
            "## Manager Signal\n\n"
            "> *\"Great cross-team collaboration on this initiative.\"* --- Sam Chen Manager (Oct 2025)\n"
        )
        f = tmp_path / (name + ".md")
        f.write_text(content, encoding="utf-8")
        return f

    def test_markdown_import_dry_run_returns_zero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        f = self._make_narrative_md(tmp_path)
        rc = bootstrap_from_import(f, dry_run=True)
        assert rc == 0

    def test_markdown_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        f = self._make_narrative_md(tmp_path)
        before = set(str(p) for p in tmp_path.rglob("*") if p.is_file())
        bootstrap_from_import(f, dry_run=True)
        after = set(str(p) for p in tmp_path.rglob("*") if p.is_file())
        assert after == before

    def test_markdown_dry_run_prints_summary(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        f = self._make_narrative_md(tmp_path)
        bootstrap_from_import(f, dry_run=True)
        out = capsys.readouterr().out
        assert "dry-run" in out.lower()
        assert "milestone" in out.lower()

    def test_markdown_import_missing_file_returns_2(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        rc = bootstrap_from_import(tmp_path / "nonexistent.md")
        assert rc == 2

    def test_markdown_import_routes_milestones_to_journeys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        journeys = state_dir / "work-project-journeys.md"
        journeys.write_text("---\ndomain: work-project-journeys\n---\n\n# Journeys\n", encoding="utf-8")
        f = self._make_narrative_md(tmp_path)
        rc = bootstrap_from_import(f)
        assert rc == 0
        content = journeys.read_text(encoding="utf-8")
        assert "Imported Milestones" in content

    def test_markdown_import_routes_vis_events_to_people(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        people = state_dir / "work-people.md"
        people.write_text(
            "---\ndomain: work-people\n---\n\n# People\n\n"
            "## Visibility Events\n\n"
            "| Date | Stakeholder | Event Type | Context |\n"
            "|------|------------|------------|----------|\n",
            encoding="utf-8",
        )
        f = self._make_narrative_md(tmp_path)
        rc = bootstrap_from_import(f)
        assert rc == 0
        content = people.read_text(encoding="utf-8")
        assert "Jane Kim (CVP)" in content

    def test_markdown_import_creates_vis_events_section(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        people = state_dir / "work-people.md"
        people.write_text("---\ndomain: work-people\n---\n\n# People\n", encoding="utf-8")
        f = self._make_narrative_md(tmp_path)
        bootstrap_from_import(f)
        content = people.read_text(encoding="utf-8")
        assert "Visibility Events" in content

    def test_markdown_import_marks_import_completed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        (tmp_path / "state" / "work").mkdir(parents=True)
        f = self._make_narrative_md(tmp_path)
        bootstrap_from_import(f)
        profile = _load_profile()
        assert profile.get("work", {}).get("bootstrap", {}).get("import_completed") is True

    def test_markdown_import_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        journeys = state_dir / "work-project-journeys.md"
        journeys.write_text("---\ndomain: work-project-journeys\n---\n\n# Journeys\n", encoding="utf-8")
        f = self._make_narrative_md(tmp_path)
        bootstrap_from_import(f)
        bootstrap_from_import(f)
        content = journeys.read_text(encoding="utf-8")
        assert content.count("Imported Milestones") == 1

    def test_main_import_md(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        (tmp_path / "state" / "work").mkdir(parents=True)
        f = tmp_path / "narrative.md"
        f.write_text("---\ndomain: test-domain\n---\n# Title\n", encoding="utf-8")
        rc = main(["--import-file", str(f)])
        assert rc == 0

    def test_main_import_md_dry_run_flag(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        f = tmp_path / "narrative.md"
        f.write_text("---\ndomain: test-domain\n---\n# Title\n", encoding="utf-8")
        rc = main(["--import-file", str(f), "--dry-run-import"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "dry-run" in out.lower()


# ---------------------------------------------------------------------------
# Phase 3 item 7: bootstrap_from_import — Markdown narrative mode
# ---------------------------------------------------------------------------

class TestBootstrapMarkdownImport:
    """Tests for Markdown narrative import mode (Phase 3 item 7, ss15.2-ss15.3)."""

    def _make_narrative_md(self, tmp_path, name="test-narrative"):
        content = (
            "---\ndomain: " + name + "\n---\n\n# Test Narrative\n\n"
            "## Timeline\n\n"
            "| Date | Milestone | Evidence | Impact |\n"
            "|------|-----------|----------|--------|\n"
            "| **2025-02-01** | PilotX delivered | Connect May 2025 | 12 team actions |\n"
            "| **2025-03-15** | Newsletter Issue 12 sent | Email evidence | Well received |\n\n"
            "## Visibility Events\n\n"
            "| Date | Stakeholder | Event Type | Context |\n"
            "|------|------------|------------|------|"
            "\n| 2025-03-01 | Jane Kim (CVP) | Newsletter review | Issue 12 well received |\n\n"
            "## Manager Signal\n\n"
            "> *\"Great cross-team collaboration on this initiative.\"* --- Sam Chen Manager (Oct 2025)\n"
        )
        f = tmp_path / (name + ".md")
        f.write_text(content, encoding="utf-8")
        return f

    def test_markdown_import_dry_run_returns_zero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        f = self._make_narrative_md(tmp_path)
        rc = bootstrap_from_import(f, dry_run=True)
        assert rc == 0

    def test_markdown_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        f = self._make_narrative_md(tmp_path)
        before = set(str(p) for p in tmp_path.rglob("*") if p.is_file())
        bootstrap_from_import(f, dry_run=True)
        after = set(str(p) for p in tmp_path.rglob("*") if p.is_file())
        assert after == before

    def test_markdown_dry_run_prints_summary(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        f = self._make_narrative_md(tmp_path)
        bootstrap_from_import(f, dry_run=True)
        out = capsys.readouterr().out
        assert "dry-run" in out.lower()
        assert "milestone" in out.lower()

    def test_markdown_import_missing_file_returns_2(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        rc = bootstrap_from_import(tmp_path / "nonexistent.md")
        assert rc == 2

    def test_markdown_import_routes_milestones_to_journeys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        journeys = state_dir / "work-project-journeys.md"
        journeys.write_text("---\ndomain: work-project-journeys\n---\n\n# Journeys\n", encoding="utf-8")
        f = self._make_narrative_md(tmp_path)
        rc = bootstrap_from_import(f)
        assert rc == 0
        content = journeys.read_text(encoding="utf-8")
        assert "Imported Milestones" in content

    def test_markdown_import_routes_vis_events_to_people(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        people = state_dir / "work-people.md"
        people.write_text(
            "---\ndomain: work-people\n---\n\n# People\n\n"
            "## Visibility Events\n\n"
            "| Date | Stakeholder | Event Type | Context |\n"
            "|------|------------|------------|----------|\n",
            encoding="utf-8",
        )
        f = self._make_narrative_md(tmp_path)
        rc = bootstrap_from_import(f)
        assert rc == 0
        content = people.read_text(encoding="utf-8")
        assert "Jane Kim (CVP)" in content

    def test_markdown_import_creates_vis_events_section(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        people = state_dir / "work-people.md"
        people.write_text("---\ndomain: work-people\n---\n\n# People\n", encoding="utf-8")
        f = self._make_narrative_md(tmp_path)
        bootstrap_from_import(f)
        content = people.read_text(encoding="utf-8")
        assert "Visibility Events" in content

    def test_markdown_import_marks_import_completed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        (tmp_path / "state" / "work").mkdir(parents=True)
        f = self._make_narrative_md(tmp_path)
        bootstrap_from_import(f)
        profile = _load_profile()
        assert profile.get("work", {}).get("bootstrap", {}).get("import_completed") is True

    def test_markdown_import_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        journeys = state_dir / "work-project-journeys.md"
        journeys.write_text("---\ndomain: work-project-journeys\n---\n\n# Journeys\n", encoding="utf-8")
        f = self._make_narrative_md(tmp_path)
        bootstrap_from_import(f)
        bootstrap_from_import(f)
        content = journeys.read_text(encoding="utf-8")
        assert content.count("Imported Milestones") == 1

    def test_main_import_md(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        (tmp_path / "state" / "work").mkdir(parents=True)
        f = tmp_path / "narrative.md"
        f.write_text("---\ndomain: test-domain\n---\n# Title\n", encoding="utf-8")
        rc = main(["--import-file", str(f)])
        assert rc == 0

    def test_main_import_md_dry_run_flag(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(work_bootstrap, "_PROFILE_PATH", tmp_path / "p.yaml")
        monkeypatch.setattr(work_bootstrap, "_REPO_ROOT", tmp_path)
        f = tmp_path / "narrative.md"
        f.write_text("---\ndomain: test-domain\n---\n# Title\n", encoding="utf-8")
        rc = main(["--import-file", str(f), "--dry-run-import"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "dry-run" in out.lower()
