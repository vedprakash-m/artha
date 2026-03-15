"""
audit_compliance.py — Post-catch-up compliance audit.

Parses a briefing file and checks for workflow step compliance signals.
Outputs a ComplianceReport as JSON.

Usage:
    python scripts/audit_compliance.py briefings/2026-03-15.md
    python scripts/audit_compliance.py briefings/2026-03-15.md --json
    python scripts/audit_compliance.py briefings/2026-03-15.md | python scripts/eval_runner.py --ingest-compliance
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    weight: int
    detail: str = ""


@dataclass
class ComplianceReport:
    briefing_path: str
    compliance_score: int          # 0–100
    degraded_mode: bool
    checks: list[CheckResult] = field(default_factory=list)
    non_compliant_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Flatten checks for readability
        d["checks"] = [
            {"name": c["name"], "passed": c["passed"], "weight": c["weight"], "detail": c["detail"]}
            for c in d["checks"]
        ]
        return d


# ---------------------------------------------------------------------------
# Compliance checks
# (Each returns a CheckResult.  Weight is its contribution out of 100.)
# ---------------------------------------------------------------------------

def _check_preflight_executed(text: str) -> CheckResult:
    """Step 0 — preflight.py ran and result surfaced in briefing."""
    signals = [
        r"preflight",
        r"pre-?flight",
        r"\bGO\b.*\bstep\s*0\b",
        r"all P0 checks pass",
        r"ADVISORY MODE",
        r"advisory.*preflight",
        r"preflight.*advisory",
        r"preflight.*pass",
        r"✅.*preflight",
        r"⚠️.*ADVISORY",
    ]
    matched = any(re.search(p, text, re.IGNORECASE) for p in signals)
    return CheckResult(
        name="preflight_executed",
        passed=matched,
        weight=20,
        detail="Preflight result found in briefing." if matched
               else "No preflight result detected — Step 0 may have been skipped.",
    )


def _check_connector_health_block(text: str) -> CheckResult:
    """Finalize mandate — Connector & Token Health block."""
    signals = [
        r"Connector\s*&?\s*Token\s*Health",
        r"connector.*health",
        r"\|\s*Connector\s*\|",     # table row with Connector column
        r"connectors? offline",
    ]
    matched = any(re.search(p, text, re.IGNORECASE) for p in signals)
    return CheckResult(
        name="connector_health_block_present",
        passed=matched,
        weight=25,
        detail="Connector & Token Health block found." if matched
               else "Missing Connector & Token Health block — required in every briefing.",
    )


def _check_state_files_referenced(text: str) -> CheckResult:
    """Step 4b — Tier A state files loaded and referenced."""
    required_files = [
        "health-check",
        "open_items",
        "memory",
    ]
    found = [f for f in required_files if f.replace("_", "[-_]") and
             re.search(f.replace("_", r"[-_]"), text, re.IGNORECASE)]
    passed = len(found) >= 2
    return CheckResult(
        name="state_files_referenced",
        passed=passed,
        weight=15,
        detail=f"State files referenced: {found}." if passed
               else f"Only {len(found)}/3 required state file references found ({found}). "
                    "Briefing may lack state context.",
    )


def _check_pii_footer(text: str) -> CheckResult:
    """Step 5 / Step 17 — PII scan stats in footer."""
    signals = [
        r"emails?_scanned",
        r"redactions?_applied",
        r"pii.*scan",
        r"scan.*pii",
        r"pii.*filter",
        r"redaction.*stats",
    ]
    matched = any(re.search(p, text, re.IGNORECASE) for p in signals)
    return CheckResult(
        name="pii_footer_present",
        passed=matched,
        weight=15,
        detail="PII scan stats found in briefing." if matched
               else "PII scan stats missing — pii_guard.py output wasn't surfaced.",
    )


def _check_no_unacknowledged_snippets(text: str) -> CheckResult:
    """Step 5 — full email bodies read (snippet warnings properly flagged)."""
    # A raw "snippet" mention without the required acknowledgement tag is a violation
    raw_snippet = re.search(r"\bsnippet\b", text, re.IGNORECASE)
    acknowledged = re.search(r"\[snippet\s*[—\-]\s*verify", text, re.IGNORECASE)

    if raw_snippet and not acknowledged:
        return CheckResult(
            name="email_bodies_not_snippets",
            passed=False,
            weight=10,
            detail="'snippet' appears without acknowledgement tag — "
                   "full email body may not have been read (PROHIBITED per process.md).",
        )
    return CheckResult(
        name="email_bodies_not_snippets",
        passed=True,
        weight=10,
        detail="No unacknowledged snippet references detected.",
    )


def _check_domain_sections_present(text: str) -> CheckResult:
    """Steps 6–7 — domain-based sections in briefing."""
    domain_keywords = [
        "finance", "immigration", "health", "calendar", "comms",
        "goals", "kids", "travel", "home", "vehicle", "learning",
        "estate", "insurance", "employment",
    ]
    found = [d for d in domain_keywords if re.search(r"\b" + d + r"\b", text, re.IGNORECASE)]
    passed = len(found) >= 2
    return CheckResult(
        name="domain_sections_present",
        passed=passed,
        weight=10,
        detail=f"Domains referenced: {found}." if passed
               else f"Only {len(found)} domain(s) mentioned — insufficient domain coverage.",
    )


def _check_one_thing_present(text: str) -> CheckResult:
    """Step 8a / Step 11 — ONE THING block present."""
    signals = [
        r"ONE THING",
        r"one thing",
        r"U×I×A",
        r"URGENCY.*IMPACT.*AGENCY",
    ]
    matched = any(re.search(p, text, re.IGNORECASE) for p in signals)
    return CheckResult(
        name="one_thing_present",
        passed=matched,
        weight=5,
        detail="ONE THING block found." if matched
               else "ONE THING block missing — top-priority item not surfaced.",
    )


def _check_ooda_protocol(text: str) -> CheckResult:
    """Step 8 — OODA protocol markers present in cross-domain reasoning output."""
    markers = ["[OBSERVE]", "[ORIENT]", "[DECIDE]", "[ACT]"]
    found = [m for m in markers if m in text]
    # Pass if at least 3 of 4 markers present (allow 1 partial miss)
    passed = len(found) >= 3
    missing = [m for m in markers if m not in text]
    return CheckResult(
        name="ooda_protocol_followed",
        passed=passed,
        weight=10,
        detail=f"OODA markers found: {found}." if passed
               else f"OODA protocol incomplete — found: {found}, missing: {missing}. "
                    "Cross-domain reasoning should produce [OBSERVE], [ORIENT], [DECIDE], [ACT] blocks.",
    )


def _check_memory_capacity(artha_dir: Path | None = None) -> CheckResult:
    """AR-1 — memory.md is within bounded capacity limits (≤30 facts, ≤3000 chars).

    Reads state/memory.md directly (not the briefing text) to verify the
    memory file has not grown beyond the AR-1 dual-limit thresholds.
    Weight: 5 — informational health check, not a briefing compliance issue.
    """
    base = artha_dir or Path(__file__).resolve().parents[1]
    memory_path = base / "state" / "memory.md"
    if not memory_path.exists():
        return CheckResult(
            name="memory_capacity_within_limits",
            passed=True,   # no memory file = trivially within limits
            weight=5,
            detail="state/memory.md not found — capacity check skipped (no memory yet).",
        )
    try:
        content = memory_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return CheckResult(
            name="memory_capacity_within_limits",
            passed=False,
            weight=5,
            detail=f"Could not read state/memory.md: {exc}",
        )
    char_count = len(content)
    # Count fact entries: lines starting with "- " or YAML list entries
    fact_lines = [ln for ln in content.splitlines() if re.match(r"^\s*-\s+\w", ln)]
    fact_count = len(fact_lines)

    MAX_CHARS = 3000
    MAX_FACTS = 30
    violations = []
    if char_count > MAX_CHARS:
        violations.append(f"char_count={char_count} > {MAX_CHARS}")
    if fact_count > MAX_FACTS:
        violations.append(f"fact_count={fact_count} > {MAX_FACTS}")

    passed = not violations
    return CheckResult(
        name="memory_capacity_within_limits",
        passed=passed,
        weight=5,
        detail=f"memory.md OK: {char_count} chars, ~{fact_count} facts." if passed
               else f"memory.md over limit: {'; '.join(violations)}. "
                    "Run fact_extractor.py to consolidate (AR-1).",
    )


def _check_prompt_stability(artha_dir: Path | None = None) -> CheckResult:
    """AR-6 — config/Artha.md has the prompt-stability marker (frozen layer).

    The stability marker is added by generate_identity.py and confirms the
    system prompt has not been modified mid-session.
    Weight: 5 — advisory check for prompt hygiene.
    """
    base = artha_dir or Path(__file__).resolve().parents[1]
    artha_md = base / "config" / "Artha.md"
    if not artha_md.exists():
        return CheckResult(
            name="prompt_stability_marker_present",
            passed=True,   # advisory: no marker required if file absent
            weight=5,
            detail="config/Artha.md not found — prompt stability check skipped (advisory).",
        )
    try:
        header = artha_md.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError as exc:
        return CheckResult(
            name="prompt_stability_marker_present",
            passed=True,   # can't determine → advisory pass
            weight=5,
            detail=f"Could not read config/Artha.md: {exc} — check skipped (advisory).",
        )
    has_marker = "PROMPT STABILITY" in header or "AUTO-GENERATED" in header
    return CheckResult(
        name="prompt_stability_marker_present",
        passed=has_marker,
        weight=5,
        detail="Prompt stability marker found in config/Artha.md." if has_marker
               else "Prompt stability marker missing — config/Artha.md may have been manually edited. "
                    "Re-run generate_identity.py (AR-6).",
    )


# ---------------------------------------------------------------------------
# Degraded-mode detection
# ---------------------------------------------------------------------------

def _detect_degraded_mode(text: str) -> tuple[bool, dict]:
    """Return (is_degraded, metadata_dict) extracted from Session Metadata footer."""
    session_meta_match = re.search(
        r"##\s*Session Metadata\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL | re.IGNORECASE
    )
    if session_meta_match:
        meta_text = session_meta_match.group(1)
        mode_match = re.search(r"mode:\s*(\S+)", meta_text, re.IGNORECASE)
        mode = mode_match.group(1) if mode_match else "unknown"

        env_match = re.search(r"environment:\s*(\S+)", meta_text, re.IGNORECASE)
        env = env_match.group(1) if env_match else "unknown"

        files_match = re.search(r"state_files_read:\s*(\S+)", meta_text, re.IGNORECASE)
        state_files_read = files_match.group(1) if files_match else "unknown"

        return mode in ("read-only", "degraded", "offline"), {
            "environment": env,
            "mode": mode,
            "state_files_read": state_files_read,
        }

    # Fallback: check for read-only header
    if re.search(r"READ-ONLY MODE", text, re.IGNORECASE):
        return True, {"mode": "read-only", "source": "header"}

    # Fallback: check for degraded/offline signals
    if re.search(r"connector.*offline|degraded.*briefing|offline.*briefing", text, re.IGNORECASE):
        return True, {"mode": "degraded", "source": "inline-signals"}

    return False, {}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _compute_score(checks: list[CheckResult], degraded_mode: bool) -> int:
    """Weighted score.  In degraded mode, connector_health_block is weighted less
    (it's harder to surface in read-only — though still required)."""
    earned = 0
    total = sum(c.weight for c in checks)
    for c in checks:
        if c.passed:
            earned += c.weight
    if total == 0:
        return 0
    raw = int(round(earned / total * 100))
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def audit_latest_briefing(briefing_path: str) -> ComplianceReport:
    """Parse a briefing file and return a ComplianceReport."""
    path = Path(briefing_path)
    if not path.exists():
        return ComplianceReport(
            briefing_path=briefing_path,
            compliance_score=0,
            degraded_mode=False,
            warnings=[f"Briefing file not found: {briefing_path}"],
        )

    text = path.read_text(encoding="utf-8", errors="replace")
    artha_dir = path.resolve().parents[1]  # briefings/ is one level below artha root

    # Run all checks
    checks: list[CheckResult] = [
        _check_preflight_executed(text),
        _check_connector_health_block(text),
        _check_state_files_referenced(text),
        _check_pii_footer(text),
        _check_no_unacknowledged_snippets(text),
        _check_domain_sections_present(text),
        _check_one_thing_present(text),
        _check_ooda_protocol(text),
        _check_memory_capacity(artha_dir),
        _check_prompt_stability(artha_dir),
    ]

    degraded_mode, metadata = _detect_degraded_mode(text)

    # In degraded mode, drop the weight of connector_health_block from 25→15
    # (harder to include in read-only; still required, but penalise less)
    if degraded_mode:
        for c in checks:
            if c.name == "connector_health_block_present":
                c.weight = 15

    score = _compute_score(checks, degraded_mode)
    non_compliant = [c.name for c in checks if not c.passed]

    warnings: list[str] = []
    if score < 60:
        warnings.append(f"Compliance score {score} is below the minimum threshold (60).")
    elif score < 80 and not degraded_mode:
        warnings.append(f"Compliance score {score} is below target (80) for local catch-ups.")

    return ComplianceReport(
        briefing_path=str(path),
        compliance_score=score,
        degraded_mode=degraded_mode,
        checks=checks,
        non_compliant_steps=non_compliant,
        warnings=warnings,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit a catch-up briefing for workflow compliance.",
    )
    parser.add_argument("briefing", help="Path to a briefing .md file")
    parser.add_argument(
        "--json", dest="output_json", action="store_true",
        help="Output raw JSON (default when stdout is non-TTY)",
    )
    parser.add_argument(
        "--threshold", type=int, default=None,
        help="Exit 1 if compliance_score < threshold",
    )
    args = parser.parse_args(argv)

    report = audit_latest_briefing(args.briefing)
    output_json = args.output_json or not sys.stdout.isatty()

    if output_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_human(report)

    if args.threshold is not None and report.compliance_score < args.threshold:
        return 1
    return 0


def _print_human(report: ComplianceReport) -> None:
    """Human-readable table output."""
    mode_tag = " [DEGRADED]" if report.degraded_mode else ""
    print(f"\n{'='*60}")
    print(f"Compliance Report: {report.briefing_path}{mode_tag}")
    print(f"Score: {report.compliance_score}/100")
    print(f"{'='*60}")
    for c in report.checks:
        status = "✅" if c.passed else "❌"
        print(f"  {status} [{c.weight:2d}pt] {c.name}")
        if not c.passed:
            print(f"         {c.detail}")
    if report.warnings:
        print()
        for w in report.warnings:
            print(f"  ⚠️  {w}")
    if report.non_compliant_steps:
        print(f"\n  Non-compliant: {', '.join(report.non_compliant_steps)}")
    if report.metadata:
        print(f"\n  Metadata: {report.metadata}")
    print()


if __name__ == "__main__":
    sys.exit(main())
