from __future__ import annotations

import json

import yaml

from skill_index import build_skill_index, write_skill_index


def test_build_skill_index_uses_config_and_discovers_plugins_without_imports(tmp_path):
    artha_dir = tmp_path / "artha"
    plugin_dir = tmp_path / "plugins" / "skills"
    (artha_dir / "config").mkdir(parents=True)
    (artha_dir / "scripts" / "skills").mkdir(parents=True)
    plugin_dir.mkdir(parents=True)

    (artha_dir / "config" / "skills.yaml").write_text(
        yaml.safe_dump(
            {
                "skills": {
                    "alpha": {
                        "enabled": True,
                        "priority": "P1",
                        "cadence": "daily",
                        "class": "operational",
                        "requires_vault": False,
                    },
                    "beta": {
                        "enabled": False,
                        "class": "scripts.skills.beta.Beta",
                    },
                    "missing_enabled": {
                        "enabled": True,
                        "priority": "P1",
                    },
                    "plugin_registered": {
                        "enabled": True,
                        "requires_connectors": ["demo"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (artha_dir / "scripts" / "skills" / "alpha.py").write_text(
        "raise RuntimeError('alpha was imported')\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin_registered.py").write_text(
        "raise RuntimeError('plugin was imported')\n",
        encoding="utf-8",
    )
    (plugin_dir / "loose_plugin.py").write_text(
        "raise RuntimeError('loose plugin was imported')\n",
        encoding="utf-8",
    )

    index = build_skill_index(artha_dir, plugin_dir=plugin_dir, home_dir=tmp_path)
    by_name = {entry["name"]: entry for entry in index["skills"]}

    assert index["counts"]["configured"] == 4
    assert index["counts"]["total"] == 5
    assert index["counts"]["enabled"] == 3
    assert index["counts"]["builtin_files"] == 1
    assert index["counts"]["plugin_files"] == 2
    assert index["counts"]["plugin_unregistered"] == 1
    assert index["enabled_missing_modules"] == ["missing_enabled"]

    assert by_name["alpha"]["source"] == "builtin"
    assert by_name["alpha"]["registered"] is True
    assert by_name["beta"]["class_path"] == "scripts.skills.beta.Beta"
    assert by_name["beta"]["registered"] is False
    assert by_name["plugin_registered"]["source"] == "plugin"
    assert by_name["plugin_registered"]["requires_connectors"] == ["demo"]
    assert by_name["loose_plugin"]["configured"] is False
    assert by_name["loose_plugin"]["enabled"] is False


def test_write_skill_index_is_atomic_json(tmp_path):
    output = tmp_path / "tmp" / "skill_index.json"
    index = {
        "schema_version": "1.0",
        "generated_at": "2026-01-01T00:00:00Z",
        "counts": {"enabled": 0},
        "skills": [],
    }

    written = write_skill_index(index, output)

    assert written == output
    assert json.loads(output.read_text(encoding="utf-8"))["counts"]["enabled"] == 0
