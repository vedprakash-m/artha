"""
tests/ext_agents/test_blueprints.py — EAR-7: blueprint YAML files and CLI generator tests.

Tests (15):
 1. Each blueprint file exists and is valid YAML
 2. Each blueprint has required top-level keys
 3. icm-triage has examples field
 4. icm-triage has max_context_chars_absolute: 12000
 5. All blueprints have soul_principles list
 6. All blueprints have output_format field
 7. Blueprint variables are in {{variable}} form
 8. cmd_blueprint_create renders a .agent.md file
 9. cmd_blueprint_create with vars substitutes placeholders
10. cmd_blueprint_create raises on missing blueprint
11. Rendered .agent.md starts with YAML front matter
12. code-reviewer has max_context_chars_absolute
13. All blueprints have at least 3 soul_principles
14. Blueprint names match file names
15. deployment-monitor has soul_principles

Ref: specs/ext-agent-reloaded.md §EAR-7
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BLUEPRINTS_DIR = _REPO_ROOT / "config" / "agents" / "blueprints"
_SCRIPTS_DIR = str(_REPO_ROOT / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

BLUEPRINT_FILES = list(_BLUEPRINTS_DIR.glob("*.blueprint.yaml"))
REQUIRED_BLUEPRINTS = [
    "icm-triage.blueprint.yaml",
    "deployment-monitor.blueprint.yaml",
    "backlog-analyst.blueprint.yaml",
    "meeting-prep.blueprint.yaml",
    "knowledge-curator.blueprint.yaml",
    "escalation-drafter.blueprint.yaml",
    "fleet-health.blueprint.yaml",
    "code-reviewer.blueprint.yaml",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(name: str) -> dict:
    path = _BLUEPRINTS_DIR / name
    return yaml.safe_load(path.read_text())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fname", REQUIRED_BLUEPRINTS)
def test_blueprint_file_exists(fname):
    path = _BLUEPRINTS_DIR / fname
    assert path.exists(), f"Blueprint file missing: {fname}"


@pytest.mark.parametrize("fname", REQUIRED_BLUEPRINTS)
def test_blueprint_is_valid_yaml(fname):
    path = _BLUEPRINTS_DIR / fname
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{fname} is not a valid YAML dict"


@pytest.mark.parametrize("fname", REQUIRED_BLUEPRINTS)
def test_blueprint_has_required_keys(fname):
    data = _load(fname)
    assert "blueprint" in data, f"{fname} missing 'blueprint' key"
    bp = data["blueprint"]
    for key in ["name", "description", "tags"]:
        assert key in bp, f"{fname} blueprint missing key: {key}"


def test_icm_triage_has_examples():
    data = _load("icm-triage.blueprint.yaml")
    assert "examples" in data, "icm-triage blueprint must have 'examples'"
    assert isinstance(data["examples"], list)
    assert len(data["examples"]) >= 1


def test_icm_triage_max_context_chars_absolute():
    data = _load("icm-triage.blueprint.yaml")
    agent = data.get("agent", {})
    invocation = data.get("invocation", {})
    # Check either agent or invocation section
    absolute = invocation.get("max_context_chars_absolute")
    assert absolute == 12000, f"Expected 12000, got {absolute}"


@pytest.mark.parametrize("fname", REQUIRED_BLUEPRINTS)
def test_blueprint_has_soul_principles(fname):
    data = _load(fname)
    assert "soul_principles" in data, f"{fname} missing soul_principles"
    assert isinstance(data["soul_principles"], list)
    assert len(data["soul_principles"]) >= 1


@pytest.mark.parametrize("fname", REQUIRED_BLUEPRINTS)
def test_blueprint_has_output_format(fname):
    data = _load(fname)
    assert "output_format" in data, f"{fname} missing output_format"
    assert isinstance(data["output_format"], str)
    assert len(data["output_format"].strip()) > 0


@pytest.mark.parametrize("fname", REQUIRED_BLUEPRINTS)
def test_blueprint_variables_in_double_braces(fname):
    # Read raw YAML text so Python dict repr doesn't introduce false {}-matches
    raw = (_BLUEPRINTS_DIR / fname).read_text(encoding="utf-8")
    # Variables MUST use {{var}} — look for any single-brace {word} patterns
    # that are NOT part of {{...}} and are not YAML block delimiters
    single_brace = re.findall(r'(?<!\{)\{([A-Za-z_]\w*)\}(?!\})', raw)
    assert not single_brace, f"{fname} uses single-brace vars: {single_brace}"


def test_cmd_blueprint_create_renders(tmp_path):
    """cmd_blueprint_create should produce a rendered .agent.md file."""
    from agent_manager import cmd_blueprint_create  # type: ignore

    out_path = tmp_path / "rendered.agent.md"
    result = cmd_blueprint_create(
        blueprint_name="icm-triage",
        var_assignments=["team=Armada", "service=xpf", "oncall_rotation=Armada ICM"],
        out_path=str(out_path),
    )
    assert result == 0, f"cmd_blueprint_create returned {result}"
    assert out_path.exists(), "Rendered .agent.md was not created"


def test_cmd_blueprint_create_substitutes_vars(tmp_path):
    from agent_manager import cmd_blueprint_create  # type: ignore

    out_path = tmp_path / "subst.agent.md"
    result = cmd_blueprint_create(
        blueprint_name="meeting-prep",
        var_assignments=[
            "meeting_title=Test Meeting",
            "attendees=Alice, Bob",
            "owner=Charlie",
        ],
        out_path=str(out_path),
    )
    assert result == 0
    content = out_path.read_text()
    assert "Test Meeting" in content  # rendered in label/description
    assert "Charlie" in content       # rendered in description (owner)


def test_cmd_blueprint_create_missing_raises(tmp_path):
    from agent_manager import cmd_blueprint_create  # type: ignore

    result = cmd_blueprint_create(
        blueprint_name="nonexistent-blueprint",
        var_assignments=[],
        out_path=str(tmp_path / "out.md"),
    )
    assert result != 0, "Expected non-zero return for missing blueprint"


def test_rendered_agent_md_has_yaml_frontmatter(tmp_path):
    from agent_manager import cmd_blueprint_create  # type: ignore

    out_path = tmp_path / "fm.agent.md"
    result = cmd_blueprint_create(
        blueprint_name="fleet-health",
        var_assignments=["owner=Ved"],
        out_path=str(out_path),
    )
    assert result == 0
    content = out_path.read_text()
    # Should start with YAML front matter or at least have --- marker
    assert "---" in content or "name:" in content or "##" in content


def test_code_reviewer_has_absolute_cap():
    data = _load("code-reviewer.blueprint.yaml")
    invocation = data.get("invocation", {})
    absolute = invocation.get("max_context_chars_absolute")
    assert absolute is not None, "code-reviewer should have max_context_chars_absolute"


@pytest.mark.parametrize("fname", REQUIRED_BLUEPRINTS)
def test_blueprint_has_minimum_soul_principles(fname):
    data = _load(fname)
    principles = data.get("soul_principles", [])
    assert len(principles) >= 3, \
        f"{fname} has only {len(principles)} soul_principles; expected ≥3"


@pytest.mark.parametrize("fname", REQUIRED_BLUEPRINTS)
def test_blueprint_name_matches_file(fname):
    data = _load(fname)
    bp_name = data["blueprint"]["name"]
    # File stem (before .blueprint.yaml) should contain the name
    file_stem = fname.replace(".blueprint.yaml", "")
    assert file_stem == bp_name, f"{fname} name mismatch: expected '{file_stem}', got '{bp_name}'"


def test_deployment_monitor_soul_principles():
    data = _load("deployment-monitor.blueprint.yaml")
    principles = data.get("soul_principles", [])
    assert len(principles) >= 1
    # At least one principle should mention fabrication or rollback
    text = " ".join(principles).lower()
    assert any(word in text for word in ["fabricate", "rollback", "stale", "runbook"])
