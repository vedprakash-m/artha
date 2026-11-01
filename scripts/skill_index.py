#!/usr/bin/env python3
"""Generate a static Artha skill index.

The index is intentionally passive: it reads config/skills.yaml through the
central config loader and lists skill files without importing skill modules.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
DEFAULT_OUTPUT = "tmp/skill_index.json"
_UTILITY_MODULES = {"__init__", "base_skill"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _display_path(path: Path, artha_dir: Path, home_dir: Path) -> str:
    resolved = path.expanduser().resolve()
    for prefix, base in (("$ARTHA_DIR", artha_dir), ("~", home_dir)):
        try:
            rel = resolved.relative_to(base.expanduser().resolve())
            return f"{prefix}/{rel.as_posix()}"
        except ValueError:
            continue
    return resolved.name


def _load_skills_config(artha_dir: Path) -> dict[str, Any]:
    from lib.config_loader import load_config  # noqa: PLC0415

    data = load_config("skills", str(artha_dir / "config"))
    skills = data.get("skills", {}) if isinstance(data, dict) else {}
    return skills if isinstance(skills, dict) else {}


def _discover_modules(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    modules: dict[str, Path] = {}
    for path in sorted(directory.glob("*.py")):
        if path.stem in _UTILITY_MODULES:
            continue
        modules[path.stem] = path
    return modules


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return []


def _module_name_from_config(name: str, cfg: dict[str, Any]) -> str:
    module = cfg.get("module")
    class_path = cfg.get("class")
    value = module if isinstance(module, str) else class_path if isinstance(class_path, str) else ""

    if value.startswith("scripts.skills."):
        remainder = value[len("scripts.skills.") :]
        return remainder.split(".", 1)[0]
    if value.startswith("skills."):
        remainder = value[len("skills.") :]
        return remainder.split(".", 1)[0]
    return name


def _class_path_from_config(cfg: dict[str, Any]) -> str | None:
    value = cfg.get("class")
    if isinstance(value, str) and "." in value:
        return value
    return None


def _classification_from_config(cfg: dict[str, Any]) -> str | None:
    class_label = cfg.get("class_label")
    if isinstance(class_label, str):
        return class_label
    value = cfg.get("class")
    if isinstance(value, str) and "." not in value:
        return value
    return None


def _entry_for_configured_skill(
    name: str,
    cfg: dict[str, Any],
    *,
    builtin_modules: dict[str, Path],
    plugin_modules: dict[str, Path],
    artha_dir: Path,
    home_dir: Path,
) -> dict[str, Any]:
    module_name = _module_name_from_config(name, cfg)
    module_path = builtin_modules.get(module_name) or plugin_modules.get(module_name)
    source = "builtin" if module_name in builtin_modules else "plugin" if module_name in plugin_modules else "config_only"

    requires_connectors = _coerce_list(cfg.get("requires_connectors"))
    requires_connector = cfg.get("requires_connector")
    if isinstance(requires_connector, str) and requires_connector not in requires_connectors:
        requires_connectors.append(requires_connector)

    return {
        "name": name,
        "configured": True,
        "registered": module_path is not None,
        "enabled": bool(cfg.get("enabled", False)),
        "priority": cfg.get("priority"),
        "cadence": cfg.get("cadence"),
        "classification": _classification_from_config(cfg),
        "class_path": _class_path_from_config(cfg),
        "command_namespace": cfg.get("command_namespace"),
        "run_on": cfg.get("run_on"),
        "requires_vault": bool(cfg.get("requires_vault", False)),
        "safety_critical": bool(cfg.get("safety_critical", False)),
        "requires_connectors": requires_connectors,
        "optional_connectors": _coerce_list(cfg.get("optional_connectors")),
        "requires_packages": _coerce_list(cfg.get("requires_packages")),
        "description": cfg.get("description"),
        "source": source,
        "module_name": module_name,
        "module_path": _display_path(module_path, artha_dir, home_dir) if module_path else None,
    }


def _entry_for_plugin_only_skill(
    name: str,
    path: Path,
    *,
    artha_dir: Path,
    home_dir: Path,
) -> dict[str, Any]:
    return {
        "name": name,
        "configured": False,
        "registered": False,
        "enabled": False,
        "priority": None,
        "cadence": None,
        "classification": None,
        "class_path": None,
        "command_namespace": None,
        "run_on": None,
        "requires_vault": False,
        "safety_critical": False,
        "requires_connectors": [],
        "optional_connectors": [],
        "requires_packages": [],
        "description": None,
        "source": "plugin",
        "module_name": name,
        "module_path": _display_path(path, artha_dir, home_dir),
    }


def build_skill_index(
    artha_dir: Path | None = None,
    *,
    plugin_dir: Path | None = None,
    home_dir: Path | None = None,
) -> dict[str, Any]:
    artha_dir = (artha_dir or Path(__file__).resolve().parents[1]).expanduser().resolve()
    home_dir = (home_dir or Path.home()).expanduser().resolve()
    plugin_dir = (plugin_dir or (home_dir / ".artha-plugins" / "skills")).expanduser()

    configured = _load_skills_config(artha_dir)
    builtin_modules = _discover_modules(artha_dir / "scripts" / "skills")
    plugin_modules = _discover_modules(plugin_dir)

    skills: list[dict[str, Any]] = []
    configured_names = set(configured.keys())
    for name, raw_cfg in sorted(configured.items()):
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        skills.append(
            _entry_for_configured_skill(
                str(name),
                cfg,
                builtin_modules=builtin_modules,
                plugin_modules=plugin_modules,
                artha_dir=artha_dir,
                home_dir=home_dir,
            )
        )

    for name, path in sorted(plugin_modules.items()):
        if name not in configured_names:
            skills.append(_entry_for_plugin_only_skill(name, path, artha_dir=artha_dir, home_dir=home_dir))

    enabled_missing = sorted(
        entry["name"]
        for entry in skills
        if entry["configured"] and entry["enabled"] and not entry["registered"]
    )
    unregistered_plugins = sorted(name for name in plugin_modules if name not in configured_names)

    warnings: list[str] = []
    if enabled_missing:
        warnings.append("enabled skills with no builtin or plugin module: " + ", ".join(enabled_missing))

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "source_files": {
            "config": _display_path(artha_dir / "config" / "skills.yaml", artha_dir, home_dir),
            "builtin_dir": _display_path(artha_dir / "scripts" / "skills", artha_dir, home_dir),
            "plugin_dir": _display_path(plugin_dir, artha_dir, home_dir),
        },
        "counts": {
            "configured": len(configured_names),
            "total": len(skills),
            "enabled": sum(1 for entry in skills if entry["configured"] and entry["enabled"]),
            "builtin_files": len(builtin_modules),
            "plugin_files": len(plugin_modules),
            "plugin_unregistered": len(unregistered_plugins),
            "enabled_missing_modules": len(enabled_missing),
            "work_namespace": sum(1 for entry in skills if entry.get("command_namespace") == "work"),
            "vault_required": sum(1 for entry in skills if entry.get("requires_vault")),
            "safety_critical": sum(1 for entry in skills if entry.get("safety_critical")),
        },
        "enabled_missing_modules": enabled_missing,
        "unregistered_plugins": unregistered_plugins,
        "skills": skills,
        "warnings": warnings,
    }


def summary_dict(index: dict[str, Any]) -> dict[str, Any]:
    counts = index.get("counts", {})
    return {
        "configured": counts.get("configured", 0),
        "total": counts.get("total", 0),
        "enabled": counts.get("enabled", 0),
        "builtin_files": counts.get("builtin_files", 0),
        "plugin_files": counts.get("plugin_files", 0),
        "enabled_missing_modules": counts.get("enabled_missing_modules", 0),
        "warnings": len(index.get("warnings", [])),
    }


def summary_text(index: dict[str, Any]) -> str:
    summary = summary_dict(index)
    return (
        f"Skill index: {summary['enabled']}/{summary['configured']} configured enabled, "
        f"{summary['builtin_files']} builtin file(s), {summary['plugin_files']} plugin file(s), "
        f"{summary['enabled_missing_modules']} enabled missing module(s)"
    )


def write_skill_index(index: dict[str, Any], output: Path | None = None) -> Path:
    output = output or (Path(__file__).resolve().parents[1] / DEFAULT_OUTPUT)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate tmp/skill_index.json without importing skills")
    parser.add_argument("--artha-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--plugin-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="print full JSON index")
    parser.add_argument("--summary-json", action="store_true", help="print compact JSON summary")
    parser.add_argument("--no-write", action="store_true", help="do not write tmp/skill_index.json")
    args = parser.parse_args(argv)

    index = build_skill_index(args.artha_dir, plugin_dir=args.plugin_dir)
    if not args.no_write:
        write_skill_index(index, args.output or (args.artha_dir / DEFAULT_OUTPUT))

    if args.summary_json:
        print(json.dumps(summary_dict(index), sort_keys=True))
    elif args.json:
        print(json.dumps(index, indent=2, sort_keys=True))
    else:
        print(summary_text(index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
