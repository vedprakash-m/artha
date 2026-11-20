"""Light mode: scripted writers of in-scope State Files refuse beside a conflict copy."""
from __future__ import annotations

from pathlib import Path

import pytest

import planning_signals as ps
from lib import memory_writer
from scripts.agents import tribe_agent


def _conflict_beside(path: Path) -> Path:
    copy = path.with_name(path.stem + "-conflict" + path.suffix)
    copy.write_text("other laptop's variant", encoding="utf-8")
    return copy


def test_planning_signals_save_refuses_beside_a_conflict_copy(tmp_path):
    signals = tmp_path / "planning_signals.md"
    ps.seed(signals)
    before = signals.read_bytes()
    doc = ps.load(signals)
    doc.frontmatter["last_updated"] = "2026-01-01"
    _conflict_beside(signals)
    with pytest.raises(Exception, match="conflict"):
        ps.save(doc, signals)
    assert signals.read_bytes() == before


def test_planning_signals_save_still_writes_without_a_conflict(tmp_path):
    signals = tmp_path / "planning_signals.md"
    ps.seed(signals)
    doc = ps.load(signals)
    ps.save(doc, signals)
    assert signals.read_text(encoding="utf-8").startswith("---")


def test_memory_add_fact_refuses_beside_a_conflict_copy(tmp_path, monkeypatch):
    memory = tmp_path / "memory.md"
    memory.write_text("---\nschema_version: '1.0'\ndomain: memory\nfacts: []\n---\n", encoding="utf-8")
    before = memory.read_bytes()
    _conflict_beside(memory)
    with pytest.raises(Exception, match="conflict"):
        memory_writer.add_fact({"text": "synthetic fact"}, memory, audit_path=tmp_path / "audit.md")
    assert memory.read_bytes() == before


def test_tribe_content_stage_refuses_beside_a_conflict_copy(tmp_path, monkeypatch):
    social = tmp_path / "social.md"
    stage = tmp_path / "content_stage.md"
    social.write_text("---\nschema_version: '1.0'\ndomain: social\nlast_updated: '2026-11-19'\n---\n", encoding="utf-8")
    stage.write_text("---\nschema_version: '1.0'\ndomain: content_stage\nlast_updated: '2026-11-19'\n---\nold\n", encoding="utf-8")
    monkeypatch.setattr(tribe_agent, "_SOCIAL_FILE", social)
    monkeypatch.setattr(tribe_agent, "_STAGE_FILE", stage)
    before = stage.read_bytes()
    _conflict_beside(stage)
    scored = [{"name": "A", "last_interaction": "2026-01-01", "days_since_last": 300, "score": 9, "circle": "general", "status": "critical", "baseline_frequency_days": 30}]
    with pytest.raises(Exception, match="conflict|refused"):
        tribe_agent._write_state_files("2026-11-19", scored, [], 1, "synthetic")
    assert stage.read_bytes() == before


def test_imessage_open_item_sync_refuses_beside_a_conflict_copy(tmp_path):
    import importlib.util
    from datetime import date

    source = Path(__file__).resolve().parents[2] / "scripts" / "skills" / "imessage_appointments.py"
    if not source.is_file():
        pytest.skip("personal skill is not part of the tracked checkout")
    spec = importlib.util.spec_from_file_location(
        "light_imessage_appointments",
        Path(__file__).resolve().parents[2] / "scripts" / "skills" / "imessage_appointments.py",
    )
    ia = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ia)

    oi = tmp_path / "open_items.md"
    oi.write_text("---\nschema_version: '1.0'\ndomain: open_items\n---\n## Active\n", encoding="utf-8")
    before = oi.read_bytes()
    _conflict_beside(oi)
    appt = {"date": "2026-11-20", "time": "09:00", "provider": "Synthetic Clinic", "description": "visit"}
    with pytest.raises(Exception, match="conflict|committed"):
        ia._sync_ois(oi, [appt], date(2026, 11, 19))
    assert oi.read_bytes() == before


def test_allowlist_excludes_files_whose_writers_are_not_yet_guarded():
    from lib.state_targets import light_markdown_targets

    root = Path(__file__).resolve().parents[2]
    names = {path.name for path in light_markdown_targets(root)}
    # These still have scripted writers that publish directly (see .archive/specs/light-artha-2026-11-20.md §14).
    unguarded = {"pr_manager.md", "ai_trend_radar.md", "home_intelligence.md", "home_iot.md",
                 "career_search.md.age"}
    assert not names & unguarded


def test_decision_tracker_appends_to_a_crlf_file(tmp_path, monkeypatch):
    """Windows files use CRLF; the read-back check must compare exact bytes, not translated text."""
    import decision_tracker as m

    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(tmp_path / "local"))
    path = tmp_path / "state" / "decisions.md"
    path.parent.mkdir()
    path.write_bytes(b"| id | title |\r\n")
    proposal = m.DecisionProposal(
        title="Synthetic decision", options=["a", "b"], deadline="2030-02-01", visibility="private", source="test"
    )
    m.DecisionTracker().persist_proposal(proposal, path)
    assert "Synthetic decision" in path.read_bytes().decode("utf-8")
