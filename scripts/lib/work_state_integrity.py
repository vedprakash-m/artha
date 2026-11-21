"""Read-only integrity checks for Work state, shared by `work health` and preflight.

Kept in lib/ so preflight does not import the work/ package (see
tests/unit/test_architectural_boundaries.py).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def find_integrity_issues(state_dir: Path) -> list[str]:
    """Return one line per Work state file whose bytes would break a Work command.

    Checks top-level *.md files for doubled carriage returns and unparseable
    or missing frontmatter. Read-only.
    """
    issues: list[str] = []
    if not state_dir.exists():
        return issues
    import yaml  # type: ignore[import]  # noqa: PLC0415

    for md_file in sorted(state_dir.glob("*.md")):
        try:
            raw = md_file.read_bytes().decode("utf-8")
        except Exception as exc:
            issues.append(f"{md_file.name}: unreadable ({type(exc).__name__})")
            continue
        if "\r\r\n" in raw:
            issues.append(f"{md_file.name}: doubled carriage returns (\\r\\r\\n)")
        if not raw.startswith("---"):
            issues.append(f"{md_file.name}: missing frontmatter")
            continue
        end = raw.find("\n---", 3)
        if end < 0:
            issues.append(f"{md_file.name}: frontmatter is not closed")
            continue
        try:
            fm = yaml.safe_load(raw[3:end])
        except yaml.YAMLError:
            issues.append(f"{md_file.name}: frontmatter is not valid YAML")
            continue
        if not isinstance(fm, dict):
            issues.append(f"{md_file.name}: frontmatter is not a mapping")
    return issues


def refresh_blockers(graph_ok: bool, bootstrap_done: Optional[bool]) -> list[str]:
    """Explain why `work refresh` cannot complete, with the owner action for each."""
    blockers: list[str] = []
    if not graph_ok:
        blockers.append(
            "Work Microsoft credential missing — Outlook and Graph refresh cannot run. "
            "Store the Work client ID as msgraph-work-client-id, then run: "
            "python scripts/setup_msgraph_oauth.py --work"
        )
    if bootstrap_done is False:
        blockers.append("Work bootstrap not completed — run: work bootstrap")
    return blockers
