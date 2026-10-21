# ruff: noqa: E501  (the register is a data table; one capability per row reads better than wrapped rows)
"""Capability register (re-artha RA-10, §11.5): what is specified, built, enabled, exercised, measured.

The *static* part (code present, tests present) is generated from this table and checked in CI
(``tools/gen_capability_register.py --check``).  The *dynamic* part is computed from configuration
(enabled), the stage journal (exercised) and a verified release scorecard (measured), so a test count
or a flag can never mark a feature production-ready on its own.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CAPABILITIES", "FR_DISPOSITION", "Capability", "manifest", "render_register", "render_traceability", "runtime_states", "static_status"]


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    scope: str                       # pilot | reduced | later
    modules: tuple[str, ...]
    tests: tuple[str, ...]
    acceptance: tuple[str, ...]
    owner: str = "owner"


_C = "scripts/catchup_core/"
_T = "tests/catchup_core/"
CAPABILITIES: tuple[Capability, ...] = (
    Capability("RA-01", "Coverage-aware Catch-up", "pilot", (_C + "coordinator.py", _C + "adapters.py"),
               (_T + "test_coordinator.py", _T + "test_adapters.py", _T + "test_recovery_failures.py"), ("RA-01.AC1",)),
    Capability("RA-02", "Evidence and temporal Fact lifecycle", "pilot", (_C + "reconcile.py", _C + "extract.py", _C + "ledger.py"),
               (_T + "test_fact_reconcile.py", _T + "test_extract.py", _T + "test_ledger.py"), ("RA-02.AC1",)),
    Capability("RA-03", "Alert and Open Item discipline", "pilot", (_C + "triage.py",), (_T + "test_triage.py",), ("RA-03.AC1",)),
    Capability("RA-04", "Briefing and question answering", "pilot", (_C + "compose.py", _C + "views.py"),
               (_T + "test_compose.py", _T + "test_user_surfaces.py"), ("RA-04.AC1",)),
    Capability("RA-05", "Staged action and review (previews; no external execution before Stage 6)", "reduced",
               (_C + "actions.py", _C + "action_mapping.py", "scripts/action_queue.py", "scripts/action_executor.py"),
               (_T + "test_actions.py", _T + "test_action_mapping.py", "tests/unit/test_action_hardening.py",
                "tests/unit/test_reminder_reconcile.py"), ("RA-05.AC1",)),
    Capability("RA-06", "Corrections and deliberate memory", "pilot", ("scripts/lib/memory_policy.py", _C + "commands.py"),
               ("tests/unit/test_memory_policy.py", _T + "test_user_surfaces.py"), ("RA-06.AC1",)),
    Capability("RA-07", "Model and tool policy (model-neutral extraction)", "pilot", (_C + "exchange.py", _C + "extractor_cmd.py"),
               (_T + "test_exchange.py", _T + "test_extractor_cmd.py"), ("RA-07.AC1",)),
    Capability("RA-08", "Connector boundary and ingest bundles", "pilot",
               (_C + "adapters.py", _C + "bundles.py", "scripts/schemas/connector_record.py"),
               (_T + "test_adapters.py", _T + "test_bundles.py"), ("RA-08.AC1",)),
    Capability("RA-09", "Privacy, Work isolation, and the bounded read-only answer interface (MCP)", "pilot",
               (_C + "policy.py", _C + "mcp.py", "scripts/tool_guard.py"),
               ("tests/unit/test_tool_guard.py", _T + "test_release_regressions.py", _T + "test_mcp.py"), ("RA-09.AC1",)),
    Capability("RA-10", "Capability and evaluation ledger (with per-run latency records)", "pilot",
               (_C + "capabilities.py", _C + "metrics.py", "tools/gen_capability_register.py", "tools/benchmark_core.py"),
               ("tests/dev/test_capability_register.py", _T + "test_metrics.py", _T + "test_benchmark.py"), ("RA-10.AC1",)),
    Capability("RA-11", "Goal and Sprint intelligence (minimum observation)", "pilot", (_C + "goals.py",),
               (_T + "test_goals_scorecard_cli.py",), ("RA-11.AC1",)),
    Capability("RA-12", "Household permission and consent (v0)", "pilot", (_C + "household.py", _C + "policy.py"),
               (_T + "test_household.py", _T + "test_release_regressions.py"), ("RA-12.AC1",)),
    Capability("RA-13", "Cross-Domain cases", "pilot", (_C + "triage.py",), (_T + "test_kids_school_scenarios.py",), ("RA-13.AC1",)),
    Capability("RA-14", "Domain and Connector packs", "pilot", (_C + "domain_pack.py", _C + "packs/kids_school.yaml"),
               (_T + "test_domain_pack.py", _T + "test_composite_pack.py"), ("RA-14.AC1",)),
    Capability("RA-15", "Onboarding, correction and feedback", "pilot", (_C + "commands.py", _C + "views.py"),
               (_T + "test_user_surfaces.py",), ("RA-15.AC1",)),
    Capability("RA-16", "Processing closure and silent-failure detection", "pilot", (_C + "coordinator.py", "scripts/lib/connector_coverage.py"),
               (_T + "test_recovery_failures.py", "tests/unit/test_stale_banner.py"), ("RA-16.AC1",)),
    Capability("RA-17", "Deadline horizon and staleness honesty", "pilot", (_C + "triage.py", "scripts/stale_banner.py", "scripts/session_stop.py"),
               (_T + "test_triage.py", "tests/unit/test_stale_banner.py", "tests/unit/test_session_stop.py"), ("RA-17.AC1",)),
    Capability("RA-18", "Security architecture and harness enforcement", "pilot", (_C + "rule_of_two.py", "scripts/tool_guard.py"),
               (_T + "test_rule_of_two.py", "tests/unit/test_tool_guard.py"), ("RA-18.AC1",)),
    Capability("RA-19", "Household coordination (shared obligations, acceptance, escalation; read-only)", "later",
               (_C + "coordination.py",), (_T + "test_coordination.py",), ("RA-19.AC1",)),
    Capability("RA-20", "Portable export, emergency access, accessibility", "later", (_C + "backup.py",), (_T + "test_user_surfaces.py",), ("RA-20.AC1",)),
    Capability("RA-21", "Document ingestion and extraction (text baseline only)", "pilot", (_C + "documents.py",),
               (_T + "test_documents.py",), ("RA-21.AC1",)),
    Capability("RA-22", "Feedback loop and precision tracking", "pilot", (_C + "views.py", _C + "migrations/0007_replay.sql"),
               (_T + "test_user_surfaces.py",), ("RA-22.AC1",)),
    Capability("RA-23", "Beliefs view, forget receipts and third-party data", "reduced", (_C + "ledger.py", _C + "commands.py"),
               (_T + "test_ledger.py", _T + "test_user_surfaces.py"), ("RA-23.AC1",)),
    Capability("RA-24", "Action policy, executor and activity view (pause flag, held types)", "reduced",
               (_C + "commands.py", "scripts/action_executor.py", "scripts/lib/action_policy.py",
                "scripts/lib/action_process.py"),
               ("tests/unit/test_action_executor.py", "tests/unit/test_action_policy.py",
                "tests/unit/test_action_process.py"), ("RA-24.AC1",)),
    Capability("RA-25", "Goal plans and standing responsibilities (evaluated at Catch-up, read-only)", "later",
               (_C + "plans.py", _C + "responsibilities.py"), (_T + "test_plans.py", _T + "test_responsibilities.py"),
               ("RA-25.AC1",)),
)


def static_status(root: Path, cap: Capability) -> tuple[bool, bool]:
    """(code present, tests present).  A capability with no modules has no code."""
    code = bool(cap.modules) and all((root / m).exists() for m in cap.modules)
    tests = bool(cap.tests) and all((root / t).exists() for t in cap.tests)
    return code, tests


def render_register(root: Path) -> str:
    lines = [
        "# Capability register (generated — do not edit)", "",
        "Generated by `tools/gen_capability_register.py` from `scripts/catchup_core/capabilities.py`; CI fails on drift.",
        "`enabled`, `exercised` and `measured` are runtime facts shown by `catchup_core health`, not claims made here.", "",
        "| ID | Capability | Scope | Code | Tests | Acceptance |", "|---|---|---|---|---|---|"]
    for cap in CAPABILITIES:
        code, tests = static_status(root, cap)
        lines.append(f"| {cap.id} | {cap.title} | {cap.scope} | {'present' if code else 'absent'} | "
                     f"{'present' if tests else 'absent'} | {', '.join(cap.acceptance)} |")
    return "\n".join(lines) + "\n"


def manifest(root: Path, previous: Mapping[str, object] | None = None) -> dict[str, object]:
    """§12.12 artifact manifest: acceptance IDs mapped to modules and tests (drift-checked in CI).

    ``spec_sha256`` digests the local-only specification when it is present; a CI checkout does not
    have it, so the previous value is kept and never compared.
    """
    import hashlib  # noqa: PLC0415

    from catchup_core import CORE_CONTRACT_VERSION  # noqa: PLC0415
    spec = root / "specs" / "re-artha.md"
    digest = hashlib.sha256(spec.read_bytes()).hexdigest() if spec.is_file() else (previous or {}).get("spec_sha256", "")
    paths = sorted({path for capability in CAPABILITIES for path in (*capability.modules, *capability.tests)})
    implementation = "\n".join(path + ":" + hashlib.sha256((root / path).read_bytes()).hexdigest()
                                for path in paths if (root / path).is_file())
    return {
        "spec": "specs/re-artha.md", "spec_version": "0.8.0", "spec_sha256": digest,
        "contract_version": CORE_CONTRACT_VERSION,
        "implementation_sha256": hashlib.sha256(implementation.encode()).hexdigest(),
        "acceptance": {a: {"capability": c.id, "scope": c.scope, "modules": list(c.modules), "tests": list(c.tests)}
                       for c in CAPABILITIES for a in c.acceptance},
    }


def runtime_states(root: Path, config: Mapping[str, object], db: sqlite3.Connection | None,
                   release_ok: bool, *, extractor_configured: bool) -> list[dict[str, str]]:
    """Per capability: disabled | not configured | unknown | last succeeded | degraded | not implemented."""
    evidence: dict[str, str] = {}
    if db is not None:
        with suppress(sqlite3.Error):
            evidence = dict(db.execute("SELECT capability,state FROM capability_evidence ORDER BY seq"))
    domains = config.get("domains") or []
    out: list[dict[str, str]] = []
    for cap in CAPABILITIES:
        code, _tests = static_status(root, cap)
        if not code:
            state = "not implemented"
        elif cap.scope == "later":
            state = "disabled"
        elif not domains or (not extractor_configured and cap.id in ("RA-02", "RA-07")):
            state = "not configured"
        else:
            state = evidence.get(cap.id, "unknown")
        reviewed = config.get("reviewed_capabilities", [])
        reviewed = reviewed if isinstance(reviewed, list) else []
        out.append({"id": cap.id, "state": state,
                    "measured": "yes" if release_ok and cap.id in reviewed and state == "last succeeded" else "no"})
    return out


#: PRD feature-group disposition (re-artha 11.4): a release decision, not a claim that each FR is implemented.
FR_DISPOSITION: tuple[tuple[str, str, str, str], ...] = (
    ("FR-1 Communications; FR-8 Calendar", "pilot", "RA-01, RA-08, RA-13", "Connector and coverage substrate plus two cross-Domain cases"),
    ("FR-2 Immigration; FR-3 Finance; FR-4 Kids/School", "pilot (Kids/School only)", "RA-02, RA-03, RA-14", "Each other Domain needs its own pack, review and release gate"),
    ("FR-5 Travel; FR-6 Health; FR-7 Home; FR-9 Shopping; FR-10 Learning; FR-11 Relationships; FR-12 Digital", "incumbent, labelled by provenance", "RA-23", "No blanket 24-Domain migration; inferred relationship profiles retired (D-14)"),
    ("FR-13 Goals; FR-14 Work-Life Boundary", "pilot (minimum Goal observation)", "RA-11, RA-13", "Forecasts and Work aggregates are destination milestones"),
    ("FR-15 Artha OS Core incl. ambient intent", "coexist", "RA-04, RA-10", "Keep/change/retire decisions per legacy step"),
    ("FR-16 Insurance; FR-17 Vehicle; FR-18 Estate; FR-20 Readiness; FR-21 Logistics; FR-22 Tribe; FR-23 Capital", "incumbent", "-", "No new autonomous effects; expand only after gates"),
    ("FR-19 Work OS; FR-31; FR-32; FR-42", "separate Work authority", "-", "Stateless code reuse or approved aggregate only"),
    ("FR-25 Career Search", "separate personal authority", "RA-12", "Employer-derived items cross only by explicit minimized export"),
    ("FR-24 Home bridge; FR-28 Hermes", "post-commit projection", "RA-14", "Versioned allowlisted adapter; no authority"),
    ("FR-26 Channels; FR-27 Briefing archive", "channels retired; archive kept", "RA-04, RA-09", "CLI/VS Code sessions only; one logical Briefing ID"),
    ("FR-29 DataCopilot quality; FR-30 Agent SRE observability", "reuse measured checks", "RA-10, RA-18", "Redacted traces; placeholder gates replaced"),
)


def render_traceability(root: Path) -> str:
    """Requirement-to-evidence table: every RA row with its acceptance IDs, code and tests, then the PRD map."""
    lines = ["# Traceability register (generated \u2014 do not edit)", "",
             "Generated by `tools/gen_capability_register.py`; CI fails on drift. Statuses are runtime facts (`health`).", "",
             "## Requirements to evidence", "", "| Requirement | Scope | Acceptance | Code | Tests |", "|---|---|---|---|---|"]
    for cap in CAPABILITIES:
        code, tests = static_status(root, cap)
        lines.append(f"| {cap.id} {cap.title} | {cap.scope} | {', '.join(cap.acceptance)} | "
                     f"{'<br>'.join(cap.modules) or '-'}{'' if code or not cap.modules else ' (missing)'} | "
                     f"{'<br>'.join(cap.tests) or '-'}{'' if tests or not cap.tests else ' (missing)'} |")
    lines += ["", "## PRD feature groups", "", "| FR group | Disposition | Requirements | Note |", "|---|---|---|---|"]
    lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in FR_DISPOSITION]
    return "\n".join(lines) + "\n"
