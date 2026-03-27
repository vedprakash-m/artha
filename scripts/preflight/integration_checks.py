"""preflight/integration_checks.py — Third-party integration health (bridge, WorkIQ, ADO, HA, channel)."""
from __future__ import annotations

import os
import sys
import subprocess
import time
from pathlib import Path

from preflight._types import (
    ARTHA_DIR, SCRIPTS_DIR, STATE_DIR, WORKIQ_CACHE_FILE, _SUBPROCESS_ENV, _rel, CheckResult, _REQUIRED_DEPS,
)

def check_bridge_health() -> CheckResult:
    """P1 non-blocking bridge health check.

    Skipped silently if multi_machine.bridge_enabled is false.
    When enabled:
      - Verifies bridge directory exists and is writable
      - Checks peer machine health file for staleness (default 48 h)

    Ref: specs/dual-setup.md §5
    """
    import sys as _sys
    if SCRIPTS_DIR not in _sys.path:
        _sys.path.insert(0, SCRIPTS_DIR)

    try:
        import yaml as _yaml  # noqa: PLC0415
        config_path = Path(ARTHA_DIR) / "config" / "artha_config.yaml"
        if not config_path.exists():
            return CheckResult("Bridge", "P1", True, "Bridge: config absent — skipped")
        with open(config_path, encoding="utf-8") as _f:
            artha_config = _yaml.safe_load(_f) or {}

        mm = artha_config.get("multi_machine", {})
        if not mm.get("bridge_enabled", False):
            return CheckResult("Bridge", "P1", True, "Bridge: disabled (multi_machine.bridge_enabled=false)")

        from action_bridge import (  # noqa: PLC0415
            get_bridge_dir, detect_role, check_health_staleness, load_artha_config,
        )
        channels_path = Path(ARTHA_DIR) / "config" / "channels.yaml"
        channels_config: dict = {}
        if channels_path.exists():
            with open(channels_path, encoding="utf-8") as _f:
                channels_config = _yaml.safe_load(_f) or {}

        role = detect_role(channels_config)
        peer_role = "windows" if role == "proposer" else "mac"
        bridge_dir = get_bridge_dir(Path(ARTHA_DIR))

        # Check bridge directory accessible + writable
        if not bridge_dir.exists():
            try:
                bridge_dir.mkdir(parents=True, exist_ok=True)
                (bridge_dir / "proposals").mkdir(exist_ok=True)
                (bridge_dir / "results").mkdir(exist_ok=True)
            except OSError as exc:
                return CheckResult(
                    "Bridge", "P1", False,
                    f"Bridge dir not writable: {exc}",
                    fix_hint="Ensure OneDrive is syncing state/.action_bridge/",
                )

        stale_hours = int(mm.get("health_stale_hours", 48))
        is_stale, elapsed_h = check_health_staleness(bridge_dir, peer_role, stale_hours)

        if is_stale and elapsed_h == float("inf"):
            return CheckResult(
                "Bridge", "P1", False,
                f"Bridge: peer machine ({peer_role}) has never written a health file",
                fix_hint=f"Start Artha on the {peer_role} machine to initialise bridge",
            )
        if is_stale:
            return CheckResult(
                "Bridge", "P1", False,
                f"Bridge: peer machine ({peer_role}) last seen {elapsed_h:.0f}h ago (threshold: {stale_hours}h)",
                fix_hint=f"Ensure Artha is running on the {peer_role} machine",
            )

        return CheckResult(
            "Bridge", "P1", True,
            f"Bridge: OK — peer ({peer_role}) seen {elapsed_h:.1f}h ago",
        )

    except ImportError as exc:
        return CheckResult("Bridge", "P1", False, f"Bridge module unavailable: {exc}",
                           fix_hint="Ensure scripts/action_bridge.py is present")
    except Exception as exc:
        return CheckResult("Bridge", "P1", False, f"Bridge health check error: {exc}")


def check_workiq() -> CheckResult:
    """Combined WorkIQ detection + auth. P1 non-blocking.

    Strategy:
      1. Platform gate: if not Windows, skip silently (Mac has no WorkIQ).
      2. Check tmp/.workiq_cache.json — if fresh (<24h), reuse cached result.
      3. If cache miss/stale: single npx call that validates both availability
         and M365 auth: "What is my name?"
      4. Write result to cache for next run.

    Ref: Tech Spec §3.2b, §7.1 Step 0(f)
    """
    import platform
    if platform.system() != "Windows":
        return CheckResult(
            "WorkIQ Calendar", "P1", True,
            "Skipped (not Windows) — Mac graceful degradation ✓",
        )

    # Check cache
    cache_path = Path(WORKIQ_CACHE_FILE)
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            checked_at = cache.get("checked_at", "")
            if checked_at:
                from datetime import datetime, timezone
                cache_time = datetime.fromisoformat(checked_at)
                age = (datetime.now(timezone.utc) - cache_time).total_seconds()
                if age < WORKIQ_CACHE_MAX_AGE:
                    if cache.get("available") and cache.get("auth_valid"):
                        return CheckResult(
                            "WorkIQ Calendar", "P1", True,
                            f"Available + authenticated (cached {int(age//3600)}h ago) ✓",
                        )
                    elif cache.get("available") and not cache.get("auth_valid"):
                        return CheckResult(
                            "WorkIQ Calendar", "P1", False,
                            "WorkIQ available but auth expired (cached)",
                            fix_hint="npx workiq logout && retry",
                        )
                    else:
                        return CheckResult(
                            "WorkIQ Calendar", "P1", False,
                            "WorkIQ not available (cached)",
                            fix_hint="Install: npm i -g @microsoft/workiq",
                        )
        except (json.JSONDecodeError, KeyError, ValueError):
            pass  # stale/corrupt cache — fall through to live check

    # Live combined detection + auth check
    # Refresh PATH from registry to pick up newly-installed Node.js (Windows)
    import platform as _plat
    if _plat.system() == "Windows":
        fresh_path = os.environ.get("PATH", "")
        for scope in ("Machine", "User"):
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE if scope == "Machine" else winreg.HKEY_CURRENT_USER,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
                    if scope == "Machine" else r"Environment",
                )
                val, _ = winreg.QueryValueEx(key, "Path")
                winreg.CloseKey(key)
                for p in val.split(";"):
                    if p and p not in fresh_path:
                        fresh_path += ";" + p
            except (OSError, FileNotFoundError):
                pass
        sub_env = {**_SUBPROCESS_ENV, "PATH": fresh_path}
    else:
        sub_env = _SUBPROCESS_ENV

    try:
        # Find npx using refreshed PATH (handles post-install Windows PATH lag)
        import shutil
        npx_cmd = shutil.which("npx", path=sub_env.get("PATH"))
        if not npx_cmd:
            raise FileNotFoundError("npx not on PATH")
        result = subprocess.run(
            [npx_cmd, "-y", f"@microsoft/workiq@{WORKIQ_VERSION_PIN}",
             "ask", "-q", "What is my name?"],
            capture_output=True, text=True, timeout=30,
            env=sub_env,
        )
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        available = result.returncode == 0 and len(result.stdout.strip()) > 0
        # Auth is valid if we got a meaningful response (not an error message)
        auth_valid = available and "error" not in result.stdout.lower()[:100]
        user_name = result.stdout.strip()[:50] if auth_valid else ""

        # Write cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_data = {
            "available": available,
            "auth_valid": auth_valid,
            "platform": "Windows",
            "checked_at": now_iso,
            "user_name": user_name,
        }
        cache_path.write_text(
            json.dumps(cache_data, indent=2), encoding="utf-8"
        )

        if available and auth_valid:
            return CheckResult(
                "WorkIQ Calendar", "P1", True,
                f"Available + authenticated ✓",
            )
        elif available and not auth_valid:
            return CheckResult(
                "WorkIQ Calendar", "P1", False,
                "WorkIQ available but M365 auth failed",
                fix_hint="npx workiq logout && retry",
            )
        else:
            return CheckResult(
                "WorkIQ Calendar", "P1", False,
                f"WorkIQ not available: {result.stderr.strip()[:80]}",
                fix_hint="Install: npm i -g @microsoft/workiq",
            )
    except FileNotFoundError:
        return CheckResult(
            "WorkIQ Calendar", "P1", False,
            "npx not found — Node.js not installed",
            fix_hint="Install Node.js (includes npx)",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            "WorkIQ Calendar", "P1", False,
            "WorkIQ check timed out (>30s)",
            fix_hint="Check network connectivity; WorkIQ requires npm registry access",
        )


def check_ado_auth() -> CheckResult:
    """P1 non-blocking: Verify Azure CLI is available and has an active ADO session.

    Only runs if azure_devops.enabled is true in user_profile.yaml.
    Silently passes if the ADO integration is not configured.
    """
    # Check if azure_devops integration is configured and enabled
    ado_enabled = False
    ado_org = ""
    try:
        _sys = sys
        if SCRIPTS_DIR not in _sys.path:
            _sys.path.insert(0, SCRIPTS_DIR)
        import yaml as _yaml
        _profile_path = os.path.join(ARTHA_DIR, "config", "user_profile.yaml")
        if os.path.exists(_profile_path):
            with open(_profile_path, encoding="utf-8") as _f:
                _profile = _yaml.safe_load(_f) or {}
            _ado = (_profile.get("integrations") or {}).get("azure_devops") or {}
            ado_enabled = bool(_ado.get("enabled", False))
            ado_org = str(_ado.get("organization_url", "")).strip()
    except Exception:
        pass

    if not ado_enabled:
        return CheckResult(
            "Azure DevOps auth", "P1", True,
            "ADO integration not enabled — skipped ✓",
        )

    if not ado_org:
        return CheckResult(
            "Azure DevOps auth", "P1", False,
            "ADO enabled but organization_url not set",
            fix_hint="Add integrations.azure_devops.organization_url to user_profile.yaml",
        )

    # Try to get an ADO bearer token from Azure CLI
    ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
    az_candidates = [
        "az",
        r"C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
        r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
    ]
    for az_cmd in az_candidates:
        try:
            result = subprocess.run(
                [az_cmd, "account", "get-access-token",
                 "--resource", ADO_RESOURCE, "--output", "json"],
                capture_output=True, text=True, timeout=15,
                env=_SUBPROCESS_ENV,
            )
            if result.returncode == 0:
                import json as _json
                data = _json.loads(result.stdout)
                expires = data.get("expiresOn", "")[:16]
                return CheckResult(
                    "Azure DevOps auth", "P1", True,
                    f"Azure CLI ADO token valid (expires {expires}) ✓",
                )
            # Non-zero return: auth failure
            return CheckResult(
                "Azure DevOps auth", "P1", False,
                "Azure CLI is available but not authenticated",
                fix_hint="Run: az login --tenant <your-tenant-id>",
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return CheckResult(
                "Azure DevOps auth", "P1", False,
                "Azure CLI token request timed out",
                fix_hint="Check network connectivity and Azure CLI installation",
            )

    return CheckResult(
        "Azure DevOps auth", "P1", False,
        "Azure CLI not found — ADO connector requires az CLI",
        fix_hint=(
            "Install Azure CLI: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli"
        ),
    )


def check_ha_connectivity() -> CheckResult:
    """P1 non-blocking: Verify HA is reachable and the token is valid.

    Only runs if homeassistant.enabled is true in connectors.yaml.
    Silently passes (skip message) if the connector is disabled.
    Returns a warning (not failure) when off-LAN — normal for travel/work.
    """
    _name = "Home Assistant"

    # Load connector config
    _connectors_path = os.path.join(ARTHA_DIR, "config", "connectors.yaml")
    if not os.path.exists(_connectors_path):
        return CheckResult(_name, "P1", True, "connectors.yaml not found — skipped ✓")

    try:
        import yaml as _yaml
        with open(_connectors_path, encoding="utf-8") as _f:
            _cfg = _yaml.safe_load(_f) or {}
        _ha = (_cfg.get("connectors") or {}).get("homeassistant") or {}
    except Exception as exc:
        return CheckResult(_name, "P1", True, f"Could not read connectors.yaml ({exc}) — skipped ✓")

    if not _ha.get("enabled", False):
        return CheckResult(_name, "P1", True, "HA connector not enabled — skipped ✓")

    _ha_url = ((_ha.get("fetch") or {}).get("ha_url") or "").rstrip("/")
    if not _ha_url:
        return CheckResult(
            _name, "P1", False,
            "HA connector enabled but ha_url is empty",
            fix_hint="Run: python scripts/setup_ha_token.py",
        )

    # Load token from keyring
    try:
        import keyring as _keyring
        _token = _keyring.get_password("artha-ha-token", "artha") or ""
    except Exception:
        _token = ""

    if not _token:
        return CheckResult(
            _name, "P1", False,
            "HA token not found in system keyring",
            fix_hint="Run: python scripts/setup_ha_token.py",
        )

    # Attempt health check via the connector's own health_check()
    try:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from connectors.homeassistant import health_check as _ha_health  # type: ignore[import]
        _fetch_cfg = _ha.get("fetch") or {}
        _ok = _ha_health(
            {"provider": "homeassistant", "method": "api_key", "api_key": _token},
            **{k: v for k, v in _fetch_cfg.items() if k != "handler"},
        )
        if _ok:
            return CheckResult(_name, "P1", True, f"HA reachable at {_ha_url} ✓")
        return CheckResult(
            _name, "P1", False,
            f"HA health check returned False for {_ha_url}",
            fix_hint=(
                "Check HA is running and on home network. "
                "Re-run token setup if needed: python scripts/setup_ha_token.py"
            ),
        )
    except Exception as exc:
        _msg = str(exc)
        if "LAN gate" in _msg or "not reachable" in _msg or "Cannot connect" in _msg:
            return CheckResult(
                _name, "P1", True,
                f"HA not on current network (off-LAN) — skipped ✓ ({_ha_url})",
            )
        return CheckResult(
            _name, "P1", False,
            f"HA check error: {_msg[:120]}",
            fix_hint="Run: python scripts/pipeline.py --health --source homeassistant",
        )


def check_dep_freshness() -> CheckResult:
    """P1: Verify key project dependencies are importable in the current venv.

    A stale venv (missing packages after a git pull that added new deps) is one
    of the most common causes of multi-script cascade failures at preflight.
    This check surfaces missing imports early with a clear fix hint.
    """
    import importlib.util

    req_file = os.path.join(ARTHA_DIR, "scripts", "requirements.txt")
    missing: list[str] = []
    install_names: list[str] = []

    for mod, pkg in _REQUIRED_DEPS.items():
        if importlib.util.find_spec(mod) is None:
            missing.append(mod)
            install_names.append(pkg)

    if missing:
        return CheckResult(
            "venv dependencies", "P1", False,
            f"Missing packages: {missing} — venv may be stale after a git pull",
            fix_hint=(
                f"Run: pip install -r {req_file}"
                if os.path.exists(req_file)
                else f"pip install {' '.join(install_names)}"
            ),
        )

    # Also check that requirements.txt exists (sanity guard)
    if not os.path.exists(req_file):
        return CheckResult(
            "venv dependencies", "P1", False,
            f"requirements.txt missing: {req_file}",
            fix_hint="Restore from source control",
        )

    return CheckResult("venv dependencies", "P1", True, f"All {len(_REQUIRED_DEPS)} core deps found ✓")


def check_channel_config() -> CheckResult:
    """P1: Validate channels.yaml has no incomplete placeholder values.

    Catches the three misconfigs that silently blocked Telegram responses:
      1. recipients.primary.id is empty or missing (CHANNEL_REJECT unknown_sender)
      2. push_enabled is False (outbound push blocked)
      3. listener_host is empty or a placeholder value (listener skips this host)

    Pure YAML parsing — no network calls, no adapter imports. Non-blocking.
    Only applicable when channels.yaml exists and at least one channel is enabled.
    """
    config_path = Path(os.path.join(ARTHA_DIR, "config", "channels.yaml"))
    if not config_path.exists():
        return CheckResult(
            "channel config", "P1", True,
            "channels.yaml not found — channel push disabled ✓",
        )

    try:
        import yaml as _yaml
        with open(config_path, encoding="utf-8") as _f:
            cfg = _yaml.safe_load(_f) or {}
    except Exception as exc:
        return CheckResult(
            "channel config", "P1", False,
            f"channels.yaml parse error: {exc}",
            fix_hint="Validate YAML syntax in config/channels.yaml",
        )

    enabled_channels = {
        k: v for k, v in cfg.get("channels", {}).items()
        if isinstance(v, dict) and v.get("enabled", False)
    }
    if not enabled_channels:
        return CheckResult("channel config", "P1", True, "No channels enabled — skipped ✓")

    issues: list[str] = []
    _PLACEHOLDER_HOSTS = {"", "NOT-THIS-HOST-XYZ", "your-hostname-here"}

    # Check 1 — listener_host not a placeholder
    listener_host = str(cfg.get("defaults", {}).get("listener_host", "")).strip()
    if listener_host in _PLACEHOLDER_HOSTS:
        issues.append(
            "defaults.listener_host is empty/placeholder — listener will skip every machine; "
            "run: python scripts/setup_channel.py --set-listener-host"
        )

    # Check 2 — push_enabled
    push_enabled = cfg.get("defaults", {}).get("push_enabled", False)
    if not push_enabled:
        issues.append(
            "defaults.push_enabled is false — post-catch-up push disabled; "
            "set push_enabled: true in config/channels.yaml"
        )

    # Check 3 — each enabled channel has a non-empty primary recipient ID
    for ch_name, ch_cfg in enabled_channels.items():
        primary_id = str(
            (ch_cfg.get("recipients") or {}).get("primary", {}).get("id", "")
        ).strip()
        if not primary_id:
            issues.append(
                f"{ch_name}: recipients.primary.id is empty — all inbound messages will be "
                f"rejected (unknown_sender); run: python scripts/setup_channel.py --channel {ch_name}"
            )

    if issues:
        return CheckResult(
            "channel config", "P1", False,
            f"{len(issues)} channel misconfiguration(s) detected",
            fix_hint=" | ".join(issues),
        )

    return CheckResult(
        "channel config", "P1", True,
        f"Channel config valid ({len(enabled_channels)} channel(s)) ✓",
    )


def check_channel_health() -> CheckResult:
    """P1: Verify enabled channel adapters (Telegram, etc.) are reachable.

    Non-blocking: gracefully skipped when config/channels.yaml does not exist
    or no channels are enabled, so the catch-up is never blocked.
    """
    config_path = Path(os.path.join(ARTHA_DIR, "config", "channels.yaml"))
    if not config_path.exists():
        return CheckResult(
            "channel health", "P1", True,
            "channels.yaml not found — channel push disabled ✓",
        )
    try:
        sys.path.insert(0, os.path.join(ARTHA_DIR, "scripts"))
        from channels.registry import (
            load_channels_config,
            iter_enabled_channels,
            create_adapter_from_config,
        )
    except ImportError:
        return CheckResult(
            "channel health", "P1", True,
            "scripts.channels not importable — channel health skipped ✓",
        )
    try:
        config = load_channels_config()
    except Exception as exc:
        return CheckResult(
            "channel health", "P1", False,
            f"channels.yaml parse error: {exc}",
            fix_hint="Validate YAML syntax in config/channels.yaml",
        )
    enabled = list(iter_enabled_channels(config))
    if not enabled:
        return CheckResult("channel health", "P1", True, "No channels enabled – skipped ✓")
    unhealthy: list[str] = []
    for ch_name, ch_cfg in enabled:
        try:
            adapter = create_adapter_from_config(ch_name, ch_cfg)
            if not adapter.health_check():
                unhealthy.append(ch_name)
                _healthy = False
            else:
                _healthy = True
        except Exception:
            unhealthy.append(ch_name)
            _healthy = False
        try:
            from lib.common import update_channel_health_md
            update_channel_health_md(ch_name, _healthy)
        except Exception:
            pass  # Non-critical
    if unhealthy:
        return CheckResult(
            "channel health", "P1", False,
            f"Unhealthy channels: {unhealthy} — channel push will be degraded",
            fix_hint="python scripts/setup_channel.py --health",
        )
    return CheckResult(
        "channel health", "P1", True,
        f"All {len(enabled)} channel(s) healthy ✓",
    )


def check_action_handlers() -> CheckResult:
    """P1 (Step 0c): Run action handler health checks and expire stale queue entries.

    Disabled handlers are excluded for the current session only (non-blocking).
    Ref: specs/act.md §5.2 Step 0c
    """
    try:
        import importlib
        # Guard: only run if actions feature is enabled in artha_config.yaml
        config_path = os.path.join(ARTHA_DIR, "config", "artha_config.yaml")
        actions_enabled = False
        if os.path.exists(config_path):
            with open(config_path) as _f:
                _content = _f.read()
            # Quick YAML check without full parser dependency
            actions_enabled = "actions:" in _content and "enabled: true" in _content

        if not actions_enabled:
            return CheckResult(
                "action handlers", "P1", True,
                "Action layer not enabled — skipping handler health checks",
            )

        # Import ActionExecutor
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from action_executor import ActionExecutor  # noqa: PLC0415

        executor = ActionExecutor(Path(ARTHA_DIR))

        # Sweep expired stale actions
        expired_count = executor.expire_stale()

        # Run handler health checks
        health: dict[str, bool] = executor.run_health_checks()

        failed = [k for k, v in health.items() if not v]
        passed = [k for k, v in health.items() if v]

        if not health:
            return CheckResult(
                "action handlers", "P1", True,
                f"Action layer enabled, no handlers loaded{' | ' + str(expired_count) + ' stale actions expired' if expired_count else ''}",
            )

        if failed:
            return CheckResult(
                "action handlers", "P1", False,
                (
                    f"Action handlers: {len(passed)} ok, {len(failed)} degraded "
                    f"({', '.join(failed)}) — those action types disabled this session"
                    + (f" | {expired_count} stale actions expired" if expired_count else "")
                ),
                fix_hint="Check connector credentials for degraded handlers",
            )

        return CheckResult(
            "action handlers", "P1", True,
            (
                f"All {len(passed)} action handlers healthy ✓"
                + (f" | {expired_count} stale actions expired" if expired_count else "")
            ),
        )

    except ImportError:
        return CheckResult(
            "action handlers", "P1", True,
            "Action layer modules not installed — skipping (run: make install)",
        )
    except Exception as exc:
        return CheckResult(
            "action handlers", "P1", False,
            f"Action handler check failed: {exc}",
            fix_hint="Review state/actions.db and action executor logs",
        )


