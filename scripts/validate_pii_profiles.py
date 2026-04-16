#!/usr/bin/env python3
"""
scripts/validate_pii_profiles.py
===================================
RD-15: Validate that all external agents have pii_profile coverage for
every field in the canonical CONTEXT_BUNDLE_FIELDS schema.

Usage:
    python scripts/validate_pii_profiles.py [--strict]

Exit codes:
    0  All agents have full pii_profile coverage
    1  One or more agents are missing coverage for bundle fields

Options:
    --strict    Treat missing coverage as an error (default: warning only)
    --agent X   Check only agent with name X
    --json      Output results as JSON (for CI integration)

This script is intended to be run as part of CI. It reads
config/agents/external-registry.yaml and checks that every field in
scripts/schemas/agent_context.CONTEXT_BUNDLE_FIELDS is present in
each agent's pii_profile.allow or pii_profile.block list.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_CONFIG_DIR = _REPO_ROOT / "config"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _load_registry() -> dict:
    """Load external-registry.yaml as a raw dict."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        print("ERROR: PyYAML not installed — cannot load registry", file=sys.stderr)
        sys.exit(1)

    registry_path = _CONFIG_DIR / "agents" / "external-registry.yaml"
    if not registry_path.exists():
        print(f"ERROR: Registry not found: {registry_path}", file=sys.stderr)
        sys.exit(1)

    return yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}


def _load_bundle_fields() -> list[str]:
    """Load canonical CONTEXT_BUNDLE_FIELDS from agent_context schema."""
    from schemas.agent_context import CONTEXT_BUNDLE_FIELDS  # noqa: PLC0415
    return CONTEXT_BUNDLE_FIELDS


def audit_agent(agent_name: str, entry: dict, bundle_fields: list[str]) -> list[str]:
    """Return list of bundle fields not covered by this agent's pii_profile.

    A field is "covered" if it appears in either allow or block list.
    Agents with an empty pii_profile (both lists empty) are flagged for
    every bundle field — this catches newly registered agents that have
    not yet been reviewed.
    """
    pii = entry.get("pii_profile") or {}
    allowed: set[str] = set(pii.get("allow") or [])
    blocked: set[str] = set(pii.get("block") or [])
    covered = allowed | blocked

    missing = [f for f in bundle_fields if f not in covered]
    return missing


def run_audit(
    agent_filter: str | None = None,
    strict: bool = False,
    output_json: bool = False,
) -> int:
    """Main audit logic. Returns exit code."""
    raw = _load_registry()
    agents_raw = raw.get("agents") or {}
    bundle_fields = _load_bundle_fields()

    results: dict[str, list[str]] = {}  # agent_name -> missing fields
    agents_checked = 0

    for agent_name, entry in agents_raw.items():
        if agent_filter and agent_name != agent_filter:
            continue
        if not isinstance(entry, dict):
            continue

        agents_checked += 1
        missing = audit_agent(agent_name, entry, bundle_fields)
        if missing:
            results[agent_name] = missing

    if output_json:
        print(json.dumps({
            "agents_checked": agents_checked,
            "agents_with_gaps": len(results),
            "gaps": results,
        }, indent=2))
        return 1 if (results and strict) else 0

    if not results:
        print(
            f"✅ pii_profile coverage: {agents_checked} agent(s) checked, "
            f"all fields covered or agents have no bundle exposure."
        )
        return 0

    print(f"{'❌' if strict else '⚠'} pii_profile gaps found in {len(results)}/{agents_checked} agent(s):\n")
    for agent_name, missing in results.items():
        print(f"  [{agent_name}] missing coverage for: {', '.join(missing)}")
        print(
            f"    → Add each field to pii_profile.allow (agent may receive it) "
            f"or pii_profile.block (agent must not receive it)\n"
        )

    if not strict:
        print(
            "Run with --strict to fail CI. Use this output to update "
            "config/agents/external-registry.yaml pii_profile sections."
        )
        return 0

    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="RD-15: Validate external agent pii_profile coverage"
    )
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any coverage gaps found")
    parser.add_argument("--agent", metavar="NAME",
                        help="Only check agent with this name")
    parser.add_argument("--json", dest="output_json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args(argv)

    return run_audit(
        agent_filter=args.agent,
        strict=args.strict,
        output_json=args.output_json,
    )


if __name__ == "__main__":
    sys.exit(main())
