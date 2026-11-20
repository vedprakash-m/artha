"""Additional preflight checks and post-run maintenance owned by the package."""
from __future__ import annotations

import glob
import json
import os
import pathlib
import re
import sys
import subprocess
import time
from datetime import date, datetime, timezone
from pathlib import Path

from preflight._types import (
    ARTHA_DIR,
    SCRIPTS_DIR,
    STATE_DIR,
    WORKIQ_CACHE_FILE,
    _SUBPROCESS_ENV,
    _rel,
    CheckResult,
)
from lib.local_paths import audit_log_path, local_dir

_READ_ONLY = False

def check_vault_policy_enforcement() -> CheckResult:
    """DEBT-VAULT-003 Phase 1: Verify requires_vault domains use .md.age state files.

    Parses domain_registry.yaml and fails if any domain with requires_vault: true
    has a state_file that does not end in '.md.age'.  Surfaced as P0 — these domains
    contain salary, RSU, compensation, and job-search data that must be encrypted.
    """
    registry_path = os.path.join(ARTHA_DIR, "config", "domain_registry.yaml")
    if not os.path.exists(registry_path):
        return CheckResult(
            "vault policy enforcement", "P0", False,
            "config/domain_registry.yaml not found — cannot validate vault policy",
            fix_hint="Ensure config/domain_registry.yaml exists",
        )
    try:
        from lib.config_loader import load_config
        registry = load_config(
            "domain_registry",
            _config_dir=os.path.dirname(registry_path),
            strict=True,
        )
    except Exception as exc:
        return CheckResult(
            "vault policy enforcement", "P0", False,
            f"Failed to parse domain_registry.yaml: {exc}",
        )

    violations: list[str] = []
    domains = registry.get("domains", {})
    if not isinstance(domains, dict):
        # Some registries use a list format
        domains = {}
    for domain, cfg in domains.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("requires_vault") and not str(cfg.get("state_file", "")).endswith(".md.age"):
            state_file = cfg.get("state_file", "<missing>")
            violations.append(
                f"VAULT_POLICY_VIOLATION: domain '{domain}' requires_vault=true "
                f"but state_file='{state_file}' (not .md.age)"
            )

    if violations:
        detail = "; ".join(violations)
        return CheckResult(
            "vault policy enforcement", "P0", False,
            f"Vault policy mismatch ({len(violations)} domain(s)): {detail}",
            fix_hint=(
                "Encrypt the state files with: age --encrypt ... state/<domain>.md > state/<domain>.md.age "
                "then update state_file in domain_registry.yaml to .md.age. "
                "Wait for DEBT-ARCH-001 (StateReader) before Phase 2 migration."
            ),
        )
    return CheckResult(
        "vault policy enforcement", "P0", True,
        "All requires_vault domains have .md.age state files ✓",
    )

def check_guardrails_yaml(force_no_guardrails: bool = False) -> CheckResult:
    """P0 (DEBT-004): Verify guardrails.yaml is present and parseable.

    Guardrails provide PII scanning, injection detection, and rate limiting.
    A missing or corrupt guardrails.yaml disables ALL safety chains — this is
    a hard-block, not a warning.  Use --force-no-guardrails only if you have
    a peer-reviewed reason and accept the audit trail entry.
    """
    cfg_path = os.path.join(ARTHA_DIR, "config", "guardrails.yaml")
    if force_no_guardrails:
        if _READ_ONLY:
            return CheckResult(
                "guardrails.yaml", "P0", False,
                "FORCE_NO_GUARDRAILS is unavailable in --read-only mode",
                fix_hint="Re-run without --read-only from an authorized terminal",
            )
        _audit_guardrail_force_override(cfg_path)
        return CheckResult(
            "guardrails.yaml", "P0", True,
            "FORCE_NO_GUARDRAILS override active — safety chains bypassed (audit logged)",
            fix_hint="Remove --force-no-guardrails to re-enable safety chains",
        )

    if not os.path.isfile(cfg_path):
        return CheckResult(
            "guardrails.yaml", "P0", False,
            f"guardrails.yaml missing: {_rel(cfg_path)}",
            fix_hint="Restore from git: git checkout config/guardrails.yaml",
        )
    try:
        from lib.config_loader import load_config
        data = load_config(
            "guardrails",
            _config_dir=os.path.dirname(cfg_path),
            strict=True,
        )
        if not data or not isinstance(data, dict):
            return CheckResult(
                "guardrails.yaml", "P0", False,
                "guardrails.yaml parsed as empty/non-dict — all safety chains would be inactive",
                fix_hint="Restore from git: git checkout config/guardrails.yaml",
            )
        # Verify at least one enabled guardrail exists
        chains_found = sum(
            1 for section in ("input", "tool", "output")
            for spec in data.get(section, [])
            if spec.get("enabled", True)
        )
        if chains_found == 0:
            return CheckResult(
                "guardrails.yaml", "P0", False,
                "guardrails.yaml has no enabled guardrails — all safety chains inactive",
                fix_hint="Restore from git: git checkout config/guardrails.yaml",
            )
        return CheckResult(
            "guardrails.yaml", "P0", True,
            f"guardrails.yaml valid — {chains_found} enabled guardrail(s) ✓",
        )
    except Exception as exc:
        return CheckResult(
            "guardrails.yaml", "P0", False,
            f"guardrails.yaml parse error: {exc}",
            fix_hint="Restore from git: git checkout config/guardrails.yaml",
        )

def _audit_guardrail_force_override(cfg_path: str) -> None:
    """Write FORCE_NO_GUARDRAILS entry to state/audit.md (best-effort)."""
    if _READ_ONLY:
        return
    try:
        from datetime import datetime, timezone
        audit_log = str(audit_log_path(ARTHA_DIR))
        if os.path.isfile(audit_log):
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            entry = f"[{ts}] FORCE_NO_GUARDRAILS | config: {cfg_path}\n"
            with open(audit_log, "a", encoding="utf-8") as f:
                f.write(entry)
    except Exception:
        pass



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

def _detect_conflict_files(state_dir: str) -> list[str]:
    """Scan registered State File targets using the shared conflict module."""
    from lib.state_targets import light_markdown_targets
    from lib.sync_conflicts import discover_conflict_copies

    directory = Path(state_dir)
    targets = light_markdown_targets(directory.parent)
    marker_targets = (
        (directory.parent / ".artha-decrypted", directory / ".handoff.json")
        if targets
        else ()
    )
    ignored: list[Path] = []
    try:
        from lib.config_loader import load_config
        profile = load_config("user_profile", str(Path(ARTHA_DIR) / "config"))
        sync = profile.get("sync", {}) if isinstance(profile, dict) else {}
        values = sync.get("conflict_ignore", ()) if isinstance(sync, dict) else ()
        if isinstance(values, list):
            ignored = [
                Path(ARTHA_DIR) / str(value)
                for value in values
                if isinstance(value, str)
            ]
    except (OSError, RuntimeError, TypeError, ValueError):
        ignored = []
    return [
        str(path)
        for path in discover_conflict_copies(
            directory,
            canonical_targets=(*targets, *marker_targets),
            ignored=ignored,
        )
    ]

def check_onedrive_conflicts() -> CheckResult:
    """P1 (DEBT-005): Detect OneDrive conflict copies in state/ directory.

    Two machines writing simultaneously produce conflict copies
    (e.g. ``finance-DESKTOP-ABC.md``).  These are NOT auto-merged; the
    user must diff and resolve manually.  See docs/sync-conflicts.md.
    """
    from lib.sync_conflicts import ConflictScanUnknown

    try:
        conflicts = _detect_conflict_files(STATE_DIR)
    except ConflictScanUnknown as exc:
        return CheckResult(
            "OneDrive conflicts", "P1", False,
            f"⚠ Conflict status unknown: {exc}",
            fix_hint="Restore access to state/ and re-run preflight before writing State Files.",
        )
    if not conflicts:
        return CheckResult(
            "OneDrive conflicts", "P1", True,
            "No OneDrive conflict copies detected in state/ ✓",
        )
    names = [os.path.basename(p) for p in conflicts]
    return CheckResult(
        "OneDrive conflicts", "P1", False,
        f"⚠ {len(conflicts)} OneDrive conflict file(s) in state/: {', '.join(names)}",
        fix_hint=(
            "Stop writes on both laptops, preserve both variants, and follow "
            "docs/sync-conflicts.md. Do not choose by filename timestamp alone."
        ),
    )


def check_light_handoff() -> CheckResult:
    """Report the local Light-mode handoff result; it never proves remote quiescence."""
    from handoff import verify
    from lib.light_mode import configured_cutover, effective_cutover

    if effective_cutover(
        configured=configured_cutover(Path(ARTHA_DIR)),
        environment=os.environ.get("ARTHA_CUTOVER"),
    ):
        return CheckResult(
            "Light-mode handoff",
            "P1",
            True,
            "not required while authoritative Engine mode is effective",
        )

    result = verify(Path(ARTHA_DIR))
    # An unverified handoff means read-only use until the owner confirms; it is not a NO-GO.
    return CheckResult(
        "Light-mode handoff",
        "P1",
        result.status == "verified",
        result.message if result.status == "verified" else f"read-only until verified: {result.message}",
        fix_hint="Wait for OneDrive to finish syncing; the other laptop seals automatically at the end of a turn. "
                 "If it stays unverified, run handoff.py seal --owner-confirmed there.",
    )


def check_effective_cutover() -> CheckResult:
    """Report file, environment and effective Engine mode separately."""
    from lib.light_mode import configured_cutover, describe_cutover

    description = describe_cutover(
        configured=configured_cutover(Path(ARTHA_DIR)),
        environment=os.environ.get("ARTHA_CUTOVER"),
    )
    return CheckResult("Effective Engine mode", "P1", True, str(description["detail"]))


def check_sensitive_file_registry() -> CheckResult:
    """Report divergence between vault, domain and hook sensitive-file lists."""
    import foundation
    import tool_guard
    from lib.config_loader import load_config

    vault_domains = set(foundation.get_sensitive_domains())
    try:
        registry = load_config(
            "domain_registry",
            str(Path(ARTHA_DIR) / "config"),
        )
        domains = registry.get("domains", {}) if isinstance(registry, dict) else {}
        domain_vaulted = {
            str(name)
            for name, settings in domains.items()
            if isinstance(settings, dict) and settings.get("requires_vault") is True
        }
    except (OSError, RuntimeError, TypeError, UnicodeDecodeError, ValueError):
        return CheckResult(
            "Sensitive State File registry",
            "P1",
            False,
            "domain registry could not be read",
            fix_hint="Repair config/domain_registry.yaml before enabling Light mode.",
        )
    hook_domains = set(tool_guard._domains())
    differences = []
    if missing := sorted(domain_vaulted - vault_domains):
        differences.append("missing from foundation: " + ", ".join(missing))
    if missing := sorted(vault_domains - domain_vaulted):
        differences.append("domain registry does not require vault: " + ", ".join(missing))
    if missing := sorted(vault_domains - hook_domains):
        differences.append("missing from tool guard: " + ", ".join(missing))
    return CheckResult(
        "Sensitive State File registry",
        "P1",
        not differences,
        "lists agree" if not differences else "; ".join(differences),
        fix_hint="Reconcile foundation.SENSITIVE_FILES, domain_registry.yaml and tool_guard.",
    )

def check_work_health_kusto_auth() -> CheckResult:
    """FR-42: Warmup Kusto.Cli against [kusto-cluster]. P1 non-blocking.

    Skips silently when:
      - work_health_dashboards.enabled: false in work_connector_policy.yaml
      - Kusto.Cli.exe is not on PATH (no Kusto tooling installed)
    """
    # Check policy gate
    policy_path = Path(ARTHA_DIR) / "config" / "work_connector_policy.yaml"
    if policy_path.exists():
        try:
            import yaml
            with policy_path.open(encoding="utf-8") as fh:
                policy = yaml.safe_load(fh) or {}
            work_health = policy.get("work_health_dashboards", {})
            if not work_health.get("enabled", False):
                return CheckResult(
                    "Work-Health Kusto Auth", "P1", True,
                    "Skipped (work_health_dashboards.enabled: false) ✓",
                )
        except Exception:
            pass  # if yaml unreadable, proceed to live check

    import shutil
    kusto_cli = shutil.which("Kusto.Cli") or shutil.which("kusto.cli")
    if not kusto_cli:
        return CheckResult(
            "Work-Health Kusto Auth", "P1", False,
            "Kusto.Cli not on PATH — Kusto queries unavailable",
            fix_hint="Install Kusto.Explorer or Kusto.Cli to enable work-health signals",
        )

    try:
        result = subprocess.run(
            [kusto_cli, "-server", "[kusto-cluster].kusto.windows.net",
             "-execute", "print 'ok'"],
            capture_output=True, text=True, timeout=15,
            env=_SUBPROCESS_ENV,
        )
        if result.returncode == 0 and "ok" in result.stdout:
            return CheckResult(
                "Work-Health Kusto Auth", "P1", True,
                "Kusto.Cli authenticated ✓",
            )
        return CheckResult(
            "Work-Health Kusto Auth", "P1", False,
            f"Kusto.Cli auth failed: {result.stderr.strip()[:80]}",
            fix_hint="Re-authenticate: Kusto.Cli -server [kusto-cluster].kusto.windows.net",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            "Work-Health Kusto Auth", "P1", False,
            "Kusto.Cli timed out (>15s) — check VPN / network",
        )
    except Exception as exc:
        return CheckResult(
            "Work-Health Kusto Auth", "P1", False,
            f"work-health Kusto warmup error: {exc}",
        )

def check_enghub_health() -> CheckResult:
    """§13.7: EngHub MCP subprocess health check (P1, non-blocking, Windows-only).

    States (priority order):
      ✗  Node.js / npx not found  → cannot launch subprocess
      ✗  .vscode/mcp.json missing or missing 'enghub' key → server not registered
      ⚠  Recent audit log shows init_failed / error → may be auth expiry
      ✓  Fresh enrichment cache (< 4h TTL) → enrichment working
      ○  No fresh cache, no known errors → VPN offline or first run (non-blocking)

    Severity: P1 — EngHub enrichment is always async/optional; never blocks catch-up.
    """
    import platform
    import shutil

    # EngHub runs only on Windows (Node.js MSAL waterfall; G9)
    if platform.system() != "Windows":
        return CheckResult(
            "EngHub MCP", "P1", True,
            "Skipped (not Windows) — EngHub enrichment unavailable on non-Windows ✓",
        )

    # ── Step 1: npx / Node.js availability ────────────────────────────────
    npx_path = shutil.which("npx")
    if not npx_path:
        return CheckResult(
            "EngHub MCP", "P1", False,
            "✗ EngHub: Node.js not found / npx unavailable",
            fix_hint="Install Node.js (https://nodejs.org) to enable EngHub enrichment",
        )

    # ── Step 2: .vscode/mcp.json has 'enghub' entry ───────────────────────
    mcp_json_path = Path(ARTHA_DIR) / ".vscode" / "mcp.json"
    if not mcp_json_path.exists():
        return CheckResult(
            "EngHub MCP", "P1", False,
            "✗ EngHub: .vscode/mcp.json missing — enghub server not registered",
            fix_hint="Re-run setup or restore .vscode/mcp.json with an 'enghub' stdio entry",
        )
    try:
        mcp_data = json.loads(mcp_json_path.read_text(encoding="utf-8"))
        if "enghub" not in mcp_data.get("servers", {}):
            return CheckResult(
                "EngHub MCP", "P1", False,
                "✗ EngHub: 'enghub' key missing from .vscode/mcp.json servers",
                fix_hint="Add enghub stdio entry to .vscode/mcp.json",
            )
    except (json.JSONDecodeError, OSError) as _exc:
        return CheckResult(
            "EngHub MCP", "P1", False,
            f"✗ EngHub: cannot read .vscode/mcp.json: {_exc}",
        )

    _tmp_dir = Path(ARTHA_DIR) / "tmp"

    # ── Step 3: Check recent audit files for init/auth failures ───────────
    try:
        audit_files = sorted(_tmp_dir.glob(".enghub_audit_*.json"))
        if audit_files:
            latest = audit_files[-1]
            audit_data = json.loads(latest.read_text(encoding="utf-8"))
            # Handle both old (list) and new (dict with events key) formats (Gap #6)
            if isinstance(audit_data, dict):
                audit_data = audit_data.get("events", [])
            events = [entry.get("event", "") for entry in audit_data if isinstance(entry, dict)]
            if "init_failed" in events or (events and events[-1] == "error"):
                return CheckResult(
                    "EngHub MCP", "P1", False,
                    "⚠ EngHub: auth expired or init failed — run npx @azure-core/enghub-mcp auth",
                    fix_hint="npx @azure-core/enghub-mcp auth",
                )
    except (json.JSONDecodeError, OSError):
        pass  # Non-critical — audit files are ephemeral

    # ── Step 4: Fresh enrichment cache → confirmed working ────────────────
    enrichment_cache = _tmp_dir / ".enghub_enrichment.json"
    if enrichment_cache.exists():
        try:
            cache = json.loads(enrichment_cache.read_text(encoding="utf-8"))
            retrieved_at = cache.get("retrieved_at", "")
            if retrieved_at:
                from datetime import datetime, timezone as _tz
                age_s = (
                    datetime.now(_tz.utc)
                    - datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
                ).total_seconds()
                if age_s < 4 * 3600:
                    return CheckResult(
                        "EngHub MCP", "P1", True,
                        f"✓ EngHub: subprocess spawnable, keychain token valid"
                        f" (cache {int(age_s // 60)}m old)",
                    )
        except (json.JSONDecodeError, ValueError, OSError):
            pass

    # ── Step 5: No fresh cache, no known errors — VPN offline / first run ─
    return CheckResult(
        "EngHub MCP", "P1", True,
        "○ EngHub: VPN offline or first run — enrichment disabled for this session",
    )

def check_hermes_context_staleness() -> CheckResult:
    """P1 non-blocking: Warn if sensor.artha_context is absent or older than 48h.

    Implements spec FR-HI §12 R-5 mitigation. Only runs on macOS where Artha
    has LAN access to HA. Silently passes when off-LAN or HA is not enabled.
    """
    _name = "Hermes Context (sensor.artha_context)"

    import platform as _platform
    if _platform.system() != "Darwin":
        return CheckResult(_name, "P1", True, "Not on macOS — skipped ✓")

    _connectors_path = os.path.join(ARTHA_DIR, "config", "connectors.yaml")
    if not os.path.exists(_connectors_path):
        return CheckResult(_name, "P1", True, "connectors.yaml not found — skipped ✓")

    try:
        from lib.config_loader import load_config
        _cfg = load_config(
            "connectors",
            _config_dir=os.path.dirname(_connectors_path),
            strict=True,
        )
        _ha = (_cfg.get("connectors") or {}).get("homeassistant") or {}
    except Exception as exc:
        return CheckResult(_name, "P1", True, f"Could not read connectors.yaml ({exc}) — skipped ✓")

    if not _ha.get("enabled", False):
        return CheckResult(_name, "P1", True, "HA connector not enabled — skipped ✓")

    _ha_url = ((_ha.get("fetch") or {}).get("ha_url") or "").rstrip("/")
    if not _ha_url:
        return CheckResult(_name, "P1", True, "ha_url not set — skipped ✓")

    try:
        import keyring as _keyring
        _token = _keyring.get_password("artha-ha-token", "artha") or ""
    except Exception:
        _token = ""

    if not _token:
        return CheckResult(_name, "P1", True, "artha-ha-token not in keyring — skipped ✓")

    try:
        import requests as _requests
        _r = _requests.get(
            f"{_ha_url}/api/states/sensor.artha_context",
            headers={"Authorization": f"Bearer {_token}"},
            timeout=3,
        )
    except Exception as exc:
        _msg = str(exc)
        if "connect" in _msg.lower() or "timeout" in _msg.lower():
            return CheckResult(_name, "P1", True, f"HA not reachable (off-LAN?) — skipped ✓")
        return CheckResult(_name, "P1", True, f"HA request error ({_msg[:80]}) — skipped ✓")

    if _r.status_code == 404:
        return CheckResult(
            _name, "P1", False,
            "sensor.artha_context does not exist in HA — context has never been written",
            fix_hint="Run pipeline on Mac or: python scripts/export_hermes_context.py --dry-run",
        )
    if _r.status_code != 200:
        return CheckResult(_name, "P1", True, f"HA returned HTTP {_r.status_code} — skipped ✓")

    try:
        import json as _json
        from datetime import datetime, timezone as _tz
        _data = _json.loads(_r.text)
        _generated = (_data.get("attributes") or {}).get("generated", "")
        if not _generated:
            return CheckResult(
                _name, "P1", False,
                "sensor.artha_context exists but has no 'generated' timestamp",
            )
        _ts = datetime.fromisoformat(_generated.replace("Z", "+00:00"))
        _age_h = (datetime.now(_tz.utc) - _ts).total_seconds() / 3600
        if _age_h > 48:
            return CheckResult(
                _name, "P1", False,
                f"HERMES_CONTEXT_EXPORT_FAIL: sensor.artha_context is {_age_h:.0f}h old "
                f"(generated: {_generated}). Hermes context is stale.",
                fix_hint="Run pipeline on Mac or: python scripts/export_hermes_context.py",
            )
        return CheckResult(_name, "P1", True, f"sensor.artha_context is fresh ({_age_h:.1f}h old) ✓")
    except Exception as exc:
        return CheckResult(_name, "P1", True, f"Could not parse context entity ({exc}) — skipped ✓")

def check_state_registry_staleness() -> CheckResult:
    """P1 non-blocking: Warn when state_registry entries exceed their max_staleness_hours.

    Reads config/state_registry.yaml and checks os.path.getmtime() for each entry
    that has a max_staleness_hours value.  Returns a single aggregate result.

    EAR-3 / PRD §3.5 (A2).
    """
    from datetime import datetime as _dt, timezone as _tz

    registry_path = Path(os.path.join(ARTHA_DIR, "config", "state_registry.yaml"))
    if not registry_path.exists():
        return CheckResult(
            "state registry staleness", "P1", True,
            "config/state_registry.yaml absent — staleness check skipped ✓",
        )

    try:
        import yaml as _yaml
        reg_data = _yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        entries = reg_data.get("state_files", []) or []
    except Exception as exc:
        return CheckResult(
            "state registry staleness", "P1", False,
            f"Could not read state_registry.yaml ({exc})",
        )

    if not entries:
        return CheckResult(
            "state registry staleness", "P1", True,
            "state_registry.yaml has no entries ✓",
        )

    now_ts = _dt.now(_tz.utc).timestamp()
    import platform
    stale: list[str] = []
    missing: list[str] = []

    for entry in entries:
        path_str = entry.get("path", "")
        max_hours = entry.get("max_staleness_hours")
        if not path_str or not max_hours:
            continue
        if path_str.startswith("state/work/") and platform.system() != "Windows":
            continue  # Work state belongs to the Windows Work host

        resolved = Path(path_str).expanduser()
        if not resolved.is_absolute():
            resolved = Path(ARTHA_DIR) / path_str

        candidates = sorted(set(glob.glob(str(resolved)) + glob.glob(str(resolved) + ".age")))
        if not candidates:
            missing.append(path_str)
            continue

        for candidate in candidates:
            try:
                age_hours = (now_ts - Path(candidate).stat().st_mtime) / 3600
                if age_hours > max_hours:
                    stale.append(f"{candidate} ({age_hours:.0f}h > {max_hours}h)")
            except OSError:
                missing.append(candidate)

    if not stale and not missing:
        return CheckResult(
            "state registry staleness", "P1", True,
            f"{len(entries)} tracked state file(s) within freshness bounds ✓",
        )

    parts: list[str] = []
    if stale:
        preview = stale[:3]
        extra = len(stale) - 3
        parts.append(
            f"{len(stale)} stale: {', '.join(preview)}"
            + (f" … +{extra} more" if extra > 0 else "")
        )
    if missing:
        preview = missing[:3]
        extra = len(missing) - 3
        parts.append(
            f"{len(missing)} missing: {', '.join(preview)}"
            + (f" … +{extra} more" if extra > 0 else "")
        )

    return CheckResult(
        "state registry staleness", "P1", False,
        f"⚠️ State freshness issues — {'; '.join(parts)}",
        fix_hint="Run scheduled agents: python scripts/precompute.py --all",
    )

def check_heartbeat_zero_records() -> CheckResult:
    """P1 non-blocking: Warn when a domain heartbeat reports records_written: 0.

    Checks tmp/{domain}_last_run.json files written by precompute.py.
    records_written: 0 means the scheduler wrote the stub but the agent script
    never updated it with actual data (cold-start or agent-side failure).

    EAR-3 / PRD §3.2.
    """
    import json as _json

    tmp_dir = Path(os.path.join(ARTHA_DIR, "tmp"))
    if not tmp_dir.exists():
        return CheckResult(
            "agent heartbeat", "P1", True,
            "No tmp/ directory — heartbeat check skipped ✓",
        )

    heartbeat_files = sorted(tmp_dir.glob("*_last_run.json"))
    if not heartbeat_files:
        return CheckResult(
            "agent heartbeat", "P1", True,
            "No agent heartbeat files found — pre-compute agents not yet run ✓",
        )

    zero_records: list[str] = []
    failure_states: list[str] = []

    for hb_path in heartbeat_files:
        try:
            data = _json.loads(hb_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        domain = data.get("domain", hb_path.stem.replace("_last_run", ""))
        status = data.get("status", "")
        records_written = data.get("records_written", -1)

        if status == "failure":
            failure_states.append(domain)
        elif records_written == 0:
            zero_records.append(domain)

    if not zero_records and not failure_states:
        return CheckResult(
            "agent heartbeat", "P1", True,
            f"{len(heartbeat_files)} agent heartbeat(s) healthy ✓",
        )

    parts: list[str] = []
    if failure_states:
        parts.append(f"failed: {', '.join(failure_states)}")
    if zero_records:
        parts.append(f"zero records written: {', '.join(zero_records)}")

    return CheckResult(
        "agent heartbeat", "P1", False,
        f"⚠️ Domain agent issues — {'; '.join(parts)}",
        fix_hint=(
            "Check agent logs or re-run: python scripts/precompute.py --all"
        ),
    )

def check_eval_alerts() -> CheckResult:
    """P0: Block catch-up if any unacked P0 eval alerts are present.

    Reads state/eval_alerts.yaml and fails hard if unacked P0 alerts exist.
    P1/P2 alerts are informational only (do not block).

    Ref: specs/eval.md EV-14
    """
    alerts_path = pathlib.Path(ARTHA_DIR) / "state" / "eval_alerts.yaml"
    if not alerts_path.exists():
        # Also check root-level path (used by tests and some deployments)
        root_alerts = pathlib.Path(ARTHA_DIR) / "eval_alerts.yaml"
        if root_alerts.exists():
            alerts_path = root_alerts
        else:
            return CheckResult("eval alerts", "P0", True, "No eval alerts file — eval layer clean")

    try:
        import yaml
        raw = yaml.safe_load(alerts_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CheckResult(
            "eval alerts", "P1", False,
            f"Could not read state/eval_alerts.yaml: {exc}",
            fix_hint="Delete or repair state/eval_alerts.yaml",
        )

    if (not isinstance(raw, dict) or "alerts" not in raw
            or not isinstance(raw["alerts"], list)
            or any(not isinstance(alert, dict) for alert in raw["alerts"])):
        return CheckResult(
            "eval alerts", "P1", False,
            "eval_alerts.yaml has an invalid alerts list",
            fix_hint="Repair state/eval_alerts.yaml to contain an alerts list of mappings",
        )
    alert_list = raw["alerts"]

    p0_active = [
        a for a in alert_list
        if isinstance(a, dict)
        and a.get("severity") == "P0"
        and not a.get("acked", False)
    ]
    if p0_active:
        names = ", ".join(a.get("code", "?") for a in p0_active[:3])
        return CheckResult(
            "eval alerts", "P0", False,
            f"{len(p0_active)} unacked P0 eval alert(s): {names}",
            fix_hint=(
                "Resolve or ack P0 alerts in state/eval_alerts.yaml "
                "(set acked: true) then re-run preflight"
            ),
        )

    # Count P1/P2 for informational suffix
    p1_count = sum(
        1 for a in alert_list
        if isinstance(a, dict) and a.get("severity") == "P1" and not a.get("acked", False)
    )
    suffix = f" | {p1_count} unacked P1 alert(s)" if p1_count else ""
    return CheckResult(
        "eval alerts", "P0", True,
        f"No blocking P0 eval alerts{suffix}",
    )

def check_eval_p1_alerts() -> "CheckResult | None":
    """P1 warning: surface unacked P1 eval alerts as a separate check row.

    Returns None if no unacked P1 alerts (caller skips appending).
    Unlike check_eval_alerts() this never blocks catch-up.

    Ref: specs/eval.md EV-14
    """
    alerts_path = pathlib.Path(ARTHA_DIR) / "state" / "eval_alerts.yaml"
    if not alerts_path.exists():
        return None
    try:
        import yaml
        raw = yaml.safe_load(alerts_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None

    alert_list = raw.get("alerts", [])
    if not isinstance(alert_list, list):
        return None

    p1_active = [
        a for a in alert_list
        if isinstance(a, dict)
        and a.get("severity") == "P1"
        and not a.get("acked", False)
    ]
    if not p1_active:
        return None

    names = ", ".join(
        a.get("code", a.get("type", "?")) for a in p1_active[:3]
    )
    return CheckResult(
        "eval alerts p1", "P1", False,
        f"{len(p1_active)} unacked P1 eval alert(s): {names}",
        fix_hint="Ack P1 alerts in state/eval_alerts.yaml (set acked: true) or resolve",
    )

def check_connector_registry() -> CheckResult:
    """P1 non-blocking: Verify all connectors in connectors.yaml appear in config/registry.md."""
    try:
        from lib.config_loader import load_config
        connectors_path = Path(ARTHA_DIR) / "config" / "connectors.yaml"
        registry_path = Path(ARTHA_DIR) / "config" / "registry.md"
        if not connectors_path.exists():
            return CheckResult(
                "connector_registry", "P1", True,
                "connectors.yaml not found — skipping",
            )
        if not registry_path.exists():
            return CheckResult(
                "connector_registry", "P1", False,
                "config/registry.md not found",
                fix_hint="Create config/registry.md with Work OS Connectors section",
            )
        conn_data = load_config(
            "connectors",
            _config_dir=str(connectors_path.parent),
            strict=True,
        )
        connector_names = set(conn_data.keys()) if isinstance(conn_data, dict) else set()
        registry_text = registry_path.read_text(encoding="utf-8")
        missing = [name for name in connector_names if name not in registry_text]
        if missing:
            return CheckResult(
                "connector_registry", "P1", False,
                f"Connectors not in registry.md: {', '.join(sorted(missing))}",
                fix_hint="Add connector entries to config/registry.md Work OS Connectors section",
            )
        return CheckResult(
            "connector_registry", "P1", True,
            f"All {len(connector_names)} connectors found in registry.md",
        )
    except Exception as exc:
        return CheckResult(
            "connector_registry", "P1", True,
            f"connector_registry check skipped: {exc}",
        )

def check_mcp_discovery() -> CheckResult:
    """P1 non-blocking: Generate passive MCP inventory for health/status."""
    try:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from mcp_discovery import discover_mcp, write_discovery

        discovery = discover_mcp(Path(ARTHA_DIR))
        if not _READ_ONLY:
            write_discovery(discovery, Path(ARTHA_DIR) / "tmp" / "mcp_discovery.json")
        counts = discovery.get("counts", {})
        msg = (
            f"{counts.get('servers', 0)} MCP server(s) in "
            f"{counts.get('config_files', 0)} config file(s)"
        )
        if counts.get("errors", 0):
            return CheckResult(
                "MCP discovery",
                "P1",
                False,
                f"{msg}; {counts.get('errors')} config read/parse warning(s)",
                fix_hint="Run: python scripts/mcp_discovery.py --json",
            )
        return CheckResult("MCP discovery", "P1", True, f"{msg} ✓")
    except Exception as exc:
        return CheckResult(
            "MCP discovery",
            "P1",
            False,
            f"MCP inventory failed: {exc}",
            fix_hint="Run: python scripts/mcp_discovery.py --json",
        )

def check_skill_index() -> CheckResult:
    """P1 non-blocking: Generate static skill index without importing skills."""
    try:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from skill_index import build_skill_index, write_skill_index

        index = build_skill_index(Path(ARTHA_DIR))
        if not _READ_ONLY:
            write_skill_index(index, Path(ARTHA_DIR) / "tmp" / "skill_index.json")
        counts = index.get("counts", {})
        msg = (
            f"{counts.get('enabled', 0)}/{counts.get('configured', 0)} "
            "configured skills enabled"
        )
        import platform

        current_platform = platform.system().lower()
        entries = {entry["name"]: entry for entry in index.get("skills", [])}
        missing = []
        for name in index.get("enabled_missing_modules", []):
            run_on = entries.get(name, {}).get("run_on") or "all"
            allowed = [run_on] if isinstance(run_on, str) else run_on
            if run_on == "all" or current_platform in allowed:
                missing.append(name)
        if missing:
            names = ", ".join(str(name) for name in missing[:5])
            suffix = "..." if len(missing) > 5 else ""
            return CheckResult(
                "skill index",
                "P1",
                False,
                f"{msg}; enabled skill module(s) missing: {names}{suffix}",
                fix_hint="Run: python scripts/skill_index.py --json",
            )
        return CheckResult("skill index", "P1", True, f"{msg} ✓")
    except Exception as exc:
        return CheckResult(
            "skill index",
            "P1",
            False,
            f"skill index generation failed: {exc}",
            fix_hint="Run: python scripts/skill_index.py --json",
        )

def check_telemetry_integrity() -> CheckResult:
    """ST-01 — Verify telemetry.jsonl hash chain integrity (P1, non-blocking)."""
    try:
        _lib_dir = os.path.join(SCRIPTS_DIR, "lib")
        import sys as _sys
        if _lib_dir not in _sys.path:
            _sys.path.insert(0, _lib_dir)
        import telemetry as _tel
        valid = _tel.verify_integrity()
        if valid:
            return CheckResult("telemetry integrity (ST-01)", "P1", True, "Hash chain intact ✓")
        try:
            historic, recent = len(_tel.chain_breaks()), _tel.unreviewed_breaks()
        except ValueError:
            historic, recent = 0, ["corrupt"]
        if historic and not recent:
            return CheckResult(
                "telemetry integrity (ST-01)", "P1", True,
                f"{historic} historic fork(s) from before the append lock (all through "
                f"{_tel._REVIEWED_FORKS_THROUGH}, reviewed); clean since ✓")
        return CheckResult(
            "telemetry integrity (ST-01)", "P1", False,
            "Hash chain violation detected — telemetry.jsonl may be tampered or corrupt",
            fix_hint="Investigate state/telemetry.jsonl for tampering or corruption",
        )
    except Exception as exc:
        return CheckResult(
            "telemetry integrity (ST-01)", "P1", False,
            f"Could not verify telemetry integrity: {exc}",
        )

def check_briefing_quality_index() -> CheckResult:
    """ST-02 — Surface rolling 30-day Briefing Quality Index (P2, informational)."""
    try:
        import yaml as _yaml
        from datetime import datetime, timezone, timedelta
        state_file = Path(ARTHA_DIR) / "state" / "catch_up_runs.yaml"
        if not state_file.exists():
            return CheckResult(
                "briefing quality index (ST-02)", "P2", True, "BQI: state file not found — skipped"
            )
        raw = _yaml.safe_load(state_file.read_text(encoding="utf-8")) or []
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        rates = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            ts_str = entry.get("timestamp")
            cr = entry.get("correction_rate")
            if cr is None or ts_str is None:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts >= cutoff:
                    rates.append(float(cr))
            except (ValueError, TypeError):
                continue
        if not rates:
            return CheckResult(
                "briefing quality index (ST-02)", "P2", True,
                "BQI: no rated runs logged yet (informational — normal at start)",
            )
        avg_rate = sum(rates) / len(rates)
        threshold = 0.15  # ≤15% correction rate target (specs/steal.md §15.5)
        ok = avg_rate <= threshold
        label = "✓" if ok else "⚠ above 15% target"
        return CheckResult(
            "briefing quality index (ST-02)", "P2", ok,
            f"BQI (30d, {len(rates)} runs): correction_rate={avg_rate:.1%} {label}",
        )
    except Exception as exc:
        return CheckResult(
            "briefing quality index (ST-02)", "P2", True,
            f"BQI check skipped: {exc}",
        )

def check_cleanup_partials() -> CheckResult:
    """S-08 — Sweep aged partial_*.json files from tmp/ (P2, informational)."""
    if _READ_ONLY:
        return CheckResult(
            "partial cleanup (S-08)", "P2", True,
            "Partial cleanup suppressed in --read-only mode",
        )
    try:
        _lib_dir = os.path.join(SCRIPTS_DIR, "lib")
        import sys as _sys
        if _lib_dir not in _sys.path:
            _sys.path.insert(0, _lib_dir)
        from partial_writer import cleanup_partials as _cleanup
        artha_dir = Path(ARTHA_DIR)
        deleted = _cleanup(artha_dir, max_age_hours=24)
        if deleted:
            return CheckResult(
                "partial cleanup (S-08)", "P2", True,
                f"Cleaned up {deleted} aged partial file(s) from tmp/ ✓",
            )
        return CheckResult(
            "partial cleanup (S-08)", "P2", True,
            "Partial cleanup: no aged files found ✓",
        )
    except Exception as exc:
        return CheckResult(
            "partial cleanup (S-08)", "P2", True,
            f"Partial cleanup skipped: {exc}",
        )

def check_evidence_lake_ttl() -> CheckResult:
    """S-04 — Enforce TTL on evidence-lake.md entries (P2, informational)."""
    if _READ_ONLY:
        return CheckResult(
            "evidence lake TTL (S-04)", "P2", True,
            "Evidence TTL writes suppressed in --read-only mode",
        )
    try:
        _lib_dir = os.path.join(SCRIPTS_DIR, "lib")
        import sys as _sys
        if _lib_dir not in _sys.path:
            _sys.path.insert(0, _lib_dir)
        from evidence_lake import enforce_ttl as _enforce
        lake_path = Path(ARTHA_DIR) / "state" / "work" / "evidence-lake.md"
        archive_dir = Path(ARTHA_DIR) / "state" / "work" / "evidence-archive"
        if not lake_path.exists():
            return CheckResult(
                "evidence lake TTL (S-04)", "P2", True,
                "Evidence lake not found — skipped (will be created on first reflect)",
            )
        archived = _enforce(lake_path, archive_dir)
        if archived:
            return CheckResult(
                "evidence lake TTL (S-04)", "P2", True,
                f"Evidence lake TTL: archived {archived} expired entries ✓",
            )
        return CheckResult(
            "evidence lake TTL (S-04)", "P2", True,
            "Evidence lake TTL: no expired entries ✓",
        )
    except Exception as exc:
        return CheckResult(
            "evidence lake TTL (S-04)", "P2", True,
            f"Evidence lake TTL check skipped: {exc}",
        )

def check_slo_burn_rate() -> CheckResult:
    """ST-04 — Verify no SLO error budget is burning faster than 1.0x (P2, informational)."""
    try:
        _lib_dir = os.path.join(SCRIPTS_DIR, "lib")
        import sys as _sys
        if _lib_dir not in _sys.path:
            _sys.path.insert(0, _lib_dir)
        from slo_engine import get_engine
        engine = get_engine(artha_dir=ARTHA_DIR)
        report = engine.status()
        burning = [s for s in report.slos.values() if s.burn_rate is not None and s.burn_rate > 1.0]
        if burning:
            names = ", ".join(s.slo_id for s in burning)
            return CheckResult(
                "SLO burn rate (ST-04)", "P2", False,
                f"SLO error budget burning fast (>1.0x): {names}",
            )
        unsampled = [s.slo_id for s in report.slos.values() if s.sample_count == 0]
        if not report.slos or unsampled:
            return CheckResult(
                "SLO burn rate (ST-04)", "P2", False,
                "SLO samples unavailable: " + (", ".join(unsampled) or "no registered SLOs"),
            )
        return CheckResult(
            "SLO burn rate (ST-04)", "P2", True,
            "SLO error budgets within limits ✓",
        )
    except Exception as exc:
        return CheckResult(
            "SLO burn rate (ST-04)", "P2", False,
            f"SLO burn rate check failed: {exc}",
        )

def check_guardrails_rings() -> CheckResult:
    """ST-07 — Verify guardrails.yaml contains all 4 governance rings (P2, informational)."""
    try:
        from lib.config_loader import load_config
        guardrails_path = Path(ARTHA_DIR) / "config" / "guardrails.yaml"
        if not guardrails_path.exists():
            return CheckResult(
                "guardrails rings (ST-07)", "P2", True,
                "guardrails.yaml not found — skipped",
            )
        data = load_config(
            "guardrails",
            _config_dir=str(guardrails_path.parent),
            strict=True,
        )
        rings = (data or {}).get("rings", {})
        required = {"ring_0", "ring_1", "ring_2", "ring_3"}
        missing = required - set(rings.keys())
        if missing:
            return CheckResult(
                "guardrails rings (ST-07)", "P2", True,
                f"Governance rings missing from guardrails.yaml: {sorted(missing)}",
            )
        return CheckResult(
            "guardrails rings (ST-07)", "P2", True,
            "Governance rings (ring_0–ring_3) present in guardrails.yaml ✓",
        )
    except Exception as exc:
        return CheckResult(
            "guardrails rings (ST-07)", "P2", True,
            f"Guardrails rings check skipped: {exc}",
        )

def _clear_stale_pipeline_checkpoint() -> None:
    """Delete the machine-local pipeline checkpoint if it is older than 4 hours.

    pipeline.py writes ~/.artha-local/pipeline-checkpoint.json after each fetch phase and
    prints a resume prompt on every subsequent run regardless of age.
    Clearing it here ensures every preflight-gated run starts fresh unless
    the user explicitly passed --resume to pipeline.py.
    """
    if _READ_ONLY:
        return

    ckpt = local_dir() / "pipeline-checkpoint.json"
    if not ckpt.exists():
        return
    try:
        data = json.loads(ckpt.read_text(encoding="utf-8"))
        ts_str = data.get("created_at", "")
        if ts_str:
            # Normalise malformed "+00:00Z" suffix produced by some pipeline versions
            ts_str = ts_str.rstrip("Z") if ts_str.endswith("+00:00Z") else ts_str
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            if age_hours > 4:
                ckpt.unlink()
    except Exception:
        pass

def _compact_health_check() -> None:
    """Trim health-check.md to prevent unbounded growth.

    Keeps:
    - YAML frontmatter (unchanged)
    - Last 20 unique catch-up run entries across all Catch-Up Run History sections
    - ## Channel Health (Structured) section (unchanged)
    - ## Autonomy State section (unchanged)
    - Last 10 unique ## Connector health — <timestamp> entries
    Only writes back if the result is smaller than the original.
    """
    if _READ_ONLY:
        return

    hc_path = pathlib.Path(ARTHA_DIR) / "state" / "health-check.md"
    if not hc_path.exists():
        return
    try:
        from lib.state_writer import read_versioned
        text, expected_token = read_versioned(hc_path)
        # The Windows machine writes CRLF; the frontmatter and section regexes below match LF only.
        text = text.replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        print(f"[preflight] health-check compaction skipped: {type(exc).__name__}", file=sys.stderr)
        return

    # 1. Extract frontmatter
    fm_match = re.match(r"^(---\n.*?\n---\n)", text, re.DOTALL)
    frontmatter = fm_match.group(1) if fm_match else ""
    body = text[len(frontmatter):]
    # Older files and templates lack fields the write guard requires.
    if frontmatter:
        from datetime import datetime, timezone
        added = ""
        if not re.search(r"^domain:", frontmatter, re.MULTILINE):
            added += "domain: health-check\n"
        if not re.search(r"^last_updated:", frontmatter, re.MULTILINE):
            added += f"last_updated: '{datetime.now(timezone.utc).isoformat(timespec='seconds')}'\n"
        frontmatter = frontmatter[:-4] + added + "---\n"

    # 2. Collect all ### run entries; deduplicate by heading; keep last 20
    run_entries: list[str] = []
    seen_run: set[str] = set()
    for m in re.finditer(r"(### [^\n]+\n(?:(?!###|## )[^\n]*\n)*)", body):
        heading = m.group(0).split("\n")[0]
        if heading not in seen_run:
            seen_run.add(heading)
            run_entries.append(m.group(0))
    run_entries = run_entries[-20:]

    # 3. Collect ## Connector health — <ts> entries; deduplicate; keep last 10
    conn_entries: list[str] = []
    seen_conn: set[str] = set()
    for m in re.finditer(r"(## Connector health — [^\n]+\n(?:(?!## )[^\n]*\n)*)", body):
        heading = m.group(0).split("\n")[0]
        if heading not in seen_conn:
            seen_conn.add(heading)
            conn_entries.append(m.group(0))
    conn_entries = conn_entries[-10:]

    # 4. Extract persistent sections verbatim
    channel_health = ""
    ch_m = re.search(r"(## Channel Health \(Structured\)\n(?:(?!## )[^\n]*\n)*)", body)
    if ch_m:
        channel_health = ch_m.group(1)

    autonomy = ""
    au_m = re.search(r"(## Autonomy State\n(?:(?!## )[^\n]*\n)*)", body)
    if au_m:
        autonomy = au_m.group(1)

    # 5. Reassemble
    sections: list[str] = [frontmatter]
    if run_entries:
        sections.append("## Catch-Up Run History\n\n" + "".join(run_entries))
    if channel_health:
        sections.append(channel_health)
    if autonomy:
        sections.append(autonomy)
    if conn_entries:
        sections.append(
            "## Connector Health\n\n<!-- connector health: last 10 entries -->\n\n"
            + "".join(conn_entries)
        )

    new_text = "\n".join(s.rstrip("\n") for s in sections if s) + "\n"

    # Only write if we actually reduced the file (safety gate)
    if len(new_text) < len(text):
        try:
            from lib.state_writer import write as _state_write
            _state_write(hc_path, new_text, domain="health-check",
                         source="preflight.prune_health_check", pii_check=False,
                         expected_token=expected_token)
        except Exception:
            pass

def _write_device_context() -> None:
    """Write tmp/device_context.json with suppressed domains for this host.

    Reads device_suppress rules from config/guardrails.yaml and writes a
    manifest that the briefing compose step must check before surfacing any
    domain content.  Non-fatal: failures are logged to stderr and skipped.
    """
    if _READ_ONLY:
        return
    import platform

    try:
        cfg_path = pathlib.Path(ARTHA_DIR) / "config" / "guardrails.yaml"
        if not cfg_path.exists():
            return

        from lib.config_loader import load_config
        cfg = load_config(
            "guardrails",
            _config_dir=str(cfg_path.parent),
            strict=True,
        )

        hostname = platform.node()
        suppressed: list[str] = []
        reason = ""
        for rule in cfg.get("device_suppress", []):
            if rule.get("hostname", "").lower() == hostname.lower():
                suppressed.extend(rule.get("suppress_domains", []))
                reason = rule.get("reason", "")

        tmp_dir = pathlib.Path(ARTHA_DIR) / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        manifest = {
            "hostname": hostname,
            "suppressed_domains": sorted(set(suppressed)),
            "reason": reason,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        out = tmp_dir / "device_context.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
    except Exception as exc:
        print(f"[preflight] WARNING: could not write device_context.json: {exc}", file=sys.stderr)
