"""Audit (2026-11-05): scripts/preflight.py and the scripts/preflight/ package define overlapping checks.

The CLI runs the script; `import preflight` loads the package. Until they are consolidated, this ratchet
freezes the known divergence: a newly diverged or newly duplicated function fails, and an entry that has
been reconciled must be removed from KNOWN_DIVERGED.
"""
from __future__ import annotations

import ast
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
KNOWN_DIVERGED = frozenset({
    "_is_bootstrap_stub", "check_action_handlers", "check_ado_auth", "check_bridge_health",
    "check_ext_agent_discovery", "check_ext_agent_health", "check_msgraph_token", "check_oauth_token",
    "check_open_items", "check_planning_signals", "check_profile_completeness", "check_script_health",
    "check_token_freshness", "check_vault_lock", "main", "run_preflight",
})
KNOWN_SHARED = 28


def _functions(paths):
    found = {}
    for path in paths:
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef):
                found[node.name] = ast.dump(node, include_attributes=False)
    return found


def test_preflight_copies_do_not_drift_further():
    script = _functions([SCRIPTS / "preflight.py"])
    package = _functions(sorted((SCRIPTS / "preflight").glob("*.py")))
    shared = set(script) & set(package)
    diverged = {name for name in shared if script[name] != package[name]}
    assert len(shared) <= KNOWN_SHARED, f"new duplicated preflight functions: {sorted(shared)}"
    assert diverged <= KNOWN_DIVERGED, f"newly diverged preflight functions: {sorted(diverged - KNOWN_DIVERGED)}"
    reconciled = KNOWN_DIVERGED - diverged
    assert not reconciled, f"reconciled or removed; drop from KNOWN_DIVERGED: {sorted(reconciled)}"
