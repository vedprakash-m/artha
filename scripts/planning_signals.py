#!/usr/bin/env python3
"""Deterministic FR-41 Ambient Intent Buffer helpers.

This module is the code fallback for ``config/Artha.md`` Step 8t. It keeps
planning-signal reads, validation, seeding, offer selection, skip handling, and
materialization idempotent so repeated agent/tool calls cannot duplicate state.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

ARTHA_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = ARTHA_DIR / "state"
SIGNALS_FILE = STATE_DIR / "planning_signals.md"
SCENARIOS_FILE = STATE_DIR / "scenarios.md"
DECISIONS_FILE = STATE_DIR / "decisions.md"
GOALS_FILE = STATE_DIR / "goals.md"
OPEN_ITEMS_FILE = STATE_DIR / "open_items.md"
AUDIT_FILE = STATE_DIR / "audit.md"

ARCHETYPE_THRESHOLDS = {
    "deadline": 1,
    "opportunity": 2,
    "pattern": 3,
    "conflict": 2,
    "goal_drift": 1,
}
VALID_CANDIDATES = {"scenario", "decision", "sprint"}
EVIDENCE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}: [^—\n]{1,32} — .{1,60}$")
GOAL_DRIFT_IDS = {"G-004", "G-005"}


class PlanningSignalError(RuntimeError):
    """Raised when validation or materialization cannot proceed safely."""


@dataclass
class SignalDocument:
    frontmatter: dict[str, Any]
    body: str

    @property
    def signals(self) -> list[dict[str, Any]]:
        signals = self.frontmatter.setdefault("signals", [])
        if not isinstance(signals, list):
            raise PlanningSignalError("planning_signals.md frontmatter signals must be a list")
        return signals


SEED_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "SIG-001",
        "entity_key": "kia_telluride_replacement",
        "text": "Kia Telluride reliability and replacement consideration",
        "domain": "vehicle",
        "archetype": "opportunity",
        "first_detected": "2026-03-15",
        "last_seen": "2026-04-22",
        "detection_count": 3,
        "materialized": False,
        "materialization_threshold": 2,
        "evidence": [
            "2026-03-15: vehicle.md — Kia Telluride warranty expiring",
            "2026-04-10: email — Kia service reminder for 45k mile check",
            "2026-04-22: email — KBB value alert for 2022 Telluride",
        ],
        "candidate_type": "scenario",
        "candidate_title": "Kia Telluride: Replace vs. Extend vs. Trade",
        "skip_count": 0,
    },
    {
        "id": "SIG-002",
        "entity_key": "eb1a_self_petition_filing",
        "text": "EB-1A extraordinary ability filing opportunity",
        "domain": "immigration",
        "archetype": "opportunity",
        "first_detected": "2026-04-29",
        "last_seen": "2026-04-29",
        "detection_count": 1,
        "materialized": False,
        "materialization_threshold": 2,
        "evidence": [
            "2026-04-29: email — ChatEB1 purchase, self-petition tool",
        ],
        "candidate_type": "decision",
        "candidate_title": "EB-1A Self-Petition: File Now vs. Wait",
        "skip_count": 0,
    },
    {
        "id": "SIG-003",
        "entity_key": "trisha_academic_recovery",
        "text": "Trisha academic performance multi-subject struggle pattern",
        "domain": "kids",
        "archetype": "pattern",
        "first_detected": "2026-04-20",
        "last_seen": "2026-04-29",
        "detection_count": 2,
        "materialized": False,
        "materialization_threshold": 3,
        "evidence": [
            "2026-04-20: email — Science 7 ED5 missing assignment",
            "2026-04-29: email — Science 7 ED6 missing, History D",
        ],
        "candidate_type": "scenario",
        "candidate_title": "Trisha Academic Recovery: Prioritize Support Plan",
        "skip_count": 0,
    },
]


def _today() -> str:
    return date.today().isoformat()


def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    fm_text = content[3:end].strip()
    body = content[end + 4 :]
    loaded = yaml.safe_load(fm_text) or {}
    if not isinstance(loaded, dict):
        raise PlanningSignalError("YAML frontmatter must be a mapping")
    return loaded, body


def _assemble(frontmatter: dict[str, Any], body: str) -> str:
    text = yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{text}---\n{body.lstrip()}"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _safe_load_file(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    return _split_frontmatter(path.read_text(encoding="utf-8"))


def _audit(action: str, signal_id: str, result: str, audit_file: Path | None = None) -> None:
    audit_file = audit_file or AUDIT_FILE
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.open("a", encoding="utf-8").write(
        f"{datetime.now().isoformat(timespec='seconds')} | Step 8t | {action} | {signal_id} | {result}\n"
    )


def _snapshot(path: Path) -> Path | None:
    if not path.exists():
        return None
    script = ARTHA_DIR / "scripts" / "backup.py"
    if script.exists() and path.is_relative_to(ARTHA_DIR):
        result = subprocess.run(
            [sys.executable, str(script), "file-snapshot", str(path)],
            cwd=ARTHA_DIR,
            text=True,
            capture_output=True,
            env={**os.environ, "ARTHA_NO_REEXEC": "1"},
        )
        if result.returncode != 0:
            raise PlanningSignalError(result.stderr.strip() or result.stdout.strip())
        return Path(str(path) + ".pre-write.bak")
    bak = Path(str(path) + ".pre-write.bak")
    shutil.copy2(path, bak)
    return bak


def load(path: Path = SIGNALS_FILE) -> SignalDocument:
    if not path.exists():
        fm = {
            "schema_version": 1,
            "domain": "planning_signals",
            "last_updated": _today(),
            "sensitivity": "medium",
            "encrypted": False,
            "next_signal_id": "SIG-001",
            "token_estimate": 0,
            "signals": [],
            "archive": [],
        }
        return SignalDocument(fm, "# Planning Signals\n")

    fm, body = _safe_load_file(path)
    # Accept the draft spec's body-level YAML shape, then normalize to
    # frontmatter so existing Artha state tooling can always load it.
    if "signals" not in fm and body.strip():
        try:
            body_yaml = yaml.safe_load(body) or {}
        except yaml.YAMLError:
            body_yaml = {}
        if isinstance(body_yaml, dict) and isinstance(body_yaml.get("signals"), list):
            fm["signals"] = body_yaml["signals"]
            body = "# Planning Signals\n"
    fm.setdefault("schema_version", 1)
    fm.setdefault("domain", "planning_signals")
    fm.setdefault("sensitivity", "medium")
    fm.setdefault("encrypted", False)
    fm.setdefault("last_updated", _today())
    fm.setdefault("next_signal_id", _next_signal_id(fm.get("signals", [])))
    fm.setdefault("token_estimate", 0)
    fm.setdefault("signals", [])
    fm.setdefault("archive", [])
    return SignalDocument(fm, body or "# Planning Signals\n")


def validate(doc: SignalDocument) -> list[str]:
    errors: list[str] = []
    signals = doc.signals
    active = [s for s in signals if not s.get("materialized")]
    if len(active) > 15:
        errors.append("more than 15 active non-materialized planning signals")

    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    for idx, signal in enumerate(signals):
        prefix = f"signals[{idx}]"
        for field in (
            "id", "entity_key", "text", "domain", "archetype", "first_detected",
            "last_seen", "detection_count", "materialized",
            "materialization_threshold", "evidence", "candidate_type",
            "candidate_title",
        ):
            if field not in signal:
                errors.append(f"{prefix}.{field} missing")
        sid = str(signal.get("id", ""))
        if not re.fullmatch(r"SIG-\d{3}", sid):
            errors.append(f"{prefix}.id must match SIG-NNN")
        if sid in seen_ids:
            errors.append(f"{prefix}.id duplicate: {sid}")
        seen_ids.add(sid)
        key = (str(signal.get("domain", "")), str(signal.get("entity_key", "")))
        if key in seen_keys:
            errors.append(f"{prefix}.entity_key duplicate in domain: {key}")
        seen_keys.add(key)
        archetype = signal.get("archetype")
        if archetype not in ARCHETYPE_THRESHOLDS:
            errors.append(f"{prefix}.archetype invalid: {archetype}")
        candidate = signal.get("candidate_type")
        if candidate not in VALID_CANDIDATES:
            errors.append(f"{prefix}.candidate_type invalid: {candidate}")
        if archetype == "deadline" and not signal.get("deadline_date"):
            errors.append(f"{prefix}.deadline_date required for deadline archetype")
        evidence = signal.get("evidence", [])
        if not isinstance(evidence, list):
            errors.append(f"{prefix}.evidence must be a list")
            continue
        if len(evidence) > 5:
            errors.append(f"{prefix}.evidence exceeds 5 entries")
        for ev in evidence:
            if not isinstance(ev, str) or len(ev) > 80 or not EVIDENCE_RE.match(ev):
                errors.append(f"{prefix}.evidence invalid canonical entry: {ev!r}")
    return errors


def normalize_evidence(evidence: str, observed_on: str | None = None, source: str = "state") -> str:
    """Return canonical evidence format without preserving raw source text."""
    if EVIDENCE_RE.match(evidence) and len(evidence) <= 80:
        return evidence
    day = observed_on or _today()
    summary = re.sub(r"\s+", " ", evidence).strip()
    summary = summary.replace("—", "-")
    if len(summary) > 60:
        summary = summary[:57].rstrip() + "..."
    src = re.sub(r"[^A-Za-z0-9_.-]", "", source)[:32] or "state"
    entry = f"{day}: {src} — {summary}"
    if len(entry) > 80:
        entry = entry[:77].rstrip() + "..."
    return entry


def save(doc: SignalDocument, path: Path = SIGNALS_FILE) -> None:
    errors = validate(doc)
    if errors:
        raise PlanningSignalError("; ".join(errors))
    content = _assemble(doc.frontmatter, doc.body)
    doc.frontmatter["token_estimate"] = _scan_token_estimate(doc.signals)
    doc.frontmatter["last_updated"] = _today()
    content = _assemble(doc.frontmatter, doc.body)
    yaml.safe_load(content.split("---", 2)[1])
    _atomic_write(path, content)


def archive_old_materialized(doc: SignalDocument, today: date | None = None) -> int:
    """Move materialized signals older than 90 days into the archive list."""
    today = today or date.today()
    archive = doc.frontmatter.setdefault("archive", [])
    if not isinstance(archive, list):
        raise PlanningSignalError("planning_signals.md archive must be a list")
    retained: list[dict[str, Any]] = []
    moved = 0
    for signal in doc.signals:
        materialized_on = _parse_date(signal.get("materialization_date"))
        if signal.get("materialized") and materialized_on and (today - materialized_on).days > 90:
            archive.append(signal)
            _audit("signal_archived", str(signal.get("id")), "older_than_90d")
            moved += 1
        else:
            retained.append(signal)
    doc.frontmatter["signals"] = retained
    return moved


def _scan_token_estimate(signals: list[dict[str, Any]]) -> int:
    """Estimate briefing-time Step 8t scan cost, not full file storage size."""
    scan_lines = []
    for signal in signals:
        if signal.get("materialized"):
            continue
        threshold = signal.get("materialization_threshold") or signal.get("threshold")
        scan_lines.append(
            f"{signal.get('id')} {signal.get('domain')} {signal.get('candidate_type')} "
            f"{signal.get('candidate_title')} {signal.get('detection_count')}/{threshold}"
        )
    return max(1, len("\n".join(scan_lines)) // 4)


def _next_signal_id(signals: list[dict[str, Any]]) -> str:
    highest = 0
    for signal in signals:
        match = re.fullmatch(r"SIG-(\d{3})", str(signal.get("id", "")))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"SIG-{highest + 1:03d}"


def seed(path: Path = SIGNALS_FILE) -> int:
    doc = load(path)
    by_key = {(s.get("domain"), s.get("entity_key")): s for s in doc.signals}
    created = 0
    for seed_signal in SEED_SIGNALS:
        key = (seed_signal["domain"], seed_signal["entity_key"])
        if key in by_key:
            existing = by_key[key]
            for field, value in seed_signal.items():
                existing.setdefault(field, deepcopy(value))
        else:
            doc.signals.append(deepcopy(seed_signal))
            _audit("signal_created", seed_signal["id"], "seeded")
            created += 1
    doc.frontmatter["next_signal_id"] = _next_signal_id(doc.signals)
    archive_old_materialized(doc)
    save(doc, path)
    return created


def observe(
    *,
    domain: str,
    entity_key: str,
    text: str,
    archetype: str,
    candidate_type: str,
    candidate_title: str,
    evidence: str,
    source: str = "state",
    observed_on: str | None = None,
    deadline_date: str | None = None,
    path: Path = SIGNALS_FILE,
) -> str:
    """Create or update one signal by exact ``domain + entity_key``.

    This is Step 8t's deterministic fallback for new evidence. It increments
    ``detection_count`` only when a new canonical evidence entry is appended.
    """
    if archetype not in ARCHETYPE_THRESHOLDS:
        raise PlanningSignalError(f"invalid archetype: {archetype}")
    if candidate_type not in VALID_CANDIDATES:
        raise PlanningSignalError(f"invalid candidate_type: {candidate_type}")
    if archetype == "deadline":
        deadline = _parse_date(deadline_date)
        now = date.fromisoformat(observed_on or _today())
        if not deadline or deadline < now or deadline > now + timedelta(days=60):
            raise PlanningSignalError("deadline signals require deadline_date within 60 days")

    doc = load(path)
    entry = normalize_evidence(evidence, observed_on, source)
    key = (domain, entity_key)
    signal = next((s for s in doc.signals if (s.get("domain"), s.get("entity_key")) == key), None)
    if signal is None:
        signal_id = str(doc.frontmatter.get("next_signal_id") or _next_signal_id(doc.signals))
        signal = {
            "id": signal_id,
            "entity_key": entity_key,
            "text": text,
            "domain": domain,
            "archetype": archetype,
            "first_detected": observed_on or _today(),
            "last_seen": observed_on or _today(),
            "detection_count": 1,
            "materialized": False,
            "materialization_threshold": ARCHETYPE_THRESHOLDS[archetype],
            "evidence": [entry],
            "candidate_type": candidate_type,
            "candidate_title": candidate_title,
            "skip_count": 0,
        }
        if deadline_date:
            signal["deadline_date"] = deadline_date
        doc.signals.append(signal)
        doc.frontmatter["next_signal_id"] = _next_signal_id(doc.signals)
        _audit("signal_created", signal_id, "observed")
    else:
        signal_id = str(signal["id"])
        evidence_list = signal.setdefault("evidence", [])
        if entry not in evidence_list:
            evidence_list.append(entry)
            signal["evidence"] = evidence_list[-5:]
            signal["detection_count"] = int(signal.get("detection_count") or 0) + 1
            signal["last_seen"] = observed_on or _today()
            _audit("evidence_appended", signal_id, "ok")
    archive_old_materialized(doc)
    save(doc, path)
    return str(signal["id"])


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _threshold_met(signal: dict[str, Any], today: date) -> bool:
    if signal.get("materialized"):
        return False
    snoozed = _parse_date(signal.get("snoozed_until"))
    if snoozed and snoozed > today:
        return False
    if signal.get("archetype") == "deadline":
        deadline = _parse_date(signal.get("deadline_date"))
        if not deadline or deadline < today or deadline > today + timedelta(days=60):
            return False
    threshold = int(signal.get("materialization_threshold") or ARCHETYPE_THRESHOLDS[signal["archetype"]])
    return int(signal.get("detection_count") or 0) >= threshold


def _signal_exists_in_target(signal: dict[str, Any], target: Path) -> bool:
    if not target.exists():
        return False
    fm, _body = _safe_load_file(target)
    sig_id = signal.get("id")
    title = str(signal.get("candidate_title", "")).casefold()
    for key in ("scenarios", "decision_links", "sprints"):
        for item in fm.get(key, []) or []:
            if item.get("signal_ref") == sig_id:
                return True
            if title and str(item.get("title", "")).casefold() == title:
                return True
    return False


def ready_offers(path: Path = SIGNALS_FILE, limit: int = 1) -> list[dict[str, Any]]:
    doc = load(path)
    today = date.today()
    candidates = [s for s in doc.signals if _threshold_met(s, today)]
    target_by_type = {"scenario": SCENARIOS_FILE, "decision": DECISIONS_FILE, "sprint": GOALS_FILE}
    filtered = [
        s for s in candidates
        if not _signal_exists_in_target(s, target_by_type[str(s.get("candidate_type"))])
    ]
    filtered.sort(key=lambda s: (-int(s.get("detection_count") or 0), str(s.get("first_detected") or "")))
    for signal in filtered[:limit]:
        _audit("threshold_evaluated", str(signal["id"]), "ready")
        _audit("materialization_offered", str(signal["id"]), "ok")
    return deepcopy(filtered[:limit])


def _load_target(path: Path, domain: str, default_key: str) -> tuple[dict[str, Any], str]:
    fm, body = _safe_load_file(path)
    fm.setdefault("schema_version", "1.0")
    fm.setdefault("domain", domain)
    fm.setdefault("last_updated", _today())
    fm.setdefault("sensitivity", "medium")
    fm.setdefault("encrypted", False)
    fm.setdefault(default_key, [])
    if not isinstance(fm[default_key], list):
        raise PlanningSignalError(f"{path.name}.{default_key} must be a list")
    return fm, body


def _write_validated_target(path: Path, fm: dict[str, Any], body: str) -> None:
    backup = _snapshot(path)
    content = _assemble(fm, body)
    try:
        yaml.safe_load(content.split("---", 2)[1])
        _atomic_write(path, content)
        _safe_load_file(path)
    except Exception as exc:
        if backup and backup.exists():
            shutil.copy2(backup, path)
        raise PlanningSignalError(f"post-write validation failed for {path.name}: {exc}") from exc


def _next_oi_id(content: str) -> str:
    highest = 0
    for match in re.finditer(r"\bOI-(\d{3})\b", content):
        highest = max(highest, int(match.group(1)))
    return f"OI-{highest + 1:03d}"


def _open_item_has_source_ref(content: str, source_ref: str) -> bool:
    blocks = re.split(r"\n(?=- id: OI-)", content)
    for block in blocks:
        if f"source_ref: {source_ref}" in block and re.search(r"^\s*status:\s*open\s*$", block, re.MULTILINE):
            return True
    return False


def _append_open_item(open_items_file: Path, item: dict[str, Any]) -> str:
    content = open_items_file.read_text(encoding="utf-8") if open_items_file.exists() else (
        "---\ndomain: open_items\nlast_updated: ''\nschema_version: '1.0'\n---\n\n# Open Items\n\n## Active\n"
    )
    if _open_item_has_source_ref(content, str(item["source_ref"])):
        return "duplicate"
    item_id = _next_oi_id(content)
    lines = [
        "",
        f"- id: {item_id}",
        f"  date_added: {_today()}",
        f"  source_domain: {item['source_domain']}",
        f"  description: \"{item['description']}\"",
        f"  deadline: {item.get('deadline', '')}",
        f"  priority: {item.get('priority', 'P2')}",
        "  status: open",
        "  todo_id: \"\"",
        "  origin: system",
        f"  source_ref: {item['source_ref']}",
        "",
    ]
    backup = _snapshot(open_items_file)
    try:
        new_content = content.rstrip() + "\n" + "\n".join(lines)
        _atomic_write(open_items_file, new_content)
        if "source_ref:" not in open_items_file.read_text(encoding="utf-8"):
            raise PlanningSignalError("open_items post-write validation failed")
        return item_id
    except Exception:
        if backup and backup.exists():
            shutil.copy2(backup, open_items_file)
        raise


def _scenario_recommendation(scenario: dict[str, Any], signal: dict[str, Any] | None) -> dict[str, Any] | None:
    title = str(scenario.get("title", "")).casefold()
    if "kia" in title:
        return {
            "source_domain": "vehicle",
            "description": (
                f"{scenario['id']} - review Kia replacement scenario paths: replace, "
                "extend warranty, or keep/monitor. Gather current mileage, warranty status, "
                "trade-in value, and repair risk before choosing a path."
            ),
            "deadline": "",
            "priority": "P2",
            "source_ref": scenario["id"],
        }
    if signal and signal.get("domain") == "kids":
        return {
            "source_domain": "kids",
            "description": (
                f"{scenario['id']} - review academic recovery scenario and choose support path "
                "with Trisha: teacher conference, tutoring, or structured home plan."
            ),
            "deadline": "",
            "priority": "P1",
            "source_ref": scenario["id"],
        }
    return None


def evaluate_scenarios(
    *,
    scenarios_file: Path = SCENARIOS_FILE,
    signals_file: Path = SIGNALS_FILE,
    open_items_file: Path = OPEN_ITEMS_FILE,
    write: bool = False,
) -> list[dict[str, Any]]:
    """Evaluate active scenarios and optionally create deduped OIs.

    Implements specs/scenarios.md Step 8e/T-8: skip if evaluated <7 days ago
    and no newer signal evidence exists; otherwise produce at most one OI per
    scenario with ``source_ref: SCN-NNN``.
    """
    scenarios_fm, scenarios_body = _load_target(scenarios_file, "scenarios", "scenarios")
    signals = {s.get("id"): s for s in load(signals_file).signals}
    open_content = open_items_file.read_text(encoding="utf-8") if open_items_file.exists() else ""
    results: list[dict[str, Any]] = []
    changed = False
    today = date.today()
    for scenario in scenarios_fm.get("scenarios", []) or []:
        if scenario.get("status") not in {"active", "watching"}:
            continue
        signal = signals.get(scenario.get("signal_ref"))
        last_eval = _parse_date(scenario.get("last_evaluated"))
        last_seen = _parse_date(signal.get("last_seen")) if signal else None
        if last_eval and (today - last_eval).days < 7 and (not last_seen or last_seen <= last_eval):
            results.append({"scenario_id": scenario["id"], "status": "skipped_recent"})
            continue
        recommendation = _scenario_recommendation(scenario, signal)
        if recommendation is None:
            results.append({"scenario_id": scenario["id"], "status": "no_recommendation"})
            scenario["last_evaluated"] = _today()
            changed = True
            continue
        if _open_item_has_source_ref(open_content, str(scenario["id"])):
            results.append({"scenario_id": scenario["id"], "status": "duplicate_oi"})
            scenario["last_evaluated"] = _today()
            changed = True
            continue
        if write:
            item_id = _append_open_item(open_items_file, recommendation)
            open_content = open_items_file.read_text(encoding="utf-8")
            results.append({"scenario_id": scenario["id"], "status": "oi_created", "item_id": item_id})
            _audit("scenario_evaluated", str(scenario["id"]), f"oi_created:{item_id}")
        else:
            results.append({"scenario_id": scenario["id"], "status": "would_create_oi", "item": recommendation})
        scenario["last_evaluated"] = _today()
        changed = True
    if write and changed:
        scenarios_fm["last_updated"] = _today()
        _write_validated_target(scenarios_file, scenarios_fm, scenarios_body)
    return results


def _scenario_paths(signal: dict[str, Any]) -> list[dict[str, Any]]:
    title = str(signal.get("candidate_title", "")).casefold()
    if "kia" in title:
        labels = ["A — Replace", "B — Extend warranty", "C — Keep and monitor"]
    elif "academic" in title:
        labels = ["A — Parent-teacher conference", "B — Tutoring support", "C — Structured home plan"]
    else:
        labels = ["A — Act now", "B — Stage and monitor", "C — Defer"]
    return [{"label": label, "pros": [], "cons": [], "probability": None} for label in labels]


def _materialize_scenario(signal: dict[str, Any], scenarios_file: Path) -> str:
    fm, body = _load_target(scenarios_file, "scenarios", "scenarios")
    for scenario in fm["scenarios"]:
        if scenario.get("signal_ref") == signal["id"]:
            return str(scenario["id"])
    next_num = 1
    for scenario in fm["scenarios"]:
        match = re.fullmatch(r"SCN-(\d{3})", str(scenario.get("id", "")))
        if match:
            next_num = max(next_num, int(match.group(1)) + 1)
    scenario_id = f"SCN-{next_num:03d}"
    evidence = "; ".join(signal.get("evidence", [])[:2])
    fm["scenarios"].append({
        "id": scenario_id,
        "title": signal["candidate_title"],
        "domain": signal["domain"],
        "status": "active",
        "created": _today(),
        "signal_ref": signal["id"],
        "signal_archetype": signal["archetype"],
        "description": f"Materialized from repeated planning signals. Evidence: {evidence}",
        "trigger": f"New evidence for {signal['entity_key']}",
        "paths": _scenario_paths(signal),
        "recommended_path": None,
        "decision_by": None,
        "last_evaluated": _today(),
    })
    fm["last_updated"] = _today()
    _write_validated_target(scenarios_file, fm, body)
    return scenario_id


def _materialize_decision(signal: dict[str, Any], decisions_file: Path) -> str:
    fm, body = _load_target(decisions_file, "decisions", "decision_links")
    for link in fm["decision_links"]:
        if link.get("signal_ref") == signal["id"]:
            return str(link["id"])
    next_num = 1
    for link in fm["decision_links"]:
        match = re.fullmatch(r"DEC-LINK-(\d{3})", str(link.get("id", "")))
        if match:
            next_num = max(next_num, int(match.group(1)) + 1)
    decision_id = f"DEC-LINK-{next_num:03d}"
    existing_title = _match_markdown_decision(body, str(signal.get("candidate_title", "")))
    fm["decision_links"].append({
        "id": decision_id,
        "title": signal["candidate_title"],
        "domain": signal["domain"],
        "signal_ref": signal["id"],
        "scenario_ref": None,
        "scenario_analysis_date": _today(),
        "status": "active",
        "existing_decision_title": existing_title,
    })
    fm["last_updated"] = _today()
    _write_validated_target(decisions_file, fm, body)
    return decision_id


def _match_markdown_decision(body: str, candidate_title: str) -> str | None:
    """Best-effort link to an existing Markdown table decision row."""
    candidate_words = {
        w for w in re.findall(r"[a-z0-9]+", candidate_title.casefold())
        if len(w) >= 3 and w not in {"the", "and", "for", "now", "wait"}
    }
    best: tuple[int, str] = (0, "")
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or cells[0].casefold() == "decision":
            continue
        row_words = set(re.findall(r"[a-z0-9]+", cells[0].casefold()))
        score = len(candidate_words & row_words)
        if score > best[0]:
            best = (score, cells[0])
    return best[1] if best[0] >= 1 else None


def _materialize_sprint(signal: dict[str, Any], goals_file: Path) -> str:
    fm, _body = _safe_load_file(goals_file)
    for sprint in fm.get("sprints", []) or []:
        if sprint.get("signal_ref") == signal["id"]:
            return str(sprint["id"])
    goal_ref = signal.get("goal_ref")
    goals = [g for g in fm.get("goals", []) or [] if g.get("status") != "parked" and g.get("target_date")]
    if goal_ref:
        goals = [g for g in goals if g.get("id") == goal_ref]
    if not goals:
        raise PlanningSignalError("no active goal with target_date available for sprint")
    goals.sort(key=lambda g: str(g.get("target_date")))
    goal = goals[0]
    start = date.today()
    end = start + timedelta(days=14)
    target_date = _parse_date(goal.get("target_date"))
    if target_date and end > target_date:
        raise PlanningSignalError(f"sprint end {end} is after goal target_date {target_date}")
    next_num = 1
    for sprint in fm.get("sprints", []) or []:
        match = re.fullmatch(r"SPR-(\d{3})", str(sprint.get("id", "")))
        if match:
            next_num = max(next_num, int(match.group(1)) + 1)
    sprint_id = f"SPR-{next_num:03d}"
    result = subprocess.run(
        [
            sys.executable, str(ARTHA_DIR / "scripts" / "goals_writer.py"),
            "--file", str(goals_file), "--add-sprint",
            "--id", sprint_id,
            "--goal-ref", str(goal["id"]),
            "--start", start.isoformat(),
            "--end", end.isoformat(),
            "--target", f"14-day re-engagement: {goal.get('next_action') or goal['title']}",
            "--signal-ref", str(signal["id"]),
        ],
        cwd=ARTHA_DIR,
        text=True,
        capture_output=True,
        env={**os.environ, "ARTHA_NO_REEXEC": "1"},
    )
    if result.returncode != 0:
        raise PlanningSignalError(result.stderr.strip() or result.stdout.strip())
    _safe_load_file(goals_file)
    return sprint_id


def bootstrap_sprint_signal(
    *,
    goals_file: Path = GOALS_FILE,
    signals_file: Path = SIGNALS_FILE,
    today: date | None = None,
) -> str | None:
    """Create one goal-drift sprint candidate from eligible goals.

    Eligibility implements specs/scenarios.md Step 3/19b:
    - active, non-parked goals only
    - G-004/G-005 priority set for no-sprint >30d detection
    - no active sprint for the same goal
    - sprint end must not exceed goal target date
    """
    today = today or date.today()
    fm, _body = _safe_load_file(goals_file)
    goals = fm.get("goals", []) or []
    sprints = fm.get("sprints", []) or []
    active_goal_refs = {s.get("goal_ref") for s in sprints if s.get("status") == "active"}
    candidates: list[dict[str, Any]] = []
    for goal in goals:
        goal_id = str(goal.get("id") or "")
        if goal.get("status") == "parked" or goal_id in active_goal_refs:
            continue
        if goal_id not in GOAL_DRIFT_IDS:
            continue
        target = _parse_date(goal.get("target_date"))
        if not target or today + timedelta(days=14) > target:
            continue
        last_progress = _parse_date(goal.get("last_progress") or goal.get("created"))
        if not last_progress or (today - last_progress).days < 30:
            continue
        candidates.append(goal)
    if not candidates:
        return None
    candidates.sort(key=lambda g: str(g.get("target_date")))
    goal = candidates[0]
    signal_id = observe(
        domain=str(goal.get("category") or "goals"),
        entity_key=f"{str(goal['id']).lower()}_goal_drift",
        text=f"{goal['title']} has drifted without sprint activity",
        archetype="goal_drift",
        candidate_type="sprint",
        candidate_title=f"{goal['title']}: 14-day re-engagement sprint",
        evidence=f"Goal stale since {goal.get('last_progress')}",
        source="goals.md",
        observed_on=today.isoformat(),
        path=signals_file,
    )
    doc = load(signals_file)
    signal = next((s for s in doc.signals if s.get("id") == signal_id), None)
    if signal is not None:
        signal["goal_ref"] = goal["id"]
        save(doc, signals_file)
    return signal_id


def sprint_triggers(goals_file: Path = GOALS_FILE, signals_file: Path = SIGNALS_FILE) -> dict[str, Any]:
    """Return Step 3 sprint calibration/bootstrap flags."""
    today = date.today()
    fm, _body = _safe_load_file(goals_file)
    due: list[dict[str, Any]] = []
    for sprint in fm.get("sprints", []) or []:
        if sprint.get("status") != "active":
            continue
        start = _parse_date(sprint.get("start") or sprint.get("sprint_start"))
        if start and start + timedelta(days=14) == today:
            due.append(sprint)
    signal_id = bootstrap_sprint_signal(goals_file=goals_file, signals_file=signals_file, today=today)
    return {
        "sprint_calibration": bool(due),
        "calibration_sprints": due,
        "bootstrap_signal_id": signal_id,
    }


def materialize(
    signal_id: str,
    *,
    signals_file: Path = SIGNALS_FILE,
    scenarios_file: Path = SCENARIOS_FILE,
    decisions_file: Path = DECISIONS_FILE,
    goals_file: Path = GOALS_FILE,
) -> str:
    doc = load(signals_file)
    signal = next((s for s in doc.signals if s.get("id") == signal_id), None)
    if signal is None:
        raise PlanningSignalError(f"unknown signal id: {signal_id}")
    if signal.get("materialized"):
        return str(signal.get("materialized_ref") or signal_id)

    candidate = signal.get("candidate_type")
    if candidate == "scenario":
        ref = _materialize_scenario(signal, scenarios_file)
    elif candidate == "decision":
        ref = _materialize_decision(signal, decisions_file)
    elif candidate == "sprint":
        ref = _materialize_sprint(signal, goals_file)
    else:
        raise PlanningSignalError(f"unsupported candidate_type: {candidate}")

    signal["materialized"] = True
    signal["materialization_date"] = _today()
    signal["materialized_ref"] = ref
    save(doc, signals_file)
    _audit("materialization_written", signal_id, ref)
    return ref


def skip(signal_id: str, path: Path = SIGNALS_FILE) -> int:
    doc = load(path)
    signal = next((s for s in doc.signals if s.get("id") == signal_id), None)
    if signal is None:
        raise PlanningSignalError(f"unknown signal id: {signal_id}")
    signal["skip_count"] = int(signal.get("skip_count") or 0) + 1
    if signal["skip_count"] >= 3:
        signal["snoozed_until"] = (date.today() + timedelta(days=30)).isoformat()
        _audit("signal_snoozed", signal_id, "30d")
    _audit("materialization_skipped", signal_id, f"skip_count={signal['skip_count']}")
    save(doc, path)
    return int(signal["skip_count"])


def _cmd_validate(args: argparse.Namespace) -> int:
    errors = validate(load(Path(args.signals_file)))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("planning_signals.md valid")
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    created = seed(Path(args.signals_file))
    print(f"seeded {created} new planning signal(s)")
    return 0


def _cmd_offers(args: argparse.Namespace) -> int:
    offers = ready_offers(Path(args.signals_file), limit=args.limit)
    print(yaml.dump({"offers": offers}, allow_unicode=True, sort_keys=False))
    return 0


def _cmd_materialize(args: argparse.Namespace) -> int:
    ref = materialize(args.signal_id, signals_file=Path(args.signals_file))
    print(f"materialized {args.signal_id} -> {ref}")
    return 0


def _cmd_skip(args: argparse.Namespace) -> int:
    count = skip(args.signal_id, Path(args.signals_file))
    print(f"skipped {args.signal_id}; skip_count={count}")
    return 0


def _cmd_observe(args: argparse.Namespace) -> int:
    signal_id = observe(
        domain=args.domain,
        entity_key=args.entity_key,
        text=args.text,
        archetype=args.archetype,
        candidate_type=args.candidate_type,
        candidate_title=args.candidate_title,
        evidence=args.evidence,
        source=args.source,
        observed_on=args.observed_on,
        deadline_date=args.deadline_date,
        path=Path(args.signals_file),
    )
    print(f"observed {signal_id}")
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    doc = load(Path(args.signals_file))
    moved = archive_old_materialized(doc)
    save(doc, Path(args.signals_file))
    print(f"archived {moved} planning signal(s)")
    return 0


def _cmd_sprint_triggers(args: argparse.Namespace) -> int:
    result = sprint_triggers(Path(args.goals_file), Path(args.signals_file))
    print(yaml.dump(result, allow_unicode=True, sort_keys=False))
    return 0


def _cmd_evaluate_scenarios(args: argparse.Namespace) -> int:
    result = evaluate_scenarios(
        scenarios_file=Path(args.scenarios_file),
        signals_file=Path(args.signals_file),
        open_items_file=Path(args.open_items_file),
        write=args.write,
    )
    print(yaml.dump({"evaluations": result}, allow_unicode=True, sort_keys=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FR-41 Ambient Intent Buffer")
    parser.add_argument("--signals-file", default=str(SIGNALS_FILE))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate").set_defaults(func=_cmd_validate)
    sub.add_parser("seed").set_defaults(func=_cmd_seed)
    offers_p = sub.add_parser("offers")
    offers_p.add_argument("--limit", type=int, default=1)
    offers_p.set_defaults(func=_cmd_offers)
    mat_p = sub.add_parser("materialize")
    mat_p.add_argument("signal_id")
    mat_p.set_defaults(func=_cmd_materialize)
    skip_p = sub.add_parser("skip")
    skip_p.add_argument("signal_id")
    skip_p.set_defaults(func=_cmd_skip)
    observe_p = sub.add_parser("observe")
    observe_p.add_argument("--domain", required=True)
    observe_p.add_argument("--entity-key", required=True)
    observe_p.add_argument("--text", required=True)
    observe_p.add_argument("--archetype", required=True, choices=list(ARCHETYPE_THRESHOLDS))
    observe_p.add_argument("--candidate-type", required=True, choices=list(VALID_CANDIDATES))
    observe_p.add_argument("--candidate-title", required=True)
    observe_p.add_argument("--evidence", required=True)
    observe_p.add_argument("--source", default="state")
    observe_p.add_argument("--observed-on")
    observe_p.add_argument("--deadline-date")
    observe_p.set_defaults(func=_cmd_observe)
    sub.add_parser("archive").set_defaults(func=_cmd_archive)
    sprint_p = sub.add_parser("sprint-triggers")
    sprint_p.add_argument("--goals-file", default=str(GOALS_FILE))
    sprint_p.set_defaults(func=_cmd_sprint_triggers)
    eval_p = sub.add_parser("evaluate-scenarios")
    eval_p.add_argument("--scenarios-file", default=str(SCENARIOS_FILE))
    eval_p.add_argument("--open-items-file", default=str(OPEN_ITEMS_FILE))
    eval_p.add_argument("--write", action="store_true")
    eval_p.set_defaults(func=_cmd_evaluate_scenarios)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except PlanningSignalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
