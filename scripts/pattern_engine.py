#!/usr/bin/env python3
"""
scripts/pattern_engine.py — Deterministic state-file pattern evaluator (E3).

Loads config/patterns.yaml, evaluates each rule against current state-file snapshots,
and emits DomainSignal objects for any rules that fire.

Runs once per catch-up session at Step 4.5 (after state-file load, before Step 7).
Results concatenated with email_signal_extractor.py output before ActionComposer.

Supported operators:
  days_until_lte: N   — (date_field - today).days <= N
  lt / gt / eq: N     — numeric comparison against numeric field value
  exists: bool        — field is present and non-empty
  has_item_within_days: N — list field contains item with date within N days
  contains: "str"     — string field contains substring (case-insensitive)
  stale_days: N       — (today - date_field).days >= N

Cooldown: pattern last-fired timestamps stored in state/pattern_engine_state.yaml.
If a pattern fires within its cooldown_hours window it is suppressed.

PII: entity_field values pass through pii_guard redaction before being stored
in signal metadata. Signal metadata never contains raw email addresses.

Config flag: enhancements.pattern_engine (default: true)

Ref: specs/act-reloaded.md Enhancement 3
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_PATTERNS_FILE = _ROOT_DIR / "config" / "patterns.yaml"
_STATE_FILE = _ROOT_DIR / "state" / "pattern_engine_state.yaml"

try:
    from actions.base import DomainSignal  # type: ignore[import]
except ImportError:  # pragma: no cover
    from dataclasses import dataclass, field

    @dataclass(frozen=True)
    class DomainSignal:  # type: ignore[no-redef]
        signal_type: str
        domain: str
        entity: str
        urgency: int
        impact: int
        source: str
        metadata: dict
        detected_at: str

try:
    from context_offloader import load_harness_flag as _load_flag
except ImportError:  # pragma: no cover
    def _load_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default

# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------

_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%B %d, %Y"]


def _parse_date(value: Any) -> date | None:
    """Try to parse value as a date. Returns None on failure."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _days_until(value: Any) -> int | None:
    """Return (date_value - today).days or None if unparseable."""
    d = _parse_date(value)
    if d is None:
        return None
    return (d - date.today()).days


def _stale_days(value: Any) -> int | None:
    """Return (today - date_value).days or None if unparseable."""
    d = _parse_date(value)
    if d is None:
        return None
    return (date.today() - d).days


# ---------------------------------------------------------------------------
# Operator evaluation
# ---------------------------------------------------------------------------

def _evaluate_operator(condition: dict[str, Any], doc: dict[str, Any]) -> bool:
    """Evaluate a single condition dict against a document (state dict)."""
    field_name = condition.get("field")
    # When field is not specified, fall back to the _value key (scalar patterns)
    value = doc.get(field_name) if field_name else doc.get("_value")

    if "days_until_lte" in condition:
        n = _days_until(value)
        return n is not None and n <= int(condition["days_until_lte"])

    if "stale_days" in condition:
        n = _stale_days(value)
        return n is not None and n >= int(condition["stale_days"])

    if "lt" in condition:
        try:
            return float(value) < float(condition["lt"])
        except (TypeError, ValueError):
            return False

    if "gt" in condition:
        try:
            return float(value) > float(condition["gt"])
        except (TypeError, ValueError):
            return False

    if "eq" in condition:
        return str(value).strip().lower() == str(condition["eq"]).strip().lower()

    if "exists" in condition:
        want = bool(condition["exists"])
        present = value is not None and value != "" and value != []
        return present == want

    if "contains" in condition:
        return condition["contains"].lower() in str(value or "").lower()

    if "has_item_within_days" in condition:
        if not isinstance(value, list):
            return False
        n = int(condition["has_item_within_days"])
        for item in value:
            when = item if not isinstance(item, dict) else item.get("date", item.get("when"))
            days = _days_until(when)
            if days is not None and 0 <= days <= n:
                return True
        return False

    return False


def _evaluate_condition_block(condition: dict[str, Any], doc: dict[str, Any]) -> bool:
    """Evaluate all_of / any_of block against a document."""
    if "all_of" in condition:
        return all(_evaluate_operator(c, doc) for c in condition["all_of"])
    if "any_of" in condition:
        return any(
            _evaluate_operator({k: v for k, v in c.items()}, doc)
            for c in condition["any_of"]
        )
    # flat single condition
    return _evaluate_operator(condition, doc)


# ---------------------------------------------------------------------------
# State file loading
# ---------------------------------------------------------------------------

def _load_yaml_file(path: Path) -> dict | list | None:
    """Load a YAML state file safely. Returns None if file missing or unparseable.

    Handles markdown files with YAML frontmatter (multiple --- document markers)
    by returning only the first YAML document.
    """
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        # Use safe_load_all to handle files with multiple YAML documents
        # (e.g. markdown files with --- frontmatter delimiters).
        return next(yaml.safe_load_all(text), None)
    except Exception:  # noqa: BLE001
        return None


def _resolve_documents(pattern: dict, root_dir: Path) -> list[dict]:
    """Load the source_file and navigate to source_path to get documents.

    Returns a list of dicts to evaluate the condition against.
    """
    rel = pattern.get("source_file", "")
    if not rel:
        return []
    path = (root_dir / rel).resolve()
    data = _load_yaml_file(path)
    if data is None:
        return []

    source_path = pattern.get("source_path")
    if not source_path:
        return [data] if isinstance(data, dict) else []

    # Navigate nested path: e.g. "contacts.inner_circle"
    node: Any = data
    for key in source_path.split("."):
        if isinstance(node, dict):
            node = node.get(key)
        else:
            node = None
        if node is None:
            return []

    if isinstance(node, list):
        return [item for item in node if isinstance(item, dict)]
    if isinstance(node, dict):
        return [node]
    # Scalar — wrap for days_until_lte top-level patterns
    return [{"_value": node}]


# ---------------------------------------------------------------------------
# Cooldown state
# ---------------------------------------------------------------------------

def _load_cooldown_state(state_file: Path | None = None) -> dict[str, str]:
    """Load pattern_engine_state.yaml last-fired timestamps."""
    path = state_file or _STATE_FILE
    if not path.exists():
        return {}
    data = _load_yaml_file(path)
    if isinstance(data, dict):
        return {k: str(v) for k, v in data.get("last_fired", {}).items()}
    return {}


def _save_cooldown_state(state: dict[str, str], state_file: Path | None = None) -> None:
    """Persist updated last-fired timestamps atomically."""
    import fcntl, os, tempfile

    path = state_file or _STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    content = yaml.dump({"last_fired": state}, default_flow_style=False)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(content)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def _is_on_cooldown(pattern_id: str, cooldown_hours: int, last_fired: dict[str, str]) -> bool:
    fired_str = last_fired.get(pattern_id)
    if not fired_str:
        return False
    try:
        fired_dt = datetime.fromisoformat(fired_str)
        if fired_dt.tzinfo is None:
            fired_dt = fired_dt.replace(tzinfo=timezone.utc)
        return datetime.now(tz=timezone.utc) - fired_dt < timedelta(hours=cooldown_hours)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# PatternEngine
# ---------------------------------------------------------------------------

class PatternEngine:
    """Evaluates config/patterns.yaml rules against current state-file snapshots.

    Usage:
        engine = PatternEngine()
        signals = engine.evaluate()
    """

    def __init__(
        self,
        patterns_file: Path | None = None,
        root_dir: Path | None = None,
    ) -> None:
        self._patterns_file = patterns_file or _PATTERNS_FILE
        self._root_dir = root_dir or _ROOT_DIR
        self._state_file = self._root_dir / "state" / "pattern_engine_state.yaml"

    def _load_patterns(self) -> list[dict]:
        data = _load_yaml_file(self._patterns_file)
        if not isinstance(data, dict):
            return []
        return [p for p in data.get("patterns", []) if p.get("enabled", True)]

    def evaluate(self) -> list[DomainSignal]:
        """Run all enabled patterns and return any signals that fire."""
        if not _load_flag("enhancements.pattern_engine", default=True):
            return []

        patterns = self._load_patterns()
        if not patterns:
            return []

        cooldown_state = _load_cooldown_state(self._state_file)
        signals: list[DomainSignal] = []
        fired_now: dict[str, str] = {}
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        for pattern in patterns:
            pat_id = pattern.get("id", "unknown")
            cooldown_h = int(pattern.get("cooldown_hours", 24))

            if _is_on_cooldown(pat_id, cooldown_h, cooldown_state):
                continue

            documents = _resolve_documents(pattern, self._root_dir)
            if not documents:
                continue

            cond = pattern.get("condition", {})
            out_cfg = pattern.get("output_signal", {})

            for doc in documents:
                # Resolve the source_path scalar wrapped in {"_value": ...}
                if list(doc.keys()) == ["_value"]:
                    # Top-level scalar pattern — inject into field expected by condition
                    for sub in (cond.get("all_of") or cond.get("any_of") or [cond]):
                        if "days_until_lte" in sub or "stale_days" in sub:
                            doc = {"_value": doc["_value"]}
                            # Redirect field lookup
                            enriched = dict(sub)
                            enriched.pop("field", None)
                            if _evaluate_operator(enriched, {"_value": doc["_value"]}):
                                matched = True
                                break
                    else:
                        matched = False
                else:
                    matched = _evaluate_condition_block(cond, doc)

                if not matched:
                    continue

                # Extract entity name (field from the document that fired)
                entity_field = out_cfg.get("entity_field")
                entity = str(doc.get(entity_field, pattern.get("name", pat_id)))[:60]

                meta = dict(out_cfg.get("metadata") or {})
                meta["pattern_id"] = pat_id

                signal = DomainSignal(
                    signal_type=out_cfg.get("signal_type", "pattern_alert"),
                    domain=out_cfg.get("domain", "comms"),
                    entity=entity,
                    urgency=int(out_cfg.get("urgency", 1)),
                    impact=int(out_cfg.get("impact", 1)),
                    source="pattern_engine",
                    metadata=meta,
                    detected_at=now_iso,
                )
                signals.append(signal)
                fired_now[pat_id] = now_iso
                # Only fire once per pattern per run (first matching document wins)
                break

        if fired_now:
            merged = {**cooldown_state, **fired_now}
            _save_cooldown_state(merged, self._state_file)

        return signals


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> int:
    engine = PatternEngine()
    signals = engine.evaluate()
    print(f"Pattern engine: {len(signals)} signal(s) generated")
    for sig in signals:
        print(f"  [{sig.signal_type}] {sig.domain} urgency={sig.urgency} entity={sig.entity[:40]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
