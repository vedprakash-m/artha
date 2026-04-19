"""preflight/state_checks.py — State file population, templates, open items, and profile checks."""
from __future__ import annotations

import os
import re
import sys
import subprocess
import time
from datetime import datetime
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
        with open(path, encoding="utf-8") as f:
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


def check_briefings_archive_coverage() -> CheckResult:
    """P1: Per-source archive coverage audit (specs/brief.md §5 Step 7 / Commit 3).

    Two sub-checks run in order; the first failure wins:

    (a) Existence check — after 10:00 AM local time, warn if no briefings/YYYY-MM-DD.md
        exists for today.  This catches the case where the LLM skipped the archive step
        entirely (R1 mitigation D).

    (b) VS Code source check — if today's briefing file exists AND at least one
        tmp/session_history_*.md file was modified today (evidence of a VS Code catch-up
        session), warn if the briefing file has no ``source: vscode`` OR ``runtime: vscode``
        entry.  (VS Code sessions now write ``source: interactive_cli, runtime: vscode``
        via the staging pickup pattern.)
        This detects the drift scenario where a catch-up ran but the LLM emitted
        ``💾 Briefing staged.`` without actually writing the staging file.

    Both sub-checks are P1 (non-blocking) — they surface a warning in the preflight
    report and log to state/audit.md but never halt catch-up.

    Ref: specs/brief.md §5 Step 7, §6 R1 Mitigation D, §6 R9; specs/rebrief.md §2
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    briefings_dir = Path(ARTHA_DIR) / "briefings"
    today_path = briefings_dir / f"{today}.md"

    # ── (a) Existence check (after 10 AM) ──────────────────────────────────
    if now.hour >= 10 and not today_path.exists():
        _write_audit_event_sc(
            "briefings_archive_missing",
            {"date": today, "check": "existence", "hour": str(now.hour)},
        )
        return CheckResult(
            "briefing archive coverage", "P1", False,
            f"No briefing archived for today ({today}) — did the LLM skip staging to tmp/briefing_incoming_<runtime>.md?",
            fix_hint="Write briefing to tmp/briefing_incoming_vscode.md, then run: python scripts/pipeline.py (ingests on startup)",
        )

    # ── (b) VS Code source check ────────────────────────────────────────────
    if today_path.exists():
        tmp_dir = Path(ARTHA_DIR) / "tmp"
        session_files = list(tmp_dir.glob("session_history_*.md")) if tmp_dir.exists() else []
        session_today = [
            f for f in session_files
            if datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d") == today
        ]
        if session_today:
            try:
                content = today_path.read_text(encoding="utf-8")
                # Accept either legacy source: vscode OR new staging-pattern runtime: vscode
                has_vscode = bool(
                    re.search(r"^source:\s*vscode\s*$", content, re.MULTILINE)
                    or re.search(r"^runtime:\s*vscode\s*$", content, re.MULTILINE)
                )
            except OSError:
                has_vscode = False
            if not has_vscode:
                _write_audit_event_sc(
                    "briefings_vscode_source_missing",
                    {"date": today, "session_files": str(len(session_today))},
                )
                return CheckResult(
                    "briefing archive coverage", "P1", False,
                    f"VS Code session detected today ({len(session_today)} session file(s)) "
                    f"but no 'source: vscode' or 'runtime: vscode' entry in briefings/{today}.md — "
                    f"LLM may have emitted \U0001f4be token without writing the staging file",
                    fix_hint="Write briefing to tmp/briefing_incoming_vscode.md, then run: python scripts/pipeline.py",
                )

    return CheckResult(
        "briefing archive coverage", "P1", True,
        f"briefings/{today}.md archive coverage OK ✓",
    )


def _write_audit_event_sc(event: str, fields: dict) -> None:
    """Best-effort audit.md append (avoids importing briefing_archive from state_checks)."""
    try:
        audit_log = Path(ARTHA_DIR) / "state" / "audit.md"
        from datetime import timezone
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        field_str = " | ".join(f"{k}:{v}" for k, v in fields.items())
        audit_log.parent.mkdir(parents=True, exist_ok=True)
        with audit_log.open("a", encoding="utf-8") as fh:
            fh.write(f"| {now_utc} | {event} | {field_str} |\n")
    except Exception:  # noqa: BLE001
        pass


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
        from lib.config_loader import load_config  # noqa: PLC0415
        profile = load_config("user_profile", str(Path(ARTHA_DIR) / "config"))
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


def check_prompt_size() -> CheckResult:
    """RD-38: Verify config/Artha.md is within the compact prompt size limit.

    The compact architecture targets ≤20KB. The CI gate is 25KB.
    If Artha.md exceeds 25KB, it likely means the compact path was not used
    when regenerating the identity prompt, or the core template has grown.
    """
    _MAX_PROMPT_BYTES = 25_000  # 25KB CI gate per RD-38 spec
    prompt_path = os.path.join(ARTHA_DIR, "config", "Artha.md")
    try:
        size = os.path.getsize(prompt_path)
    except OSError:
        return CheckResult(
            "prompt size (RD-38)", "P2", False,
            "config/Artha.md not found",
            fix_hint="Run: python scripts/generate_identity.py",
        )

    if size > _MAX_PROMPT_BYTES:
        return CheckResult(
            "prompt size (RD-38)", "P2", False,
            f"config/Artha.md is {size:,} bytes — exceeds {_MAX_PROMPT_BYTES:,}B limit",
            fix_hint=(
                "Run: python scripts/generate_identity.py "
                "(compact mode is the default; --no-compact produces the full file)"
            ),
        )
    return CheckResult(
        "prompt size (RD-38)", "P2", True,
        f"config/Artha.md is {size:,} bytes ✓ (limit: {_MAX_PROMPT_BYTES:,}B)",
    )


