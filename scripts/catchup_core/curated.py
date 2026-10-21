"""Read canonical curated records without decrypting a State File to disk."""
from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import yaml


def read_state(directory: Path, name: str) -> str:
    if not re.fullmatch(r"[a-z_]+", name):
        raise ValueError("invalid State File name")
    if (directory / f"{name}.md.age").exists():
        vault = importlib.import_module("foundation").get_age_vault()
        return str(vault.read(name))
    path = directory / f"{name}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def records(text: str, kind: str) -> list[dict[str, Any]]:
    if not text:
        return []
    if kind == "open_items":
        output = []
        for block in re.findall(r"(?m)^- id:.*(?:\n(?:  .*|$))*", text):
            row = yaml.safe_load(block)
            if (isinstance(row, list) and row and isinstance(row[0], dict)
                    and row[0].get("status") not in ("done", "closed", "expired", "not_proceeding")):
                output.append(row[0])
        return output
    front = re.match(r"\A---\s*\n(.*?)\n---", text, re.DOTALL)
    data = yaml.safe_load(front.group(1)) if front else yaml.safe_load(text)
    if isinstance(data, dict):
        values = data.get(kind, [])
        if isinstance(values, dict):
            return [{"id": key, **value} for key, value in values.items() if isinstance(value, dict)]
        if isinstance(values, list):
            return [value for value in values if isinstance(value, dict)]
    return []
