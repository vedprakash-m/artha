#!/usr/bin/env python3
"""
scripts/agents/logistics_agent.py — LogisticsAgent pre-compute (EAR-3, §5.2).

YAML-to-Markdown rebuilder — NO LLM, NO SQLite.
Reads state/logistics.yaml (entity store: appliances, warranties, subscriptions)
and writes a structured summary to state/logistics.md.

This script is a pure transformation — it performs no AI inference, no scoring,
and no outbound network calls. The LLM reads the output file to compose proposals.

Rules (§5.2):
    - Skips all entities with domain: home (handled by HomeAgent, not yet implemented)
    - Expiration windows: 30 / 90 / 180 days from today
    - Subscription renewals: yearly within next 90 days, monthly always shown
    - Amazon deeplinks: https://www.amazon.com/s?k={url-encoded query}
    - No PII left in output (state/logistics.md is not encrypted tier)

NOTE: The injection-scan (C1.4, §8.3) is for receipt OCR input, NOT for this
YAML-to-Markdown rebuild path. This script trusts state/logistics.yaml as an
already-sanitized source.

State files written:
    state/logistics.md          — rebuilt human/LLM-readable summary
    tmp/logistics_last_run.json — EAR-8 heartbeat

YAML schema expected in state/logistics.yaml:
    items:                   # list of entities
      - name: "Dishwasher"
        type: appliance      # appliance | subscription | warranty | membership | other
        domain: home         # SKIP these
        warranty_expiry: "2026-08-15"     # optional
        subscription_renewal: "2026-05-01" # optional
        renewal_amount: 14.99             # optional (USD)
        renewal_period: monthly           # monthly | yearly | one-time
        notes: "Extended plan from Costco"
        purchase_url: ""     # optional deeplink
        shopping_query: ""   # optional search term for Amazon deeplink
        tags: []             # optional

Ref: specs/prd-reloaded.md §5.2, §8.3, §9.1
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_STATE_DIR = _REPO_ROOT / "state"
_TMP_DIR = _REPO_ROOT / "tmp"

_YAML_FILE = _STATE_DIR / "logistics.yaml"
_OUT_FILE = _STATE_DIR / "logistics.md"
_HEARTBEAT = _TMP_DIR / "logistics_last_run.json"

_EXPIRY_WINDOWS = (30, 90, 180)   # days
_AMAZON_SEARCH_URL = "https://www.amazon.com/s?k={}"
_SKIP_DOMAIN = "home"             # §5.2: skip home-domain entities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_heartbeat(status: str, records_written: int, trace_id: str) -> None:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "domain": "logistics",
        "session_trace_id": trace_id,
        "timestamp_utc": _now_utc(),
        "status": status,
        "records_written": records_written,
    }
    _HEARTBEAT.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML using stdlib only (no PyYAML — it may not be installed).
    Falls back to a minimal block-scalar parser sufficient for flat list-of-dicts.
    If PyYAML is available, uses it for robustness.
    """
    text = path.read_text(encoding="utf-8", errors="replace")

    # Prefer PyYAML if available
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    # Minimal fallback: parse simple flat keys under `items:` blocks.
    # Handles only single-level: "  key: value" style (no nested structures).
    result: dict[str, Any] = {"items": []}
    current_item: dict[str, str] | None = None
    in_items = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("items:"):
            in_items = True
            continue
        if in_items:
            if stripped.startswith("- "):
                if current_item is not None:
                    result["items"].append(current_item)
                current_item = {}
                rest = stripped[2:].strip()
                if ":" in rest:
                    k, _, v = rest.partition(":")
                    current_item[k.strip()] = v.strip().strip('"').strip("'")
            elif stripped.startswith("#") or stripped == "":
                continue
            elif ":" in stripped and current_item is not None:
                k, _, v = stripped.partition(":")
                current_item[k.strip()] = v.strip().strip('"').strip("'")
            elif stripped and not stripped.startswith(" ") and not stripped.startswith("-"):
                in_items = False  # top-level key — end of items block

    if current_item is not None and in_items:
        result["items"].append(current_item)

    return result


def _days_until(date_str: str) -> int | None:
    """Parse YYYY-MM-DD and return days until that date (negative = past)."""
    if not date_str:
        return None
    try:
        target = date.fromisoformat(str(date_str).strip())
        return (target - date.today()).days
    except ValueError:
        return None


def _amazon_link(query: str) -> str:
    return _AMAZON_SEARCH_URL.format(quote_plus(query))


def _window_label(days: int) -> str:
    if days <= 30:
        return "30-day"
    if days <= 90:
        return "90-day"
    return "180-day"


# ---------------------------------------------------------------------------
# Core transformation
# ---------------------------------------------------------------------------

def _build_sections(items: list[dict[str, Any]], today: date) -> tuple[
    list[str], list[str], list[str]
]:
    """Returns (expiration_lines, subscription_lines, shopping_lines)."""
    expiry_rows: list[tuple[int, dict[str, Any]]] = []
    subscription_rows: list[dict[str, Any]] = []
    shopping_items: list[dict[str, Any]] = []
    unrecorded: list[str] = []

    for item in items:
        # Skip home-domain entities (§5.2)
        if str(item.get("domain", "")).lower() == _SKIP_DOMAIN:
            continue

        name = item.get("name", "Unknown")
        itype = str(item.get("type", "other")).lower()

        # ── Warranties ──────────────────────────────────────────────────────
        warranty_raw = item.get("warranty_expiry", "")
        if warranty_raw:
            days = _days_until(str(warranty_raw))
            if days is not None and days <= max(_EXPIRY_WINDOWS):
                expiry_rows.append((days, item))
        elif itype == "appliance":
            unrecorded.append(name)

        # ── Subscriptions ────────────────────────────────────────────────────
        renewal_raw = item.get("subscription_renewal", "")
        if renewal_raw:
            days = _days_until(str(renewal_raw))
            period = str(item.get("renewal_period", "yearly")).lower()
            amount = item.get("renewal_amount", "?")
            if days is not None:
                show_renewal = (
                    period == "monthly"
                    or (period == "yearly" and days <= 90)
                )
                if show_renewal:
                    subscription_rows.append({
                        "name": name,
                        "period": period,
                        "amount": amount,
                        "days_until": days,
                        "renewal_date": str(renewal_raw),
                    })

        # ── Shopping list ─────────────────────────────────────────────────────
        sq = str(item.get("shopping_query", "")).strip()
        if sq:
            shopping_items.append({
                "name": name,
                "query": sq,
                "link": _amazon_link(sq),
            })

    # Sort expirations by days ascending
    expiry_rows.sort(key=lambda x: x[0])

    # Build expiration section
    exp_lines: list[str] = []
    if expiry_rows:
        exp_lines.append("| Item | warranty_expiry | Days Until | Window |")
        exp_lines.append("|------|-----------------|-----------|--------|")
        for days, item in expiry_rows:
            expiry_val = str(item.get("warranty_expiry", ""))
            status = _window_label(days) if days >= 0 else "EXPIRED"
            exp_lines.append(
                f"| {item.get('name', 'Unknown')} | {expiry_val} | "
                f"{days} | {status} |"
            )
    if unrecorded:
        exp_lines.append("")
        for n in unrecorded:
            exp_lines.append(f"- warranty: unrecorded — `{n}` has no warranty entry.")
    if not exp_lines:
        exp_lines.append("_No warranty expirations within 180 days._")

    # Build subscription section
    sub_lines: list[str] = []
    if subscription_rows:
        sub_lines.append("| Name | Period | Amount | Renewal Date | Days Until |")
        sub_lines.append("|------|--------|--------|-------------|-----------|")
        for s in sorted(subscription_rows, key=lambda x: x["days_until"]):
            amount_str = f"${s['amount']}" if s["amount"] != "?" else "?"
            sub_lines.append(
                f"| {s['name']} | {s['period']} | {amount_str} | "
                f"{s['renewal_date']} | {s['days_until']} |"
            )
    if not sub_lines:
        sub_lines.append("_No subscriptions due for renewal within 90 days._")

    # Build shopping list
    shop_lines: list[str] = []
    if shopping_items:
        for si in shopping_items:
            shop_lines.append(f"- [{si['name']}]({si['link']})")
    if not shop_lines:
        shop_lines.append("_Shopping list is empty._")

    return exp_lines, sub_lines, shop_lines


def _write_state_file(
    today: str,
    exp_lines: list[str],
    sub_lines: list[str],
    shop_lines: list[str],
    trace_id: str,
) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    content = f"""# Logistics Summary
date: {today}

## Upcoming Expirations
_Windows: 30 / 90 / 180 days. Appliances without a recorded warranty are flagged._

{chr(10).join(exp_lines)}

## Subscription Review
_Monthly subscriptions shown always. Yearly subscriptions shown within 90 days._

{chr(10).join(sub_lines)}

## Shopping List
_Amazon deeplinks generated from `shopping_query` fields._

{chr(10).join(shop_lines)}

---
_source: state/logistics.yaml_
_generated_at: {_now_utc()}_
_session_trace_id: {trace_id}_
"""
    _OUT_FILE.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    today = date.today()
    today_str = today.isoformat()
    iso_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    trace_id = f"pre-compute-logistics-{iso_ts}"

    # Graceful degradation: no YAML file yet
    if not _YAML_FILE.exists():
        _write_state_file(
            today_str,
            ["_state/logistics.yaml not found — add entries to initialize._"],
            ["_No subscription data._"],
            ["_No shopping data._"],
            trace_id,
        )
        _write_heartbeat("no-yaml", 0, trace_id)
        print("⚠ LogisticsAgent: state/logistics.yaml not found — wrote empty summary.")
        return 0

    try:
        data = _load_yaml(_YAML_FILE)
    except Exception as exc:
        print(f"⛔ LogisticsAgent: failed to parse logistics.yaml: {exc}", file=sys.stderr)
        _write_heartbeat("parse-error", 0, trace_id)
        return 1

    items: list[dict[str, Any]] = data.get("items", []) or []

    try:
        exp_lines, sub_lines, shop_lines = _build_sections(items, today)
        _write_state_file(today_str, exp_lines, sub_lines, shop_lines, trace_id)

        records_written = len(exp_lines) + len(sub_lines) + len(shop_lines)
        _write_heartbeat("success", records_written, trace_id)
        print(
            f"✓ LogisticsAgent: items={len(items)}, "
            f"exp_rows={len(exp_lines)}, sub_rows={len(sub_lines)}, "
            f"shop_items={len(shop_lines)}"
        )
        return 0

    except Exception as exc:
        print(f"⛔ LogisticsAgent failed: {exc}", file=sys.stderr)
        _write_heartbeat("error", 0, trace_id)
        return 1


if __name__ == "__main__":
    sys.exit(main())
