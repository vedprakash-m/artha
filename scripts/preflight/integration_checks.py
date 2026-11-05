"""preflight/integration_checks.py — Third-party integration health (bridge, WorkIQ, ADO, HA, channel)."""
from __future__ import annotations

import json
import os
import sys
import subprocess
import time
from pathlib import Path

from preflight._types import (
    ARTHA_DIR, SCRIPTS_DIR, STATE_DIR, WORKIQ_CACHE_FILE, WORKIQ_CACHE_MAX_AGE, WORKIQ_VERSION_PIN,
    _SUBPROCESS_ENV, _rel, CheckResult, _REQUIRED_DEPS,
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
        from lib.config_loader import load_config as _load_config  # noqa: PLC0415
        artha_config = _load_config("artha_config")

        mm = artha_config.get("multi_machine", {})
        if not mm.get("bridge_enabled", False):
            return CheckResult("Bridge", "P1", True, "Bridge: disabled (multi_machine.bridge_enabled=false)")

        from action_bridge import (  # noqa: PLC0415
            get_bridge_dir, detect_role, check_health_staleness, load_artha_config,
        )
        channels_config: dict = _load_config("channels")

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
      2. Check the separate machine-local Work health receipt — if fresh (<24h), reuse cached result.
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

    from lib.local_paths import work_cache_dir
    from lib.workiq_health import read_receipt, write_receipt
    from catchup_core.session_lock import require_active_installation
    try:
        cache_path = Path(WORKIQ_CACHE_FILE) if WORKIQ_CACHE_FILE else work_cache_dir() / "health.json"
        require_active_installation(Path(ARTHA_DIR), cache_path)
    except (ValueError, RuntimeError):
        return CheckResult("WorkIQ Calendar", "P1", False, "WorkIQ needs active machine-local storage")
    cache = read_receipt(cache_path, max_age=WORKIQ_CACHE_MAX_AGE)
    if cache is not None:
        if cache["available"] and cache["auth_valid"]:
            return CheckResult("WorkIQ Calendar", "P1", True, "Available + authenticated (cached) ✓")
        return CheckResult("WorkIQ Calendar", "P1", False, "WorkIQ unavailable or auth expired (cached)",
                           fix_hint="Check WorkIQ installation and M365 authentication")

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
        available = result.returncode == 0 and len(result.stdout.strip()) > 0
        # Auth is valid if we got a meaningful response (not an error message)
        auth_valid = available and "error" not in result.stdout.lower()[:100]
        # Persist only technical flags; provider stdout can contain a private identity.
        try:
            write_receipt(cache_path, available=available, auth_valid=auth_valid)
        except (OSError, ValueError, RuntimeError):
            return CheckResult("WorkIQ Calendar", "P1", False, "WorkIQ probe receipt could not be persisted")

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
                "WorkIQ not available (probe failed)",
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
        from lib.config_loader import load_config as _lc  # noqa: PLC0415
        _profile = _lc("user_profile", str(Path(ARTHA_DIR) / "config"))
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
    Unavailable or unverified connectivity is a failed P1 advisory, never a P0 block.
    """
    _name = "Home Assistant"

    # Load connector config
    import yaml as _yaml
    from lib.config_loader import load_config as _load_config
    try:
        _cfg = _load_config("connectors", os.path.join(ARTHA_DIR, "config"), strict=True)
    except (OSError, _yaml.YAMLError, UnicodeError, ValueError) as exc:
        return CheckResult(_name, "P1", False, f"Could not read connectors.yaml ({type(exc).__name__})")
    if not isinstance(_cfg, dict) or not isinstance(_cfg.get("connectors"), dict):
        return CheckResult(_name, "P1", False, "Invalid connectors.yaml: expected connectors mapping")
    _ha = _cfg["connectors"].get("homeassistant", {})
    if not isinstance(_ha, dict) or not isinstance(_ha.get("enabled", False), bool):
        return CheckResult(_name, "P1", False, "Invalid HA configuration: expected mapping and boolean enabled")

    if not _ha.get("enabled", False):
        return CheckResult(_name, "P1", True, "HA connector not enabled — skipped ✓")

    _fetch_cfg = _ha.get("fetch", {})
    if not isinstance(_fetch_cfg, dict):
        return CheckResult(_name, "P1", False, "Invalid HA configuration: expected fetch mapping")
    _ha_url = _fetch_cfg.get("ha_url")
    _ha_url = _ha_url.strip().rstrip("/") if isinstance(_ha_url, str) else ""
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
    except Exception as exc:
        return CheckResult(_name, "P1", False, f"HA token store unavailable ({type(exc).__name__})")

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
                _name, "P1", False,
                "HA unavailable on current network — connectivity not verified",
            )
        return CheckResult(
            _name, "P1", False,
            f"HA check error: {type(exc).__name__}",
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


def _held_action_types() -> frozenset[str]:
    """Action types the live policy holds (config/actions.yaml live_trial.held); they never dispatch."""
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        held = (load_config("actions", os.path.join(ARTHA_DIR, "config")).get("live_trial") or {}).get("held") or []
    except (ImportError, AttributeError):
        return frozenset()
    if not isinstance(held, list):
        return frozenset()
    return frozenset(str(name) for name in held if isinstance(name, str))


def check_action_handlers() -> CheckResult:
    """P1 (Step 0c): Run action handler health checks and expire stale queue entries.

    Disabled handlers are excluded for the current session only (non-blocking).
    Ref: specs/act.md §5.2 Step 0c
    """
    try:
        import importlib
        # Guard: only run if actions feature is enabled in artha_config.yaml
        actions_enabled = False
        try:
            from lib.config_loader import load_config as _lc_action  # noqa: PLC0415
            _acfg = _lc_action("artha_config")
            actions_enabled = (
                _acfg.get("harness", {}).get("actions", {}).get("enabled", False)
            )
        except Exception:
            pass  # config unreadable → default to disabled

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

        held = _held_action_types()
        held_degraded = sorted(k for k, v in health.items() if not v and k in held)
        failed = [k for k, v in health.items() if not v and k not in held]
        passed = [k for k, v in health.items() if v]
        held_note = (f" | held, not checked: {', '.join(held_degraded)}" if held_degraded else "")

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
                    + held_note
                    + (f" | {expired_count} stale actions expired" if expired_count else "")
                ),
                fix_hint="Check connector credentials for degraded handlers",
            )

        return CheckResult(
            "action handlers", "P1", True,
            (
                f"All {len(passed)} action handlers healthy ✓"
                + held_note
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


# ---------------------------------------------------------------------------
# EA-11a — External agent discovery
# ---------------------------------------------------------------------------

def _external_agent_settings(artha_dir: str) -> tuple[bool, bool]:
    """Read external-agent flags without treating invalid config as disabled."""
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    from lib.config_loader import load_config  # noqa: PLC0415

    config = load_config("artha_config", os.path.join(artha_dir, "config"), strict=True)
    harness = config.get("harness", {})
    if not isinstance(harness, dict):
        raise ValueError("harness must be a mapping")
    agentic = harness.get("agentic", {})
    if not isinstance(agentic, dict):
        raise ValueError("harness.agentic must be a mapping")
    external_agents = agentic.get("external_agents", {})
    if not isinstance(external_agents, dict):
        raise ValueError("harness.agentic.external_agents must be a mapping")
    enabled = external_agents.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("external_agents.enabled must be a boolean")
    discovery = external_agents.get("discovery", {})
    if not isinstance(discovery, dict):
        raise ValueError("external_agents.discovery must be a mapping")
    discovery_enabled = discovery.get("enabled", True)
    if not isinstance(discovery_enabled, bool):
        raise ValueError("external_agents.discovery.enabled must be a boolean")
    return enabled, discovery_enabled


def check_ext_agent_discovery() -> CheckResult:
    """P1 non-blocking: Scan config/agents/external/ for unregistered .agent.md files.

    Discovers new agent definitions dropped into the file-drop folder that have
    not yet been registered via agent-manager register. Only runs when the AR-9
    external agents feature is enabled in artha_config.yaml.
    """
    # Gate: only run if external agents feature is enabled
    try:
        _enabled, _discovery_enabled = _external_agent_settings(ARTHA_DIR)
        if not _enabled:
            return CheckResult("ext agent discovery", "P1", True,
                               "External agents not enabled — skipped ✓")
        if not _discovery_enabled:
            return CheckResult("ext agent discovery", "P1", True,
                               "Agent discovery disabled in config — skipped ✓")
    except Exception:
        return CheckResult("ext agent discovery", "P1", False,
                           "External-agent configuration is unreadable or invalid")

    # Load registry to know already-registered agent names
    registered_names: set[str] = set()
    try:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from lib.agent_registry import AgentRegistry  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415
        _registry_path = os.path.join(ARTHA_DIR, "config", "agents", "external-registry.yaml")
        if os.path.exists(_registry_path):
            _reg = AgentRegistry.load(_Path(ARTHA_DIR) / "config", strict=True)
            registered_names = {
                a.name for a in (
                    _reg.active_agents() if hasattr(_reg, "active_agents") else []
                )
            }
    except Exception:
        return CheckResult("ext agent discovery", "P1", False,
                           "External-agent registry is unreadable or invalid")

    # Scan drop folder
    drop_dir = os.path.join(ARTHA_DIR, "config", "agents", "external")
    if not os.path.isdir(drop_dir):
        return CheckResult("ext agent discovery", "P1", True,
                           "config/agents/external/ not found — skipped ✓")

    agent_files = [
        f for f in os.listdir(drop_dir)
        if f.endswith(".agent.md") and f != "README.md"
    ]
    if not agent_files:
        return CheckResult("ext agent discovery", "P1", True,
                           "config/agents/external/ — no .agent.md files ✓")

    # Identify unregistered agents
    unregistered = []
    for fname in agent_files:
        agent_name = fname[: -len(".agent.md")]
        if agent_name not in registered_names:
            unregistered.append(agent_name)

    if not unregistered:
        return CheckResult(
            "ext agent discovery", "P1", True,
            f"config/agents/external/: {len(agent_files)} agent(s) all registered ✓",
        )

    names_str = ", ".join(unregistered[:5]) + ("…" if len(unregistered) > 5 else "")
    return CheckResult(
        "ext agent discovery", "P1", False,
        f"📡 Discovered {len(unregistered)} unregistered agent(s) in config/agents/external/: {names_str}",
        fix_hint=(
            "Run: python scripts/agent_manager.py register  "
            "(registers all unregistered .agent.md files from the drop folder)"
        ),
    )


# ---------------------------------------------------------------------------
# EA-11b — External agent health dashboard
# ---------------------------------------------------------------------------

def check_ext_agent_health() -> CheckResult:
    """P1 non-blocking: Report degraded or suspended registered external agents.

    Only runs when the AR-9 external agents feature is enabled.
    Surfaces agents in 'degraded' or 'suspended' state so operators can take action.
    """
    # Gate: only run if external agents feature is enabled
    try:
        _enabled, _ = _external_agent_settings(ARTHA_DIR)
        if not _enabled:
            return CheckResult("ext agent health", "P1", True,
                               "External agents not enabled — skipped ✓")
    except Exception:
        return CheckResult("ext agent health", "P1", False,
                           "External-agent configuration is unreadable or invalid")

    # Load registry
    try:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from lib.agent_registry import AgentRegistry  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415
        _registry_path = os.path.join(ARTHA_DIR, "config", "agents", "external-registry.yaml")
        if not os.path.exists(_registry_path):
            return CheckResult("ext agent health", "P1", True,
                               "No agent registry found — skipped ✓")
        _reg = AgentRegistry.load(_Path(ARTHA_DIR) / "config", strict=True)
        _all_agents = list(_reg.all_agents()) if hasattr(_reg, "all_agents") else []
    except Exception:
        return CheckResult("ext agent health", "P1", False,
                           "External-agent registry is unreadable or invalid")

    if not _all_agents:
        return CheckResult("ext agent health", "P1", True, "No registered agents ✓")

    degraded = [a.name for a in _all_agents if a.health.status == "degraded"]
    suspended = [a.name for a in _all_agents if a.health.status == "suspended"]

    if not degraded and not suspended:
        return CheckResult(
            "ext agent health", "P1", True,
            f"{len(_all_agents)} agent(s) all healthy ✓",
        )

    parts = []
    if degraded:
        parts.append(
            f"degraded: {', '.join(degraded[:3])}{'…' if len(degraded) > 3 else ''}"
        )
    if suspended:
        parts.append(
            f"suspended: {', '.join(suspended[:3])}{'…' if len(suspended) > 3 else ''}"
        )

    return CheckResult(
        "ext agent health", "P1", False,
        f"⚠️ {len(degraded) + len(suspended)} agent(s) need attention — {'; '.join(parts)}",
        fix_hint=(
            "Run: python scripts/agent_manager.py status  "
            "to review and reinstate suspended agents"
        ),
    )
