#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module; PII guard applied to all action params
"""
scripts/action_orchestrator.py — Single CLI entry-point for the Artha Action Layer.

Wires together signal producers (email_signal_extractor, pattern_engine)
with action consumers (ActionComposer → ActionQueue → ActionExecutor) and
presents a numbered approval UX to the AI and user.

Usage
-----
    # Run signal extraction, compose proposals, print pending queue:
    python3 scripts/action_orchestrator.py --run
    python3 scripts/action_orchestrator.py --run --mcp          # skip email signals (pattern engine only)
    python3 scripts/action_orchestrator.py --run --verbose

    # Inspect / act on proposals:
    python3 scripts/action_orchestrator.py --list
    python3 scripts/action_orchestrator.py --show <action_id>
    python3 scripts/action_orchestrator.py --approve <action_id>
    python3 scripts/action_orchestrator.py --reject <action_id> [--reason "..."]
    python3 scripts/action_orchestrator.py --defer <action_id> [--until "+1h"|"tomorrow"|"next-session"]
    python3 scripts/action_orchestrator.py --approve-all-low
    python3 scripts/action_orchestrator.py --expire
    python3 scripts/action_orchestrator.py --health

Exit codes
----------
    0   All operations succeeded
    1   Partial failure (some proposals failed; others OK)
    2   Invalid arguments / config error
    3   Complete failure

Design
------
- Pure Python, no new dependencies
- All output: stdout for AI consumption; stderr for logging
- Non-blocking: never crashes the catch-up session
- Respects harness.actions.enabled kill switch
- Respects read-only mode via detect_environment.py
- Platform-local SQLite DB via ActionQueue._resolve_db_path()

Ref: specs/actions-reloaded.md v1.3.0
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: ensure scripts/ is on sys.path (mirrors pipeline.py pattern)
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Ensure correct venv before third-party imports (no-op if already in venv or CI)
try:
    from _bootstrap import reexec_in_venv  # type: ignore[import]
    reexec_in_venv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Lazy imports — deferred so kill-switch check runs before any heavy work
# ---------------------------------------------------------------------------

def _import_action_modules() -> tuple[Any, Any, Any]:
    """Return (ActionComposer, ActionExecutor, EmailSignalExtractor)."""
    from action_composer import ActionComposer  # type: ignore[import]
    from action_executor import ActionExecutor  # type: ignore[import]
    from email_signal_extractor import EmailSignalExtractor  # type: ignore[import]
    return ActionComposer, ActionExecutor, EmailSignalExtractor


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_artha_config(artha_dir: Path) -> dict[str, Any]:
    """Load config/artha_config.yaml. Returns empty dict on failure."""
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        return load_config("artha_config", str(artha_dir / "config")) or {}
    except Exception:
        pass
    return {}


def _nested_get(d: dict, *keys: str, default: Any = None) -> Any:
    """Navigate nested dict with dot-style keys. Returns default on missing."""
    node: Any = d
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k, default)
    return node


def _actions_enabled(artha_dir: Path) -> bool:
    """Check harness.actions.enabled in artha_config.yaml (default: True)."""
    cfg = _load_artha_config(artha_dir)
    return bool(_nested_get(cfg, "harness", "actions", "enabled", default=True))


def _burn_in_mode(artha_dir: Path) -> bool:
    """Check harness.actions.burn_in in artha_config.yaml (default: False)."""
    cfg = _load_artha_config(artha_dir)
    return bool(_nested_get(cfg, "harness", "actions", "burn_in", default=False))


def _ai_signals_enabled(artha_dir: Path) -> bool:
    """Check harness.actions.ai_signals in artha_config.yaml (default: False).

    AI-emitted signals are default-off (V1.1) pending security burn-in.
    Ref: specs/actions-reloaded.md §SP-4
    """
    cfg = _load_artha_config(artha_dir)
    return bool(_nested_get(cfg, "harness", "actions", "ai_signals", default=False))


def _is_read_only(artha_dir: Path) -> bool:
    """Check if running in read-only mode via detect_environment.py."""
    try:
        from detect_environment import probe_environment  # type: ignore[import]
        manifest = probe_environment()
        return not manifest.capabilities.get("filesystem_writable", True)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Signal loading helpers
# ---------------------------------------------------------------------------

def _load_emails(path: Path) -> list[dict]:
    """Load email records from pipeline JSONL, excluding marketing."""
    emails: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue
                    if record.get("marketing"):
                        continue
                    emails.append(record)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return emails


# Required fields for a valid AI-emitted signal (schema validation, §SP-4 hardening step 3)
_AI_SIGNAL_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"signal_type", "domain", "entity", "urgency", "impact", "source"}
)
# AI signals MUST identify themselves as such (§SP-4 hardening step 1)
_AI_SIGNAL_SOURCE_VALUE = "ai"

# Signal types that route to communication actions (email_send / email_reply / whatsapp_send).
# Entity values for these signals are validated against the trusted-contact whitelist (OWASP A03).
# Source: config/signal_routing.yaml — regenerate if routing table changes.
_COMM_SIGNAL_TYPES: frozenset[str] = frozenset({
    "medical_bill_high", "appointment_needed", "email_needs_reply",
    "birthday_approaching", "birthday_in_7d", "life_event_detected",
    "missing_assignment", "school_event_rsvp", "immigration_deadline",
    "event_rsvp_needed",
    # Canonical types (Phase 4 consolidation — initiative 5)
    "deadline", "confirmation", "security", "informational",
})


def _load_trusted_contacts(artha_dir: Path) -> dict[str, set[str]]:
    """Build trusted email + phone whitelist from user_profile.yaml.

    Returns a dict with two sets: 'emails' and 'phones'. Used by the AI signal
    param whitelist validator (OWASP A03 — §SP-4 hardening step 4) to detect
    adversarial entity values in communication-type signals.

    Conservative: returns empty sets on any parse error so validation fails-open
    (allows signal through) rather than blocking legitimate signals.
    """
    emails: set[str] = set()
    phones: set[str] = set()
    try:
        from lib.config_loader import load_config as _lc  # noqa: PLC0415
        profile = _lc("user_profile", _config_dir=str(artha_dir / "config")) or {}
        family = profile.get("family", {})
        # Primary user emails
        primary = family.get("primary_user", {})
        for addr in (primary.get("emails") or {}).values():
            if isinstance(addr, str) and "@" in addr:
                emails.add(addr.lower())
        if isinstance(primary.get("phone"), str):
            phones.add(primary["phone"])
        # Spouse
        spouse = family.get("spouse", {})
        if isinstance(spouse.get("phone"), str):
            phones.add(spouse["phone"])
        if isinstance(spouse.get("email"), str) and "@" in spouse["email"]:
            emails.add(spouse["email"].lower())
        # Children
        for child in family.get("children", []):
            if isinstance(child.get("phone"), str):
                phones.add(child["phone"])
            school = child.get("school", {})
            if isinstance(school.get("email_domain"), str):
                # Accept any address at the school domain
                emails.add(f"@{school['email_domain'].lower()}")
    except Exception as exc:
        print(
            f"[action_orchestrator] trusted_contacts load failed (validation disabled): {exc}",
            file=sys.stderr,
        )
    return {"emails": emails, "phones": phones}


def _is_trusted_entity(entity: str, contacts: dict[str, set[str]]) -> bool:
    """Return True if entity is a known contact or does not look like an email/phone.

    Entities that don't contain '@' and don't look like a phone number are
    assumed to be names or natural-language descriptions — not validated so as
    to avoid false positives. Only explicit address/phone values are checked.
    """
    entity = entity.strip()
    is_email = "@" in entity
    is_phone = entity.replace("+", "").replace("-", "").replace(" ", "").isdigit() and len(entity) >= 7
    if not is_email and not is_phone:
        return True  # name/description — not an injection vector
    if is_email:
        email_lower = entity.lower()
        # Exact match or domain suffix match
        if email_lower in contacts["emails"]:
            return True
        for allowed in contacts["emails"]:
            if allowed.startswith("@") and email_lower.endswith(allowed):
                return True
        return False
    if is_phone:
        return entity in contacts["phones"]
    return False  # unreachable


def _load_ai_signals(path: Path) -> list[Any]:
    """Load and validate AI-emitted signals from tmp/ai_signals.jsonl.

    Security hardening (§SP-4, §7.2.1):
    1. Mandatory source field — must be "ai" (not spoofable as email_extractor/pattern_engine)
    2. Schema validation — required fields enforced; malformed lines silently skipped
    3. Unknown signal types pass through — routing table drops them during compose()
    4. Friction escalation applied downstream by _apply_ai_signal_hardening()

    Skips silently on any parse or IO error — AI signals are never blocking.
    """
    from types import SimpleNamespace

    signals: list[Any] = []
    skipped = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    print(
                        f"[action_orchestrator] ai_signals line {i}: invalid JSON — skipped",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue

                if not isinstance(record, dict):
                    skipped += 1
                    continue

                # Validate required fields (hardening step 3 — schema validation)
                missing_fields = _AI_SIGNAL_REQUIRED_FIELDS - set(record.keys())
                if missing_fields:
                    print(
                        f"[action_orchestrator] ai_signals line {i}: missing fields "
                        f"{sorted(missing_fields)} — skipped",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue

                # Enforce mandatory source tag (hardening step 1)
                # Reject attempts to impersonate deterministic signal sources
                if record.get("source") != _AI_SIGNAL_SOURCE_VALUE:
                    print(
                        f"[action_orchestrator] ai_signals line {i}: source must be "
                        f"'{_AI_SIGNAL_SOURCE_VALUE}', got '{record.get('source')}' — skipped",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue

                # Param whitelist validation — OWASP A03 (§SP-4 hardening step 4)
                # For comm-type signals, entity must resolve to a trusted contact.
                # Unknown email/phone entities are tagged INJECTION_SUSPECTED.
                sig_type = str(record["signal_type"])
                entity_val = str(record["entity"])
                injection_suspected = False
                if sig_type in _COMM_SIGNAL_TYPES:
                    trusted = _load_trusted_contacts(path.parent.parent)
                    if not _is_trusted_entity(entity_val, trusted):
                        injection_suspected = True
                        print(
                            f"[action_orchestrator] INJECTION_SUSPECTED line {i}: "
                            f"signal_type={sig_type!r} entity={entity_val!r} "
                            f"not in trusted contacts — friction=blocked",
                            file=sys.stderr,
                        )

                # DEBT-SIG-006 (Sprint 1): Enforce urgency/impact range 0–3.
                # Default was 5 (out of range) — reject instead of allowing priority inflation.
                # Sprint 2: full migration to SignalEnvelope (DEBT-ARCH-002) replaces this check.
                try:
                    raw_urgency = int(record.get("urgency", 2))
                    raw_impact = int(record.get("impact", 2))
                except (ValueError, TypeError) as _range_exc:
                    print(
                        f"[action_orchestrator] ai_signals line {i}: non-integer urgency/impact "
                        f"({record.get('urgency')!r}/{record.get('impact')!r}) — rejected",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue
                if not (0 <= raw_urgency <= 3) or not (0 <= raw_impact <= 3):
                    print(
                        f"[action_orchestrator] ai_signals line {i}: urgency/impact out of range "
                        f"({raw_urgency}/{raw_impact}, valid: 0–3) — rejected",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue

                signals.append(SimpleNamespace(
                    signal_type=sig_type,
                    domain=str(record["domain"]),
                    entity=entity_val,
                    urgency=raw_urgency,
                    impact=raw_impact,
                    source=_AI_SIGNAL_SOURCE_VALUE,
                    detected_at=record.get("detected_at", ""),
                    metadata={},  # AI-emitted metadata never trusted
                    _ai_origin=True,  # Internal tag for friction escalation
                    _injection_suspected=injection_suspected,  # OWASP A03 flag
                ))
    except OSError:
        pass

    if skipped:
        print(
            f"[action_orchestrator] ai_signals: {skipped} line(s) skipped (schema/source validation)",
            file=sys.stderr,
        )
    return signals


def _apply_ai_signal_hardening(proposal: Any, signal: Any = None) -> Any:
    """Escalate friction for all AI-originated proposals.

    Security hardening step 2 (§SP-4, §7.2.1): AI signals must never produce
    low/standard friction proposals — batch-approve via --approve-all-low must
    not be usable for AI-originated actions. Human review is always required.

    If the source signal was tagged _injection_suspected (OWASP A03 §SP-4 step 4),
    friction is escalated to 'blocked' — the proposal remains visible in --list
    output as INJECTION_SUSPECTED but cannot be approved via any code path.

    Returns a new ActionProposal with friction='high' or 'blocked' using
    dataclasses.replace().
    """
    import dataclasses
    injection_suspected = getattr(signal, "_injection_suspected", False) if signal else False
    if injection_suspected:
        target_friction = "blocked"
    else:
        target_friction = "high"
    if getattr(proposal, "friction", None) != target_friction:
        proposal = dataclasses.replace(proposal, friction=target_friction)
    return proposal


def _normalize_entity_for_dedup(entity: str) -> str:
    """Strip dates, amounts, and transaction IDs for cross-session dedup.

    Ensures that "Xfinity bill Mar" and "Xfinity bill Apr" produce the same
    normalized string, preventing the same bill from being proposed each month.

    Ref: specs/action-convert.md §4.4.3
    """
    import re
    s = entity.lower().strip()
    # Remove dates in various formats
    s = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "", s)
    # Month name followed by a day or year (Mar 25, Mar 2025, March 2026)
    s = re.sub(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,4}(?:,?\s+\d{4})?\b",
        "",
        s,
        flags=re.IGNORECASE,
    )
    # Standalone 4-digit years
    s = re.sub(r"\b20\d{2}\b", "", s)
    # Remove dollar amounts
    s = re.sub(r"\$[\d,]+\.?\d*", "", s)
    # Remove transaction/confirmation IDs (8+ uppercase alphanumeric)
    s = re.sub(r"\b[A-Z0-9]{8,}\b", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s or entity.lower().strip()


def _deduplicate(signals: list[Any]) -> list[Any]:
    """Remove duplicate signals by (signal_type, domain, entity) within the same run.

    Cross-session dedup is handled by ActionQueue.propose() status-based guard.
    Ref: specs/actions-reloaded.md §WB-3
    """
    def _key_part(value: Any) -> str:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return str(value)
        try:
            return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        except TypeError:
            return repr(value)

    seen: set[tuple[str, str, str]] = set()
    unique: list[Any] = []
    for s in signals:
        entity_val = getattr(s, "entity", "")
        if isinstance(entity_val, dict):
            # Dict entities use json.dumps(sort_keys=True) for stable key order
            entity_key = _key_part(entity_val)
        else:
            entity_key = _key_part(_normalize_entity_for_dedup(str(entity_val)))
        key = (
            _key_part(getattr(s, "signal_type", "")),
            _key_part(getattr(s, "domain", "")),
            entity_key,
        )
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def _domain_confidence(conn: Any, signal_subtype: str, domain: str) -> float:
    """Return acceptance rate for (signal_subtype, domain) over last 90 days.

    Returns 1.0 (optimistic default) when fewer than 3 decisions exist —
    avoids suppressing signals that have never been tried.

    Ref: specs/action-convert.md §4.5.2
    """
    try:
        cur = conn.execute(
            """
            SELECT
                SUM(CASE WHEN user_decision = 'accepted' THEN 1.0 ELSE 0 END) as accepted,
                COUNT(*) as total
            FROM trust_metrics
            WHERE signal_subtype = ? AND domain = ?
            AND proposed_at > datetime('now', '-90 days')
            """,
            (signal_subtype, domain),
        )
        row = cur.fetchone()
        if row and row["total"] and row["total"] >= 3:
            return float(row["accepted"]) / float(row["total"])
    except Exception:  # noqa: BLE001
        pass
    return 1.0  # optimistic default (insufficient data)


def _check_loop_quarantine(
    conn: Any,
    signal_subtype: str,
    domain: str,
    normalized_entity: str,
) -> bool:
    """Return True if this entity is currently in loop quarantine (should suppress).

    Ref: specs/action-convert.md §4.4.4
    """
    try:
        cur = conn.execute(
            """
            SELECT quarantine_until FROM loop_quarantine
            WHERE signal_subtype = ? AND domain = ? AND normalized_entity = ?
            """,
            (signal_subtype, domain, normalized_entity),
        )
        row = cur.fetchone()
        if row:
            quarantine_until = row[0] if isinstance(row, (list, tuple)) else row["quarantine_until"]
            if quarantine_until and quarantine_until > datetime.now(timezone.utc).isoformat():
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _record_loop_quarantine(
    conn: Any,
    signal_subtype: str,
    domain: str,
    normalized_entity: str,
    days: int = 14,
) -> None:
    """Write a loop quarantine record to prevent repeated rejected proposals.

    Ref: specs/action-convert.md §4.4.4
    """
    try:
        quarantine_until = (
            datetime.now(timezone.utc) + timedelta(days=days)
        ).isoformat()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS loop_quarantine (
                signal_subtype    TEXT NOT NULL,
                domain            TEXT NOT NULL,
                normalized_entity TEXT NOT NULL,
                quarantine_until  TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                PRIMARY KEY (signal_subtype, domain, normalized_entity)
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO loop_quarantine
            (signal_subtype, domain, normalized_entity, quarantine_until, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                signal_subtype,
                domain,
                normalized_entity,
                quarantine_until,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception:  # noqa: BLE001
        pass


def _check_signal_suppression(conn: Any, signal_subtype: str, domain: str) -> bool:
    """Return True if (signal_subtype, domain) is in the ML-learned suppression list.

    Entries are written by cmd_apply_suggestions when user approves a domain_suppress
    suggestion. Returns False if the table doesn't exist yet.
    Ref: specs/action-convert.md §4.3.1 point 4
    """
    try:
        cur = conn.execute(
            "SELECT 1 FROM signal_suppression WHERE signal_subtype = ? AND domain = ? LIMIT 1",
            (signal_subtype, domain),
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def _check_category_dedup(
    conn: Any, signal_subtype: str, domain: str, normalized_entity: str
) -> bool:
    """Return True if the signal should be suppressed due to recent rejection.

    Uses normalized_entity to match across sessions, respecting the
    category-aware dedup windows from §4.4.3:
      already_handled → 90 days
      not_relevant / duplicate → 30 days
      wrong_action_type / bad_timing → 0 days (no extension)
      (no reason) → 30 days (conservative default)

    Returns False if insufficient data or no matching rejection found.
    Ref: specs/action-convert.md §4.4.3
    """
    _CATEGORY_WINDOWS: dict[str, int] = {
        "already_handled": 90,
        "wrong_action_type": 0,
        "not_relevant": 30,
        "bad_timing": 0,
        "duplicate": 30,
    }
    _DEFAULT_WINDOW = 30

    try:
        cur = conn.execute(
            """
            SELECT tm.rejection_category, tm.proposed_at
            FROM trust_metrics tm
            JOIN actions a ON tm.action_type = a.action_type
            WHERE a.normalized_entity = ?
              AND a.domain = ?
              AND tm.user_decision = 'rejected'
            ORDER BY tm.proposed_at DESC
            LIMIT 1
            """,
            (normalized_entity, domain),
        )
        row = cur.fetchone()
        if not row:
            return False

        cat_raw = row[0] if isinstance(row, (list, tuple)) else row["rejection_category"]
        proposed_at_str = row[1] if isinstance(row, (list, tuple)) else row["proposed_at"]

        try:
            import json as _json  # noqa: PLC0415
            cat_parsed = _json.loads(cat_raw) if cat_raw else {}
            cat_name = cat_parsed.get("category", "") if isinstance(cat_parsed, dict) else ""
        except Exception:
            cat_name = cat_raw or ""

        window_days = _CATEGORY_WINDOWS.get(cat_name, _DEFAULT_WINDOW)
        if window_days == 0:
            return False

        try:
            from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
            proposed_at = _dt.fromisoformat(proposed_at_str.replace("Z", "+00:00"))
            age_days = (_dt.now(_tz.utc) - proposed_at).days
            return age_days < window_days
        except Exception:
            return False
    except Exception:
        return False


def _count_consecutive_non_accepted(
    conn: Any,
    signal_subtype: str,
    domain: str,
    normalized_entity: str,
) -> int:
    """Count consecutive non-accepted decisions for this entity (most recent first).

    Counts until the first 'accepted' decision is found (that resets the streak).
    Only considers rows where normalized_entity IS NOT NULL (skips historical rows).

    Ref: specs/action-convert.md §4.4.4 + CONSTRAINT 7
    """
    try:
        cur = conn.execute(
            """
            SELECT user_decision FROM trust_metrics
            WHERE signal_subtype = ? AND domain = ? AND normalized_entity = ?
            AND normalized_entity IS NOT NULL
            ORDER BY proposed_at DESC
            LIMIT 20
            """,
            (signal_subtype, domain, normalized_entity),
        )
        rows = cur.fetchall()
        count = 0
        for row in rows:
            decision = row[0] if isinstance(row, (list, tuple)) else row["user_decision"]
            if decision == "accepted":
                break
            count += 1
        return count
    except Exception:  # noqa: BLE001
        return 0


def _acceptance_rate_windowed(
    conn: Any,
    window_days: int = 30,
    min_decisions: int = 10,
) -> "float | None":
    """Compute rolling acceptance rate with 0.5× expiry penalty.

    Returns None if fewer than min_decisions in the window (insufficient data).
    Expired actions contribute 0.5 to the denominator (§4.5.3).

    Ref: specs/action-convert.md §4.5.3
    """
    try:
        cur = conn.execute(
            f"""
            SELECT
                SUM(CASE WHEN user_decision = 'accepted' THEN 1.0 ELSE 0 END) as accepted,
                SUM(CASE
                    WHEN user_decision IN ('accepted', 'rejected') THEN 1.0
                    WHEN user_decision = 'expired' THEN 0.5
                    ELSE 0
                END) as effective_denominator,
                COUNT(*) as total
            FROM trust_metrics
            WHERE proposed_at > datetime('now', '-{window_days} days')
            AND user_decision IN ('accepted', 'rejected', 'expired')
            """,
        )
        row = cur.fetchone()
        if row:
            effective_denom = row[1] if isinstance(row, (list, tuple)) else row["effective_denominator"]
            accepted = row[0] if isinstance(row, (list, tuple)) else row["accepted"]
            if effective_denom and effective_denom >= min_decisions:
                return float(accepted) / float(effective_denom)
    except Exception:  # noqa: BLE001
        pass
    return None


def _write_rejection_category(
    artha_dir: Path,
    action_id: str,
    reason: str,
    category: "int | None",
) -> None:
    """Update trust_metrics.rejection_category for a rejected action.

    Ref: specs/action-convert.md §4.5.1
    """
    try:
        from action_queue import _open_db as _aq_open, ActionQueue as _AQ  # noqa: PLC0415
        db_path = _AQ._resolve_db_path(artha_dir)
        if not db_path.exists():
            return
        conn = _aq_open(db_path)
        try:
            conn.execute(
                "UPDATE trust_metrics SET rejection_category = ? WHERE action_type IN "
                "(SELECT action_type FROM actions WHERE id = ?)",
                (reason[:500] if reason else None, action_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


def _stage_policy_suggestion(
    artha_dir: Path,
    action_id: str,
    category: int,
    executor: Any,
) -> None:
    """Stage a policy suggestion in policy_suggestions table.

    Category 1 (already handled) → autopay_add suggestion for the entity service.
    Category 2 (wrong action type) → action_override suggestion for the signal_type.

    Staged suggestions are NOT applied until user runs --apply-suggestions.

    Ref: specs/action-convert.md §4.5.1
    """
    import uuid as _uuid

    try:
        action = executor.get_action(action_id)
        if not action:
            return

        action_type = getattr(action, "action_type", "")
        domain = getattr(action, "domain", "")
        title = getattr(action, "title", "")

        # Derive entity/service name from title (best effort)
        entity = title.split(":")[0].strip() if ":" in title else title.split()[0] if title.split() else ""
        entity_lower = entity.lower()[:50]

        from action_queue import _open_db as _aq_open, ActionQueue as _AQ  # noqa: PLC0415
        db_path = _AQ._resolve_db_path(artha_dir)
        if not db_path.exists():
            return
        conn = _aq_open(db_path)

        ts = datetime.now(timezone.utc).isoformat()

        try:
            if category == 1:
                # Suggest adding to autopay_services
                conn.execute(
                    """
                    INSERT OR IGNORE INTO policy_suggestions
                    (id, type, value, signal_type, source_action_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(_uuid.uuid4()),
                        "autopay_add",
                        entity_lower or "unknown",
                        None,
                        action_id,
                        ts,
                    ),
                )
            elif category == 2:
                # Suggest action_type override for this signal/domain combo.
                # Prompt for desired type if interactive; store as value so apply can write it.
                desired_action_type = "PENDING_USER_INPUT"
                if sys.stdin.isatty():
                    try:
                        desired_action_type = input(
                            f"What action type would you prefer for '{domain}' signals? "
                            f"(current: {action_type}): "
                        ).strip() or "PENDING_USER_INPUT"
                    except (EOFError, KeyboardInterrupt):
                        desired_action_type = "PENDING_USER_INPUT"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO policy_suggestions
                    (id, type, value, signal_type, source_action_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(_uuid.uuid4()),
                        "action_override",
                        desired_action_type,  # desired action_type (or PENDING_USER_INPUT)
                        domain,               # domain used as signal_type context
                        action_id,
                        ts,
                    ),
                )
            elif category == 3:
                # Category 3 + very low domain confidence → suggest permanent suppression.
                # Use action_type as signal_subtype proxy (closest available identifier).
                _dc = _domain_confidence(conn, action_type, domain)
                if _dc < 0.3:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO policy_suggestions
                        (id, type, value, signal_type, source_action_id, created_at)
                        VALUES (?, 'domain_suppress', ?, ?, ?, ?)
                        """,
                        (
                            str(_uuid.uuid4()),
                            domain,
                            action_type,
                            action_id,
                            ts,
                        ),
                    )
                    print(
                        f"[action] Suggestion staged: permanently suppress "
                        f"'{action_type}×{domain}' (domain confidence: {_dc:.0%}). "
                        "Run --apply-suggestions to review."
                    )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


def cmd_apply_suggestions(artha_dir: Path) -> int:
    """Review and apply staged policy suggestions to user_context.yaml.

    Loads pending suggestions from DB, prints a numbered list, prompts for
    confirmation, and atomically writes approved entries to user_context.yaml.
    Updates applied_at in DB and emits POLICY_APPLIED audit event.

    Ref: specs/action-convert.md §4.5.1
    """
    import copy
    import os

    try:
        from action_queue import _open_db as _aq_open, ActionQueue as _AQ  # noqa: PLC0415
        db_path = _AQ._resolve_db_path(artha_dir)
        if not db_path.exists():
            print("[action] No actions DB found.")
            return 0
        conn = _aq_open(db_path)
    except Exception as exc:
        print(f"[action] ✗ cannot open DB: {exc}", file=sys.stderr)
        return 1

    try:
        cur = conn.execute(
            "SELECT id, type, value, signal_type, source_action_id, created_at "
            "FROM policy_suggestions WHERE applied_at IS NULL ORDER BY created_at"
        )
        rows = cur.fetchall()
    except Exception as exc:
        print(f"[action] ✗ cannot read policy_suggestions: {exc}", file=sys.stderr)
        conn.close()
        return 1

    if not rows:
        print("[action] No pending policy suggestions.")
        conn.close()
        return 0

    print("\n═══ PENDING POLICY SUGGESTIONS ════════════════════════════════")
    suggestions = []
    for i, row in enumerate(rows, 1):
        row_dict = dict(row) if hasattr(row, "keys") else {
            "id": row[0], "type": row[1], "value": row[2],
            "signal_type": row[3], "source_action_id": row[4], "created_at": row[5],
        }
        suggestions.append(row_dict)
        src = (row_dict.get("source_action_id") or "")[:8]
        ts = (row_dict.get("created_at") or "")[:10]
        if row_dict["type"] == "autopay_add":
            print(f"  [A{i}] Add '{row_dict['value']}' to autopay_services  (from {src}, {ts})")
        elif row_dict["type"] == "action_override":
            print(
                f"  [A{i}] Override action for '{row_dict.get('signal_type', '?')}' "
                f"(desired: {row_dict['value']})  (from {src}, {ts})"
            )
        elif row_dict["type"] == "domain_suppress":
            print(
                f"  [A{i}] Permanently suppress '{row_dict.get('signal_type','?')} × {row_dict['value']}'  "
                f"(from {src}, {ts})"
            )
        else:
            print(f"  [A{i}] {row_dict['type']}: {row_dict['value']}  (from {src}, {ts})")

    print()
    if not sys.stdin.isatty():
        print("[action] Non-interactive mode — skipping apply prompt.")
        conn.close()
        return 0

    answer = input("Apply all? [y/n/review]: ").strip().lower()
    if answer not in ("y", "yes"):
        print("[action] No changes applied.")
        conn.close()
        return 0

    # Load user_context.yaml fresh (NOT from cache — we're about to write it)
    uc_path = artha_dir / "config" / "user_context.yaml"
    try:
        import yaml  # type: ignore[import]
        raw_uc = yaml.safe_load(uc_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw_uc, dict):
            raw_uc = {}
    except Exception as exc:
        print(f"[action] ✗ cannot read user_context.yaml: {exc}", file=sys.stderr)
        conn.close()
        return 1

    user_ctx = copy.deepcopy(raw_uc)
    applied_ids: list[str] = []
    ts_now = datetime.now(timezone.utc).isoformat()

    for sug in suggestions:
        if sug["type"] == "autopay_add":
            services = user_ctx.setdefault("autopay_services", []) or []
            val = sug["value"].lower().strip()
            if val and val not in [str(s).lower() for s in services]:
                services.append(val)
                user_ctx["autopay_services"] = services
                applied_ids.append(sug["id"])
        elif sug["type"] == "action_override":
            sig_type = sug.get("signal_type", "")
            desired_type = sug.get("value", "")
            if sig_type and desired_type and desired_type != "PENDING_USER_INPUT":
                overrides = user_ctx.setdefault("action_type_overrides", {})
                overrides[sig_type] = {
                    "action_type": desired_type,
                    "override_reason": "Applied from --apply-suggestions (user rejection feedback)",
                    "override_date": datetime.now(timezone.utc).date().isoformat(),
                }
                user_ctx["action_type_overrides"] = overrides
                applied_ids.append(sug["id"])
            else:
                print(
                    f"  ⚠ Skipping action_override for '{sig_type}': desired type unknown. "
                    "Edit user_context.yaml manually."
                )
        elif sug["type"] == "domain_suppress":
            try:
                ts_now_apply = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO signal_suppression "
                    "(signal_subtype, domain, reason, created_at, source_action_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        sug.get("signal_type", sug.get("value", "")),
                        sug.get("value", ""),
                        f"ML-learned: user approved suppression (suggestion {sug['id'][:8]})",
                        ts_now_apply,
                        sug.get("source_action_id", ""),
                    ),
                )
                conn.commit()
                applied_ids.append(sug["id"])
            except Exception as exc:
                print(f"[action] ✗ failed to write signal_suppression: {exc}", file=sys.stderr)

    if not applied_ids:
        print("[action] Nothing to apply.")
        conn.close()
        return 0

    # Atomic write of user_context.yaml
    try:
        import yaml  # type: ignore[import]
        tmp_path = uc_path.with_suffix(".tmp")
        tmp_path.write_text(
            yaml.safe_dump(user_ctx, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(uc_path))
    except Exception as exc:
        print(f"[action] ✗ failed to write user_context.yaml: {exc}", file=sys.stderr)
        conn.close()
        return 1

    # Invalidate user_context cache so next compose() reads fresh data
    try:
        from lib.user_context import invalidate_user_context_cache  # noqa: PLC0415
        invalidate_user_context_cache()
    except Exception:  # noqa: BLE001
        pass

    # Mark applied in DB
    try:
        for sid in applied_ids:
            conn.execute(
                "UPDATE policy_suggestions SET applied_at = ? WHERE id = ?",
                (ts_now, sid),
            )
        conn.commit()
    except Exception as exc:
        print(f"[action] ✗ failed to update DB applied_at: {exc}", file=sys.stderr)

    _audit_log(
        artha_dir,
        f"POLICY_APPLIED | ids:{','.join(a[:8] for a in applied_ids)} | count:{len(applied_ids)}",
    )
    print(f"[action] ✓ Applied {len(applied_ids)} suggestion(s) to user_context.yaml")
    conn.close()
    return 0


def cmd_reset_domain_confidence(artha_dir: Path, signal_subtype: str, domain: str) -> int:
    """Reset domain confidence for a signal_subtype × domain pair.

    Deletes trust_metrics rows for the pair so the confidence returns to 1.0
    (optimistic default). Use when the underlying signal quality has improved.

    Ref: specs/action-convert.md §4.5.2
    """
    try:
        from action_queue import _open_db as _aq_open, ActionQueue as _AQ  # noqa: PLC0415
        db_path = _AQ._resolve_db_path(artha_dir)
        if not db_path.exists():
            print("[action] No actions DB found.")
            return 0
        conn = _aq_open(db_path)
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM trust_metrics WHERE signal_subtype = ? AND domain = ?",
                (signal_subtype, domain),
            )
            count = cur.fetchone()[0]
            conn.execute(
                "DELETE FROM trust_metrics WHERE signal_subtype = ? AND domain = ?",
                (signal_subtype, domain),
            )
            conn.commit()
            print(f"[action] ✓ Reset domain confidence: {signal_subtype}×{domain} ({count} rows deleted)")
            _audit_log(
                artha_dir,
                f"DOMAIN_CONFIDENCE_RESET | signal_subtype:{signal_subtype} | domain:{domain} | rows:{count}",
            )
        finally:
            conn.close()
        return 0
    except Exception as exc:
        print(f"[action] ✗ reset-domain-confidence failed: {exc}", file=sys.stderr)
        return 1


def _persist_signals(path: Path, signals: list[Any]) -> None:
    """Write signal envelope (no PII metadata) to JSONL for auditability.

    Intentionally excludes signal.metadata to prevent PII leakage.
    Ref: specs/actions-reloaded.md §WB-4
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for s in signals:
                record = {
                    "signal_type": getattr(s, "signal_type", ""),
                    "domain": getattr(s, "domain", ""),
                    "entity": getattr(s, "entity", ""),
                    "urgency": getattr(s, "urgency", 0),
                    "impact": getattr(s, "impact", 0),
                    "source": getattr(s, "source", ""),
                    "detected_at": getattr(s, "detected_at", ""),
                    # metadata intentionally excluded — may contain PII
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[action_orchestrator] signal persistence failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Handler health check (RH-1: import-level, fast startup check)
# ---------------------------------------------------------------------------

def _handler_health_check(artha_dir: Path) -> list[str]:
    """Verify all handler modules import cleanly. Returns list of failure strings.

    Import-level check only (~10ms) — deliberately avoids executing
    handler.health_check() which may make network calls.
    Full functional health is available via --health / executor.run_health_checks().
    Ref: specs/actions-reloaded.md §RH-1
    """
    try:
        from actions import _HANDLER_MAP  # type: ignore[import]
        handler_map = _HANDLER_MAP
    except ImportError:
        # Fallback: get map from executor's fallback
        try:
            from action_executor import _FALLBACK_ACTION_MAP  # type: ignore[import]
            handler_map = _FALLBACK_ACTION_MAP
        except ImportError:
            return ["actions._HANDLER_MAP: import failed (scripts/ path error)"]

    failures: list[str] = []
    for action_type, module_path in handler_map.items():
        try:
            importlib.import_module(module_path)
        except ImportError as exc:
            failures.append(f"{action_type} ({module_path}): {exc}")
    return failures


# ---------------------------------------------------------------------------
# Pre-enqueue handler validation (WB-1 _validate_proposal_handler)
# ---------------------------------------------------------------------------

def _validate_proposal_handler(executor: Any, proposal: Any) -> None:
    """Run handler.validate() before enqueue to catch structural issues early.

    Raises ValueError if the proposal would fail at execution time.
    Catches issues like email_send with no 'to' field before the user
    ever approves.
    Ref: specs/actions-reloaded.md §WB-1 _validate_proposal_handler
    """
    handler = executor._get_handler(proposal.action_type)
    ok, reason = handler.validate(proposal)
    if not ok:
        raise ValueError(f"Handler pre-validation failed: {reason}")


# ---------------------------------------------------------------------------
# Defer preset resolution (F-5: must resolve before calling executor.defer)
# ---------------------------------------------------------------------------

def _resolve_defer_preset(preset: str) -> str:
    """Translate human-friendly defer presets to ISO-8601 UTC strings.

    The executor's _resolve_defer_time() only understands '+Nh'/'+Nm' offsets
    and ISO strings — it does NOT understand 'tomorrow' or 'next-session'.
    This function is the translation layer.

    Supported presets:
        +1h, +4h, +Xh/+Xm  → N hours/minutes from now
        tomorrow            → next calendar day at 09:00 local time
        next-session        → +24h (default when bare 'defer' is used)

    Ref: specs/actions-reloaded.md §WB-1 _resolve_defer_preset
    """
    preset = preset.strip().lower()

    if preset in ("next-session", ""):
        # Default: 24h from now
        dt = datetime.now(timezone.utc) + timedelta(hours=24)
        return dt.isoformat(timespec="seconds")

    if preset == "tomorrow":
        # Next day at 09:00 local time, expressed as a UTC ISO string
        import time as _time
        local_now = datetime.now()
        tomorrow_local = (local_now + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        # Get UTC offset
        utc_offset_sec = -_time.timezone if not _time.daylight else -_time.altzone
        utc_offset = timedelta(seconds=utc_offset_sec)
        tomorrow_utc = tomorrow_local - utc_offset
        tomorrow_utc = tomorrow_utc.replace(tzinfo=timezone.utc)
        return tomorrow_utc.isoformat(timespec="seconds")

    if preset.startswith("+") and preset[1:].rstrip("hm").isdigit():
        # Offset: +Nh or +Nm
        raw = preset[1:]
        if raw.endswith("h"):
            hours = int(raw[:-1])
            dt = datetime.now(timezone.utc) + timedelta(hours=hours)
        elif raw.endswith("m"):
            minutes = int(raw[:-1])
            dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        else:
            hours = int(raw)
            dt = datetime.now(timezone.utc) + timedelta(hours=hours)
        return dt.isoformat(timespec="seconds")

    # Pass through as-is (may be an ISO string already)
    return preset


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _audit_log(artha_dir: Path, message: str) -> None:
    """Append a timestamped line to state/audit.md."""
    try:
        audit_path = artha_dir / "state" / "audit.md"
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"[{ts}] {message}\n"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass  # Audit logging is best-effort — never blocks operation


# ---------------------------------------------------------------------------
# Blueprint 5: compose_draft() — Worker tool; writes to action queue only
# ---------------------------------------------------------------------------

def compose_draft(domain: str, payload: dict, *, session_id: str = "") -> str:
    """Compose an action proposal and write it to state/action_queue.yaml.

    This is the *only* write tool available to domain Workers (§2.5.1).
    No network calls. No state writes outside state/action_queue.yaml.

    Args:
        domain:     Domain scope of the action (e.g. "finance", "immigration").
        payload:    Structured action payload; must match the declared schema
                    for its ``action_type`` (see §2.5.2 validation table).
        session_id: Caller's session_id (for audit trail linkage); optional.

    Returns:
        action_id — first 16 hex chars of SHA-256(domain + canonical JSON payload).
        Workers include this in their ``proposed_actions`` list.

    Raises:
        Nothing — all errors are caught and logged; returns empty string on failure
        so the caller can detect the error without crashing the Worker.
    """
    import hashlib
    import tempfile
    import os as _os

    try:
        # Compute deterministic action_id (spec §2.5.1)
        canonical = domain + json.dumps(payload, sort_keys=True, ensure_ascii=False)
        action_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"
        entry: dict = {
            "action_id": action_id,
            "domain": domain,
            "status": "proposed",
            "proposed_at": ts,
            "session_id": session_id or "",
            **payload,
        }

        # Atomic append to state/action_queue.yaml
        queue_path = _ARTHA_DIR / "state" / "action_queue.yaml"
        queue_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing queue (list of entries) or start fresh
        existing: list[dict] = []
        if queue_path.exists():
            try:
                import yaml as _yaml  # type: ignore[import]
                raw = queue_path.read_text(encoding="utf-8")
                loaded = _yaml.safe_load(raw)
                if isinstance(loaded, list):
                    existing = loaded
            except Exception:
                pass  # Start with empty list if parse fails

        # Deduplicate by action_id (idempotent compose)
        if not any(e.get("action_id") == action_id for e in existing):
            existing.append(entry)

        # Atomic write
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=queue_path.parent, prefix=".action_queue_tmp_", suffix=".yaml"
        )
        try:
            import yaml as _yaml  # type: ignore[import]
            with _os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                _yaml.safe_dump(existing, fh, default_flow_style=False, allow_unicode=True)
            _os.replace(tmp_path_str, queue_path)
        except Exception:
            try:
                _os.unlink(tmp_path_str)
            except Exception:
                pass
            raise

        # Append pipe-delimited audit row (spec §2.1.4 audit.md schema)
        action_type = str(payload.get("action_type", "default"))
        description = str(payload.get("description", payload.get("intent", "")))
        # Truncate payload_summary to ≤1 sentence, ≤80 chars; no PII
        payload_summary = description[:80].split(".")[0]
        audit_path = _ARTHA_DIR / "state" / "audit.md"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_row = (
            f"| {ts} | {session_id or '-'} | {domain} "
            f"| {action_type} | proposed | {payload_summary} |\n"
        )
        with open(audit_path, "a", encoding="utf-8") as fh:
            fh.write(audit_row)

        # Emit telemetry (non-fatal)
        try:
            from lib.telemetry import emit as _emit_tel  # type: ignore[import]
            _emit_tel(
                "action.composed",
                domain=domain,
                extra={"action_id": action_id, "action_type": action_type, "session_id": session_id},
            )
        except Exception:
            pass

        return action_id

    except Exception as exc:
        print(
            f"[action_orchestrator] compose_draft error: {exc}",
            file=sys.stderr,
        )
        return ""


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

_FRICTION_ICON = {"high": "🔴", "standard": "🟠", "low": "🟢"}
_CONTENT_BEARING_TYPES = frozenset({"email_send", "email_reply", "whatsapp_send"})

# RH-5: wall-clock timeout for the entire --run operation.
# Set via _run_with_timeout(); not applied to other CLI commands.
# UNIX/macOS only (SIGALRM unavailable on Windows — operation is deterministic
# regex + YAML so a runaway is implausible).
_RUN_TIMEOUT_SEC = 60


class _OrchestratorTimeout(BaseException):
    """Raised by SIGALRM when --run exceeds _RUN_TIMEOUT_SEC (RH-5).

    Extends BaseException (not Exception) so it propagates through bare
    'except Exception:' guards inside run() without being silently swallowed.
    """


def _print_summary(
    all_signals: list[Any],
    email_signal_count: int,
    pattern_signal_count: int,
    proposed: int,
    suppressed: int,
    quality_suppressed: int,
    expired: int,
    pending: list[Any],
    recently_expired: list[Any] | None = None,
    burn_in: bool = False,
    verbose: bool = False,
) -> None:
    """Print structured summary to stdout for AI consumption.

    Format matches specs/actions-reloaded.md §WB-1 output spec.
    """
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    depth = len(pending)
    errors = 0  # counted by caller

    prefix = "[BURN-IN] " if burn_in else ""

    print(f"═══ {prefix}ACTION ORCHESTRATOR ═══════════════════════════════════")
    print(
        f"Signals detected: {len(all_signals)} "
        f"(email: {email_signal_count}, pattern: {pattern_signal_count})"
    )
    print(f"Proposals queued: {proposed} ({suppressed} duplicate suppressed)")
    if quality_suppressed:
        print(f"Quality rejected: {quality_suppressed}")
    print(f"Expired: {expired}")

    # Emit structured counter line to stdout for machine parsing
    print(
        f"\n[{ts}] ACTION_ORCHESTRATOR | "
        f"signals:{len(all_signals)} suppressed:{suppressed} "
        f"quality_rejected:{quality_suppressed} queued:{proposed} "
        f"expired:{expired} depth:{depth} errors:{errors}"
    )

    if pending:
        cap = 10
        displayed = pending[:cap]
        print(f"\n─── PENDING ACTIONS ({depth}) ───────────────────────────────────────")
        for i, p in enumerate(displayed, 1):
            icon = _FRICTION_ICON.get(getattr(p, "friction", "standard"), "🟠")
            action_type = getattr(p, "action_type", "")
            domain = getattr(p, "domain", "")
            title = getattr(p, "title", "")
            pid = getattr(p, "id", "")[:8]
            friction = getattr(p, "friction", "standard")
            min_trust = getattr(p, "min_trust", 1)
            expires_at = getattr(p, "expires_at", "")
            content_flag = " [content]" if action_type in _CONTENT_BEARING_TYPES else ""
            print(
                f"{i}. [{pid}] {icon} {action_type} | {domain} | {title[:60]}{content_flag}"
            )
            print(
                f"   Friction: {friction} | Trust: {min_trust} | Expires: {expires_at}"
            )
        if depth > cap:
            extra = depth - cap
            print(f"... and {extra} more. Run 'items' or "
                  f"'python3 scripts/action_orchestrator.py --list' to see all.")

        print(
            "\nCommands: approve <id>, reject <id>, "
            "approve-all-low, defer <id> [--until \"+1h\"|\"tomorrow\"|\"next-session\"]"
        )
    else:
        print("\n(No pending actions)")

    # Surface recently expired actions so the AI can inform the user
    if recently_expired:
        print(f"\n─── EXPIRED SINCE LAST SESSION ({len(recently_expired)}) ────────────────────")
        for p in recently_expired[:5]:
            action_type = p.get("action_type", "") if isinstance(p, dict) else getattr(p, "action_type", "")
            domain = p.get("domain", "") if isinstance(p, dict) else getattr(p, "domain", "")
            title = p.get("title", "") if isinstance(p, dict) else getattr(p, "title", "")
            pid = (p.get("id", "") if isinstance(p, dict) else getattr(p, "id", ""))[:8]
            print(f"  ⏰ [{pid}] {action_type} | {domain} | {title[:60]}")
        if len(recently_expired) > 5:
            print(f"  ... and {len(recently_expired) - 5} more")
        print("  (These expired by time window or current proposal-quality cleanup)")

    print("════════════════════════════════════════════════════════════════")


def _print_expanded_preview(proposal: Any) -> None:
    """Print full detail view of a single proposal for content review.

    Ref: specs/actions-reloaded.md §WB-1 --show output format
    """
    action_type = getattr(proposal, "action_type", "")
    domain = getattr(proposal, "domain", "")
    friction = getattr(proposal, "friction", "standard")
    min_trust = getattr(proposal, "min_trust", 1)
    expires_at = getattr(proposal, "expires_at", "")
    title = getattr(proposal, "title", "")
    pid = getattr(proposal, "id", "")
    params = getattr(proposal, "parameters", {})

    print("═══ ACTION DETAIL ══════════════════════════════════════════════")
    print(f"ID:       {pid}")
    print(f"Type:     {action_type}")
    print(f"Domain:   {domain}")
    print(f"Friction: {friction}")
    print(f"Trust:    {min_trust}")
    print(f"Expires:  {expires_at}")
    print()
    print(f"Title:    {title}")

    if action_type in _CONTENT_BEARING_TYPES:
        if isinstance(params, dict):
            print()
            print("─── CONTENT PREVIEW ──────────────────────────────────────────")
            if "to" in params:
                print(f"To:       {params['to']}")
            if "subject" in params:
                print(f"Subject:  {params['subject']}")
            if "body" in params:
                body_lines = str(params["body"]).splitlines()
                cap = 80
                print("Body:")
                for line in body_lines[:cap]:
                    print(f"  {line}")
                if len(body_lines) > cap:
                    print(f"  ... (truncated, {len(body_lines) - cap} more lines)")
        else:
            print("🔒 Parameters encrypted — decrypt from Mac terminal to preview.")
    else:
        if isinstance(params, dict):
            print()
            print("─── PARAMETERS ───────────────────────────────────────────────")
            for k, v in list(params.items())[:10]:
                print(f"  {k}: {str(v)[:120]}")

    print("════════════════════════════════════════════════════════════════")


# ---------------------------------------------------------------------------
# M15: M365 MCP write dispatch (D3/A4/G1)
# ---------------------------------------------------------------------------

def _dispatch_m365_write(action_type: str, payload: dict) -> None:
    """Stub dispatch router for M15 M365 write actions.

    A4 HIGH RISK: This function routes to MCP tools that make real writes
    (flag email, reply, accept/decline calendar events, Teams messages).
    All M15 action types carry autonomy_cap='L1_permanent' — user confirmation
    is ALWAYS required before any call reaches this function (FR-19, G1, F11).

    OQ-1 (P0 BLOCKING): Python batch MCP token proof is unresolved.
    Until OQ-1 is resolved, this function logs a warning and does NOT
    execute any MCP call. Wire to the action executor once unblocked.
    """
    import logging as _log
    _DISPATCH_MAP = {
        "m365_flag":        "mcp_m365-mail_FlagEmail",
        "m365_reply":       "mcp_m365-mail_ReplyToMessage",
        "m365_decline":     "mcp_m365-calendar_DeclineEvent",
        "m365_accept":      "mcp_m365-calendar_AcceptEvent",
        "m365_teams_reply": "mcp_m365-teams_SendMessageToChat",
    }
    if action_type not in _DISPATCH_MAP:
        raise ValueError(f"Unknown M15 action type: {action_type!r}")
    _log.getLogger(__name__).warning(
        "m365_write_dispatch_pending action=%s mcp_tool=%s payload=%r "
        "(OQ-1: Python batch MCP token unresolved — write blocked)",
        action_type, _DISPATCH_MAP[action_type], payload,
    )


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def run(
    artha_dir: Path,
    mcp_mode: bool = False,
    verbose: bool = False,
) -> int:
    """Signal extraction → composition → queue. Returns proposal count.

    Ref: specs/actions-reloaded.md §WB-1 run() implementation
    """
    # Guard: actions enabled?
    if not _actions_enabled(artha_dir):
        print("[action_orchestrator] actions disabled — skipping", file=sys.stderr)
        return 0

    # Guard: read-only mode?
    if _is_read_only(artha_dir):
        print("[action_orchestrator] read-only mode — skipping", file=sys.stderr)
        return 0

    burn_in = _burn_in_mode(artha_dir)

    # RH-1: Import-level handler health check at startup
    handler_failures = _handler_health_check(artha_dir)
    unavailable_types: set[str] = set()
    if handler_failures:
        print(
            f"[action_orchestrator] {len(handler_failures)} handler(s) failed import check:",
            file=sys.stderr,
        )
        for f in handler_failures:
            print(f"  ✗ {f}", file=sys.stderr)
            # Extract action type from "action_type (module): error" format
            action_type = f.split(" (")[0].strip()
            unavailable_types.add(action_type)
        _audit_log(
            artha_dir,
            f"ACTION_HANDLER_CHECK | failures:{len(handler_failures)} | "
            f"types:{','.join(sorted(unavailable_types))}",
        )

    ActionComposer, ActionExecutor, EmailSignalExtractor = _import_action_modules()

    # 1. Collect signals from email extractor (skip in --mcp mode)
    email_signals: list[Any] = []
    emails_path = artha_dir / "tmp" / "pipeline_output.jsonl"

    if mcp_mode:
        if verbose:
            print("[action_orchestrator] --mcp: skipping email signal extraction", file=sys.stderr)
    elif emails_path.exists():
        emails = _load_emails(emails_path)
        if emails:
            try:
                extractor = EmailSignalExtractor()
                email_signals = extractor.extract(emails)
                if verbose:
                    print(
                        f"[action_orchestrator] email signals: {len(email_signals)} from {len(emails)} emails",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(f"[action_orchestrator] email extractor error: {exc}", file=sys.stderr)
        else:
            if verbose:
                print("[action_orchestrator] email JSONL found but 0 non-marketing records", file=sys.stderr)
    else:
        # RH-2: Graceful degradation on missing pipeline file
        print(
            "[action_orchestrator] no pipeline output found — "
            "email signals: 0 (no pipeline data). Use --mcp if running Tier 1.",
            file=sys.stderr,
        )

    # 2. Collect signals from pattern engine
    pattern_signals: list[Any] = []
    try:
        from pattern_engine import PatternEngine  # type: ignore[import]
        engine = PatternEngine(root_dir=artha_dir)
        pattern_signals = engine.evaluate()
        if verbose:
            print(f"[action_orchestrator] pattern signals: {len(pattern_signals)}", file=sys.stderr)
    except Exception as exc:
        print(f"[action_orchestrator] pattern engine error: {exc}", file=sys.stderr)

    # 3. Load AI-emitted signals (default-off; gated by harness.actions.ai_signals)
    # Hardening: schema validation, source enforcement, friction escalation applied here.
    # Ref: specs/actions-reloaded.md §SP-4, §7.2.1
    ai_signals: list[Any] = []
    if _ai_signals_enabled(artha_dir):
        ai_signal_path = artha_dir / "tmp" / "ai_signals.jsonl"
        if ai_signal_path.exists():
            ai_signals = _load_ai_signals(ai_signal_path)
            if verbose:
                print(
                    f"[action_orchestrator] ai signals: {len(ai_signals)} loaded",
                    file=sys.stderr,
                )
        elif verbose:
            print("[action_orchestrator] ai_signals enabled but tmp/ai_signals.jsonl absent", file=sys.stderr)
    else:
        if verbose:
            print("[action_orchestrator] ai signals disabled (harness.actions.ai_signals=false)", file=sys.stderr)

    # 4. Merge + deduplicate (email + pattern + AI)
    all_signals = _deduplicate(email_signals + pattern_signals + ai_signals)
    suppressed = (len(email_signals) + len(pattern_signals) + len(ai_signals)) - len(all_signals)

    if verbose:
        print(
            f"[action_orchestrator] total signals: {len(all_signals)} "
            f"({suppressed} within-run duplicates suppressed)",
            file=sys.stderr,
        )

    # 5. Persist signals for audit (envelope only — no PII metadata)
    signals_path = artha_dir / "tmp" / "signals.jsonl"
    _persist_signals(signals_path, all_signals)

    # 6. Compose → propose
    composer = ActionComposer(artha_dir=artha_dir)
    executor = ActionExecutor(artha_dir)
    proposed = 0
    quality_suppressed = 0

    # Quality layer: ONE DB connection for the entire run (CONSTRAINT 9)
    _quality_conn: Any = None
    _quality_cfg: dict = {}
    try:
        from action_queue import _open_db as _aq_open_qc, ActionQueue as _AQ_qc  # noqa: PLC0415
        _qdb_path = _AQ_qc._resolve_db_path(artha_dir)
        if _qdb_path.exists():
            _quality_conn = _aq_open_qc(_qdb_path)
    except Exception as exc:
        print(f"[action_orchestrator] quality DB unavailable: {exc}", file=sys.stderr)

    try:
        cfg = _load_config(artha_dir)
        _quality_cfg = (
            (cfg.get("harness") or {}).get("actions", {}).get("quality") or {}
        )
    except Exception:
        pass

    _min_confidence: float = float(_quality_cfg.get("min_confidence", 0.5))
    _suggestion_threshold: float = float(_quality_cfg.get("suggestion_threshold", 0.65))
    _loop_threshold: int = int(_quality_cfg.get("loop_quarantine_threshold", 4))

    for signal in all_signals:
        signal_type = getattr(signal, "signal_type", "unknown")
        is_ai_signal = getattr(signal, "_ai_origin", False)
        action_type_target = ""

        # Skip signals whose target action_type's handler failed import
        try:
            from action_composer import _load_signal_routing  # type: ignore[import]
            route = _load_signal_routing().get(signal_type, {})
            action_type_target = route.get("action_type", "")
        except Exception:
            pass

        if action_type_target in unavailable_types:
            print(
                f"[action_orchestrator] skipping {signal_type} — handler unavailable for {action_type_target}",
                file=sys.stderr,
            )
            continue

        # Quality layer: pre-compose checks on the signal itself
        _signal_subtype = getattr(signal, "subtype", signal_type) or signal_type
        _domain = getattr(signal, "domain", "unknown")
        _entity_raw = str(getattr(signal, "entity", "") or "")
        _normalized_entity = _normalize_entity_for_dedup(_entity_raw)
        _sig_confidence = float(getattr(signal, "confidence", 0.0) or 0.0)

        if _quality_conn is not None:
            # Loop quarantine check (CONSTRAINT 7)
            if _check_loop_quarantine(_quality_conn, _signal_subtype, _domain, _normalized_entity):
                if verbose:
                    print(
                        f"[action_orchestrator] loop-quarantined: {signal_type}/{_domain}/{_normalized_entity[:30]}",
                        file=sys.stderr,
                    )
                quality_suppressed += 1
                continue

            # Signal suppression check: ML-learned suppressions (§4.3.1 point 4)
            if _check_signal_suppression(_quality_conn, _signal_subtype, _domain):
                quality_suppressed += 1
                if verbose:
                    print(
                        f"[action_orchestrator] signal_suppression suppressed: {_signal_subtype}×{_domain}",
                        file=sys.stderr,
                    )
                continue

            # Consecutive non-accepted check → quarantine threshold
            consecutive = _count_consecutive_non_accepted(
                _quality_conn, _signal_subtype, _domain, _normalized_entity
            )
            if consecutive >= _loop_threshold:
                _record_loop_quarantine(_quality_conn, _signal_subtype, _domain, _normalized_entity)
                if verbose:
                    print(
                        f"[action_orchestrator] entering quarantine ({consecutive}× rejected): "
                        f"{signal_type}/{_domain}",
                        file=sys.stderr,
                    )
                quality_suppressed += 1
                continue

            # Category-aware dedup: suppress if recently rejected with a window-extending category (§4.4.3)
            if _check_category_dedup(_quality_conn, _signal_subtype, _domain, _normalized_entity):
                quality_suppressed += 1
                if verbose:
                    print(
                        f"[action_orchestrator] category-dedup suppressed: {_signal_subtype}×{_domain}",
                        file=sys.stderr,
                    )
                continue

            # Domain confidence gate
            _dc_rate = _domain_confidence(_quality_conn, _signal_subtype, _domain)
            if _dc_rate < 0.2:
                # Suppressed entirely
                quality_suppressed += 1
                if verbose:
                    print(
                        f"[action_orchestrator] domain confidence suppressed ({_dc_rate:.2f}): {signal_type}",
                        file=sys.stderr,
                    )
                continue
            # (0.2–0.5 range → suggestion-only; handled post-propose)
            # (0.5–0.8 range → friction escalation; handled post-propose)

        # Signal confidence gate
        if _sig_confidence > 0.0 and _sig_confidence < _min_confidence:
            quality_suppressed += 1
            if verbose:
                print(
                    f"[action_orchestrator] confidence below min ({_sig_confidence:.2f}): {signal_type}",
                    file=sys.stderr,
                )
            continue

        try:
            proposal = composer.compose(signal)
            if proposal is None:
                continue

            # Hardening step 2+4: Escalate friction for all AI-origin proposals.
            # Injection-suspected signals get friction='blocked'; others get 'high'.
            # Prevents batch-approval via --approve-all-low for AI-generated actions.
            # Ref: specs/actions-reloaded.md §SP-4
            if is_ai_signal:
                proposal = _apply_ai_signal_hardening(proposal, signal=signal)

            # Pre-enqueue handler validation (catches structural issues before user sees proposal)
            try:
                _validate_proposal_handler(executor, proposal)
            except ValueError as ve:
                print(
                    f"[action_orchestrator] handler validation failed for {signal_type}: {ve}",
                    file=sys.stderr,
                )
                continue

            # Domain confidence → friction escalation (0.5–0.8) or suggestion-only (<0.5)
            if _quality_conn is not None and _dc_rate < 0.8:
                from dataclasses import replace as _dc_replace  # noqa: PLC0415
                if _dc_rate < 0.5:
                    # Below 0.5 → suggestion only (do NOT enqueue; §4.4.2)
                    quality_suppressed += 1
                    if verbose:
                        print(
                            f"  📝 SUGGESTION (low domain confidence {_dc_rate:.0%}) | "
                            f"{signal_type} | {getattr(signal, 'entity', '')[:50]}",
                        )
                    _audit_log(
                        artha_dir,
                        f"SIGNAL_SUGGESTION | type:{signal_type} | domain_conf:{_dc_rate:.2f}",
                    )
                    continue
                else:
                    # 0.5–0.8 → escalate friction
                    try:
                        proposal = _dc_replace(proposal, friction="high")
                    except Exception:
                        pass

            # Signal confidence → suggestion threshold: surface as informational, do NOT enqueue
            # Suggestions don't count against acceptance rate (§4.4.2)
            if _sig_confidence > 0.0 and _sig_confidence < _suggestion_threshold:
                if verbose:
                    print(
                        f"  📝 SUGGESTION | {signal_type} | {getattr(signal, 'entity', '')[:50]}"
                        f" | confidence:{_sig_confidence:.0%}",
                    )
                _audit_log(
                    artha_dir,
                    f"SIGNAL_SUGGESTION | type:{signal_type} | confidence:{_sig_confidence:.2f} "
                    f"| entity:{getattr(signal, 'entity', '')[:40]}",
                )
                continue

            executor.propose_direct(proposal)
            proposed += 1

            # Write normalized_entity and confidence to actions row (quality metadata)
            if _quality_conn is not None:
                try:
                    _quality_conn.execute(
                        "UPDATE actions SET normalized_entity = ?, confidence = ? WHERE id = ?",
                        (_normalized_entity, _sig_confidence if _sig_confidence > 0.0 else None, proposal.id),
                    )
                    _quality_conn.commit()
                except Exception:
                    pass

            # Hardening step 4: AI-signal proposals get [AI-SIGNAL] audit prefix for
            # easy filtering during burn-in review.
            # Ref: specs/actions-reloaded.md §SP-4
            ai_tag = "[AI-SIGNAL] " if is_ai_signal else ""
            _audit_log(
                artha_dir,
                f"{ai_tag}ACTION_PROPOSED | id:{proposal.id} | type:{proposal.action_type} "
                f"| domain:{proposal.domain} | friction:{proposal.friction}",
            )

        except ValueError as exc:
            msg = str(exc)
            if msg.startswith("Duplicate") or "idempotency_pending:" in msg or "idempotency_duplicate:" in msg:
                suppressed += 1
                if verbose:
                    print(
                        f"[action_orchestrator] duplicate suppressed for {signal_type}: {exc}",
                        file=sys.stderr,
                    )
                continue
            if "quality_rejected:" in msg:
                quality_suppressed += 1
                if verbose:
                    print(
                        f"[action_orchestrator] quality rejected for {signal_type}: {exc}",
                        file=sys.stderr,
                    )
                continue
            print(
                f"[action_orchestrator] compose/propose failed for {signal_type}: {exc}",
                file=sys.stderr,
            )
        except Exception as exc:
            # compose/propose loop must never crash the session (Principle 7)
            print(
                f"[action_orchestrator] compose/propose failed for {signal_type}: {exc}",
                file=sys.stderr,
            )

    if _quality_conn is not None:
        try:
            _quality_conn.close()
        except Exception:
            pass

    # 7. Expire stale proposals
    expired = 0
    try:
        expired = executor.expire_stale()
    except Exception as exc:
        print(f"[action_orchestrator] expire_stale failed: {exc}", file=sys.stderr)
    try:
        expired += executor.expire_low_quality_pending()
    except Exception as exc:
        print(f"[action_orchestrator] expire_low_quality_pending failed: {exc}", file=sys.stderr)

    # 7b. Query recently expired actions (last 96h) for surfacing in briefing
    recently_expired: list[dict[str, str]] = []
    try:
        import sqlite3 as _sqlite3
        _db_path = artha_dir.parent / ".artha-local" / "actions.db"
        if not _db_path.exists():
            _db_path = Path.home() / ".artha-local" / "actions.db"
        if _db_path.exists():
            _conn = _sqlite3.connect(str(_db_path))
            _conn.row_factory = _sqlite3.Row
            _rows = _conn.execute(
                "SELECT id, action_type, domain, title FROM actions "
                "WHERE status = 'expired' "
                "AND updated_at > datetime('now', '-96 hours') "
                "ORDER BY updated_at DESC LIMIT 10"
            ).fetchall()
            recently_expired = [dict(r) for r in _rows]
            _conn.close()
    except Exception as exc:
        print(f"[action_orchestrator] recently-expired query failed: {exc}", file=sys.stderr)

    # 8. Fetch pending for display
    pending: list[Any] = []
    try:
        pending = executor.list_pending()
    except Exception as exc:
        print(f"[action_orchestrator] list_pending failed: {exc}", file=sys.stderr)

    # 9. Audit the run
    _audit_log(
        artha_dir,
        f"ACTION_ORCHESTRATOR | signals:{len(all_signals)} suppressed:{suppressed} "
        f"queued:{proposed} expired:{expired} depth:{len(pending)} errors:0",
    )

    # 10. Print summary for AI consumption
    _print_summary(
        all_signals=all_signals,
        email_signal_count=len(email_signals),
        pattern_signal_count=len(pattern_signals),
        proposed=proposed,
        suppressed=suppressed,
        quality_suppressed=quality_suppressed,
        expired=expired,
        pending=pending,
        recently_expired=recently_expired,
        burn_in=burn_in,
        verbose=verbose,
    )

    # RD-43: Write orchestrator funnel metrics for eval_runner and CI
    try:
        import json as _json  # noqa: PLC0415
        from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
        _metrics_dir = artha_dir / "tmp"
        _metrics_dir.mkdir(parents=True, exist_ok=True)
        _orch_metrics = {
            "run_at": _dt.now(tz=_tz.utc).isoformat(),
            "signals_in": len(all_signals),
            "proposals_composed": proposed,
            "proposals_suppressed_duplicates": suppressed,
            "proposals_rejected_quality": quality_suppressed,
            "proposals_queued": proposed,
        }
        (_metrics_dir / "orchestrator_metrics.json").write_text(
            _json.dumps(_orch_metrics, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass  # metrics write failure must never crash pipeline

    executor.close()
    return proposed


def _run_with_timeout(
    artha_dir: Path,
    mcp_mode: bool = False,
    verbose: bool = False,
    timeout_sec: int = _RUN_TIMEOUT_SEC,
) -> int:
    """Invoke run() with a SIGALRM wall-clock timeout (RH-5).

    On timeout: logs ACTION_ORCHESTRATOR_TIMEOUT to audit.md, prints a warning
    to stderr, and returns 0 (proposals already committed to SQLite remain valid).

    Windows: SIGALRM is unavailable — run() is called without a timeout wrapper.
    The operation is deterministic regex + YAML so a runaway is implausible.

    Ref: specs/actions-reloaded.md §RH-5
    """
    import signal as _sig  # local import avoids shadowing 'signal' loop var in run()

    if not hasattr(_sig, "SIGALRM"):
        # Windows / platforms without SIGALRM — skip timeout enforcement
        return run(artha_dir, mcp_mode=mcp_mode, verbose=verbose)

    def _alarm_handler(signum: int, frame: Any) -> None:  # noqa: ANN001
        raise _OrchestratorTimeout()

    prev_handler = _sig.signal(_sig.SIGALRM, _alarm_handler)
    _sig.alarm(timeout_sec)
    try:
        return run(artha_dir, mcp_mode=mcp_mode, verbose=verbose)
    except _OrchestratorTimeout:
        print(
            f"[action_orchestrator] --run timed out after {timeout_sec}s "
            "— partial results remain in DB",
            file=sys.stderr,
        )
        _audit_log(
            artha_dir,
            f"ACTION_ORCHESTRATOR_TIMEOUT | limit_sec:{timeout_sec}",
        )
        return 0  # proposals committed before timeout remain in DB
    finally:
        _sig.alarm(0)  # cancel any pending alarm
        _sig.signal(_sig.SIGALRM, prev_handler)  # restore original handler


def cmd_list(artha_dir: Path) -> int:
    """Print all pending proposals in numbered format."""
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        pending = executor.list_pending()
        if not pending:
            print("(No pending actions)")
            return 0

        # Read confidence values from actions table (§4.4.2 spec format)
        confidence_map: dict[str, float | None] = {}
        try:
            from action_queue import _open_db as _aq_open_c, ActionQueue as _AQ_c  # noqa: PLC0415
            _c_db = _AQ_c._resolve_db_path(artha_dir)
            if _c_db.exists():
                _c_conn = _aq_open_c(_c_db)
                try:
                    ids_csv = ",".join(f"'{getattr(p,'id','')}'" for p in pending)
                    if ids_csv:
                        for row in _c_conn.execute(
                            f"SELECT id, confidence FROM actions WHERE id IN ({ids_csv})"
                        ):
                            confidence_map[row[0]] = row[1]
                finally:
                    _c_conn.close()
        except Exception:
            pass

        print(f"═══ PENDING ACTIONS ({len(pending)}) ═══════════════════════════════════")
        for i, p in enumerate(pending, 1):
            icon = _FRICTION_ICON.get(getattr(p, "friction", "standard"), "🟠")
            action_type = getattr(p, "action_type", "")
            domain = getattr(p, "domain", "")
            title = getattr(p, "title", "")
            full_id = getattr(p, "id", "")
            pid = full_id[:8]
            friction = getattr(p, "friction", "standard")
            min_trust = getattr(p, "min_trust", 1)
            expires_at = getattr(p, "expires_at", "")
            content_flag = " [content]" if action_type in _CONTENT_BEARING_TYPES else ""
            conf = confidence_map.get(full_id)
            conf_str = f" | {conf*100:.0f}%" if conf is not None else ""
            print(f"{i}. [{pid}] {icon} {action_type} | {domain}{conf_str} | {title[:60]}{content_flag}")
            print(f"   Friction: {friction} | Trust: {min_trust} | Expires: {expires_at}")

        # Acceptance rate line (§4.5.3)
        try:
            from action_queue import _open_db as _aq_open_lr, ActionQueue as _AQ_lr  # noqa: PLC0415
            _lr_db = _AQ_lr._resolve_db_path(artha_dir)
            if _lr_db.exists():
                _lrconn = _aq_open_lr(_lr_db)
                try:
                    _rate_30 = _acceptance_rate_windowed(_lrconn, window_days=30, min_decisions=10)
                    _rate_all = _acceptance_rate_windowed(_lrconn, window_days=3650, min_decisions=3)
                    if _rate_30 is not None or _rate_all is not None:
                        _r30_str = f"{_rate_30*100:.0f}%" if _rate_30 is not None else "—"
                        _rall_str = f"{_rate_all*100:.0f}%" if _rate_all is not None else "—"
                        print(f"\n  Acceptance rate: {_r30_str} (30d) | {_rall_str} (all-time)")
                finally:
                    _lrconn.close()
        except Exception:
            pass

        print("════════════════════════════════════════════════════════════════")
        return 0
    finally:
        executor.close()


def cmd_show(artha_dir: Path, action_id: str) -> int:
    """Display full content preview for a proposal (required for content-bearing actions).

    Ref: specs/actions-reloaded.md §WB-1 --show behaviour
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        proposal = executor.get_action(action_id)
        if not proposal:
            # Try prefix match (user may have given the 8-char prefix)
            if len(action_id) < 36:
                try:
                    full_id = _resolve_id(executor, action_id)
                    proposal = executor.get_action(full_id)
                except ValueError as e:
                    print(f"[action] {e}", file=sys.stderr)
                    return 1
            if not proposal:
                print(f"[action] Action '{action_id}' not found.", file=sys.stderr)
                return 1
        _print_expanded_preview(proposal)
        return 0
    finally:
        executor.close()


def cmd_approve(artha_dir: Path, action_id: str) -> int:
    """Approve and execute a pending proposal.

    Ref: specs/actions-reloaded.md §WB-1 --approve behaviour
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        # Resolve prefix to full ID if needed
        action_id = _resolve_id(executor, action_id)
        result = executor.approve(action_id, approved_by="user:terminal")
        status = getattr(result, "status", "unknown")
        message = getattr(result, "message", "")
        if status == "success":
            print(f"[action] ✓ {status}: {message}")
            _audit_log(artha_dir, f"ACTION_APPROVED | id:{action_id} | by:user:terminal")
            _audit_log(artha_dir, f"ACTION_EXECUTED | id:{action_id} | status:success | msg:{message!r}")
            return 0
        else:
            print(f"[action] ✗ {status}: {message}")
            _audit_log(artha_dir, f"ACTION_EXECUTED | id:{action_id} | status:failure | msg:{message!r}")
            return 1
    except Exception as exc:
        print(f"[action] ✗ approve failed: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()


def cmd_reject(artha_dir: Path, action_id: str, reason: str = "") -> int:
    """Reject a pending proposal.

    When run interactively (stdin is a TTY) and no --reason flag was given,
    prompts for a rejection category to drive policy learning (§4.5.1).

    Categories:
      1 — Already handled (manual / autopay)
      2 — Wrong action type
      3 — Not relevant / stale
      4 — Other
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        action_id = _resolve_id(executor, action_id)

        # Interactive category prompt (CONSTRAINT 8)
        rejection_category: int | None = None
        _category_labels = {
            1: "already_handled",
            2: "wrong_action_type",
            3: "not_relevant",
            4: "other",
        }
        if sys.stdin.isatty() and not reason:
            print("\n  Rejection category:")
            print("    1 — Already handled (manual / autopay)")
            print("    2 — Wrong action type")
            print("    3 — Not relevant / stale")
            print("    4 — Other")
            _cat_input = input("  Category [1-4, Enter to skip]: ").strip()
            if _cat_input.isdigit() and 1 <= int(_cat_input) <= 4:
                rejection_category = int(_cat_input)
                if not reason:
                    reason = _category_labels[rejection_category]

        executor.reject(action_id, reason=reason)
        print(f"[action] ✓ rejected: {action_id[:8]}")
        _audit_log(artha_dir, f"ACTION_REJECTED | id:{action_id} | reason:{reason!r}")

        # Write rejection_category to trust_metrics
        if rejection_category is not None or reason:
            _write_rejection_category(artha_dir, action_id, reason, rejection_category)

        # Stage policy suggestion for cat 1, 2, or 3
        if rejection_category in (1, 2, 3):
            _stage_policy_suggestion(artha_dir, action_id, rejection_category, executor)

        return 0
    except Exception as exc:
        print(f"[action] ✗ reject failed: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()


def cmd_defer(artha_dir: Path, action_id: str, until: str = "next-session") -> int:
    """Defer a pending proposal to a later time.

    The until preset is resolved to an ISO-8601 UTC string before calling
    executor.defer(), which only understands +Nh offsets and ISO strings.
    Ref: specs/actions-reloaded.md §WB-1 --defer behaviour
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        action_id = _resolve_id(executor, action_id)
        resolved_until = _resolve_defer_preset(until)
        executor.defer(action_id, until=resolved_until)
        print(f"[action] ✓ deferred to {resolved_until}: {action_id[:8]}")
        _audit_log(artha_dir, f"ACTION_DEFERRED | id:{action_id} | until:{resolved_until}")
        return 0
    except Exception as exc:
        print(f"[action] ✗ defer failed: {exc}", file=sys.stderr)
        return 1
    finally:
        executor.close()


def cmd_approve_all_low(artha_dir: Path) -> int:
    """Approve all low-friction pending proposals. High/standard friction untouched.

    RD-11: Idempotency is checked BEFORE auto-approval — idempotency keys that
    were reserved at enqueue time may have expired (TTL boundary) since then,
    creating a window where the same action could be re-queued and re-approved.
    This check re-validates the key at approval time to prevent duplicate execution.

    Ref: specs/actions-reloaded.md §T-U-19
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        pending = executor.list_pending()
        low_friction = [p for p in pending if getattr(p, "friction", "standard") == "low"]
        if not low_friction:
            print("[action] No low-friction proposals pending.")
            return 0
        approved = 0
        failed = 0
        skipped_idem = 0
        for p in low_friction:
            pid = getattr(p, "id", "")
            # RD-11: Re-run idempotency check at approval time to guard TTL boundary
            _stored_key = getattr(p, "_idem_key", None)
            if _stored_key:
                try:
                    from lib.idempotency import IdempotencyStore as _IdemStore  # noqa: PLC0415
                    _idem_store = _IdemStore(
                        artha_dir / "state" / "idempotency_keys.json"
                    )
                    _idem_data = _idem_store._load()
                    _entry = _idem_data.get(_stored_key)
                    if _entry and _entry.get("status") == "COMPLETED":
                        # Key still marked COMPLETED — this is a duplicate at TTL boundary
                        from datetime import datetime, timezone as _tz  # noqa: PLC0415
                        _expires = _entry.get("expires_at", "")
                        _is_expired = False
                        if _expires:
                            try:
                                _is_expired = datetime.now(_tz.utc) > datetime.fromisoformat(_expires)
                            except ValueError:
                                pass
                        if not _is_expired:
                            print(
                                f"[action] ⟳ skipped {pid[:8]} (idempotency: already completed, "
                                f"key not yet expired — TTL boundary guard)",
                            )
                            _audit_log(
                                artha_dir,
                                f"ACTION_IDEMPOTENCY_SKIP_AT_APPROVAL | id:{pid} | "
                                f"key:{_stored_key[:8]} | reason:approve_all_low_ttl_boundary",
                            )
                            skipped_idem += 1
                            continue
                except Exception:
                    pass  # Idempotency re-check is best-effort; never block approval
            try:
                result = executor.approve(pid, approved_by="user:terminal")
                status = getattr(result, "status", "unknown")
                if status == "success":
                    print(f"[action] ✓ approved {pid[:8]}: {getattr(p, 'title', '')[:60]}")
                    approved += 1
                else:
                    print(f"[action] ✗ {pid[:8]}: {getattr(result, 'message', '')}")
                    failed += 1
            except Exception as exc:
                print(f"[action] ✗ {pid[:8]}: {exc}", file=sys.stderr)
                failed += 1
        print(f"[action] approve-all-low: {approved} approved, {failed} failed, "
              f"{skipped_idem} idempotency-blocked, "
              f"{len(pending) - len(low_friction)} high/standard skipped")
        return 0 if failed == 0 else 1
    finally:
        executor.close()


def cmd_expire(artha_dir: Path) -> int:
    """Sweep expired proposals from the queue.

    Writes trust_metrics rows with user_decision='expired' for each
    action that is expiring (CONSTRAINT 10).
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        # Query expirable actions BEFORE calling expire_stale() (CONSTRAINT 10)
        expirable: list[dict] = []
        try:
            from action_queue import _open_db as _aq_open_exp, ActionQueue as _AQ_exp  # noqa: PLC0415
            _exp_db = _AQ_exp._resolve_db_path(artha_dir)
            if _exp_db.exists():
                _econn = _aq_open_exp(_exp_db)
                try:
                    _ecur = _econn.execute(
                        "SELECT id, action_type, domain FROM actions "
                        "WHERE status IN ('pending', 'deferred') "
                        "AND expires_at < datetime('now')"
                    )
                    expirable = [
                        {"id": r[0], "action_type": r[1], "domain": r[2]}
                        if isinstance(r, (list, tuple))
                        else dict(r)
                        for r in _ecur.fetchall()
                    ]
                finally:
                    _econn.close()
        except Exception as exc:
            print(f"[action] expiry pre-query failed (trust_metrics will not be written): {exc}", file=sys.stderr)

        count = executor.expire_stale()
        print(f"[action] expired {count} stale proposal(s)")

        # Write trust_metrics rows for each expired action (CONSTRAINT 10)
        if expirable:
            try:
                from action_queue import _open_db as _aq_open_tm, ActionQueue as _AQ_tm  # noqa: PLC0415
                _tm_db = _AQ_tm._resolve_db_path(artha_dir)
                if _tm_db.exists():
                    _tmconn = _aq_open_tm(_tm_db)
                    _ts = datetime.now(timezone.utc).isoformat()
                    try:
                        for ea in expirable:
                            _tmconn.execute(
                                """
                                INSERT OR IGNORE INTO trust_metrics
                                (action_type, domain, signal_subtype, user_decision, proposed_at)
                                VALUES (?, ?, NULL, 'expired', ?)
                                """,
                                (ea["action_type"], ea["domain"], _ts),
                            )
                        _tmconn.commit()
                    finally:
                        _tmconn.close()
            except Exception as exc:
                print(f"[action] trust_metrics expiry write failed: {exc}", file=sys.stderr)

        if count:
            _audit_log(artha_dir, f"ACTION_EXPIRY_SWEEP | expired:{count}")
        return 0
    finally:
        executor.close()


def cmd_health(artha_dir: Path) -> int:
    """Print action layer health status.

    Ref: specs/actions-reloaded.md §E.2 --health extended output
    """
    _, ActionExecutor, _ = _import_action_modules()
    executor = ActionExecutor(artha_dir)
    try:
        # Queue stats
        try:
            stats = executor.queue_stats()
        except Exception:
            stats = {}

        # Handler health (full functional check)
        try:
            handler_results = executor.run_health_checks()
        except Exception:
            handler_results = {}

        healthy_handlers = sum(1 for ok in handler_results.values() if ok)
        total_handlers = len(handler_results)

        # Config flags
        cfg = _load_artha_config(artha_dir)
        actions_enabled = _nested_get(cfg, "harness", "actions", "enabled", default=True)
        ai_signals = _nested_get(cfg, "harness", "actions", "ai_signals", default=False)
        burn_in = _nested_get(cfg, "harness", "actions", "burn_in", default=False)

        # DB path
        from action_queue import ActionQueue  # type: ignore[import]
        db_path = ActionQueue._resolve_db_path(artha_dir)

        print("═══ ACTION LAYER HEALTH ════════════════════════════════════════")
        pending_count = stats.get("total_pending", stats.get("pending", 0))
        deferred_count = stats.get("total_deferred", stats.get("deferred", 0))
        print(f"Queue: {pending_count} pending, {deferred_count} deferred")

        if handler_results:
            unhealthy = [t for t, ok in handler_results.items() if not ok]
            health_str = f"{healthy_handlers}/{total_handlers} healthy"
            if unhealthy:
                health_str += f" ({', '.join(unhealthy)}: unavailable)"
            print(f"Handlers: {health_str}")
        else:
            print("Handlers: (no data)")

        print(
            f"Config: actions.enabled={actions_enabled}, "
            f"ai_signals={ai_signals}, burn_in={burn_in}"
        )
        print(f"DB: {db_path}")
        print("════════════════════════════════════════════════════════════════")
        return 0
    finally:
        executor.close()


# ---------------------------------------------------------------------------
# ID resolution helper (prefix → full UUID)
# ---------------------------------------------------------------------------

def _resolve_id(executor: Any, id_or_prefix: str) -> str:
    """Resolve a short (8-char) ID prefix to a full UUID.

    If the prefix is already a full UUID (36 chars), return as-is.
    Raises ValueError if the prefix is ambiguous or not found.
    """
    if len(id_or_prefix) == 36:
        return id_or_prefix  # already a full UUID

    pending = executor.list_pending()
    matches = [p for p in pending if getattr(p, "id", "").startswith(id_or_prefix)]

    if not matches:
        # Not in pending — check deferred and other non-terminal statuses
        matches = executor.queue.find_by_prefix(id_or_prefix)

    if not matches:
        raise ValueError(
            f"Action prefix '{id_or_prefix}' not found in any active action."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous prefix '{id_or_prefix}' — {len(matches)} proposals match. "
            "Use a longer prefix or the full ID."
        )
    return getattr(matches[0], "id", id_or_prefix)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="action_orchestrator.py",
        description="Artha action layer CLI — signal extraction, proposal management.",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run",
        action="store_true",
        help="Extract signals, compose proposals, print pending queue",
    )
    group.add_argument(
        "--list",
        action="store_true",
        dest="list_pending",
        help="List all pending proposals",
    )
    group.add_argument(
        "--show",
        metavar="ID",
        help="Show expanded preview of a proposal",
    )
    group.add_argument(
        "--approve",
        metavar="ID",
        help="Approve and execute a proposal",
    )
    group.add_argument(
        "--reject",
        metavar="ID",
        help="Reject a proposal",
    )
    group.add_argument(
        "--defer",
        metavar="ID",
        help="Defer a proposal to a later time",
    )
    group.add_argument(
        "--approve-all-low",
        action="store_true",
        help="Approve all low-friction proposals",
    )
    group.add_argument(
        "--expire",
        action="store_true",
        help="Sweep expired proposals",
    )
    group.add_argument(
        "--health",
        action="store_true",
        help="Print action layer health status",
    )
    group.add_argument(
        "--apply-suggestions",
        action="store_true",
        dest="apply_suggestions",
        help="Review and apply staged policy suggestions to user_context.yaml",
    )
    group.add_argument(
        "--reset-domain-confidence",
        nargs=2,
        metavar=("SIGNAL_SUBTYPE", "DOMAIN"),
        dest="reset_domain_confidence",
        help="Reset trust metrics for a signal_subtype × domain pair",
    )

    p.add_argument(
        "--mcp",
        action="store_true",
        help="MCP Tier 1: skip email signal extraction (pattern engine only)",
    )
    p.add_argument(
        "--reason",
        default="",
        help="Reason for rejection (used with --reject)",
    )
    p.add_argument(
        "--until",
        default="next-session",
        help="Defer horizon: +1h, +4h, tomorrow, next-session (default: next-session)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging to stderr",
    )
    p.add_argument(
        "--artha-dir",
        metavar="PATH",
        default=str(_ARTHA_DIR),
        help="Artha workspace root (default: parent of scripts/)",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    artha_dir = Path(args.artha_dir).resolve()

    if args.run:
        # RH-5: wrapped with wall-clock timeout (UNIX only)
        proposed = _run_with_timeout(artha_dir, mcp_mode=args.mcp, verbose=args.verbose)
        return 0 if proposed >= 0 else 3

    if args.list_pending:
        return cmd_list(artha_dir)

    if args.show:
        return cmd_show(artha_dir, args.show)

    if args.approve:
        return cmd_approve(artha_dir, args.approve)

    if args.reject:
        return cmd_reject(artha_dir, args.reject, reason=args.reason)

    if args.defer:
        return cmd_defer(artha_dir, args.defer, until=args.until)

    if args.approve_all_low:
        return cmd_approve_all_low(artha_dir)

    if args.expire:
        return cmd_expire(artha_dir)

    if args.health:
        return cmd_health(artha_dir)

    if args.apply_suggestions:
        return cmd_apply_suggestions(artha_dir)

    if args.reset_domain_confidence:
        signal_subtype, domain = args.reset_domain_confidence
        return cmd_reset_domain_confidence(artha_dir, signal_subtype, domain)

    return 2  # unreachable — argparse enforces mutual exclusion


if __name__ == "__main__":
    sys.exit(main())
