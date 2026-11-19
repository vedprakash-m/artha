"""Preflight's health-check compaction must survive the CRLF files the Windows machine writes."""
from __future__ import annotations

import lib.state_writer as sw
import preflight.runtime_checks as rc
from middleware.write_guard import _count_yaml_fields


def _health_check(runs: int) -> str:
    fm = "---\nschema_version: '1.1'\nlast_catch_up: x\ncatch_up_count: 3\ndomain: health-check\nlast_updated: '2026-11-01'\n---\n"
    body = "## Catch-Up Run History\n\n" + "".join(f"### Run {i}\n- n: {i}\n\n" for i in range(runs))
    return fm + body + "## Autonomy State\n\n```yaml\nautonomy:\n  trust_level: 1\n```\n"


def test_crlf_health_check_compacts_without_losing_frontmatter(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    original = _health_check(40).replace("\n", "\r\n")
    (state / "health-check.md").write_bytes(original.encode())
    monkeypatch.setattr(rc, "ARTHA_DIR", str(tmp_path))
    monkeypatch.setattr(rc, "_READ_ONLY", False, raising=False)
    captured = {}
    monkeypatch.setattr(sw, "write", lambda path, content, **kw: captured.update(content=content))
    rc._compact_health_check()
    new = captured["content"]
    assert new.startswith("---\n") and "\r" not in new
    assert _count_yaml_fields(new) == _count_yaml_fields(original)
    assert len(new) < len(original)
