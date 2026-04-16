"""preflight/__init__.py — Artha pre-catch-up go/no-go health gate.

Usage:
  python -m preflight            — full check, exits 0/1
  python -m preflight --json     — machine-readable JSON output
  python -m preflight --quiet    — minimal output (only failures)
  python -m preflight --fix      — attempt common auto-fixes

Public API:
  run_preflight(auto_fix, quiet) -> list[CheckResult]
  format_results(checks, ...)    -> tuple[str, bool]
  CheckResult                    — dataclass
  main()                         — CLI entry point
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import types

from preflight._types import ARTHA_DIR, SCRIPTS_DIR, STATE_DIR, TOKEN_DIR, LOCK_FILE, WORKIQ_CACHE_FILE, _rel, CheckResult, _REQUIRED_DEPS
from preflight.vault_checks import check_keyring_backend, check_vault_health, check_vault_lock, check_vault_watchdog
from preflight.oauth_checks import check_oauth_token, check_token_freshness, check_msgraph_token
from preflight.api_checks import check_script_health, check_pii_guard
from preflight.state_checks import (
    check_state_directory, check_state_templates, check_open_items,
    check_briefings_directory, check_profile_completeness, _is_bootstrap_stub,
    check_prompt_size,
)
from preflight.integration_checks import (
    check_bridge_health, check_workiq, check_ado_auth, check_ha_connectivity,
    check_dep_freshness, check_channel_config, check_channel_health, check_action_handlers,
    check_ext_agent_discovery, check_ext_agent_health,
)

__all__ = [
    "CheckResult",
    "run_preflight",
    "format_results",
    "main",
]

# ---------------------------------------------------------------------------
# Module attribute propagation for backward-compatible monkeypatching.
# When tests patch preflight.ARTHA_DIR (or STATE_DIR etc.), the new value
# automatically propagates to all submodules that use the same constant.
# ---------------------------------------------------------------------------

_PROPAGATE_ATTRS = frozenset({
    "ARTHA_DIR", "SCRIPTS_DIR", "STATE_DIR", "TOKEN_DIR", "LOCK_FILE", "WORKIQ_CACHE_FILE",
})
_SUBMODULE_NAMES = (
    "vault_checks", "oauth_checks", "api_checks",
    "state_checks", "integration_checks", "_types",
)


class _PrefightModule(types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if name in _PROPAGATE_ATTRS:
            for sub in _SUBMODULE_NAMES:
                mod = sys.modules.get(f"preflight.{sub}")
                if mod is not None and hasattr(mod, name):
                    object.__setattr__(mod, name, value)


sys.modules[__name__].__class__ = _PrefightModule


def run_preflight(auto_fix: bool = False, quiet: bool = False) -> list[CheckResult]:
    """Run all preflight checks. Returns list of CheckResult objects."""
    checks: list[CheckResult] = []

    # ── P0 — Hard blocks ──────────────────────────────────────────────────
    checks.append(check_keyring_backend())
    checks.append(check_vault_health())
    checks.append(check_vault_lock(auto_fix=auto_fix))
    checks.append(check_vault_watchdog())  # RD-40: advisory check for watchdog daemon
    checks.append(check_oauth_token("Gmail", "gmail-oauth-token.json"))
    checks.append(check_oauth_token("Calendar", "gcal-oauth-token.json"))
    checks.append(check_pii_guard())
    # Global API live connection via unified pipeline.py (skip if --quiet to reduce latency)
    if not quiet:
        try:
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "gmail"], severity="P0"
            ))
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "google_calendar"], severity="P0"
            ))
            checks.append(check_script_health(
                "gmail_send.py", ["--health"], severity="P0"
            ))
        except subprocess.TimeoutExpired:
            checks.append(CheckResult(
                "API connectivity", "P0", False,
                "API health check timed out (>30s)",
                fix_hint="Check network connectivity and OAuth credentials",
            ))
    checks.append(check_state_directory())

    # ── P1 — State file population from templates (first-run) ─────────────
    checks.append(check_state_templates(auto_fix=auto_fix))

    # ── P1 — Warnings only ────────────────────────────────────────────────
    checks.append(check_token_freshness("Gmail", "gmail-oauth-token.json"))
    checks.append(check_token_freshness("Calendar", "gcal-oauth-token.json"))
    checks.append(check_open_items(auto_fix=auto_fix))
    checks.append(check_briefings_directory())
    checks.append(check_msgraph_token())  # T-1B.6.1: non-blocking, To Do sync optional

    # ── P1 — Profile completeness (vm-hardening.md Phase 2.2) ─────────────
    checks.append(check_profile_completeness())

    # ── P2 — Prompt size ceiling (RD-38: compact Artha.md ≤ 25KB) ─────────
    checks.append(check_prompt_size())

    # ── P1 — Action handler health checks + expiry sweep (Step 0c) ───────
    checks.append(check_action_handlers())

    # ── P1 — WorkIQ Calendar (v2.2 — Windows-only, non-blocking) ─────────
    checks.append(check_workiq())

    # ── P1 — Bridge health (dual-setup.md — non-blocking; skipped if disabled) ──
    checks.append(check_bridge_health())

    # ── P1 — Home Assistant (ARTHA-IOT Wave 1 — non-blocking) ────────────
    checks.append(check_ha_connectivity())

    # ── P1 — ADO auth (work-projects domain, opt-in, non-blocking) ────────
    checks.append(check_ado_auth())

    # ── P1 — Channel config (v5.1 — non-blocking) ──────────────────────────
    checks.append(check_channel_config())

    # ── P1 — Channel health (v5.0 — non-blocking) ─────────────────────────
    checks.append(check_channel_health())

    # ── P1 — Dependency freshness ─────────────────────────────────────────
    checks.append(check_dep_freshness())

    # ── P1 — External agent discovery + health (AR-9, non-blocking) ──────
    checks.append(check_ext_agent_discovery())
    checks.append(check_ext_agent_health())

    # ── P1 — Skill dependencies ───────────────────────────────────────────
    try:
        import bs4
        checks.append(CheckResult("BeautifulSoup", "P1", True, "beautifulsoup4 found"))
    except ImportError:
        checks.append(CheckResult(
            "BeautifulSoup", "P1", False,
            "beautifulsoup4 not installed — Data Skills will be disabled",
            fix_hint="pip install beautifulsoup4"
        ))

    # Career PDF feature — Playwright + Chromium (P1, non-blocking)
    # Spec FR-CS-3: "Preflight check validates Chromium binary availability"
    try:
        import playwright  # noqa: F401
        # Chromium browser cache location (cross-platform)
        if sys.platform == "darwin":
            _pw_cache = pathlib.Path.home() / "Library" / "Caches" / "ms-playwright"
        elif sys.platform == "win32":
            _pw_cache = pathlib.Path.home() / "AppData" / "Local" / "ms-playwright"
        else:
            _pw_cache = pathlib.Path.home() / ".cache" / "ms-playwright"
        _chromium_dirs = sorted(_pw_cache.glob("chromium-*")) if _pw_cache.exists() else []
        if _chromium_dirs:
            checks.append(CheckResult(
                "Playwright Chromium", "P1", True,
                f"playwright + Chromium ready ({_chromium_dirs[-1].name}) ✓",
            ))
        else:
            checks.append(CheckResult(
                "Playwright Chromium", "P1", False,
                "playwright installed but Chromium browser not found — career PDF generation will fail",
                fix_hint="Run: playwright install chromium  (or: make install-playwright)",
            ))
    except ImportError:
        checks.append(CheckResult(
            "Playwright", "P1", False,
            "playwright not installed — career PDF generation unavailable (non-blocking)",
            fix_hint="Run: make install-playwright  (or: pip install playwright && playwright install chromium)",
        ))

    # MS Graph live connectivity via unified pipeline.py (P1)
    if not quiet:
        try:
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "outlook_email"], severity="P1"
            ))
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "outlook_calendar"], severity="P1"
            ))
        except subprocess.TimeoutExpired:
            checks.append(CheckResult(
                "MS Graph connectivity", "P1", False,
                "MS Graph health check timed out — Outlook data may be unavailable",
                fix_hint="Check network or re-run: python scripts/pipeline.py --health -s outlook_email",
            ))

    # iCloud live connectivity via unified pipeline.py (P1)
    if not quiet:
        try:
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "icloud_email"], severity="P1"
            ))
            checks.append(check_script_health(
                "pipeline.py", ["--health", "--source", "icloud_calendar"], severity="P1"
            ))
        except subprocess.TimeoutExpired:
            checks.append(CheckResult(
                "iCloud connectivity", "P1", False,
                "iCloud health check timed out — iCloud data may be unavailable",
                fix_hint="Check network or re-run: python scripts/pipeline.py --health -s icloud_email",
            ))

    # Canvas LMS via pipeline.py (P1 — only if configured)
    _canvas_configured = False
    try:
        import sys as _sys
        if SCRIPTS_DIR not in _sys.path:
            _sys.path.insert(0, SCRIPTS_DIR)
        from profile_loader import children as _children, has_profile as _has_profile
        if _has_profile():
            for _child in _children():
                _school = _child.get("school", {}) or {}
                if _school.get("canvas_url") and _school.get("canvas_keychain_key"):
                    _canvas_configured = True
                    break
    except Exception:
        pass  # Non-critical — Canvas is an optional connector
    if _canvas_configured and not quiet:
        checks.append(check_script_health(
            "pipeline.py", ["--health", "--source", "canvas_lms"], severity="P1"
        ))

    return checks


def format_results(
    checks: list[CheckResult],
    quiet: bool = False,
    first_run: bool = False,
    advisory: bool = False,
) -> tuple[str, bool]:
    """
    Format check results into terminal output.
    Returns (output_string, all_p0_passed).

    first_run=True: softer "Setup Checklist" display — OAuth failures shown
    as ○ (not yet configured) rather than ⛔ (hard block).

    advisory=True: P0 failures reclassified to ADVISORY in output; exit always 0.
    Use ONLY in sandboxed/VM environments where hard blocks are known and accepted.
    NEVER use on local machines.
    """
    _FIRST_RUN_OAUTH_HINTS = (
        "setup_google_oauth.py",
        "setup_msgraph_oauth.py",
        "setup_icloud_auth.py",
    )

    def _is_expected_on_first_run(c: CheckResult) -> bool:
        """True when a check failure is normal on a fresh install."""
        hint = c.fix_hint or ""
        msg  = c.message  or ""
        if any(h in hint for h in _FIRST_RUN_OAUTH_HINTS):
            return True
        if "Token file missing" in msg or "token file missing" in msg.lower():
            return True
        if c.name.startswith("pipeline.py") and not c.passed:
            return True
        if c.name == "gmail_send.py health" and not c.passed:
            return True
        return False

    if first_run:
        p0_failures = [
            c for c in checks
            if c.severity == "P0" and not c.passed and not _is_expected_on_first_run(c)
        ]
    else:
        p0_failures = [c for c in checks if c.severity == "P0" and not c.passed]

    p1_warnings = [c for c in checks if c.severity == "P1" and not c.passed]
    # In advisory mode, P0 failures become advisories — exit always 0
    all_passed  = advisory or not p0_failures

    lines: list[str] = []

    if not quiet:
        if advisory:
            lines.append("━━ ARTHA PRE-FLIGHT GATE (ADVISORY MODE) ━━━━━━━━━")
            lines.append("  ⚠️  Advisory mode active — P0 failures are non-blocking")
            lines.append("  ⚠️  Use ONLY in sandboxed/VM environments. NEVER on local Mac.")
        elif first_run:
            lines.append("━━ ARTHA SETUP CHECKLIST ━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("  first-run mode — OAuth failures are expected and not blocking")
        else:
            lines.append("━━ ARTHA PRE-FLIGHT GATE ━━━━━━━━━━━━━━━━━━━━━━━━")
        for c in checks:
            if quiet and c.passed:
                continue
            if first_run and not c.passed and _is_expected_on_first_run(c):
                lines.append(f"  ○ [{c.severity}] {c.name}: not yet configured")
                if c.fix_hint:
                    lines.append(f"       → {c.fix_hint}")
            elif advisory and not c.passed and c.severity == "P0":
                lines.append(f"  ⚠️  [ADVISORY] {c.name}: {c.message}")
                if c.fix_hint:
                    lines.append(f"       → {c.fix_hint}")
            else:
                icon = "✓" if c.passed else ("⛔" if c.severity == "P0" else "⚠")
                auto_note = " (auto-fixed)" if c.auto_fixed else ""
                lines.append(f"  {icon} [{c.severity}] {c.name}: {c.message}{auto_note}")
                if not c.passed and c.fix_hint:
                    lines.append(f"       → {c.fix_hint}")
        lines.append("")

    if all_passed:
        auto_fixed_count = sum(1 for c in checks if c.auto_fixed)
        notes = []
        if p1_warnings:
            notes.append(f"{len(p1_warnings)} warning(s)")
        if auto_fixed_count:
            notes.append(f"{auto_fixed_count} auto-fixed")
        if advisory and p0_failures:
            notes.append(f"{len(p0_failures)} advisory (non-blocking in this environment)")
        suffix = f" ({', '.join(notes)})" if notes else ""
        if advisory:
            lines.append(f"⚠️  Pre-flight: ADVISORY GO{suffix} — proceed with degraded catch-up")
        elif first_run:
            lines.append(f"✓ Setup checklist: ready{suffix} — open your AI CLI and say: catch me up")
        else:
            lines.append(f"✓ Pre-flight: GO{suffix} — proceed with catch-up")
    else:
        if first_run:
            lines.append(f"⛔ {len(p0_failures)} critical setup issue(s) need attention:")
        else:
            lines.append(f"⛔ Pre-flight: NO-GO — {len(p0_failures)} critical check(s) failed")
        for c in p0_failures:
            lines.append(f"   • {c.name}: {c.message}")
        lines.append("")
        if first_run:
            lines.append("Fix the above issues to proceed.")
        else:
            lines.append("Catch-up aborted. Fix the above issues and re-run.")

    if not quiet:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines), all_passed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Artha pre-catch-up health gate. "
            "Exits 0 if all P0 checks pass, 1 if any P0 check fails."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (for scripting)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Minimal output — only show failures. Skips live API connectivity checks.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix safe issues (e.g. clear stale lock file, fix token file permissions)",
    )
    parser.add_argument(
        "--first-run",
        action="store_true",
        help=(
            "First-run / setup-checklist mode: OAuth and connector failures are shown as "
            "'not yet configured' (\u25cb) rather than hard blocks (⛔). "
            "Exits 0 when only expected setup steps remain."
        ),
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help=(
            "Advisory mode: run all checks but report P0 failures as ADVISORY instead of "
            "NO-GO. Exits 0 regardless of P0 failures. "
            "Use ONLY in sandboxed/VM environments where hard blocks are known and accepted. "
            "NEVER use on local machines."
        ),
    )

    args = parser.parse_args()

    # Cold-start detection: exit 3 if user_profile.yaml is missing (first run)
    profile_path = pathlib.Path(ARTHA_DIR) / "config" / "user_profile.yaml"
    if not profile_path.exists():
        if args.json:
            print(json.dumps({"pre_flight_ok": False, "cold_start": True, "checks": []}))
        else:
            print("⛔ Cold start detected — config/user_profile.yaml not found.")
            print("   Run /bootstrap or say 'catch me up' to start guided setup.")
        sys.exit(3)

    advisory = getattr(args, "advisory", False)
    checks   = run_preflight(auto_fix=args.fix, quiet=args.quiet)
    all_ok   = advisory or all(c.passed for c in checks if c.severity == "P0")

    if args.json:
        output = {
            "pre_flight_ok":  all_ok,
            "advisory_mode":  advisory,
            "degradation_list": [
                c.name for c in checks if c.severity == "P0" and not c.passed
            ] if advisory else [],
            "checks": [
                {
                    "name":       c.name,
                    "severity":   c.severity,
                    "passed":     c.passed,
                    "message":    c.message,
                    "fix_hint":   c.fix_hint,
                    "auto_fixed": c.auto_fixed,
                }
                for c in checks
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        formatted, first_run_ok = format_results(
            checks, quiet=args.quiet, first_run=args.first_run, advisory=advisory
        )
        print(formatted)
        # In first-run mode use the softer exit-code logic
        if args.first_run:
            all_ok = first_run_ok

    sys.exit(0 if all_ok else 1)



if __name__ == '__main__':
    main()
