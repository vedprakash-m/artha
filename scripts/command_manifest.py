"""Read and render the canonical command matrix in config/commands.md."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import NamedTuple

ARTHA_DIR = Path(__file__).resolve().parent.parent
COMMANDS_PATH = ARTHA_DIR / "config" / "commands.md"
MATRIX_HEADING = "## Canonical command matrix"
EXPECTED_COLUMNS = (
    "Bare command / aliases",
    "Colon sub-commands",
    "Deprecated slash forms",
    "Authority / scope",
    "Execution",
)


class CommandSpec(NamedTuple):
    bare: str
    colon: str
    slash: str
    scope: str
    execution: str


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def load_command_matrix(path: Path | None = None) -> list[CommandSpec]:
    """Parse the one machine-readable command table."""
    source = (path or COMMANDS_PATH).read_text(encoding="utf-8")
    marker = source.find(MATRIX_HEADING)
    if marker < 0:
        raise ValueError(f"{MATRIX_HEADING} not found")
    table_lines: list[str] = []
    for line in source[marker + len(MATRIX_HEADING):].splitlines():
        if line.startswith("## "):
            break
        if line.lstrip().startswith("|"):
            table_lines.append(line)
    if len(table_lines) < 3:
        raise ValueError("canonical command matrix is missing its table")
    header = _cells(table_lines[0])
    if tuple(header) != EXPECTED_COLUMNS:
        raise ValueError(f"unexpected command matrix columns: {header}")
    specs = [
        CommandSpec(*_cells(line))
        for line in table_lines[2:]
        if _cells(line)
    ]
    if not specs:
        raise ValueError("canonical command matrix is empty")
    return specs


def render_router_table(path: Path | None = None) -> str:
    """Render the compact generated command router from the canonical matrix."""
    rows = load_command_matrix(path)
    lines = [
        "## §R — Command Router",
        "",
        "Bare words are primary. Colon syntax selects a sub-command; slash forms are deprecated compatibility aliases.",
        "The canonical command matrix is `config/commands.md`.",
        "",
        "| Bare command / aliases | Authority / scope | Execution |",
        "|---|---|---|",
    ]
    lines.extend(f"| {row.bare} | {row.scope} | {row.execution} |" for row in rows)
    return "\n".join(lines)


def check_command_matrix(path: Path | None = None) -> list[str]:
    """Return contract violations in the canonical command matrix."""
    errors: list[str] = []
    rows = load_command_matrix(path)
    bare = {
        entry.strip().strip("`").split("<", 1)[0].strip()
        for row in rows
        for entry in row.bare.split(",")
    }
    for required in ("brief", "work", "items", "goals", "domain", "content", "guide", "health",
                     "scope-review", "show everything"):
        if required not in bare:
            errors.append(f"missing bare command: {required}")
    for row in rows:
        if ":" in row.colon and not re.search(r"`?([a-z-]+):", row.colon):
            errors.append(f"invalid colon sub-command: {row.colon}")
        if row.slash not in ("—", "-") and "/" not in row.slash:
            errors.append(f"slash column must identify aliases: {row.slash}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Artha command matrix")
    parser.add_argument("--path", type=Path, default=COMMANDS_PATH)
    args = parser.parse_args(argv)
    try:
        rows = load_command_matrix(args.path)
        errors = check_command_matrix(args.path)
    except (OSError, ValueError) as exc:
        print(f"command matrix: FAIL — {exc}")
        return 1
    if errors:
        print("command matrix: FAIL — " + "; ".join(errors))
        return 1
    print(f"command matrix: OK — {len(rows)} canonical routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
