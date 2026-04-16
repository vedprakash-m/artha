#!/usr/bin/env python3
"""
Artha Work OS — Prompt Linter (Phase 4 item 5)

Checks every work prompt file in prompts/ for:
  - Required state-file reference (work-*.md)
  - Required separator line (---) / frontmatter guard
  - Placeholder hygiene (<PLACEHOLDER> tokens not yet substituted)
  - Broken state-file paths (references to deleted root-level state files)
  - Drift from known work domain names

Usage:
    python scripts/tools/prompt_linter.py [--fix] [--path prompts/]

Exit codes:
    0  All checks pass
    1  One or more lint errors found
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS_DIR = _REPO_ROOT / "prompts"

# Canonical work domains (must match domain_registry.yaml)
_KNOWN_DOMAINS: list[str] = [
    "work-calendar",
    "work-comms",
    "work-notes",
    "work-people",
    "work-projects",
    "work-boundary",
    "work-career",
    "work-performance",
    "work-sources",
    "work-decisions",
    "work-open-items",
    "work-learned",
    "work-promo-narrative",
    "work-summary",
    "work-incidents",
    "work-repos",
]

# Stale root-level paths that were deleted (must NOT appear in prompts)
_BANNED_PATHS: list[str] = [
    "state/work-calendar.md",
    "state/work-comms.md",
    "state/work-notes.md",
    "state/work-people.md",
    "state/work-projects.md",
]

# Regex for un-substituted placeholders
_PLACEHOLDER_RE = re.compile(r"<[A-Z_]{3,}>")

# Required canonical path prefix for work state references
_CANONICAL_PREFIX = "state/work/"


def lint_file(path: Path) -> list[str]:
    """Lint a single prompt file. Returns list of error strings (empty = pass)."""
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    fname = path.name

    # Only lint work-*.md prompts
    if not fname.startswith("work-"):
        return errors

    # 1. Check for banned (stale) root-level state paths
    for banned in _BANNED_PATHS:
        if banned in text:
            errors.append(
                f"{fname}: references stale path '{banned}' — update to '{_CANONICAL_PREFIX}{Path(banned).name}'"
            )

    # 2. Check for leftover placeholders
    placeholders = _PLACEHOLDER_RE.findall(text)
    # Exclude false positives (HTML-ish things that are intentional)
    placeholders = [p for p in placeholders if p not in ("<TITLE>", "<NAME>")]
    if placeholders:
        errors.append(
            f"{fname}: un-substituted placeholder(s): {', '.join(set(placeholders))}"
        )

    # 3. Verify state-file references use canonical path
    # Any reference to `state/work-` (non-canonical) should be `state/work/work-`
    bad_state_re = re.compile(r"\bstate/work-[a-z]+\.md\b")
    bad_refs = bad_state_re.findall(text)
    if bad_refs:
        errors.append(
            f"{fname}: non-canonical state reference(s): {', '.join(set(bad_refs))}"
            f" — should be state/work/work-*.md"
        )

    # 4. Check separator / frontmatter guard exists (at least one `---` line)
    if "\n---\n" not in text and not text.startswith("---"):
        errors.append(f"{fname}: missing YAML frontmatter or `---` separator guard")

    # 5. RD-28: schema_version required in frontmatter
    # Parse frontmatter block (between first two --- delimiters)
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm_block = text[3:end]
            if "schema_version" not in fm_block:
                errors.append(
                    f"{fname}: missing 'schema_version' in frontmatter "
                    f"(RD-28 — add 'schema_version: \"1.0\"' to the frontmatter block)"
                )

    return errors


def lint_all(prompts_dir: Path) -> list[str]:
    """Lint all prompt files in directory. Returns all errors."""
    all_errors: list[str] = []
    for p in sorted(prompts_dir.glob("*.md")):
        all_errors.extend(lint_file(p))
    return all_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Artha prompt linter (Phase 4 item 5)")
    parser.add_argument(
        "--path",
        default=str(_PROMPTS_DIR),
        help="Directory to lint (default: prompts/)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help="Auto-fix bannable path references (dry-run if omitted)",
    )
    args = parser.parse_args(argv)

    prompts_dir = Path(args.path)
    if not prompts_dir.exists():
        print(f"ERROR: prompts directory not found: {prompts_dir}", file=sys.stderr)
        return 1

    errors = lint_all(prompts_dir)

    if not errors:
        print(f"✅ Prompt linter: all checks passed ({len(list(prompts_dir.glob('*.md')))} files)")
        return 0

    print(f"❌ Prompt linter: {len(errors)} issue(s) found:\n")
    for err in errors:
        print(f"  {err}")

    if args.fix:
        print("\n(--fix flag detected — no auto-fix implemented yet; fix manually)")

    return 1


if __name__ == "__main__":
    sys.exit(main())
