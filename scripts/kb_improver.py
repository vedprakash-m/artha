#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""scripts/kb_improver.py — Work KB Quality Improvement Orchestrator.

Reads state/kb_quality_results.json (written by kb_quality_check skill),
applies programmatic fixes to knowledge/*.md files, and writes an improvement
report to state/kb_improvement_report.md.

Programmatic fixes applied:
  1. TIMESTAMP → PreciseTimeStamp in KQL code blocks
  2. Remove copy-paste corruption (email-row bleed into non-people sections)
  3. Caveat/remove speculative headers (§ containing "Public Assessment")
  4. Add standard cross-reference footer if missing
  5. Mark empty stub sections with structured improvement tags

All fixes are atomic: file is written only if at least one change was made.
Each fix is logged to the improvement report.

Usage:
  python scripts/kb_improver.py            # dry-run: report without changes
  python scripts/kb_improver.py --apply    # apply all programmatic fixes
  python scripts/kb_improver.py --auto     # --apply + quiet (for background spawn)
  python scripts/kb_improver.py --report   # print improvement report to stdout

This script is spawned non-blocking by kb_quality_check.py when any KB
falls below the quality threshold.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_KB_DIR = _ARTHA_DIR / "knowledge"
_QUALITY_RESULTS_PATH = _ARTHA_DIR / "state" / "kb_quality_results.json"
_REPORT_PATH = _ARTHA_DIR / "state" / "kb_improvement_report.md"

_DOMAIN_KB_FILES = [
    "xpf-repairs-kb.md",
    "xpf-deployment-kb.md",
    "xpf-networking-kb.md",
    "xpf-monitoring-kb.md",
    "xpf-safety-kb.md",
    "xpf-fleet-health-kb.md",
    "armada-kb.md",
    "titan-convergence-kb.md",
    "rubik-kb.md",
    "dd-xpf-kb.md",
    "sku-generations-kb.md",
    "xstore-kb.md",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [kb_improver] %(message)s",
)
_log = logging.getLogger("kb_improver")

# ---------------------------------------------------------------------------
# Fix functions — each returns (new_content, list_of_changes)
# ---------------------------------------------------------------------------

def fix_timestamp_in_kql(content: str, kb_name: str) -> tuple[str, list[str]]:
    """Replace bare `timestamp` with `PreciseTimeStamp` in KQL code blocks.

    Only applies inside ```kusto / ```kql fenced blocks. Does not touch prose.
    """
    changes: list[str] = []

    def _replace_in_block(m: re.Match) -> str:
        block = m.group(0)
        # Only replace `timestamp` that is a standalone column reference
        # (not part of e.g. EventTimestamp, ingestion_time(), etc.)
        fixed = re.sub(
            r"(?<!\w)timestamp(?!\w)",
            "PreciseTimeStamp",
            block,
            flags=re.IGNORECASE,
        )
        if fixed != block:
            changes.append("Fixed 'timestamp' → 'PreciseTimeStamp' in KQL block")
        return fixed

    new_content = re.sub(
        r"```(?:kusto|kql)\s*\n.*?```",
        _replace_in_block,
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return new_content, changes


def fix_copy_paste_corruption(content: str, kb_name: str) -> tuple[str, list[str]]:
    """Remove email-row bleed (person table rows) from non-people sections.

    Detects table rows containing @microsoft.com or @<domain>.com
    that appear inside sections not titled 'people', 'team', or 'contacts'.
    Moves them or removes them with a NOTE marker.
    """
    changes: list[str] = []
    lines = content.splitlines(keepends=True)
    result_lines: list[str] = []
    current_section_is_people = False

    for line in lines:
        # Track section context
        if re.match(r"^## ", line):
            current_section_is_people = bool(
                re.search(r"people|team|contacts|org", line, re.IGNORECASE)
            )

        # Detect corrupted person rows in non-people sections
        if (
            not current_section_is_people
            and re.match(r"^\|", line)
            and re.search(r"@\w+\.\w{2,}", line)
            and re.search(r"\|\s*\w+\s*\|", line)
        ):
            changes.append(f"Removed stray person row from non-people section: {line.strip()[:60]}")
            # Skip (remove) the corrupted row
            continue

        result_lines.append(line)

    return "".join(result_lines), changes


def fix_speculative_sections(content: str, kb_name: str) -> tuple[str, list[str]]:
    """Add a caveat note to speculative assessment sections.

    Detects H2 sections titled with 'Public Assessment', 'Speculative', etc.
    and prepends a ⚠️ caveat block.
    """
    changes: list[str] = []
    caveat = (
        "> ⚠️ **Note:** The following section contains hypotheses and "
        "unverified assessments. Treat as working theory, not authoritative documentation.\n\n"
    )

    speculative_section_pattern = re.compile(
        r"(^## [^\n]*(public assessment|speculative|hypothesis|debated)[^\n]*\n)",
        re.MULTILINE | re.IGNORECASE,
    )

    def _add_caveat(m: re.Match) -> str:
        heading = m.group(0)
        if caveat.strip()[:20] not in content[m.start():m.start() + 200]:
            changes.append(f"Added caveat to speculative section: {heading.strip()}")
            return heading + caveat
        return heading

    new_content = speculative_section_pattern.sub(_add_caveat, content)
    return new_content, changes


def fix_gq020_duplication(content: str, kb_name: str) -> tuple[str, list[str]]:
    """In deployment-kb: deduplicate GQ-020 by keeping the parameterized version.

    The hardcoded BuildMonth version is replaced with a reference note.
    """
    if "deployment" not in kb_name:
        return content, []

    changes: list[str] = []
    # Find hardcoded BuildMonth GQ-020 pattern and replace with note
    hardcoded_pattern = re.compile(
        r"(```(?:kusto|kql)\s*\n[^`]*BuildMonth\s*==\s*[\"\']\d{2}\.\d{2}[\"\']\s*[^`]*```)",
        re.DOTALL | re.IGNORECASE,
    )
    matches = list(hardcoded_pattern.finditer(content))
    if len(matches) >= 1:
        replacement = (
            "```kusto\n"
            "// ⚠️ Hardcoded version removed — use the parameterized GQ-020 above.\n"
            "// Set BuildMonth parameter to target OS build (e.g., \"26.03\").\n"
            "```"
        )
        new_content = hardcoded_pattern.sub(replacement, content, count=1)
        if new_content != content:
            changes.append("Replaced hardcoded BuildMonth GQ-020 with parameterized reference note")
            return new_content, changes

    return content, changes


def fix_gq041_join_key(content: str, kb_name: str) -> tuple[str, list[str]]:
    """In deployment-kb: fix GQ-041 bad join key (FabricCluster not defined).

    Adds a comment noting the broken join and a corrected version.
    """
    if "deployment" not in kb_name:
        return content, []

    changes: list[str] = []
    bad_join_pattern = re.compile(
        r"(join\s+kind=leftouter\s+pfTenantRegions\s+on\s+FabricCluster)",
        re.IGNORECASE,
    )
    if bad_join_pattern.search(content):
        # Add a warning comment before the bad join
        replacement = r"// ⚠️ FIX NEEDED: FabricCluster is not defined on the left side. Use TenantName or verify join key.\n// \1"
        new_content = bad_join_pattern.sub(replacement, content, count=1)
        if new_content != content:
            changes.append("Added warning comment to GQ-041 broken join key (FabricCluster)")
            return new_content, changes

    return content, []


def add_missing_crossref_block(content: str, kb_name: str) -> tuple[str, list[str]]:
    """Add a minimal cross-references section footer if none exists."""
    changes: list[str] = []
    has_crossref = bool(re.search(
        r"^## .*(cross.ref|references?|glossary)",
        content,
        re.MULTILINE | re.IGNORECASE,
    ))
    if has_crossref:
        return content, []

    footer = (
        "\n\n## Cross-References\n\n"
        "<!-- kb_improver: auto-added stub — populate with relevant KB links, ADO items, and IcMs -->\n\n"
        "| KB File | Relationship |\n"
        "|---------|-------------|\n"
        "| *(add cross-references here)* | *(describe relationship)* |\n"
    )
    changes.append("Added missing cross-references section stub")
    return content + footer, changes


def mark_empty_stubs(content: str, kb_name: str) -> tuple[str, list[str]]:
    """Add structured improvement tags to empty/thin stub sections."""
    changes: list[str] = []
    stub_tag = "<!-- kb_improver: STUB — add content here (auto-flagged {date}) -->\n".format(
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )

    def _tag_stub(m: re.Match) -> str:
        heading = m.group(1)
        body = m.group(2)
        stripped = body.strip()
        if len(stripped) < 30 and stub_tag.strip()[:20] not in stripped:
            changes.append(f"Tagged stub section: {heading.strip()}")
            return f"{heading}\n{stub_tag}\n{body}"
        return m.group(0)

    new_content = re.sub(
        r"(^## [^\n]+\n)((?:(?!^## ).)*)",
        _tag_stub,
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    return new_content, changes


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

ALL_FIXES = [
    fix_timestamp_in_kql,
    fix_copy_paste_corruption,
    fix_speculative_sections,
    fix_gq020_duplication,
    fix_gq041_join_key,
    add_missing_crossref_block,
    mark_empty_stubs,
]


def apply_fixes_to_kb(kb_path: Path, apply: bool = False) -> dict[str, Any]:
    """Apply all programmatic fixes to a single KB file.

    Returns a dict with keys: kb_name, changes, applied, error.
    """
    kb_name = kb_path.stem
    try:
        content = kb_path.read_text(encoding="utf-8")
    except OSError as e:
        return {"kb_name": kb_name, "changes": [], "applied": False, "error": str(e)}

    all_changes: list[str] = []
    current = content

    for fix_fn in ALL_FIXES:
        try:
            current, changes = fix_fn(current, kb_name)
            all_changes.extend(changes)
        except Exception as e:
            _log.warning("Fix %s failed on %s: %s", fix_fn.__name__, kb_name, e)

    if not all_changes:
        return {"kb_name": kb_name, "changes": [], "applied": False, "error": None}

    if apply and current != content:
        try:
            # Atomic write via temp file + replace
            tmp_fd, tmp_path = tempfile.mkstemp(dir=kb_path.parent, suffix=".tmp")
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    f.write(current)
                os.replace(tmp_path, kb_path)
                _log.info("Applied %d fixes to %s", len(all_changes), kb_name)
            except Exception:
                os.unlink(tmp_path)
                raise
        except Exception as e:
            return {"kb_name": kb_name, "changes": all_changes, "applied": False, "error": str(e)}

    return {"kb_name": kb_name, "changes": all_changes, "applied": apply, "error": None}


def load_quality_results() -> dict[str, Any]:
    """Load the latest quality results from state/."""
    if not _QUALITY_RESULTS_PATH.exists():
        return {}
    try:
        return json.loads(_QUALITY_RESULTS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        _log.warning("Cannot read quality results: %s", e)
        return {}


def write_report(
    fix_results: list[dict],
    quality_results: dict,
    apply: bool,
) -> None:
    """Write improvement report to state/kb_improvement_report.md."""
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mode = "APPLIED" if apply else "DRY-RUN"

    lines: list[str] = [
        f"# KB Improvement Report — {run_at} ({mode})\n",
        f"\n**Overall avg quality score:** {quality_results.get('overall_avg', 'n/a')}/10  ",
        f"\n**Threshold:** {quality_results.get('threshold', 8.0)}/10  ",
        f"\n**Below threshold:** {', '.join(quality_results.get('below_threshold', [])) or 'none'}\n",
        "\n---\n",
        "\n## Programmatic Fixes\n",
    ]

    total_changes = 0
    for r in fix_results:
        kb = r["kb_name"]
        changes = r["changes"]
        applied = r["applied"]
        error = r["error"]
        total_changes += len(changes)

        if error:
            lines.append(f"\n### {kb} — ❌ ERROR: {error}\n")
        elif not changes:
            lines.append(f"\n### {kb} — ✅ No programmatic fixes needed\n")
        else:
            status = "✅ Applied" if applied else "🔍 Detected (dry-run)"
            lines.append(f"\n### {kb} — {status} ({len(changes)} fix(es))\n")
            for c in changes:
                lines.append(f"- {c}\n")

    lines.append(f"\n**Total programmatic fixes: {total_changes}**\n")
    lines.append("\n---\n")
    lines.append("\n## LLM-Assistance Required\n")
    lines.append("\nThe following improvements require manual or LLM review:\n\n")

    # Surface the top issues from quality results for LLM review
    files = quality_results.get("files", {})
    llm_items: list[str] = []
    for kb_name, kb_data in sorted(files.items(), key=lambda x: x[1].get("score", 10)):
        score = kb_data.get("score", 0)
        if score < 9.0:
            issues = kb_data.get("issues", [])
            kusto_issues = [i for i in issues if "kusto" in i.lower() or "kql" in i.lower() or "query" in i.lower()]
            other_issues = [i for i in issues if i not in kusto_issues]
            if kusto_issues:
                for iss in kusto_issues[:3]:
                    llm_items.append(f"- **{kb_name}** (score {score}): {iss}")
            if other_issues:
                for iss in other_issues[:2]:
                    llm_items.append(f"- **{kb_name}** (score {score}): {iss}")

    lines.extend([item + "\n" for item in llm_items[:20]])

    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REPORT_PATH.write_text("".join(lines), encoding="utf-8")
    _log.info("Improvement report written to %s", _REPORT_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(description="Artha KB Quality Improver")
    parser.add_argument("--apply", action="store_true", help="Apply all programmatic fixes")
    parser.add_argument("--auto", action="store_true", help="--apply + quiet (background mode)")
    parser.add_argument("--report", action="store_true", help="Print report to stdout")
    parser.add_argument("--kb", metavar="NAME", help="Process only this KB (stem name)")
    args = parser.parse_args()

    apply = args.apply or args.auto

    if args.auto:
        logging.disable(logging.WARNING)  # Silent in background mode

    quality_results = load_quality_results()
    below_threshold = quality_results.get("below_threshold", [])

    # Determine which KBs to process
    if args.kb:
        targets = [f for f in _DOMAIN_KB_FILES if Path(f).stem == args.kb]
    elif below_threshold:
        targets = [f for f in _DOMAIN_KB_FILES if Path(f).stem in below_threshold]
    else:
        targets = _DOMAIN_KB_FILES  # Process all in dry-run

    fix_results: list[dict] = []
    for kb_file in targets:
        kb_path = _KB_DIR / kb_file
        if not kb_path.exists():
            fix_results.append({
                "kb_name": Path(kb_file).stem,
                "changes": [],
                "applied": False,
                "error": "File not found",
            })
            continue
        result = apply_fixes_to_kb(kb_path, apply=apply)
        fix_results.append(result)

    write_report(fix_results, quality_results, apply=apply)

    if args.report:
        print(_REPORT_PATH.read_text(encoding="utf-8"))

    total = sum(len(r["changes"]) for r in fix_results)
    if not args.auto:
        mode_str = "Applied" if apply else "Detected (dry-run)"
        print(f"KB Improver: {mode_str} {total} programmatic fix(es) across {len(targets)} KB(s).")
        print(f"Report: {_REPORT_PATH}")


if __name__ == "__main__":
    main()
