"""Health-check compaction must produce frontmatter the write guard accepts."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

_ARTHA_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.state_schema import validate_frontmatter  # noqa: E402


def _load_cli():
    spec = importlib.util.spec_from_file_location("preflight_cli_under_test", _SCRIPTS_DIR / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _frontmatter(text: str) -> dict:
    return yaml.safe_load(text.split("---\n")[1]) or {}


def test_template_satisfies_health_check_schema():
    text = (_ARTHA_ROOT / "state" / "templates" / "health-check.md").read_text(encoding="utf-8")
    assert validate_frontmatter("health-check.md", _frontmatter(text)) == []


def test_compaction_writes_through_the_schema_guard(tmp_path, monkeypatch):
    cli = _load_cli()
    state = tmp_path / "state"
    state.mkdir()
    runs = "".join(f"### run {i}\n- ok\n" for i in range(40))
    original = (
        "---\nschema_version: '1.1'\nlast_catch_up: never\ncatch_up_count: 0\n---\n\n"
        "## Catch-Up Run History\n\n" + runs
    )
    hc = state / "health-check.md"
    hc.write_text(original, encoding="utf-8")
    written: dict[str, str] = {}

    def fake_write(path, content, **kwargs):
        written["content"] = content
        written["domain"] = kwargs["domain"]

    import lib.state_writer as state_writer

    monkeypatch.setattr(cli, "ARTHA_DIR", str(tmp_path))
    monkeypatch.setattr(state_writer, "write", fake_write)
    cli._compact_health_check()

    fm = _frontmatter(written["content"])
    assert validate_frontmatter("health-check.md", fm) == []
    assert fm["domain"] == "health-check"
    assert fm["schema_version"] == "1.1"
    assert fm["catch_up_count"] == 0
    assert written["content"].count("### run ") == 20
