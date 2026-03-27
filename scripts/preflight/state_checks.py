"""preflight/state_checks.py — State file population, templates, open items, and profile checks."""
from __future__ import annotations

import os
import sys
import subprocess
import time
from pathlib import Path

from preflight._types import (
    ARTHA_DIR, SCRIPTS_DIR, STATE_DIR, _SUBPROCESS_ENV, _rel, CheckResult,
)

def check_state_directory() -> CheckResult:
    """Verify state/ directory exists and is writable."""
    if not os.path.isdir(STATE_DIR):
        return CheckResult(
            "state directory", "P0", False,
            f"State directory missing: {_rel(STATE_DIR)}",
            fix_hint="Run: python scripts/preflight.py --fix",
        )
    test_path = os.path.join(STATE_DIR, ".preflight_write_test")
    try:
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        return CheckResult("state directory", "P0", True, f"{_rel(STATE_DIR)} writable ✓")
    except OSError as exc:
        return CheckResult(
            "state directory", "P0", False,
            f"State directory not writable: {exc}",
            fix_hint=f"Check OneDrive sync status and permissions on {_rel(STATE_DIR)}",
        )


def _is_bootstrap_stub(path: str) -> bool:
    """Return True if the file is an unpopulated bootstrap placeholder.

    Bootstrap stubs are created by setup.sh/setup.ps1 and contain the exact
    two-line body ``# Content\\nsome: value`` inside the YAML frontmatter.
    Any file that has been genuinely populated will have different frontmatter.
    We match only the exact fingerprint to avoid false positives on real data.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read(256)  # Only need the first few lines
        # Exact stub fingerprint: frontmatter starts with ---\n# Content\nsome: value
        return "# Content\nsome: value" in raw
    except OSError:
        return False


def check_state_templates(auto_fix: bool = False) -> CheckResult:
    """P1: Populate missing state files from state/templates/ on first run."""
    templates_dir = os.path.join(STATE_DIR, "templates")
    if not os.path.isdir(templates_dir):
        return CheckResult(
            "state templates", "P1", False,
            "state/templates/ not found — state files cannot be auto-populated",
            fix_hint="Run: python scripts/preflight.py --fix  (or use /bootstrap in your AI CLI)",
        )
    templates = [f for f in os.listdir(templates_dir) if f.endswith(".md") and f != "README.md"]
    missing = []
    stubs = []
    for tpl in templates:
        target = os.path.join(STATE_DIR, tpl)
        if not os.path.exists(target):
            missing.append(tpl)
        elif _is_bootstrap_stub(target):
            stubs.append(tpl)

    if not missing and not stubs:
        return CheckResult("state templates", "P1", True, "All state files present ✓")

    if auto_fix:
        populated = []
        for tpl in list(missing) + list(stubs):
            src = os.path.join(templates_dir, tpl)
            dst = os.path.join(STATE_DIR, tpl)
            # Special rule for health-check.md: only populate if file is truly absent
            # OR exists but has no structured YAML frontmatter (last_catch_up field).
            if tpl == "health-check.md" and os.path.exists(dst):
                try:
                    with open(dst, encoding="utf-8") as f:
                        header_lines = [f.readline() for _ in range(10)]
                    if any("last_catch_up" in line for line in header_lines):
                        continue  # Structured health-check already present — skip
                except OSError:
                    pass  # Unreadable — let the copy proceed
            try:
                import shutil
                shutil.copy2(src, dst)
                populated.append(tpl)
            except OSError:
                pass
        msg_parts = []
        if any(t in populated for t in missing):
            msg_parts.append(f"created {sum(1 for t in missing if t in populated)}")
        if any(t in populated for t in stubs):
            msg_parts.append(f"replaced {sum(1 for t in stubs if t in populated)} bootstrap stubs")
        msg = f"Populated {len(populated)} state files ({', '.join(msg_parts)}): {', '.join(populated)}"
        return CheckResult(
            "state templates", "P1", True,
            msg,
            auto_fixed=True,
        )

    all_needing_fix = missing + stubs
    stub_note = f" ({len(stubs)} are bootstrap stubs)" if stubs else ""
    return CheckResult(
        "state templates", "P1", False,
        f"{len(all_needing_fix)} state file(s) need population{stub_note}: {', '.join(all_needing_fix[:5])}{'…' if len(all_needing_fix) > 5 else ''}",
        fix_hint="Run preflight with --fix to auto-populate from state/templates/",
    )


def check_open_items(auto_fix: bool = False) -> CheckResult:
    """P1: Verify open_items.md exists and is readable. Auto-creates from template with --fix."""
    path = os.path.join(STATE_DIR, "open_items.md")
    if not os.path.exists(path):
        template_path = os.path.join(STATE_DIR, "templates", "open_items.md")
        if auto_fix and os.path.exists(template_path):
            try:
                import shutil
                shutil.copy2(template_path, path)
                return CheckResult(
                    "open_items.md", "P1", True,
                    "Created state/open_items.md from template ✓",
                    auto_fixed=True,
                )
            except OSError as exc:
                return CheckResult(
                    "open_items.md", "P1", False,
                    f"Could not create open_items.md: {exc}",
                    fix_hint="Create state/open_items.md manually",
                )
        return CheckResult(
            "open_items.md", "P1", False,
            "open_items.md not found — action tracking unavailable",
            fix_hint="Run: python scripts/preflight.py --fix  (auto-creates from template)",
        )
    try:
        with open(path) as f:
            f.read(100)
        return CheckResult("open_items.md", "P1", True, "open_items.md accessible ✓")
    except OSError as exc:
        return CheckResult("open_items.md", "P1", False, f"open_items.md unreadable: {exc}")


def check_briefings_directory() -> CheckResult:
    """P1: Verify briefings/ directory is writable for archiving."""
    briefings_dir = os.path.join(ARTHA_DIR, "briefings")
    if not os.path.isdir(briefings_dir):
        try:
            os.makedirs(briefings_dir, exist_ok=True)
            return CheckResult(
                "briefings directory", "P1", True,
                f"Created {_rel(briefings_dir)} ✓",
                auto_fixed=True,
            )
        except OSError as exc:
            return CheckResult(
                "briefings directory", "P1", False,
                f"Cannot create briefings/: {exc}",
            )
    test_path = os.path.join(briefings_dir, ".preflight_write_test")
    try:
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        return CheckResult("briefings directory", "P1", True, f"{_rel(briefings_dir)} writable ✓")
    except OSError as exc:
        return CheckResult("briefings directory", "P1", False, f"briefings/ not writable: {exc}")


def check_profile_completeness() -> CheckResult:
    """P1: Verify user_profile.yaml has minimum viable fields populated.

    Only fires on near-empty profiles (≤10 YAML keys total). Users with
    intentionally partial configs (>10 keys) are not warned.
    Ref: vm-hardening.md Phase 2.2
    """
    profile_path = os.path.join(ARTHA_DIR, "config", "user_profile.yaml")
    if not os.path.exists(profile_path):
        return CheckResult(
            "user_profile completeness", "P1", True,
            "user_profile.yaml not found — cold start (handled by preflight gate) ✓",
        )

    try:
        import yaml  # type: ignore
        with open(profile_path, encoding="utf-8") as f:
            profile = yaml.safe_load(f) or {}
    except Exception as exc:
        return CheckResult(
            "user_profile completeness", "P1", False,
            f"user_profile.yaml unreadable: {exc}",
        )

    def _count_keys(d: dict) -> int:
        """Recursively count all keys in a nested dict."""
        if not isinstance(d, dict):
            return 0
        total = len(d)
        for v in d.values():
            total += _count_keys(v)
        return total

    total_keys = _count_keys(profile)

    # Silent pass for profiles that have been meaningfully filled in
    if total_keys > 10:
        return CheckResult(
            "user_profile completeness", "P1", True,
            f"Profile populated ({total_keys} keys) ✓",
        )

    # Near-empty profile — surface actionable warnings
    missing: list[str] = []

    def _get(d: dict, path: str):
        parts = path.split(".")
        node = d
        for part in parts:
            if not isinstance(node, dict):
                return None
            node = node.get(part)
            if node is None:
                return None
        return node

    if not _get(profile, "family.primary_user.name"):
        missing.append("family.primary_user.name")
    emails = _get(profile, "family.primary_user.emails") or {}
    if not any((emails or {}).values()):
        missing.append("family.primary_user.emails")
    if not _get(profile, "location.timezone"):
        missing.append("location.timezone")

    domains = _get(profile, "domains") or {}
    enabled = [d for d, v in (domains if isinstance(domains, dict) else {}).items()
               if isinstance(v, dict) and v.get("enabled")]
    if not enabled:
        missing.append("domains.<at least one>.enabled: true")

    recommendations: list[str] = []
    if not _get(profile, "integrations.google_calendar.calendar_ids"):
        recommendations.append("integrations.google_calendar.calendar_ids")
    if not _get(profile, "household.type"):
        recommendations.append("household.type")

    hint_parts = []
    if missing:
        hint_parts.append(f"Required missing: {', '.join(missing)}")
    if recommendations:
        hint_parts.append(f"Recommended: {', '.join(recommendations)}")
    hint_parts.append("Run /bootstrap or edit config/user_profile.yaml")

    return CheckResult(
        "user_profile completeness", "P1", False,
        f"Profile near-empty ({total_keys} keys) — catch-up will have limited context",
        fix_hint=" | ".join(hint_parts),
    )


