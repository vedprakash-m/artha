#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""scripts/kb_lint.py — Cross-domain data health linter for Artha state files.

KB-LINT scans state/*.md files for schema issues, staleness, TODO fields,
date validity, cross-domain reference gaps, and open-items integrity.

Architecture: extends dq_gate.py primitives. Adopts the CheckResult pattern
from preflight.py. Uses parse_frontmatter from lib/common.py.

Usage:
    python scripts/kb_lint.py                 — full lint (P1–P6)
    python scripts/kb_lint.py finance         — single domain
    python scripts/kb_lint.py --fix           — lint + propose corrections
    python scripts/kb_lint.py --brief-mode    — P1–P3 only, one-line, always exit 0
    python scripts/kb_lint.py --init          — add frontmatter skeleton to missing files
    python scripts/kb_lint.py --json          — machine-readable JSON output

Spec: specs/kb.md v4
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.common import STATE_DIR, CONFIG_DIR, ARTHA_DIR, parse_frontmatter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TODO_PATTERN = re.compile(r"\bTODO\b|\bTBD\b|\bPLACEHOLDER\b", re.IGNORECASE)
_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_ACTION_DATE_CONTEXT_PATTERN = re.compile(
    r"(?:appointment|deadline|due|expires?|expiry|renewal|review)\s*[:\|]?\s*\**\s*(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
# Valid ISO-8601 timestamp patterns (accept both datetime and date-only)
_ISO_DATETIME_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
_PLACEHOLDER_VALUES: frozenset[Any] = frozenset({"", "TODO", "TBD", "PLACEHOLDER", "—", None})
_REQUIRED_FM_FIELDS: frozenset[str] = frozenset({"schema_version", "last_updated", "sensitivity"})
_VALID_SENSITIVITIES: frozenset[str] = frozenset({"high", "medium", "standard", "low", "elevated", "reference"})

# Staleness fallback TTLs by sensitivity (days) when domain_registry entry has no value
_SENSITIVITY_TTL_DEFAULTS: dict[str, int] = {
    "high": 90,
    "medium": 90,
    "elevated": 90,
    "standard": 180,
    "low": 180,
    "reference": 365,
}
_DEFAULT_TTL = 180  # final fallback

BOOTSTRAP_THRESHOLD_PCT = 50  # trigger bootstrap mode when ≥50% of P1 files have errors

# Cross-domain coherence rules (formerly config/lint_rules.yaml — Initiative 8 Phase 2)
_EMBEDDED_LINT_RULES: list[dict[str, Any]] = [
    {"id": "xref-immigration-travel", "domain_a": "immigration", "field_a": "visa_expiry", "domain_b": "travel", "field_b": "passport_expiry", "message": "Immigration visa_expiry set but travel.passport_expiry is missing", "severity": "WARNING"},
    {"id": "xref-insurance-finance", "domain_a": "insurance", "field_a": "premium_monthly", "domain_b": "finance", "field_b": "monthly_budget", "message": "Insurance premiums tracked but finance.monthly_budget is missing", "severity": "WARNING"},
    {"id": "xref-kids-health", "domain_a": "kids", "field_a": "children", "domain_b": "health", "field_b": "last_updated", "message": "Kids domain active but health.last_updated is missing", "severity": "INFO"},
    {"id": "xref-employment-insurance", "domain_a": "employment", "field_a": "employer", "domain_b": "insurance", "field_b": "health_plan", "message": "Employment.employer set but insurance.health_plan is missing", "severity": "WARNING"},
    {"id": "xref-health-insurance", "domain_a": "health", "field_a": "primary_care_provider", "domain_b": "insurance", "field_b": "health_plan", "message": "Health.primary_care_provider set but insurance.health_plan is missing", "severity": "WARNING"},
    {"id": "xref-vehicle-insurance", "domain_a": "vehicle", "field_a": "vehicles", "domain_b": "insurance", "field_b": "auto_policy", "message": "Vehicle domain active but insurance.auto_policy is missing", "severity": "WARNING"},
    {"id": "xref-home-insurance", "domain_a": "home", "field_a": "address", "domain_b": "insurance", "field_b": "home_policy", "message": "Home.address set but insurance.home_policy is missing", "severity": "WARNING"},
    {"id": "xref-finance-items", "domain_a": "finance", "field_a": "monthly_budget", "domain_b": "decisions", "field_b": "last_updated", "message": "Finance.monthly_budget set but decisions state file has no last_updated", "severity": "INFO"},
]

# Frontmatter skeleton added by --init
_FM_SKELETON_TEMPLATE = """\
---
schema_version: "1.0"
domain: {domain}
last_updated: ""
sensitivity: {sensitivity}
updated_by: bootstrap
---
"""

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class LintFinding:
    severity: Severity
    domain: str
    file_name: str
    pass_id: str
    message: str
    fixable: bool = False
    fix_description: str = ""
    fix_data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "domain": self.domain,
            "file_name": self.file_name,
            "pass_id": self.pass_id,
            "message": self.message,
            "fixable": self.fixable,
            "fix_description": self.fix_description,
        }


@dataclass
class LintResult:
    files_scanned: int = 0
    duration_ms: float = 0.0
    findings: list[LintFinding] = field(default_factory=list)
    bootstrap_mode: bool = False
    bootstrap_count: int = 0

    @property
    def errors(self) -> list[LintFinding]:
        return [f for f in self.findings if f.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[LintFinding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]

    @property
    def infos(self) -> list[LintFinding]:
        return [f for f in self.findings if f.severity == Severity.INFO]

    @property
    def health_pct(self) -> int:
        """Percentage of scanned files with zero P1 errors.

        Spec §1.7: health_pct = round(100 * error_free / max(1, files_scanned))
        where error_free = files with zero *P1* ERROR findings.  WARNINGs and
        non-P1 errors (hypothetical future passes) do not reduce health_pct.
        """
        if self.files_scanned == 0:
            return 100
        p1_error_files = {
            f.file_name for f in self.findings
            if f.severity == Severity.ERROR and f.pass_id.startswith("P1")
        }
        error_free = self.files_scanned - len(p1_error_files)
        return round(100 * error_free / self.files_scanned)

    def per_domain(self) -> dict[str, dict[str, int]]:
        domains: dict[str, dict[str, int]] = {}
        for f in self.findings:
            if f.domain not in domains:
                domains[f.domain] = {"errors": 0, "warnings": 0, "info": 0}
            if f.severity == Severity.ERROR:
                domains[f.domain]["errors"] += 1
            elif f.severity == Severity.WARNING:
                domains[f.domain]["warnings"] += 1
            else:
                domains[f.domain]["info"] += 1
        return domains

    def as_dict(self) -> dict[str, Any]:
        return {
            "files_scanned": self.files_scanned,
            "duration_ms": round(self.duration_ms, 1),
            "health_pct": self.health_pct,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "infos": len(self.infos),
            "bootstrap_mode": self.bootstrap_mode,
            "bootstrap_count": self.bootstrap_count,
            "findings": [f.as_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Registry loader
# ---------------------------------------------------------------------------

def _load_domain_registry() -> dict[str, Any]:
    """Load config/domain_registry.yaml. Returns {} on failure."""
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        data = load_config("domain_registry", _config_dir=str(CONFIG_DIR))
        return data.get("domains", {}) if data else {}
    except Exception:
        return {}


def _load_lint_rules() -> list[dict[str, Any]]:
    """Return cross-domain coherence rules.

    Returns the 8 built-in rules embedded as _EMBEDDED_LINT_RULES (P5), merged
    with any user-defined rules in CONFIG_DIR/lint_rules.yaml (P6 custom rules).
    The external file is optional — if absent, only embedded rules are used.
    """
    rules = list(_EMBEDDED_LINT_RULES)
    external = CONFIG_DIR / "lint_rules.yaml"
    if external.exists():
        try:
            import yaml as _yaml
            data = _yaml.safe_load(external.read_text(encoding="utf-8")) or {}
            # Support both 'cross_domain_rules' (spec format) and 'rules' (legacy/test format)
            extra = data.get("cross_domain_rules") or data.get("rules") or []
            rules.extend(extra)
        except Exception:
            pass  # graceful degradation — embedded rules still returned
    return rules


# ---------------------------------------------------------------------------
# State file discovery
# ---------------------------------------------------------------------------

def _discover_state_files(
    state_dir: Path,
    domain_filter: str | None = None,
) -> list[tuple[Path, str]]:
    """Return list of (path, domain_name) for plaintext state files.

    Only processes ``state/*.md`` (not ``.md.age`` encrypted files, not work-*).
    The domain name is derived from the file stem (e.g. ``finance.md`` → ``finance``).
    If *domain_filter* is given, only that domain is returned.
    """
    if not state_dir.exists():
        return []
    results: list[tuple[Path, str]] = []
    for md_path in sorted(state_dir.glob("*.md")):
        domain = md_path.stem
        # Skip generated/internal files
        if domain.startswith(("health-check", "audit", "memory", "open_items",
                              "dashboard", "self_model", "learned")):
            continue
        if domain_filter and domain != domain_filter:
            continue
        results.append((md_path, domain))
    return results


# ---------------------------------------------------------------------------
# P1 — Schema validation
# ---------------------------------------------------------------------------

def _check_p1_schema(
    path: Path,
    fm: dict[str, Any],
    domain: str,
) -> list[LintFinding]:
    """P1: Validate required frontmatter fields exist and are non-empty."""
    findings: list[LintFinding] = []
    file_name = path.name

    # Missing frontmatter entirely
    if not fm:
        findings.append(LintFinding(
            severity=Severity.ERROR,
            domain=domain,
            file_name=file_name,
            pass_id="P1-no-frontmatter",
            message="No YAML frontmatter found (missing --- delimiters or empty)",
            fixable=True,
            fix_description="Add frontmatter skeleton with schema_version, last_updated, sensitivity",
            fix_data={"action": "add_frontmatter_skeleton"},
        ))
        return findings  # no point in checking further

    for required_field in sorted(_REQUIRED_FM_FIELDS):
        value = fm.get(required_field)
        if value is None:
            findings.append(LintFinding(
                severity=Severity.ERROR,
                domain=domain,
                file_name=file_name,
                pass_id=f"P1-missing-{required_field}",
                message=f"Missing required frontmatter field: {required_field}",
                fixable=True,
                fix_description=f"Add '{required_field}:' to frontmatter",
                fix_data={"field": required_field, "action": "add_field"},
            ))
        elif str(value).strip() in _PLACEHOLDER_VALUES:
            findings.append(LintFinding(
                severity=Severity.ERROR,
                domain=domain,
                file_name=file_name,
                pass_id=f"P1-empty-{required_field}",
                message=f"Required field '{required_field}' is empty or placeholder",
                fixable=(required_field == "sensitivity"),
                fix_description=(
                    f"Set '{required_field}' to a valid value"
                    if required_field != "sensitivity"
                    else f"Set sensitivity to: high | standard | reference"
                ),
                fix_data={"field": required_field, "action": "set_value"},
            ))
        elif required_field == "sensitivity" and str(value).lower() not in _VALID_SENSITIVITIES:
            findings.append(LintFinding(
                severity=Severity.WARNING,
                domain=domain,
                file_name=file_name,
                pass_id="P1-invalid-sensitivity",
                message=(
                    f"Invalid sensitivity value '{value}' — "
                    f"expected one of: {', '.join(sorted(_VALID_SENSITIVITIES))}"
                ),
                fixable=False,
            ))
        elif required_field == "last_updated":
            # Accept ISO-8601 datetime or date-only (YYYY-MM-DD)
            ts_str = str(value).strip()
            if ts_str and not _ISO_DATETIME_PATTERN.match(ts_str):
                findings.append(LintFinding(
                    severity=Severity.WARNING,
                    domain=domain,
                    file_name=file_name,
                    pass_id="P1-invalid-last-updated",
                    message=(
                        f"'last_updated' value '{ts_str}' is not a valid "
                        "ISO-8601 datetime or YYYY-MM-DD date"
                    ),
                    fixable=False,
                ))

    return findings


# ---------------------------------------------------------------------------
# P2 — Staleness check
# ---------------------------------------------------------------------------

def _parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO-8601 datetime or date-only string. Returns None on failure."""
    if not value:
        return None
    ts = str(value).strip()
    if not ts:
        return None
    # Try full ISO-8601
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass
    # Try date-only YYYY-MM-DD
    try:
        d = date.fromisoformat(ts[:10])
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _check_p2_staleness(
    path: Path,
    fm: dict[str, Any],
    domain: str,
    ttl_days: int,
) -> list[LintFinding]:
    """P2: Flag files not updated within their staleness TTL."""
    findings: list[LintFinding] = []
    file_name = path.name
    last_updated = fm.get("last_updated")
    dt = _parse_timestamp(last_updated)

    if dt is None:
        # Already flagged by P1 (empty/missing last_updated); skip silent duplicate
        return findings

    now = datetime.now(timezone.utc)
    age_days = (now - dt).total_seconds() / 86400

    if age_days > ttl_days:
        findings.append(LintFinding(
            severity=Severity.WARNING,
            domain=domain,
            file_name=file_name,
            pass_id="P2-stale",
            message=(
                f"State file is stale ({int(age_days)}d old, TTL={ttl_days}d) — "
                f"last_updated: {last_updated}"
            ),
            fixable=False,
        ))

    return findings


# ---------------------------------------------------------------------------
# P3 — TODO / placeholder audit
# ---------------------------------------------------------------------------

def _check_p3_todos(path: Path, domain: str) -> list[LintFinding]:
    """P3: Scan the entire file body for TODO/TBD/PLACEHOLDER markers."""
    findings: list[LintFinding] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    for i, line in enumerate(text.splitlines(), start=1):
        if _TODO_PATTERN.search(line):
            # Limit message to first 120 chars of the line
            snippet = line.strip()[:120]
            findings.append(LintFinding(
                severity=Severity.WARNING,
                domain=domain,
                file_name=path.name,
                pass_id="P3-todo",
                message=f"Line {i}: unresolved marker — {repr(snippet)}",
                fixable=False,
            ))
            # Report at most 5 TODO findings per file to avoid noise
            if len(findings) >= 5:
                break

    return findings


# ---------------------------------------------------------------------------
# P4 — Past-date action items
# ---------------------------------------------------------------------------

def _check_p4_dates(path: Path, domain: str) -> list[LintFinding]:
    """P4: Flag action-context dates that are in the past."""
    findings: list[LintFinding] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    today = date.today()
    seen_dates: set[str] = set()

    for match in _ACTION_DATE_CONTEXT_PATTERN.finditer(text):
        date_str = match.group(1)
        if date_str in seen_dates:
            continue
        seen_dates.add(date_str)
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            continue
        if d < today:
            context_start = max(0, match.start() - 30)
            context_snippet = text[context_start: match.end()][:80].strip()
            findings.append(LintFinding(
                severity=Severity.WARNING,
                domain=domain,
                file_name=path.name,
                pass_id="P4-past-date",
                message=f"Action-context date {date_str} is in the past — context: {repr(context_snippet)}",
                fixable=False,
            ))
            if len(findings) >= 5:
                break

    return findings


# ---------------------------------------------------------------------------
# P5 — Cross-domain reference checker
# ---------------------------------------------------------------------------

def _check_p5_xrefs(
    domain_fms: dict[str, dict[str, Any]],
    rules: list[dict[str, Any]],
) -> list[LintFinding]:
    """P5: Validate cross-domain coherence rules.

    For each rule, if domain_a.field_a is present and non-empty,
    domain_b.field_b must also be present and non-empty.
    """
    findings: list[LintFinding] = []

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        rule_id = rule.get("id", "xref-unknown")
        domain_a = rule["domain_a"]
        field_a = rule["field_a"]
        domain_b = rule["domain_b"]
        field_b = rule["field_b"]
        message = rule["message"]
        sev_str = str(rule.get("severity", "WARNING")).upper()
        try:
            sev = Severity(sev_str)
        except ValueError:
            sev = Severity.WARNING

        fm_a = domain_fms.get(domain_a, {})
        fm_b = domain_fms.get(domain_b, {})

        # Only fire if domain_a has field_a set
        val_a = fm_a.get(field_a)
        if val_a is None or str(val_a).strip() in _PLACEHOLDER_VALUES:
            continue

        # domain_a has the field — check domain_b
        val_b = fm_b.get(field_b)
        if val_b is None or str(val_b).strip() in _PLACEHOLDER_VALUES:
            findings.append(LintFinding(
                severity=sev,
                domain=domain_b,
                file_name=f"{domain_b}.md",
                pass_id=rule_id,
                message=message,
                fixable=False,
            ))

    return findings


# ---------------------------------------------------------------------------
# P6 — Open items validator
# ---------------------------------------------------------------------------

def _check_p6_open_items(
    state_dir: Path,
    known_domains: set[str],
) -> list[LintFinding]:
    """P6: Scan open_items.md for items referencing unknown domains."""
    findings: list[LintFinding] = []
    oi_path = state_dir / "open_items.md"
    if not oi_path.exists():
        return findings

    try:
        text = oi_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    # Find source_domain references in YAML list items
    domain_ref_pattern = re.compile(r"source_domain:\s*(\S+)", re.IGNORECASE)
    for match in domain_ref_pattern.finditer(text):
        ref_domain = match.group(1).strip().lower().rstrip("\"'")
        if ref_domain and ref_domain not in known_domains:
            findings.append(LintFinding(
                severity=Severity.WARNING,
                domain="open_items",
                file_name="open_items.md",
                pass_id="P6-unknown-domain",
                message=f"open_items.md references unknown domain: '{ref_domain}'",
                fixable=False,
            ))

    return findings


# ---------------------------------------------------------------------------
# Core linter
# ---------------------------------------------------------------------------

def run_lint(
    state_dir: Path | None = None,
    domain_filter: str | None = None,
    passes: set[str] | None = None,
    brief_mode: bool = False,
) -> LintResult:
    """Run KB-LINT passes against state files. Returns a LintResult.

    Args:
        state_dir:     Path to the state/ directory (defaults to STATE_DIR).
        domain_filter: If set, only lint this domain.
        passes:        Set of pass IDs to run, e.g. {"P1", "P2"}.
                       None = run all passes appropriate for the mode.
        brief_mode:    If True, run P1–P3 only (for briefing hook).
    """
    _state_dir = state_dir or STATE_DIR
    start_time = time.monotonic()
    result = LintResult()

    if passes is None:
        passes = {"P1", "P2", "P3"} if brief_mode else {"P1", "P2", "P3", "P4", "P5", "P6"}

    registry = _load_domain_registry()
    lint_rules = _load_lint_rules() if "P5" in passes else []

    # Discover state files
    state_files = _discover_state_files(_state_dir, domain_filter)
    result.files_scanned = len(state_files)

    if result.files_scanned == 0:
        result.duration_ms = (time.monotonic() - start_time) * 1000
        return result

    # P1 first pass to check for bootstrap mode
    domain_fms: dict[str, dict[str, Any]] = {}
    p1_error_files: set[str] = set()

    for path, domain in state_files:
        fm = parse_frontmatter(path)
        domain_fms[domain] = fm
        if "P1" in passes:
            p1_findings = _check_p1_schema(path, fm, domain)
            if any(f.severity == Severity.ERROR for f in p1_findings):
                p1_error_files.add(path.name)
            result.findings.extend(p1_findings)

    # Bootstrap mode: ≥50% of files have P1 errors
    if result.files_scanned > 0:
        error_pct = len(p1_error_files) / result.files_scanned * 100
        if error_pct >= BOOTSTRAP_THRESHOLD_PCT:
            result.bootstrap_mode = True
            result.bootstrap_count = len(p1_error_files)
            # In bootstrap mode suppress individual P1 errors to reduce noise;
            # the caller will suggest --init instead
            result.findings = [
                f for f in result.findings if f.pass_id not in (
                    "P1-no-frontmatter", "P1-missing-schema_version",
                    "P1-missing-last_updated", "P1-missing-sensitivity",
                    "P1-empty-schema_version", "P1-empty-last_updated",
                    "P1-empty-sensitivity",
                )
            ]

    # P2–P6 passes
    for path, domain in state_files:
        fm = domain_fms[domain]

        # P2 — Staleness
        if "P2" in passes:
            domain_def = registry.get(domain, {})
            if isinstance(domain_def, dict):
                ttl = domain_def.get("staleness_ttl_days")
                if not isinstance(ttl, int) or ttl <= 0:
                    sens = str(fm.get("sensitivity", "standard")).lower()
                    ttl = _SENSITIVITY_TTL_DEFAULTS.get(sens, _DEFAULT_TTL)
            else:
                sens = str(fm.get("sensitivity", "standard")).lower()
                ttl = _SENSITIVITY_TTL_DEFAULTS.get(sens, _DEFAULT_TTL)
            result.findings.extend(_check_p2_staleness(path, fm, domain, ttl))

        # P3 — TODO audit
        if "P3" in passes:
            result.findings.extend(_check_p3_todos(path, domain))

        # P4 — Past dates
        if "P4" in passes:
            result.findings.extend(_check_p4_dates(path, domain))

    # P5 — Cross-reference checks (operates on all domain frontmatter at once)
    if "P5" in passes and lint_rules:
        result.findings.extend(_check_p5_xrefs(domain_fms, lint_rules))

    # P6 — Open items validator
    if "P6" in passes:
        known_domains = {domain for _, domain in state_files} | {
            "employment", "comms", "finance", "health", "immigration",
        }
        result.findings.extend(_check_p6_open_items(_state_dir, known_domains))

    result.duration_ms = (time.monotonic() - start_time) * 1000
    return result


# ---------------------------------------------------------------------------
# --init mode
# ---------------------------------------------------------------------------

def _run_init(state_dir: Path, registry: dict[str, Any]) -> None:
    """Add minimal frontmatter skeletons to state files that have none."""
    state_files = _discover_state_files(state_dir)
    patched = 0
    for path, domain in state_files:
        fm = parse_frontmatter(path)
        if fm:
            continue
        domain_def = registry.get(domain, {})
        sensitivity = "standard"
        if isinstance(domain_def, dict):
            sensitivity = domain_def.get("sensitivity", "standard")
        existing_content = ""
        try:
            existing_content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        new_content = _FM_SKELETON_TEMPLATE.format(
            domain=domain,
            sensitivity=sensitivity,
        ) + existing_content
        _atomic_write(path, new_content)
        print(f"  ✓ Added frontmatter skeleton to {path.name}")
        patched += 1
    if patched == 0:
        print("  All state files already have frontmatter — nothing to do.")
    else:
        print(f"\n  --init: patched {patched} file(s). Run kb_lint.py to re-check.")


# ---------------------------------------------------------------------------
# --fix mode
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via a temporary file."""
    parent = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=f".{path.stem}_lint_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_with_verify(path: Path, content: str, domain: str) -> str:
    """Write content atomically with P1–P2 pre-rename verification.

    Writes to a temp file, runs P1 schema check on it before renaming.
    Returns one of: 'ok', 'verify_failed', 'error'.

    Spec: specs/kb.md §1.5 step 6 — post-fix verification.
    """
    parent = path.parent
    fd, tmp_path_str = tempfile.mkstemp(
        dir=parent, prefix=f".{path.stem}_lint_", suffix=".tmp"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        # Post-fix P1 verification on the temp file to catch regressions
        tmp_fm = parse_frontmatter(tmp_path)
        p1_findings = _check_p1_schema(tmp_path, tmp_fm, domain)
        new_errors = [f for f in p1_findings if f.severity == Severity.ERROR]
        if new_errors:
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
            return "verify_failed"
        os.replace(tmp_path_str, path)
        return "ok"
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        return "error"


def _apply_fix(finding: LintFinding, state_dir: Path, registry: dict[str, Any]) -> str:
    """Attempt to apply a fix for a fixable finding.

    Returns a status string:
      'ok'            — fix applied successfully
      'blocked'       — WriteGuardMiddleware blocked the write (>20% field loss)
      'verify_failed' — post-fix P1 validation introduced new errors
      'no_action'     — no matching fix action for this finding type
      'error'         — unexpected failure

    Spec: specs/kb.md §1.5 steps 4+6.
    """
    path = state_dir / finding.file_name
    if not path.exists():
        return "error"

    fix_data = finding.fix_data
    action = fix_data.get("action", "")

    if action == "add_frontmatter_skeleton":
        domain = finding.domain
        domain_def = registry.get(domain, {})
        sensitivity = "standard"
        if isinstance(domain_def, dict):
            sensitivity = domain_def.get("sensitivity", "standard")
        try:
            current_content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "error"
        new_content = _FM_SKELETON_TEMPLATE.format(
            domain=domain,
            sensitivity=sensitivity,
        ) + current_content

        # Step 4 (spec §1.5): WriteGuardMiddleware field-loss check
        try:
            from middleware.write_guard import WriteGuardMiddleware  # noqa: PLC0415
            guard = WriteGuardMiddleware()
            if guard.before_write(domain, current_content, new_content) is None:
                return "blocked"
        except ImportError:
            pass  # write_guard unavailable on partial install — proceed

        # Steps 5+6 (spec §1.5): atomic write with post-fix P1 verification
        return _atomic_write_with_verify(path, new_content, domain)

    return "no_action"


def _run_fix(result: LintResult, state_dir: Path, registry: dict[str, Any]) -> None:
    """Interactive fix mode: propose each fixable finding and apply if approved."""
    fixable = [f for f in result.findings if f.fixable]
    if not fixable:
        print("No fixable findings.")
        return

    print(f"\n{len(fixable)} fixable finding(s) found.\n")
    applied = 0
    for i, finding in enumerate(fixable, start=1):
        print(f"  [{i}/{len(fixable)}] {finding.severity.value} — {finding.file_name}")
        print(f"       {finding.message}")
        print(f"       Fix: {finding.fix_description}")
        try:
            answer = input("  Apply fix? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            break
        if answer in ("y", "yes"):
            status = _apply_fix(finding, state_dir, registry)
            if status == "ok":
                print("       ✓ Applied.")
                applied += 1
            elif status == "blocked":
                print(
                    f"       ✗ Fix blocked: proposed change would reduce existing "
                    f"field count by >20% — skipping [{finding.domain}]"
                )
            elif status == "verify_failed":
                print(
                    "       ✗ Fix aborted: post-fix P1 validation found new errors "
                    "— original file untouched."
                )
            else:
                print("       ✗ Could not apply fix automatically.")
        else:
            print("       Skipped.")

    print(f"\n{applied}/{len(fixable)} fixes applied.")


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _format_brief(result: LintResult) -> str:
    """One-line summary for --brief-mode briefing hook.

    Spec §1.2.1: healthy → plain line; errors present → ⚠ prefix +
    actionable suffix.  No-error warnings are shown but do not escalate.
    """
    e = len(result.errors)
    w = len(result.warnings)
    if result.bootstrap_mode:
        return (
            f"Data Health: BOOTSTRAP ({result.bootstrap_count}/{result.files_scanned} files "
            "need --init)"
        )
    parts = []
    if e:
        parts.append(f"{e}E")
    if w:
        parts.append(f"{w}W")
    status = " ".join(parts) if parts else "OK"
    line = (
        f"Data Health: {result.health_pct}% "
        f"({result.files_scanned} files, {status}, "
        f"{result.duration_ms:.0f}ms)"
    )
    if e:
        line = f"⚠ {line} — run `lint` for details"
    return line


def _format_full(result: LintResult) -> str:
    """Human-readable full report."""
    lines: list[str] = []
    lines.append("")
    lines.append("╔══════════════════════════════════════════════╗")
    lines.append("║          Artha KB-LINT Report                ║")
    lines.append("╚══════════════════════════════════════════════╝")
    lines.append("")

    if result.bootstrap_mode:
        lines.append(
            f"⚠  BOOTSTRAP MODE — {result.bootstrap_count}/{result.files_scanned} files "
            "have missing frontmatter."
        )
        lines.append("   Run: python scripts/kb_lint.py --init  to add frontmatter skeletons.\n")

    lines.append(
        f"Files scanned : {result.files_scanned}   "
        f"Errors: {len(result.errors)}   "
        f"Warnings: {len(result.warnings)}   "
        f"Info: {len(result.infos)}"
    )
    lines.append(f"Data Health   : {result.health_pct}%   ({result.duration_ms:.0f}ms)")

    if result.findings:
        lines.append("")
        lines.append("── Findings ──────────────────────────────────")
        current_domain = ""
        for f in sorted(result.findings, key=lambda x: (x.domain, x.severity.value, x.pass_id)):
            if f.domain != current_domain:
                lines.append(f"\n  {f.domain}")
                current_domain = f.domain
            icon = "✖" if f.severity == Severity.ERROR else ("⚠" if f.severity == Severity.WARNING else "ℹ")
            lines.append(f"    {icon} [{f.pass_id}] {f.file_name}: {f.message}")
            if f.fixable:
                lines.append(f"      → Fix: {f.fix_description}")
    else:
        lines.append("\n  ✓ No findings — all scanned files pass lint.")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# lint_summary.yaml writer
# ---------------------------------------------------------------------------

def _write_lint_summary(result: LintResult, state_dir: Path) -> None:
    """Write state/lint_summary.yaml after each explicit lint run."""
    summary_path = state_dir / "lint_summary.yaml"
    now_iso = datetime.now(timezone.utc).isoformat()
    per_domain = result.per_domain()

    lines: list[str] = [
        "# Auto-generated by kb_lint.py — do not edit manually",
        f"last_run: \"{now_iso}\"",
        f"duration_ms: {round(result.duration_ms, 1)}",
        f"files_scanned: {result.files_scanned}",
        f"errors: {len(result.errors)}",
        f"warnings: {len(result.warnings)}",
        f"info: {len(result.infos)}",
        f"health_pct: {result.health_pct}",
        f"bootstrap_mode: {str(result.bootstrap_mode).lower()}",
        "per_domain:",
    ]
    for domain, counts in sorted(per_domain.items()):
        lines.append(f"  {domain}:")
        lines.append(f"    errors: {counts['errors']}")
        lines.append(f"    warnings: {counts['warnings']}")
    lines.append("")

    try:
        _atomic_write(summary_path, "\n".join(lines))
    except Exception as exc:
        print(f"kb_lint: WARN could not write lint_summary.yaml: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kb_lint",
        description="Artha KB-LINT — Cross-domain data health linter",
    )
    parser.add_argument(
        "domain",
        nargs="?",
        default=None,
        help="Lint a single domain (e.g. 'finance'). Omit to lint all domains.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Interactively propose and apply fixes for fixable findings.",
    )
    parser.add_argument(
        "--brief-mode",
        action="store_true",
        help=(
            "Run P1–P3 only; emit a single-line summary to stdout; always exit 0. "
            "Used by the briefing hook in Artha.core.md Step 20b."
        ),
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Add minimal frontmatter skeletons to state files that lack them.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of human-readable text.",
    )
    parser.add_argument(
        "--passes",
        default=None,
        help="Comma-separated pass IDs to run (default: all). E.g. 'P1,P2,P3'.",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Override the state directory path. Default: Artha state/.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    state_dir = Path(args.state_dir) if args.state_dir else STATE_DIR

    # --init
    if args.init:
        registry = _load_domain_registry()
        _run_init(state_dir, registry)
        return 0

    # Determine passes
    passes: set[str] | None = None
    if args.brief_mode:
        passes = {"P1", "P2", "P3"}
    elif args.passes:
        passes = {p.strip().upper() for p in args.passes.split(",") if p.strip()}

    # --brief-mode: catch all exceptions, always exit 0
    if args.brief_mode:
        try:
            result = run_lint(
                state_dir=state_dir,
                domain_filter=args.domain,
                passes=passes,
                brief_mode=True,
            )
            print(_format_brief(result))
        except Exception:
            print("Data Health: ⚠ lint error — run `lint` manually")
        return 0

    # Normal / --json / --fix modes
    result = run_lint(
        state_dir=state_dir,
        domain_filter=args.domain,
        passes=passes,
    )

    # Write observability summary (skip in single-domain or pass-filtered runs)
    if args.domain is None and passes is None:
        _write_lint_summary(result, state_dir)

    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(_format_full(result))

    if args.fix:
        registry = _load_domain_registry()
        _run_fix(result, state_dir, registry)

    # Exit code: 1 if any errors found; 0 if only warnings/info
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
