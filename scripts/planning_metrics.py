#!/usr/bin/env python3
"""planning_metrics.py — Phase 4 observability stub for FR-41 Ambient Intent Buffer.

Reports G-1 through G-6 metrics defined in specs/scenarios.md §3.

Usage:
    python3 scripts/planning_metrics.py [--signals-file PATH] [--audit-file PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_SIGNALS_FILE = _ROOT / "state" / "planning_signals.md"
_AUDIT_FILE = _ROOT / "state" / "audit.md"
_SCENARIOS_FILE = _ROOT / "state" / "scenarios.md"
_DECISIONS_FILE = _ROOT / "state" / "decisions.md"
_GOALS_FILE = _ROOT / "state" / "goals.md"


def _load_signals(path: Path) -> list[dict]:
    """Parse signals from planning_signals.md YAML frontmatter or body YAML."""
    if not path.exists():
        return []
    import yaml  # type: ignore[import-untyped]
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return []
    parts = text.split("---", 2)
    if len(parts) < 3:
        return []
    fm = yaml.safe_load(parts[1]) or {}
    if isinstance(fm.get("signals"), list):
        return fm["signals"]
    body = yaml.safe_load(parts[2]) or {}
    return body.get("signals", []) if isinstance(body, dict) else []


def _load_audit_rows(path: Path) -> list[str]:
    """Return non-header rows from audit.md table."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and "---" not in stripped and "date" not in stripped.lower():
            rows.append(stripped)
    return rows


def _load_frontmatter(path: Path) -> dict:
    if not path.exists():
        return {}
    import yaml  # type: ignore[import-untyped]
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    loaded = yaml.safe_load(parts[1]) or {}
    return loaded if isinstance(loaded, dict) else {}


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description="FR-41 planning metrics report")
    parser.add_argument("--signals-file", default=str(_SIGNALS_FILE))
    parser.add_argument("--audit-file", default=str(_AUDIT_FILE))
    parser.add_argument("--scenarios-file", default=str(_SCENARIOS_FILE))
    parser.add_argument("--decisions-file", default=str(_DECISIONS_FILE))
    parser.add_argument("--goals-file", default=str(_GOALS_FILE))
    args = parser.parse_args(argv)

    signals = _load_signals(Path(args.signals_file))
    audit_rows = _load_audit_rows(Path(args.audit_file))

    total = len(signals)
    materialized = sum(1 for s in signals if s.get("materialized"))
    pending = total - materialized
    skipped = sum(s.get("skip_count", 0) for s in signals)
    snoozed = sum(1 for s in signals if s.get("snoozed_until"))

    mat_events = [r for r in audit_rows if "materialization_written" in r.lower()]
    skip_events = [r for r in audit_rows if "materialization_skipped" in r.lower()]
    scenarios_fm = _load_frontmatter(Path(args.scenarios_file))
    decisions_fm = _load_frontmatter(Path(args.decisions_file))
    goals_fm = _load_frontmatter(Path(args.goals_file))
    scenarios = scenarios_fm.get("scenarios", []) or []
    decision_links = decisions_fm.get("decision_links", []) or []
    sprints = goals_fm.get("sprints", []) or []
    scenario_count = len(scenarios)
    sprint_count = len(sprints)
    decision_scenario_refs = sum(1 for d in decision_links if d.get("scenario_ref"))

    # G-5: false-positive proxy — signals that were skipped ≥3 times
    false_positives = sum(1 for s in signals if s.get("skip_count", 0) >= 3)
    token_estimate = max(0, path_tokens(Path(args.signals_file)))

    print("=== FR-41 Planning Metrics ===")
    print(f"G-1  Planning objects written    : {scenario_count + len(decision_links) + sprint_count}")
    print(f"G-2  Scenarios present           : {scenario_count}")
    print(f"G-3  Sprints present             : {sprint_count}")
    print(f"G-4  Decisions with scenario_ref : {decision_scenario_refs}")
    print(f"G-5  High-skip signals (>=3)     : {false_positives}  [false-positive proxy]")
    print(f"G-6  Planning signal tokens      : {token_estimate}  [target <200/session]")
    print(f"G-7  Graceful pending signals    : {pending} pending / {snoozed} snoozed")
    print()
    print(f"Signals — total/materialized     : {total}/{materialized}")
    print(f"Signals — skip events            : {skipped}")
    print(f"Audit rows — materialization     : {len(mat_events)}")
    print(f"Audit rows — skip events         : {len(skip_events)}")
    return 0


def path_tokens(path: Path) -> int:
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            import yaml  # type: ignore[import-untyped]
            fm = yaml.safe_load(parts[1]) or {}
            if isinstance(fm, dict) and isinstance(fm.get("token_estimate"), int):
                return fm["token_estimate"]
    return max(1, len(text) // 4)


if __name__ == "__main__":
    sys.exit(main())
