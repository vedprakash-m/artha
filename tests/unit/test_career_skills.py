"""tests/unit/test_career_skills.py — FR-25 Career Search Intelligence.

AR-1 (P0 Phase 1 exit criterion): Verifies career skills are registered in
_ALLOWED_SKILLS so they can be invoked via the Skill Runner.

Also validates:
- output/career/ directory exists (PDF output location — §9.1 FR-CS-3)
- briefings/career/ directory exists (evaluation report archive — §7.3)
- career skills registered in config/skills.yaml

Ref: specs/career-ops.md AR-1, §9.1, §3.1
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# AR-1 (P0): _ALLOWED_SKILLS must include career skills
# ---------------------------------------------------------------------------

def test_career_pdf_generator_in_allowed_skills():
    """career_pdf_generator MUST be in _ALLOWED_SKILLS (spec AR-1 — P0)."""
    from skill_runner import _ALLOWED_SKILLS  # noqa: PLC0415
    assert "career_pdf_generator" in _ALLOWED_SKILLS, (
        "career_pdf_generator missing from _ALLOWED_SKILLS in skill_runner.py — "
        "this is a P0 Phase 1 exit criterion (spec AR-1)"
    )


def test_portal_scanner_in_allowed_skills():
    """portal_scanner MUST be pre-registered in _ALLOWED_SKILLS for Phase 2 readiness."""
    from skill_runner import _ALLOWED_SKILLS  # noqa: PLC0415
    assert "portal_scanner" in _ALLOWED_SKILLS, (
        "portal_scanner missing from _ALLOWED_SKILLS in skill_runner.py — "
        "Phase 2 portal scanning requires pre-registration"
    )


def test_allowed_skills_is_frozenset():
    """_ALLOWED_SKILLS must be a frozenset (immutable allowlist contract)."""
    from skill_runner import _ALLOWED_SKILLS  # noqa: PLC0415
    assert isinstance(_ALLOWED_SKILLS, frozenset), (
        f"_ALLOWED_SKILLS must be frozenset, got {type(_ALLOWED_SKILLS).__name__}"
    )


# ---------------------------------------------------------------------------
# Directory existence (FR-CS-3, §7.3)
# ---------------------------------------------------------------------------

def test_output_career_directory_exists():
    """output/career/ must exist — PDF generation target (FR-CS-3 §9.1)."""
    output_career = REPO_ROOT / "output" / "career"
    assert output_career.is_dir(), (
        f"output/career/ directory missing — create it with: mkdir -p {output_career}"
    )


def test_briefings_career_directory_exists():
    """briefings/career/ must exist — evaluation report archive (§7.3)."""
    briefings_career = REPO_ROOT / "briefings" / "career"
    assert briefings_career.is_dir(), (
        f"briefings/career/ directory missing — create it with: mkdir -p {briefings_career}"
    )


# ---------------------------------------------------------------------------
# Config/skills.yaml registration
# ---------------------------------------------------------------------------

def test_career_pdf_generator_in_skills_yaml():
    """career_pdf_generator must be registered in config/skills.yaml."""
    import yaml  # noqa: PLC0415 — this is the config_loader exemption file
    skills_path = REPO_ROOT / "config" / "skills.yaml"
    assert skills_path.exists(), "config/skills.yaml missing"
    data = yaml.safe_load(skills_path.read_text(encoding="utf-8")) or {}
    skills = data.get("skills", {})
    assert "career_pdf_generator" in skills, (
        "career_pdf_generator not registered in config/skills.yaml"
    )


def test_portal_scanner_in_skills_yaml():
    """portal_scanner must be pre-registered in config/skills.yaml for Phase 2."""
    import yaml  # noqa: PLC0415 — config read-only check
    skills_path = REPO_ROOT / "config" / "skills.yaml"
    data = yaml.safe_load(skills_path.read_text(encoding="utf-8")) or {}
    skills = data.get("skills", {})
    assert "portal_scanner" in skills, (
        "portal_scanner not pre-registered in config/skills.yaml"
    )


# ---------------------------------------------------------------------------
# Skill module syntax (imports cleanly without Playwright)
# ---------------------------------------------------------------------------

def test_career_pdf_generator_compiles():
    """career_pdf_generator.py must compile without syntax errors."""
    import py_compile  # noqa: PLC0415
    skill_path = SCRIPTS_DIR / "skills" / "career_pdf_generator.py"
    assert skill_path.exists(), "scripts/skills/career_pdf_generator.py missing"
    py_compile.compile(str(skill_path), doraise=True)


def test_career_state_compiles():
    """scripts/lib/career_state.py must compile cleanly."""
    import py_compile  # noqa: PLC0415
    path = SCRIPTS_DIR / "lib" / "career_state.py"
    assert path.exists(), "scripts/lib/career_state.py missing"
    py_compile.compile(str(path), doraise=True)


def test_career_trace_compiles():
    """scripts/lib/career_trace.py must compile cleanly."""
    import py_compile  # noqa: PLC0415
    path = SCRIPTS_DIR / "lib" / "career_trace.py"
    assert path.exists(), "scripts/lib/career_trace.py missing"
    py_compile.compile(str(path), doraise=True)
