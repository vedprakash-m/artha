from __future__ import annotations

import json

from mcp_discovery import discover_mcp, write_discovery


def test_discover_mcp_reads_project_and_home_configs_without_secrets(tmp_path):
    project = tmp_path / "artha"
    home = tmp_path / "home"
    (project / ".vscode").mkdir(parents=True)
    (home / ".warp").mkdir(parents=True)

    (project / ".vscode" / "mcp.json").write_text(
        """
        {
          // VS Code accepts JSONC; discovery should too.
          "servers": {
            "local": {
              "command": "python",
              "args": ["-m", "demo"],
              "env": {"TOKEN": "secret-value"},
            },
            "remote": {
              "type": "http",
              "url": "https://example.com/mcp?token=secret-value#fragment",
            },
          },
        }
        """,
        encoding="utf-8",
    )
    (home / ".warp" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"home-server": {"command": "node"}}}),
        encoding="utf-8",
    )

    discovery = discover_mcp(project, home_dir=home)

    assert discovery["counts"]["config_files"] == 2
    assert discovery["counts"]["servers"] == 3
    assert discovery["counts"]["project_servers"] == 2
    assert discovery["counts"]["home_servers"] == 1
    assert discovery["counts"]["errors"] == 0

    encoded = json.dumps(discovery)
    assert "secret-value" not in encoded
    assert "token=" not in encoded
    assert "https://example.com/mcp" in encoded


def test_write_discovery_is_atomic_json(tmp_path):
    output = tmp_path / "tmp" / "mcp_discovery.json"
    discovery = {
        "schema_version": "1.0",
        "generated_at": "2026-01-01T00:00:00Z",
        "counts": {"servers": 0},
        "configs": [],
        "servers": [],
    }

    written = write_discovery(discovery, output)

    assert written == output
    assert json.loads(output.read_text(encoding="utf-8"))["counts"]["servers"] == 0
