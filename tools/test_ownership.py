#!/usr/bin/env python3
"""Requirement disposition and AST-parsed test ownership registry (outside catchup_core).

Keeping legacy PRD FR dispositions and test-to-requirement ownership here avoids
mutating ``scripts/catchup_core/capabilities.py`` (which is release-fingerprinted).
Ownership declarations in test files use literal ``ARTHA_TEST_OWNER = {...}``
assignments parsed strictly via ``ast.literal_eval`` (no test module imports);
``disposition`` and ``pillar`` are derived automatically from each requirement ID.
"""
from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

VALID_DISPOSITIONS = frozenset({
    "pilot",
    "destination milestone",
    "replaced",
    "rejected",
    "separate_work",
    "incumbent",
    "infrastructure",
})

PILLARS = (
    "PULL over PUSH",
    "Simple",
    "Secure",
    "Robust",
    "Evidence over Eloquence",
)


@dataclass(frozen=True)
class RequirementSpec:
    """Canonical disposition and primary philosophy pillar for a requirement ID."""

    id: str
    title: str
    disposition: str
    pillar: str
    note: str = ""


# Canonical PRD 35 FRs (FR-1..FR-32, FR-40..FR-42), TR-1..TR-3, RA-01..RA-25,
# AR-01..AR-26, and tracked test-infrastructure categories.
REQUIREMENTS: dict[str, RequirementSpec] = {
    # ── PRD Functional Requirements (35 FRs) ───────────────────────────────
    "FR-1": RequirementSpec("FR-1", "Communications Intelligence", "pilot", "Robust"),
    "FR-2": RequirementSpec("FR-2", "Immigration Sentinel", "incumbent", "Evidence over Eloquence", "Needs Domain pack before pilot"),
    "FR-3": RequirementSpec("FR-3", "Financial Command Center", "incumbent", "Evidence over Eloquence", "Needs Domain pack before pilot"),
    "FR-4": RequirementSpec("FR-4", "Kids & School Intelligence", "pilot", "Evidence over Eloquence", "Pilot Domain under RA-14"),
    "FR-5": RequirementSpec("FR-5", "Travel & Loyalty Management", "incumbent", "Simple"),
    "FR-6": RequirementSpec("FR-6", "Health & Wellness Radar", "incumbent", "Secure"),
    "FR-7": RequirementSpec("FR-7", "Home & Property Management", "incumbent", "Simple"),
    "FR-8": RequirementSpec("FR-8", "Calendar & Time Intelligence", "pilot", "Robust"),
    "FR-9": RequirementSpec("FR-9", "Shopping & Commerce Intelligence", "incumbent", "Simple"),
    "FR-10": RequirementSpec("FR-10", "Learning & Development Tracker", "incumbent", "Simple"),
    "FR-11": RequirementSpec("FR-11", "Relationship Intelligence & Social Fabric", "replaced", "Simple", "Inferred relationship profiles retired (D-14)"),
    "FR-12": RequirementSpec("FR-12", "Digital Life Management", "incumbent", "Secure"),
    "FR-13": RequirementSpec("FR-13", "Goal Intelligence Engine", "pilot", "Evidence over Eloquence"),
    "FR-14": RequirementSpec("FR-14", "Work-Life Boundary Guardian", "pilot", "Secure"),
    "FR-15": RequirementSpec("FR-15", "Artha OS Core", "pilot", "PULL over PUSH"),
    "FR-16": RequirementSpec("FR-16", "Insurance & Risk Management", "incumbent", "Simple"),
    "FR-17": RequirementSpec("FR-17", "Vehicle Management", "incumbent", "Simple"),
    "FR-18": RequirementSpec("FR-18", "Estate Planning & Legal Readiness", "incumbent", "Secure"),
    "FR-19": RequirementSpec("FR-19", "Work Intelligence OS", "separate_work", "Secure"),
    "FR-20": RequirementSpec("FR-20", "Readiness Intelligence", "incumbent", "Simple"),
    "FR-21": RequirementSpec("FR-21", "Logistics Intelligence", "incumbent", "Simple"),
    "FR-22": RequirementSpec("FR-22", "Tribe Intelligence", "incumbent", "Secure"),
    "FR-23": RequirementSpec("FR-23", "Capital Intelligence", "incumbent", "Evidence over Eloquence"),
    "FR-24": RequirementSpec("FR-24", "OpenClaw Home Bridge", "destination milestone", "Simple"),
    "FR-25": RequirementSpec("FR-25", "Career Search Intelligence", "pilot", "Secure"),
    "FR-26": RequirementSpec("FR-26", "Artha Channel Integration (ACI)", "replaced", "PULL over PUSH", "Push channel daemons retired (re-artha §6.11)"),
    "FR-27": RequirementSpec("FR-27", "Universal Briefing Archival", "pilot", "Evidence over Eloquence"),
    "FR-28": RequirementSpec("FR-28", "Hermes Home Intelligence Layer", "replaced", "PULL over PUSH", "Background hermes launchd job retired (§6.11)"),
    "FR-29": RequirementSpec("FR-29", "DataCopilot Quality Infrastructure", "pilot", "Evidence over Eloquence"),
    "FR-30": RequirementSpec("FR-30", "Agent SRE Observability", "pilot", "Robust"),
    "FR-31": RequirementSpec("FR-31", "MCP Hybrid Connector Architecture", "separate_work", "Secure"),
    "FR-32": RequirementSpec("FR-32", "PM Starter Kit Adoption", "separate_work", "Simple"),
    "FR-40": RequirementSpec("FR-40", "Action Layer Quality Conversion", "pilot", "Robust"),
    "FR-41": RequirementSpec("FR-41", "Ambient Intent Buffer", "destination milestone", "PULL over PUSH"),
    "FR-42": RequirementSpec("FR-42", "Work-Health Dashboard Intelligence", "separate_work", "Secure"),
    # ── PRD Technical Testing Requirements (TR-1..TR-3) ────────────────────
    "TR-1": RequirementSpec("TR-1", "Security-Critical Script Validation (pii_guard.py, vault.py, preflight.py)", "pilot", "Secure"),
    "TR-2": RequirementSpec("TR-2", "Deterministic Golden Extraction Regression", "pilot", "Evidence over Eloquence"),
    "TR-3": RequirementSpec("TR-3", "Cross-Platform Consistency & --health Checks", "pilot", "Robust"),
    # ── Re-Artha Capabilities (RA-01..RA-25) ───────────────────────────────
    "RA-01": RequirementSpec("RA-01", "Coverage-aware Catch-up", "pilot", "Robust"),
    "RA-02": RequirementSpec("RA-02", "Evidence and temporal Fact lifecycle", "pilot", "Evidence over Eloquence"),
    "RA-03": RequirementSpec("RA-03", "Alert and Open Item discipline", "pilot", "PULL over PUSH"),
    "RA-04": RequirementSpec("RA-04", "Briefing and question answering", "pilot", "Evidence over Eloquence"),
    "RA-05": RequirementSpec("RA-05", "Staged action and review", "pilot", "Secure"),
    "RA-06": RequirementSpec("RA-06", "Corrections and deliberate memory", "pilot", "Evidence over Eloquence"),
    "RA-07": RequirementSpec("RA-07", "Model and tool policy", "pilot", "Secure"),
    "RA-08": RequirementSpec("RA-08", "Connector boundary and ingest bundles", "pilot", "Robust"),
    "RA-09": RequirementSpec("RA-09", "Privacy, Work isolation, and bounded MCP", "pilot", "Secure"),
    "RA-10": RequirementSpec("RA-10", "Capability and evaluation ledger", "pilot", "Evidence over Eloquence"),
    "RA-11": RequirementSpec("RA-11", "Goal and Sprint intelligence", "pilot", "Evidence over Eloquence"),
    "RA-12": RequirementSpec("RA-12", "Household permission and consent", "pilot", "Secure"),
    "RA-13": RequirementSpec("RA-13", "Cross-Domain cases", "pilot", "Evidence over Eloquence"),
    "RA-14": RequirementSpec("RA-14", "Domain and Connector packs", "pilot", "Simple"),
    "RA-15": RequirementSpec("RA-15", "Onboarding, correction and feedback", "pilot", "Simple"),
    "RA-16": RequirementSpec("RA-16", "Processing closure and silent-failure detection", "pilot", "Robust"),
    "RA-17": RequirementSpec("RA-17", "Deadline horizon and staleness honesty", "pilot", "PULL over PUSH"),
    "RA-18": RequirementSpec("RA-18", "Security architecture and harness enforcement", "pilot", "Secure"),
    "RA-19": RequirementSpec("RA-19", "Household coordination", "destination milestone", "Secure"),
    "RA-20": RequirementSpec("RA-20", "Portable export, emergency access, checkpoint & recovery", "destination milestone", "Robust"),
    "RA-21": RequirementSpec("RA-21", "Document ingestion and extraction", "pilot", "Evidence over Eloquence"),
    "RA-22": RequirementSpec("RA-22", "Feedback loop and precision tracking", "pilot", "Evidence over Eloquence"),
    "RA-23": RequirementSpec("RA-23", "Beliefs view, forget receipts and third-party data", "pilot", "Secure"),
    "RA-24": RequirementSpec("RA-24", "Action policy, executor and activity view", "pilot", "Secure"),
    "RA-25": RequirementSpec("RA-25", "Goal instruction sheets and standing responsibilities", "destination milestone", "PULL over PUSH"),
    # ── Automatic Release Renewal (AR-01..AR-26) ───────────────────────────
    **{
        f"AR-{n:02d}": RequirementSpec(
            f"AR-{n:02d}",
            f"Automatic Release Renewal contract AR-{n:02d}",
            "pilot",
            "Secure" if n <= 16 else "Robust",
        )
        for n in range(1, 27)
    },
    # ── Infrastructure / Test-Harness Contracts ────────────────────────────
    "INFRA-ISOLATION": RequirementSpec("INFRA-ISOLATION", "Hermetic workspace, network & credential isolation", "infrastructure", "Secure"),
    "INFRA-CI": RequirementSpec("INFRA-CI", "CI/CD gates, canaries, packaging & register drift checks", "infrastructure", "Robust"),
    "INFRA-UX": RequirementSpec("INFRA-UX", "UX command router, prompt linter & config schema parity", "infrastructure", "Simple"),
}


# Per-directory defaults when a test file does not explicitly declare ARTHA_TEST_OWNER
# and is not directly mapped by catchup_core.capabilities.CAPABILITIES.
DIR_DEFAULTS: dict[str, tuple[str, ...]] = {
    "tests/catchup_core": ("RA-01",),
    "tests/dev": ("INFRA-CI",),
    "tests/eval": ("FR-29", "RA-10"),
    "tests/ext_agents": ("FR-30", "RA-18"),
    "tests/extraction": ("TR-2",),
    "tests/integration": ("FR-15", "RA-01"),
    "tests/lib": ("FR-27",),
    "tests/uat": ("RA-10",),
    "tests/unit": ("FR-15",),
    "tests": ("FR-15",),
}


# Specific filename prefix rules for legacy tests before falling back to DIR_DEFAULTS.
PREFIX_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tests/catchup_core/test_renewal_", tuple(f"AR-{n:02d}" for n in range(1, 27))),
    ("tests/dev/test_isolat", ("INFRA-ISOLATION",)),
    ("tests/dev/test_privacy_gate", ("TR-1", "RA-09")),
    ("tests/dev/test_pii_doc_scan", ("TR-1",)),
    ("tests/dev/test_windows_contract_launcher", ("RA-20", "INFRA-CI")),
    ("tests/unit/test_pii_", ("TR-1",)),
    ("tests/unit/test_vault", ("TR-1", "RA-09")),
    ("tests/unit/test_backup", ("TR-1", "RA-20")),
    ("tests/unit/test_preflight", ("TR-1",)),
    ("tests/unit/test_footprint_", ("FR-15", "FR-26", "FR-28")),
    ("tests/unit/test_job_retirement", ("FR-26", "FR-28")),
    ("tests/unit/test_career_", ("FR-25",)),
    ("tests/unit/test_action_", ("FR-40", "RA-05", "RA-24")),
    ("tests/unit/test_credentials", ("RA-09", "RA-18")),
    ("tests/unit/test_credential_migration", ("RA-18",)),
    ("tests/unit/test_google_", ("FR-1", "FR-8", "RA-08")),
    ("tests/unit/test_msgraph_", ("FR-1", "FR-8", "RA-08")),
    ("tests/unit/test_imap_", ("FR-1", "RA-08")),
    ("tests/unit/test_caldav_", ("FR-8", "RA-08")),
    ("tests/unit/test_apple_reminders", ("FR-8", "RA-08")),
    ("tests/unit/test_apple_health", ("FR-6", "RA-08")),
    ("tests/unit/test_slack_", ("FR-1", "RA-08")),
    ("tests/unit/test_todoist", ("FR-8", "FR-15")),
    ("tests/unit/test_plaid_", ("FR-3", "RA-08")),
    ("tests/unit/test_homeassistant_", ("FR-7", "RA-08")),
    ("tests/unit/test_connectors_reddit", ("FR-10", "RA-08")),
    ("tests/unit/test_connector_", ("RA-08", "RA-16")),
    ("tests/unit/test_goals_", ("FR-13", "RA-11")),
    ("tests/unit/test_tool_guard", ("RA-09", "RA-18")),
    ("tests/unit/test_hook_portability", ("RA-18",)),
    ("tests/unit/test_session_stop", ("RA-17",)),
    ("tests/unit/test_stale_banner", ("RA-17",)),
    ("tests/unit/test_work_", ("FR-14", "RA-09")),
    ("tests/unit/test_pipeline", ("FR-15", "RA-01")),
)


def parse_file_owner(path: Path) -> dict[str, Any] | None:
    """Extract literal ``ARTHA_TEST_OWNER = {...}`` from ``path`` via AST without importing."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ARTHA_TEST_OWNER":
                    value = ast.literal_eval(node.value)
                    if not isinstance(value, dict):
                        raise ValueError(f"{path}: ARTHA_TEST_OWNER must be a dict literal")
                    return value
    return None


def _capability_index(root: Path) -> dict[str, set[str]]:
    sys.path.insert(0, str(root / "scripts"))
    try:
        from catchup_core.capabilities import BOUNDARY_CASES, CAPABILITIES, _BOUNDARY_TESTS  # noqa: PLC0415
    finally:
        sys.path.pop(0)

    by_test: dict[str, set[str]] = {}
    for cap in CAPABILITIES:
        for test_path in cap.tests:
            by_test.setdefault(test_path, set()).add(cap.id)
    for case, fixture in BOUNDARY_CASES:
        test_path = fixture.split("::", 1)[0] if "::" in fixture else _BOUNDARY_TESTS
        req_id = case.split(".", 1)[0]
        if req_id in REQUIREMENTS:
            by_test.setdefault(test_path, set()).add(req_id)
    return by_test


def _tracked_test_paths(root: Path) -> list[str]:
    """List tracked public ``test_*.py`` paths (never picking up gitignored private Work/lib tests)."""
    import subprocess  # noqa: PLC0415

    if (root / ".git").exists():
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "tests"],
            capture_output=True,
            check=False,
        )
        if res.returncode == 0:
            return sorted(
                p for p in res.stdout.decode("utf-8", "surrogateescape").split("\0")
                if p
                and (root / p).exists()
                and Path(p).name.startswith("test_")
                and Path(p).suffix == ".py"
                and "work" not in Path(p).parts
            )
    return sorted(
        p.relative_to(root).as_posix()
        for p in (root / "tests").rglob("test_*.py")
        if "work" not in p.parts and "__pycache__" not in p.parts
    )


def resolve_ownership(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    """Resolve requirement IDs, derived dispositions, pillars, and kind for every tracked test."""
    cap_reqs = _capability_index(root)
    tracked_tests = _tracked_test_paths(root)
    entries: dict[str, dict[str, Any]] = {}
    for rel in tracked_tests:
        explicit = parse_file_owner(root / rel)
        reqs: list[str] = []
        source = "default"
        modules: list[str] = []
        if explicit is not None:
            reqs = list(explicit.get("requirements") or ())
            modules = list(explicit.get("modules") or ())
            source = "explicit"
        elif rel in cap_reqs:
            reqs = sorted(cap_reqs[rel])
            source = "capabilities"
        else:
            matched = False
            for prefix, prefix_reqs in PREFIX_RULES:
                if rel.startswith(prefix):
                    reqs = list(prefix_reqs)
                    source = "prefix"
                    matched = True
                    break
            if not matched:
                parent = Path(rel).parent.as_posix()
                while parent and parent not in DIR_DEFAULTS:
                    parent = Path(parent).parent.as_posix()
                    if parent == ".":
                        parent = "tests"
                reqs = list(DIR_DEFAULTS.get(parent, ("FR-15",)))
                source = "dir-default"

        unknown = [r for r in reqs if r not in REQUIREMENTS]
        if unknown or not reqs:
            raise ValueError(f"{rel}: invalid or empty requirements {reqs!r} (unknown: {unknown})")

        dispositions = sorted({REQUIREMENTS[r].disposition for r in reqs})
        pillars = sorted({REQUIREMENTS[r].pillar for r in reqs})
        entries[rel] = {
            "requirements": reqs,
            "dispositions": dispositions,
            "pillars": pillars,
            "source": source,
            **({"modules": modules} if modules else {}),
        }
    return entries


def validate_node_resolutions(root: Path = ROOT) -> list[str]:
    """Verify every CAPABILITIES test file and BOUNDARY_CASES target exists cleanly in AST."""
    sys.path.insert(0, str(root / "scripts"))
    try:
        from catchup_core.capabilities import BOUNDARY_CASES, _BOUNDARY_TESTS  # noqa: PLC0415
    finally:
        sys.path.pop(0)

    errors: list[str] = []
    parsed_defs: dict[str, set[str]] = {}
    for case, fixture in BOUNDARY_CASES:
        rel, func_expr = fixture.split("::", 1) if "::" in fixture else (_BOUNDARY_TESTS, fixture)
        file_path = root / rel
        if not file_path.is_file():
            errors.append(f"{case}: missing fixture file {rel}")
            continue
        if rel not in parsed_defs:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
            parsed_defs[rel] = {
                node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
        base_name = func_expr.split("[", 1)[0].split("::")[-1]
        if base_name not in parsed_defs[rel]:
            errors.append(f"{case}: function '{base_name}' not found in {rel}")
    return errors


def render_ownership_yaml(root: Path = ROOT) -> str:
    """Serialize resolved ownership mapping into clean deterministic YAML-compatible JSON/YAML."""
    entries = resolve_ownership(root)
    lines = [
        "# Generated by tools/test_ownership.py — do not edit by hand.",
        "# Source of truth: ARTHA_TEST_OWNER AST declarations + capabilities.py + tools/test_ownership.py.",
        f"total_files: {len(entries)}",
        "files:",
    ]
    for rel, data in entries.items():
        lines.append(f"  {rel}:")
        lines.append(f"    requirements: {json.dumps(data['requirements'])}")
        lines.append(f"    dispositions: {json.dumps(data['dispositions'])}")
        lines.append(f"    pillars: {json.dumps(data['pillars'])}")
        lines.append(f"    source: {data['source']}")
        if "modules" in data:
            lines.append(f"    modules: {json.dumps(data['modules'])}")
    return "\n".join(lines) + "\n"


def render_prd_disposition_section() -> str:
    """Render full 35-FR + TR-1..3 disposition rows for docs/traceability-register.md checks."""
    rows = [
        "| ID | Title | Disposition | Pillar | Note |",
        "|---|---|---|---|---|",
    ]
    for key in [f"FR-{n}" for n in (*range(1, 33), 40, 41, 42)] + ["TR-1", "TR-2", "TR-3"]:
        spec = REQUIREMENTS[key]
        rows.append(f"| {spec.id} | {spec.title} | {spec.disposition} | {spec.pillar} | {spec.note or '-'} |")
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    """Generate or --check docs/test-ownership.yaml and BOUNDARY_CASES node resolution."""
    node_errors = validate_node_resolutions(ROOT)
    if node_errors:
        print("BOUNDARY_CASES resolution errors:\n" + "\n".join(node_errors), file=sys.stderr)
        return 1
    rendered = render_ownership_yaml(ROOT)
    target = ROOT / "docs" / "test-ownership.yaml"
    if "--check" in argv:
        if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
            print("docs/test-ownership.yaml is out of date: run python tools/test_ownership.py", file=sys.stderr)
            return 1
        return 0
    target.write_text(rendered, encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)} ({len(rendered.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
