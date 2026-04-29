"""channel/handlers.py — All Artha channel command implementations."""
from __future__ import annotations
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from channel.formatters import (
    _strip_frontmatter, _clean_for_telegram, _trim_to_cap,
    _extract_section_summaries, _split_message, _truncate,
    _filter_noise_bullets, _is_noise_section,
)
from channel.state_readers import (
    _read_state_file, _apply_scope_filter, _get_domain_open_items,
    _get_latest_briefing_path, _domain_freshness, _format_age,
    _READABLE_STATE_FILES, _DOMAIN_TO_STATE_FILE, _get_last_catchup_iso,
)
from channel.llm_bridge import _ask_llm, _gather_context, _detect_llm_cli, _detect_all_llm_clis
from channel.security import _SessionTokenStore
from channel.audit import _audit_log

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_STATE_DIR = _ARTHA_DIR / "state"
_BRIEFINGS_DIR = _ARTHA_DIR / "briefings"

log = logging.getLogger("channel_listener")

_STATUS_EMOJI: dict[str, str] = {"green": "\U0001f7e2", "yellow": "\U0001f7e1", "red": "\U0001f534", "grey": "\u26aa"}

_DOMAIN_STATE_FILES: dict[str, str] = {
    "immigration": "immigration.md.age",
    "finance": "finance.md.age",
    "kids": "kids.md",
    "health": "health.md.age",
    "travel": "travel.md",
    "home": "home.md",
    "shopping": "shopping.md",
    "goals": "goals.md",
    "vehicle": "vehicle.md.age",
    "estate": "estate.md.age",
    "insurance": "insurance.md.age",
    "calendar": "calendar.md",
    "comms": "comms.md",
    "social": "social.md",
    "learning": "learning.md",
    "boundary": "boundary.md",
    "employment": "employment.md",
}


def _system_inventory_lines() -> list[str]:
    """Return passive capability inventory for status surfaces."""
    lines: list[str] = []

    try:
        import mcp_discovery as _mcp_discovery  # noqa: PLC0415

        discovery = _mcp_discovery.discover_mcp(_ARTHA_DIR)
        _mcp_discovery.write_discovery(discovery, _ARTHA_DIR / "tmp" / "mcp_discovery.json")
        counts = discovery.get("counts", {})
        warning_note = ""
        if counts.get("errors", 0):
            warning_note = f", {counts.get('errors')} warning(s)"
        lines.append(
            f"MCP: {counts.get('servers', 0)} server(s) / "
            f"{counts.get('config_files', 0)} config file(s){warning_note}"
        )
    except Exception as exc:
        lines.append(f"MCP: inventory unavailable ({str(exc)[:80]})")

    try:
        import skill_index as _skill_index  # noqa: PLC0415

        index = _skill_index.build_skill_index(_ARTHA_DIR)
        _skill_index.write_skill_index(index, _ARTHA_DIR / "tmp" / "skill_index.json")
        counts = index.get("counts", {})
        warning_note = ""
        missing = counts.get("enabled_missing_modules", 0)
        if missing:
            warning_note = f", {missing} enabled missing module(s)"
        lines.append(
            f"Skills: {counts.get('enabled', 0)}/"
            f"{counts.get('configured', 0)} configured enabled{warning_note}"
        )
    except Exception as exc:
        lines.append(f"Skills: index unavailable ({str(exc)[:80]})")

    return lines



# ── cmd_status ──────────────────────────────────────────────────

async def cmd_status(args: list[str], scope: str) -> tuple[str, str]:
    """Return current system health + active alerts + goal overview."""
    content, staleness = _read_state_file("health_check")
    content = _strip_frontmatter(content)

    # Extract the Last Catch-Up block as a compact summary
    lines = content.splitlines()
    summary_parts: list[str] = ["Artha System Status\n"]
    in_last_catchup = False
    catchup_fields: dict[str, str] = {}

    for line in lines:
        s = line.strip()
        if s.startswith("## Last Catch-Up") or s.startswith("Last Catch-Up"):
            in_last_catchup = True
            continue
        if s.startswith("## ") and in_last_catchup:
            break  # Hit next section
        if in_last_catchup and ":" in s and not s.startswith("#") and not s.startswith("```"):
            k, v = s.split(":", 1)
            catchup_fields[k.strip()] = v.strip().strip('"')

    if catchup_fields:
        ts = catchup_fields.get("last_catch_up", "unknown")
        summary_parts.append(f"Last catch-up: {ts}")
        summary_parts.append(f"Emails processed: {catchup_fields.get('emails_processed', '?')}")
        alerts = catchup_fields.get("alerts_generated", "0")
        if alerts != "0":
            summary_parts.append(f"Alerts generated: {alerts}")
        oi_added = catchup_fields.get("open_items_added", "0")
        oi_closed = catchup_fields.get("open_items_closed", "0")
        if oi_added != "0" or oi_closed != "0":
            summary_parts.append(f"Items: +{oi_added} / -{oi_closed}")
        summary_parts.append(f"Preflight: {catchup_fields.get('preflight', '?')}")
        summary_parts.append(f"Context: {catchup_fields.get('context_window_pct', '?')}%")
    else:
        summary_parts.append("No catch-up data available")

    # Append open item count
    oi_content, _ = _read_state_file("open_items")
    open_count = oi_content.lower().count("status: open")
    summary_parts.append(f"\nOpen items: {open_count}")

    inventory_lines = _system_inventory_lines()
    if inventory_lines:
        summary_parts.append("\nConnections & capabilities:")
        summary_parts.extend(f"- {line}" for line in inventory_lines)

    text = "\n".join(summary_parts)
    return _apply_scope_filter(text, scope), staleness


# ── _build_dashboard_html ───────────────────────────────────────

def _build_dashboard_html() -> str:
    """Build a compact HTML dashboard from live state files.

    Domain colors are based on actual risk:
      🔴 = has P0 items or overdue items
      🟡 = has P1 items (no P0/overdue)
      🟢 = P2 only or no open items
    Non-green domains get a one-liner path-to-green.
    """
    import html as _html
    from datetime import datetime as _dt, date as _date

    # ── Parse all open items ──
    oi_content, _ = _read_state_file("open_items")
    # Per-domain: { domain: { "items": [...], "p0": n, "p1": n, "p2": n, "overdue": n } }
    domain_risk: dict[str, dict] = {}
    total_open = 0
    total_overdue = 0
    total_p0 = 0

    if oi_content:
        lines = oi_content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("- id: OI-"):
                block: dict[str, str] = {"id": line.split(":", 1)[1].strip()}
                i += 1
                while i < len(lines):
                    bline = lines[i].strip()
                    if bline.startswith("- id: OI-") or (not bline and i + 1 < len(lines) and lines[i + 1].strip().startswith("- id:")):
                        break
                    if ":" in bline and not bline.startswith("#"):
                        k, v = bline.split(":", 1)
                        block[k.strip()] = v.strip().strip('"')
                    i += 1
                if block.get("status") == "open":
                    total_open += 1
                    dom = block.get("source_domain", "other")
                    pri = block.get("priority", "P2")
                    desc = block.get("description", "")
                    deadline = block.get("deadline", "")

                    if dom not in domain_risk:
                        domain_risk[dom] = {"items": [], "p0": 0, "p1": 0, "p2": 0, "overdue": 0}
                    dr = domain_risk[dom]
                    dr["items"].append({"pri": pri, "desc": desc, "deadline": deadline, "id": block.get("id", "")})

                    if pri == "P0":
                        dr["p0"] += 1
                        total_p0 += 1
                    elif pri == "P1":
                        dr["p1"] += 1
                    else:
                        dr["p2"] += 1

                    if deadline:
                        try:
                            dl = _dt.strptime(deadline, "%Y-%m-%d").date()
                            if dl < _date.today():
                                dr["overdue"] += 1
                                total_overdue += 1
                        except ValueError:
                            pass
                continue
            i += 1

    # ── Determine color per domain ──
    all_domains = list(_DOMAIN_STATE_FILES.keys())
    domain_color: dict[str, str] = {}
    domain_reason: dict[str, str] = {}

    for dom in all_domains:
        dr = domain_risk.get(dom)
        if not dr:
            domain_color[dom] = "🟢"
            continue
        if dr["p0"] > 0 or dr["overdue"] > 0:
            domain_color[dom] = "🔴"
            # Path-to-green: summarize the top item
            top = next((it for it in dr["items"] if it["pri"] == "P0"), None)
            if top:
                domain_reason[dom] = _truncate(top["desc"], 60)
            elif dr["overdue"] > 0:
                overdue_item = next((it for it in dr["items"] if it["deadline"]), dr["items"][0])
                domain_reason[dom] = f"overdue: {_truncate(overdue_item['desc'], 50)}"
        elif dr["p1"] > 0:
            domain_color[dom] = "🟡"
            top = next((it for it in dr["items"] if it["pri"] == "P1"), dr["items"][0])
            domain_reason[dom] = _truncate(top["desc"], 60)
        else:
            domain_color[dom] = "🟢"

    # ── Build HTML ──
    parts: list[str] = []
    parts.append("<b>📊 Artha Dashboard</b>\n")

    # Life Pulse — legend based on risk
    parts.append("<b>Life Pulse</b>  <i>🔴 critical  🟡 needs attention  🟢 ok</i>")

    # Group: red first, then yellow, then green
    for color_emoji in ("🔴", "🟡", "🟢"):
        for dom in all_domains:
            if domain_color.get(dom, "🟢") != color_emoji:
                continue
            dr = domain_risk.get(dom)
            count = sum(1 for _ in (dr["items"] if dr else []))
            if color_emoji == "🟢":
                parts.append(f"  {color_emoji} {dom}")
            else:
                reason = domain_reason.get(dom, "")
                tag = ""
                if dr and dr["p0"]:
                    tag = " [P0]"
                elif dr and dr["overdue"]:
                    tag = " [overdue]"
                elif dr and dr["p1"]:
                    tag = f" [P1×{dr['p1']}]"
                parts.append(f"  {color_emoji} <b>{dom}</b>{tag}")
                if reason:
                    parts.append(f"      ↳ {_html.escape(reason)}")
    parts.append("")

    # ── Summary line ──
    parts.append(f"<b>Open Items</b>: {total_open}")
    if total_p0:
        parts.append(f"  🔴 P0: {total_p0}")
    if total_overdue:
        parts.append(f"  ⚠️ Overdue: {total_overdue}")
    parts.append("")

    # ── System Health ──
    hc_content, _ = _read_state_file("health_check")
    if hc_content:
        hc_lines = hc_content.splitlines()
        catchup_fields: dict[str, str] = {}
        in_block = False
        for line in hc_lines:
            s = line.strip()
            if "Last Catch-Up" in s:
                in_block = True
                continue
            if s.startswith("## ") and in_block:
                break
            if in_block and ":" in s and not s.startswith("#") and not s.startswith("```"):
                k, v = s.split(":", 1)
                catchup_fields[k.strip()] = v.strip().strip('"')
        ts = catchup_fields.get("last_catch_up", "?")
        if "T" in ts:
            ts_display = ts.split("T")[0] + " " + ts.split("T")[1][:5]
        else:
            ts_display = ts
        ctx = catchup_fields.get("context_window_pct", "?")
        pf = catchup_fields.get("preflight", "?")
        pf_emoji = "✅" if pf == "pass" else "⚠️"
        ctx_emoji = "🟢" if ctx != "?" and int(ctx) < 70 else "🟡" if ctx != "?" and int(ctx) < 90 else "🔴"
        parts.append("<b>System</b>")
        parts.append(f"  Last catch-up: {ts_display}")
        parts.append(f"  {pf_emoji} Preflight: {pf}  {ctx_emoji} Context: {ctx}%")

    return "\n".join(parts)


# ── cmd_dashboard ───────────────────────────────────────────────

async def cmd_dashboard(args: list[str], scope: str) -> tuple[str, str]:
    """Return rich HTML-formatted life dashboard."""
    html = _build_dashboard_html()
    return _apply_scope_filter(html, scope), "N/A"


# ── cmd_power ───────────────────────────────────────────────────

async def cmd_power(args: list[str], scope: str) -> tuple[str, str]:
    """Return Power Half Hour view (E14 — power_half_hour_view.py)."""
    try:
        from power_half_hour_view import render_power_session  # noqa: PLC0415
    except ImportError:
        return "⚠️ power_half_hour_view module not found.", "N/A"

    fmt_arg = args[0].lstrip("-") if args else "standard"
    text, _ = render_power_session(fmt=fmt_arg)
    return _apply_scope_filter(text, scope), "N/A"


# ── cmd_relationships ───────────────────────────────────────────

async def cmd_relationships(args: list[str], scope: str) -> tuple[str, str]:
    """Return Relationship Pulse view (E13 — relationship_pulse_view.py)."""
    try:
        from relationship_pulse_view import render_relationships  # noqa: PLC0415
    except ImportError:
        return "⚠️ relationship_pulse_view module not found.", "N/A"

    fmt_arg = args[0].lstrip("-") if args else "standard"
    text, _ = render_relationships(fmt=fmt_arg)
    return _apply_scope_filter(text, scope), "N/A"


# ── cmd_alerts ──────────────────────────────────────────────────

async def cmd_alerts(args: list[str], scope: str) -> tuple[str, str]:
    """Return active alerts from latest briefing."""
    briefing_path = _get_latest_briefing_path()
    if briefing_path is None:
        return "_No briefing available_", "never"

    try:
        content = briefing_path.read_text(encoding="utf-8", errors="replace")
        mtime = briefing_path.stat().st_mtime
        staleness = _format_age(time.time() - mtime)
    except OSError:
        return "_Could not read briefing_", "unknown"

    # Extract lines with alert emoji
    alert_lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if any(em in stripped for em in ("🔴", "🟠", "🟡", "🔵", "⚠️")):
            alert_lines.append(stripped)
        if stripped.startswith("ARTHA ·"):
            alert_lines.insert(0, stripped)

    text = "\n".join(alert_lines[:20]) or "_No alerts found_"
    return _apply_scope_filter(text, scope), staleness


# ── cmd_tasks ───────────────────────────────────────────────────

async def cmd_tasks(args: list[str], scope: str) -> tuple[str, str]:
    """Return open action items sorted by priority."""
    content, staleness = _read_state_file("open_items")

    # Parse structured open items
    items: list[tuple[str, str]] = []  # (priority, display_line)
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("- id: OI-"):
            item_id = line.split(":", 1)[1].strip()
            block: dict[str, str] = {"id": item_id}
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("- id: OI-"):
                l = lines[i].strip()
                if ":" in l:
                    k, v = l.split(":", 1)
                    block[k.strip()] = v.strip().strip('"')
                i += 1
            if block.get("status") == "open":
                desc = block.get("description", "")
                dl = block.get("deadline", "")
                pri = block.get("priority", "")
                entry = f"[{item_id}] {desc}"
                if dl:
                    entry += f" (due {dl})"
                if pri:
                    entry += f" [{pri}]"
                items.append((pri, entry))
        else:
            i += 1

    if not items:
        text = "No open tasks"
    else:
        _pri_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        items.sort(key=lambda t: _pri_order.get(t[0], 9))
        task_lines = [entry for _, entry in items[:10]]
        text = f"Open tasks ({len(items)}):\n" + "\n".join(task_lines)

    return _apply_scope_filter(text, scope), staleness


# ── cmd_quick ───────────────────────────────────────────────────

async def cmd_quick(args: list[str], scope: str) -> tuple[str, str]:
    """Return tasks that take ≤5 minutes (phone-ready)."""
    content, staleness = _read_state_file("open_items")

    quick_keywords = ("5 min", "5min", "quick", "< 5", "<5", "phone", "2 min", "1 min")
    quick_lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        line_lower = stripped.lower()
        if any(kw in line_lower for kw in quick_keywords):
            if "done" not in line_lower and "resolved" not in line_lower:
                quick_lines.append(stripped)
        if len(quick_lines) >= 5:
            break

    if not quick_lines:
        text = "_No quick tasks found (≤5 min)_"
    else:
        text = f"Quick tasks ({len(quick_lines)}):\n" + "\n".join(quick_lines)

    return _apply_scope_filter(text, scope), staleness


# ── cmd_domain ──────────────────────────────────────────────────

async def cmd_domain(args: list[str], scope: str) -> tuple[str, str]:
    """Return state summary for a specific domain."""
    all_unencrypted = sorted(_DOMAIN_TO_STATE_FILE.keys())
    encrypted_domains = {"finance", "insurance", "immigration", "estate", "vehicle", "health"}

    if not args:
        lines = [
            "Pick a domain:",
            "",
            "Direct read (fast):",
            "  " + ", ".join(all_unencrypted),
            "",
            "Via AI (needs vault, ~20s):",
            "  " + ", ".join(sorted(encrypted_domains)),
            "",
            "Example: d kids",
        ]
        return "\n".join(lines), "N/A"

    domain_name = args[0].lower()

    # Redirect /domain dashboard → /dashboard (rich HTML handler)
    if domain_name == "dashboard":
        return await cmd_dashboard(args[1:], scope)

    # Check scope constraints
    if scope in ("family", "standard") and domain_name in _FAMILY_EXCLUDED_DOMAINS:
        return (
            f"_{domain_name.title()} domain is not available in your access scope. "
            "Full details are available in the CLI session._",
            "N/A",
        )

    state_key = _DOMAIN_TO_STATE_FILE.get(domain_name)

    # Encrypted domain → route through LLM (Claude can use vault skills)
    if domain_name in encrypted_domains and state_key is None:
        question = f"Give me a complete summary of my {domain_name} domain: key items, deadlines, risks, and any actions needed."
        context = _gather_context([domain_name])
        answer = await _ask_llm(question, context)
        return answer, "N/A"

    if state_key is None:
        available = ", ".join(all_unencrypted) + "\n+ encrypted: " + ", ".join(sorted(encrypted_domains))
        return f"_Unknown domain '{domain_name}'._\nAvailable: {available}", "N/A"

    try:
        content, staleness = _read_state_file(state_key)
    except Exception as _ve:
        # RD-34: VaultAccessRequired — surface vault-locked notice
        if "vault" in str(_ve).lower() or "VaultAccess" in type(_ve).__name__:
            return (
                f"_{domain_name.title()} domain is vault-protected. "
                "Unlock the vault first (run `vault unlock`), then retry._",
                "N/A",
            )
        raise
    # Use section-aware extraction for large files (Telegram limit: 4096 chars)
    if len(content) > 1000:
        content = _extract_section_summaries(content, max_total=3200)

    # Append relevant open action items for this domain
    action_items = _get_domain_open_items(domain_name)
    if action_items:
        remaining = 3800 - len(content)
        if remaining > 100:
            content += "\n" + action_items[:remaining]

    return _apply_scope_filter(content, scope), staleness


# ── cmd_goals ───────────────────────────────────────────────────

async def cmd_goals(args: list[str], scope: str) -> tuple[str, str]:
    """Shortcut: equivalent to /domain goals."""
    return await cmd_domain(["goals"] + args, scope)


# ── cmd_diff ────────────────────────────────────────────────────

async def cmd_diff(args: list[str], scope: str) -> tuple[str, str]:
    """Show state files that changed since last catch-up (or N days)."""
    import re as _re

    # Parse optional time argument: "7d", "3d", "24h", or default to since last catchup
    hours = None
    if args:
        m = _re.match(r'^(\d+)\s*(d|h)$', args[0].lower())
        if m:
            val, unit = int(m.group(1)), m.group(2)
            hours = val * 24 if unit == "d" else val

    if hours is None:
        since_iso = _get_last_catchup_iso()
        try:
            since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
            hours = (datetime.now(timezone.utc) - since_dt).total_seconds() / 3600
            since_label = f"since last catch-up ({since_iso[:10]})"
        except (ValueError, TypeError):
            hours = 48
            since_label = "last 48h (fallback)"
    else:
        since_label = f"last {args[0]}"

    cutoff = time.time() - (hours * 3600)
    changed: list[str] = []
    unchanged: list[str] = []

    for domain in sorted(_DOMAIN_STATE_FILES.keys()):
        fname = _DOMAIN_STATE_FILES[domain]
        fpath = _STATE_DIR / fname
        if not fpath.exists():
            continue
        # Skip encrypted files — can't read mtime meaningfully
        if fname.endswith(".age"):
            continue
        try:
            mtime = fpath.stat().st_mtime
            age_str = _format_age(time.time() - mtime)
            if mtime > cutoff:
                changed.append(f"  📝 {domain} (updated {age_str} ago)")
            else:
                unchanged.append(domain)
        except OSError:
            continue

    # Also check open_items
    oi_path = _STATE_DIR / "open_items.md"
    if oi_path.exists():
        try:
            mtime = oi_path.stat().st_mtime
            if mtime > cutoff:
                age_str = _format_age(time.time() - mtime)
                changed.append(f"  📝 open_items (updated {age_str} ago)")
        except OSError:
            pass

    lines = [f"State changes {since_label}:", ""]
    if changed:
        lines.append(f"Changed ({len(changed)}):")
        lines.extend(changed)
    else:
        lines.append("No state files changed.")
    if unchanged:
        lines.append(f"\nUnchanged: {', '.join(unchanged)}")

    return "\n".join(lines), "N/A"


# ── cmd_items_add ───────────────────────────────────────────────

async def cmd_items_add(args: list[str], scope: str) -> tuple[str, str]:
    """Add a new open item from Telegram.

    Usage: items add <description> [P0|P1|P2] [domain] [YYYY-MM-DD]
    Example: items add Call estate attorney P0 estate 2026-03-20
    """
    if not args:
        return (
            "Usage: items add <description> [priority] [domain] [deadline]\n"
            "Example: items add Call estate attorney P0 estate 2026-03-20\n"
            "Priority: P0/P1/P2 (default P1)\n"
            "Domain: kids/finance/health/home/etc (default general)\n"
            "Deadline: YYYY-MM-DD (optional)"
        ), "N/A"

    import re as _re

    raw = " ".join(args)

    # Extract priority
    priority = "P1"
    m = _re.search(r'\b(P[012])\b', raw)
    if m:
        priority = m.group(1)
        raw = raw[:m.start()] + raw[m.end():]

    # Extract deadline
    deadline = ""
    m = _re.search(r'\b(\d{4}-\d{2}-\d{2})\b', raw)
    if m:
        deadline = m.group(1)
        raw = raw[:m.start()] + raw[m.end():]

    # Extract domain
    known_domains = set(_DOMAIN_STATE_FILES.keys()) | {"general"}
    domain = "general"
    for d in known_domains:
        pattern = r'\b' + _re.escape(d) + r'\b'
        if _re.search(pattern, raw.lower()):
            domain = d
            raw = _re.sub(pattern, '', raw, flags=_re.IGNORECASE)
            break

    description = raw.strip().rstrip(".")
    if not description:
        return "Need a description. Example: items add Call estate attorney P0", "N/A"

    # Find next OI number
    oi_path = _STATE_DIR / "open_items.md"
    content = oi_path.read_text(encoding="utf-8", errors="replace") if oi_path.exists() else ""
    numbers = [int(m.group(1)) for m in _re.finditer(r'id: OI-(\d+)', content)]
    next_num = max(numbers) + 1 if numbers else 1
    oi_id = f"OI-{next_num:03d}"
    today = datetime.now().strftime("%Y-%m-%d")

    # Append new item
    entry = (
        f"\n- id: {oi_id}\n"
        f"  date_added: \"{today}\"\n"
        f"  source_domain: {domain}\n"
        f"  description: \"{description}\"\n"
        f"  deadline: \"{deadline}\"\n"
        f"  priority: {priority}\n"
        f"  status: open\n"
        f"  todo_id: \"\"\n"
    )

    try:
        with open(oi_path, "a", encoding="utf-8") as f:
            f.write(entry)
        _audit_log("ITEM_ADD", item_id=oi_id, description=description[:80],
                   priority=priority, domain=domain, deadline=deadline)
        return (
            f"Added {oi_id}:\n"
            f"  {description}\n"
            f"  Priority: {priority} | Domain: {domain}"
            + (f" | Due: {deadline}" if deadline else "")
        ), "N/A"
    except OSError as exc:
        return f"Failed to write item: {exc}", "N/A"


# ── cmd_items_done ──────────────────────────────────────────────

async def cmd_items_done(args: list[str], scope: str) -> tuple[str, str]:
    """Mark an open item as done.

    Usage: items done OI-NNN [resolution note]
    Example: items done OI-005 Called and scheduled for March 20
    """
    import re as _re

    if not args:
        return "Usage: done OI-NNN [resolution note]\nExample: done OI-005 Completed", "N/A"

    # Parse OI ID — accept "OI-005", "oi-005", "005", "5"
    raw_id = args[0].upper()
    m = _re.match(r'^(?:OI-)?(\d+)$', raw_id)
    if not m:
        return f"Invalid item ID: {args[0]}. Expected OI-NNN or just the number.", "N/A"
    oi_id = f"OI-{int(m.group(1)):03d}"
    resolution = " ".join(args[1:]).strip() if len(args) > 1 else "Marked done via Telegram"

    oi_path = _STATE_DIR / "open_items.md"
    if not oi_path.exists():
        return "No open_items.md found.", "N/A"

    content = oi_path.read_text(encoding="utf-8", errors="replace")

    # Find and update the item
    pattern = rf'(- id: {_re.escape(oi_id)}\b.*?)(\n- id: OI-|\Z)'
    match = _re.search(pattern, content, _re.DOTALL)
    if not match:
        return f"Item {oi_id} not found.", "N/A"

    block = match.group(1)
    if "status: done" in block:
        return f"{oi_id} is already done.", "N/A"
    if "status: open" not in block:
        return f"{oi_id} is not in open status (current: deferred?).", "N/A"

    today = datetime.now().strftime("%Y-%m-%d")
    new_block = block.replace("status: open", "status: done")
    # Add date_resolved and resolution if not present
    if "date_resolved:" not in new_block:
        new_block = new_block.rstrip() + f"\n  date_resolved: \"{today}\"\n"
    if "resolution:" not in new_block:
        new_block = new_block.rstrip() + f"\n  resolution: \"{resolution}\"\n"

    content = content[:match.start()] + new_block + match.group(2) + content[match.end():]

    try:
        oi_path.write_text(content, encoding="utf-8")
        _audit_log("ITEM_DONE", item_id=oi_id, resolution=resolution[:80])

        # Extract description for confirmation
        desc_m = _re.search(r'description:\s*"?([^"\n]+)', block)
        desc = desc_m.group(1).strip() if desc_m else oi_id

        return f"✅ {oi_id} marked done:\n  {desc}\n  Resolution: {resolution}", "N/A"
    except OSError as exc:
        return f"Failed to update: {exc}", "N/A"


# ── _remember_rate_ok ───────────────────────────────────────────

def _remember_rate_ok(key: str) -> bool:
    """Return True if key has not exceeded _REMEMBER_WRITE_RATE_LIMIT per hour."""
    import time as _time_mod
    now = _time_mod.monotonic()
    ts_list = _remember_write_times[key]
    cutoff = now - 3600
    while ts_list and ts_list[0] < cutoff:
        ts_list.pop(0)
    if len(ts_list) >= _REMEMBER_WRITE_RATE_LIMIT:
        return False
    ts_list.append(now)
    return True


# ── cmd_remember ────────────────────────────────────────────────

async def cmd_remember(args: list[str], scope: str) -> tuple[str, str]:
    """Capture a quick note into state/inbox.md for triage during next catch-up.

    Usage: /remember <text>
    Aliases: /note, /inbox

    Only available to full-scope users. Applies PII guard pre-write.
    Rate-limited: 5 writes/hour.
    """
    import re as _re2
    try:
        import fcntl as _fcntl2
    except ImportError:  # Windows
        _fcntl2 = None  # type: ignore[assignment]

    if scope not in ("full", "admin"):
        return "❌ /remember is available to full-scope users only.", "N/A"

    if not args:
        return (
            "Usage: /remember <text>\n"
            "Example: /remember Pick up science project materials from Staples"
        ), "N/A"

    raw_text = " ".join(args).strip()[:500]
    if not raw_text:
        return "Need some text to note.", "N/A"

    # PII guard
    try:
        from pii_guard import filter_text as _pii_filter  # type: ignore[import]
        filtered_text, _pii_found = _pii_filter(raw_text)
    except ImportError:
        filtered_text = raw_text

    # Write rate limit: 5/hour (global key — sender_id not available here)
    if not _remember_rate_ok("_global"):
        return "⛔ Write rate limit reached (5/hour). Try again later.", "N/A"

    inbox_path = _STATE_DIR / "inbox.md"
    existing = inbox_path.read_text(encoding="utf-8", errors="replace") if inbox_path.exists() else ""

    # Untriaged cap
    untriaged_count = len(_re2.findall(r"triaged:\s*false", existing))
    if untriaged_count >= _REMEMBER_MAX_UNTRIAGED:
        return (
            f"⚠️ Inbox full ({_REMEMBER_MAX_UNTRIAGED} untriaged items). "
            "Run /catch-up to triage first."
        ), "N/A"

    # Next INB-NNN id
    numbers = [int(m.group(1)) for m in _re2.finditer(r"id:\s*INB-(\d+)", existing)]
    next_num = max(numbers) + 1 if numbers else 1
    inb_id = f"INB-{next_num:03d}"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entry = (
        f"\n- id: {inb_id}\n"
        f"  text: \"{filtered_text}\"\n"
        f"  source: telegram\n"
        f"  timestamp: {ts}\n"
        f"  triaged: false\n"
        f"  routed_to: null\n"
        f"  created_oi: null\n"
    )

    if not inbox_path.exists() or not existing.strip():
        header = "---\ndomain: inbox\nsensitivity: standard\n---\n\n## Inbox Items\n"
        existing = header

    try:
        with open(inbox_path, "a", encoding="utf-8") as fh:
            if _fcntl2 is not None:
                _fcntl2.flock(fh, _fcntl2.LOCK_EX)
            if not inbox_path.stat().st_size:
                fh.write(existing)
            fh.write(entry)
        _audit_log("CHANNEL_REMEMBER", item_id=inb_id, text_preview=filtered_text[:60], scope=scope)
        return (
            f"📥 Noted: {inb_id}\n  {filtered_text[:80]}"
            + ("\n  (Partial PII redaction applied)" if filtered_text != raw_text else "")
        ), "N/A"
    except OSError as exc:
        return f"Failed to write inbox: {exc}", "N/A"


# ── cmd_cost ────────────────────────────────────────────────────

async def cmd_cost(args: list[str], scope: str) -> tuple[str, str]:
    """Show API cost telemetry estimate for the current session and rolling windows.

    Usage: /cost
    Output: Today / this week / this month estimates + breakdown + optimisation tip.
    """
    try:
        from cost_tracker import CostTracker  # type: ignore[import]
        tracker = CostTracker()
        report = tracker.build_report()
        return tracker.format_report(report), "N/A"
    except ImportError:
        return "cost_tracker module not available. Run: python scripts/cost_tracker.py", "N/A"
    except Exception as exc:  # noqa: BLE001
        return f"Cost estimation failed: {exc}", "N/A"


# ── cmd_queue ───────────────────────────────────────────────────

async def cmd_queue(args: list[str], scope: str) -> tuple[str, str]:
    """Show pending action queue."""
    try:
        from action_executor import ActionExecutor  # noqa: PLC0415
        executor = ActionExecutor(_ARTHA_DIR)
        pending = executor.pending()
        if not pending:
            return "✅ No pending actions.", "N/A"

        lines = [f"⚡ PENDING ACTIONS ({len(pending)})"]
        lines.append("")
        for i, p in enumerate(pending, 1):
            friction_badge = {"low": "🟢", "standard": "🟡", "high": "🔴"}.get(
                p.get("friction", "standard"), "🟡"
            )
            lines.append(
                f"{i}. {friction_badge} {p.get('title', '?')} "
                f"[{p.get('action_type', '?')}]"
            )
            lines.append(f"   ID: {p.get('id', '?')[:12]}…")
        lines.append("")
        lines.append("Say: approve <ID> · reject <ID> · undo <ID>")
        return "\n".join(lines), "N/A"
    except ImportError:
        return "⚠️ Action layer not available.", "N/A"
    except Exception as e:
        return f"⚠️ Queue error: {e}", "N/A"


# ── cmd_approve ─────────────────────────────────────────────────

async def cmd_approve(args: list[str], scope: str) -> tuple[str, str]:
    """Approve a pending action by ID prefix."""
    if not args:
        return "_Usage: approve <action-id>_", "N/A"

    action_id_prefix = args[0].strip()

    try:
        from action_executor import ActionExecutor  # noqa: PLC0415
        executor = ActionExecutor(_ARTHA_DIR)

        # Resolve full ID from prefix
        pending = executor.pending()
        matched = [
            p for p in pending
            if p.get("id", "").startswith(action_id_prefix)
        ]
        if not matched:
            return f"⚠️ No pending action found matching '{action_id_prefix}'.", "N/A"
        if len(matched) > 1:
            ids = [m["id"][:12] for m in matched]
            return f"⚠️ Ambiguous ID prefix — matches: {', '.join(ids)}", "N/A"

        full_id = matched[0]["id"]
        result = executor.approve(full_id, approved_by="user:telegram")

        if result.status == "success":
            return f"✅ Approved + executed: {result.message}", "N/A"
        elif result.status == "failure":
            return f"❌ Execution failed: {result.message}", "N/A"
        else:
            return f"ℹ️ {result.status}: {result.message}", "N/A"

    except ImportError:
        return "⚠️ Action layer not available.", "N/A"
    except Exception as e:
        return f"⚠️ Approve error: {e}", "N/A"


# ── cmd_reject ──────────────────────────────────────────────────

async def cmd_reject(args: list[str], scope: str) -> tuple[str, str]:
    """Reject a pending action by ID prefix."""
    if not args:
        return "_Usage: reject <action-id> [reason]_", "N/A"

    action_id_prefix = args[0].strip()
    reason = " ".join(args[1:]) if len(args) > 1 else "user:telegram:rejected"

    try:
        from action_executor import ActionExecutor  # noqa: PLC0415
        executor = ActionExecutor(_ARTHA_DIR)

        pending = executor.pending()
        matched = [
            p for p in pending
            if p.get("id", "").startswith(action_id_prefix)
        ]
        if not matched:
            return f"⚠️ No pending action found matching '{action_id_prefix}'.", "N/A"
        if len(matched) > 1:
            ids = [m["id"][:12] for m in matched]
            return f"⚠️ Ambiguous ID prefix — matches: {', '.join(ids)}", "N/A"

        full_id = matched[0]["id"]
        executor.reject(full_id, reason=reason)
        return f"❌ Rejected: {matched[0].get('title', full_id[:12])}", "N/A"

    except ImportError:
        return "⚠️ Action layer not available.", "N/A"
    except Exception as e:
        return f"⚠️ Reject error: {e}", "N/A"


# ── cmd_undo ────────────────────────────────────────────────────

async def cmd_undo(args: list[str], scope: str) -> tuple[str, str]:
    """Undo a recently executed action by ID prefix."""
    if not args:
        return "_Usage: undo <action-id>_", "N/A"

    action_id_prefix = args[0].strip()

    try:
        from action_executor import ActionExecutor  # noqa: PLC0415
        executor = ActionExecutor(_ARTHA_DIR)

        # Check recent history for the action
        history = executor.history(limit=50)
        matched = [
            h for h in history
            if h.get("id", "").startswith(action_id_prefix)
        ]
        if not matched:
            return f"⚠️ No recent action found matching '{action_id_prefix}'.", "N/A"
        if len(matched) > 1:
            ids = [m["id"][:12] for m in matched]
            return f"⚠️ Ambiguous ID prefix — matches: {', '.join(ids)}", "N/A"

        full_id = matched[0]["id"]
        result = executor.undo(full_id, actor="user:telegram")

        if result.status == "success":
            return f"↩️ Undone: {result.message}", "N/A"
        else:
            return f"⚠️ Undo failed: {result.message}", "N/A"

    except ImportError:
        return "⚠️ Action layer not available.", "N/A"
    except Exception as e:
        return f"⚠️ Undo error: {e}", "N/A"


# ── cmd_help ────────────────────────────────────────────────────

async def cmd_help(args: list[str], scope: str) -> tuple[str, str]:
    """Return available commands."""
    lines = [
        "ARTHA Commands",
        "",
        "READ",
        "s  — Status + alerts",
        "a  — All alerts by severity",
        "t  — Open tasks / action items",
        "q  — Quick tasks (≤5 min)",
        "g  — Goals overview",
        "d <name>  — Domain deep-dive",
        "  (kids, health, finance, insurance, ...)",
        "dash  — Life dashboard",
        "diff  — Changes since last catch-up",
        "diff 7d  — Changes in last 7 days",
        "catchup  — Run a fresh briefing",
        "",
        "WRITE",
        "items add <desc> [P0|P1|P2] [domain] [date]",
        "done <OI-NNN>  — Mark item complete",
        "",
        "ACTIONS",
        "queue  — Show pending approvals",
        "approve <id>  — Approve + execute action",
        "reject <id>  — Reject action",
        "undo <id>  — Undo within window",
        "",
        "OTHER",
        "unlock <PIN>  — 15-min sensitive session",
        "stage  — Content Stage card list",
        "stage preview <ID>  — Show card draft",
        "?  — This help",
        "",
        "Just type any question to ask Artha.",
        '"aa <question>" for an ensemble answer from all 3 AIs.',
        "",
        "Slash optional. catchup = catch up = briefing.",
    ]
    if scope == "family":
        lines.insert(-3, "(finance, insurance, estate, immigration need /unlock)")
    return "\n".join(lines), "N/A"


# ── cmd_unlock ──────────────────────────────────────────────────

async def cmd_unlock(args: list[str], scope: str, sender_id: str,
                     token_store: _SessionTokenStore) -> tuple[str, str]:
    """Verify PIN and create session token."""
    if not args:
        return "_Usage: /unlock <PIN>_", "N/A"
    pin = args[0]
    if token_store.unlock(sender_id, pin):
        _audit_log("CHANNEL_SESSION", recipient=sender_id, action="unlock")
        return f"_Session unlocked for {_SESSION_TOKEN_MINUTES} minutes._", "N/A"
    else:
        _audit_log("CHANNEL_SESSION", recipient=sender_id, action="unlock_failed")
        return "_Incorrect PIN. Session not unlocked._", "N/A"


# ── _handle_callback_query ──────────────────────────────────────────────

async def _handle_callback_query(
    callback_data: str,
    sender_id: str,
    msg,
    adapter,
) -> None:
    """Handle Telegram inline keyboard button presses for action approval.

    callback_data format: "act:APPROVE:action_id" | "act:REJECT:action_id" | "act:DEFER:action_id"

    Ref: specs/act.md §5.3
    """
    from channels.base import ChannelMessage as _CM  # noqa: PLC0415

    parts = callback_data.split(":", 2)
    if len(parts) != 3 or parts[0] != "act":
        return  # Not an action callback — ignore

    verb = parts[1].upper()
    action_id = parts[2]

    if not action_id:
        return

    try:
        from action_executor import ActionExecutor  # noqa: PLC0415
        executor = ActionExecutor(_ARTHA_DIR)

        if verb == "APPROVE":
            result = executor.approve(action_id, approved_by="user:telegram")
            if result.status == "success":
                reply = f"✅ {result.message}"
            elif result.status == "failure":
                reply = f"❌ Failed: {result.message}"
            else:
                reply = f"ℹ️ {result.status}: {result.message}"

        elif verb == "REJECT":
            executor.reject(action_id, reason="user:telegram:button")
            reply = f"❌ Action rejected."

        elif verb == "DEFER":
            executor.defer(action_id, until="+24h")
            reply = f"⏰ Deferred 24 hours."

        else:
            reply = f"⚠️ Unknown action verb: {verb}"

        _audit_log(
            "CHANNEL_ACTION_CALLBACK",
            sender=sender_id,
            verb=verb,
            action_id=action_id[:16],
        )
        adapter.send_message(_CM(text=reply, recipient_id=sender_id))

    except ImportError:
        adapter.send_message(_CM(
            text="⚠️ Action layer not available.",
            recipient_id=sender_id,
        ))
    except Exception as e:
        log.error("[channel_listener] callback_query handler error: %s", e)
        adapter.send_message(_CM(
            text=f"⚠️ Action handler error: {e}",
            recipient_id=sender_id,
        ))


# ── cmd_workout_log ─────────────────────────────────────────────────────────
# P6.1 — Physiological Engine (specs/ac-int.md §7.7)
# Trigger: message starting with log/logged/weight/rest day or activity word.
# Caller: channel_listener passes sender_id + message_id as keyword args.

_LOCAL_WORKOUT_DIR = Path.home() / ".artha-local"
_WORKOUTS_FILE = _LOCAL_WORKOUT_DIR / "workouts.jsonl"
_WORKOUT_DEDUP_WINDOW = 50  # scan last N lines for (sender_id, message_id) dedup

# Distance: "8km", "5mi", "4 miles", "4.5 km"
_RE_DIST = re.compile(r"(\d+(?:\.\d+)?)\s*(km|mi(?:les?)?)", re.IGNORECASE)
# Duration: "58min", "1h30m", "2h15m", "45 min", "1h"
_RE_DUR = re.compile(r"(?:(\d+)h\s*)?(\d+)\s*min|(\d+)\s*h(?:our)?s?(?!\s*\d)", re.IGNORECASE)
# HR: "HR 142", "avg hr 155", "hr142"
_RE_HR = re.compile(r"(?:avg\s*)?hr\s*(\d{2,3})", re.IGNORECASE)
# Elevation: "1100ft gain", "300m elev", "1100 ft"
_RE_ELEV = re.compile(r"(\d+(?:\.\d+)?)\s*(ft|m)\s*(?:gain|elev(?:ation)?)?", re.IGNORECASE)
# Activity type keywords
_ACTIVITY_WORDS: dict[str, str] = {
    "run": "run", "ran": "run", "running": "run",
    "hike": "hike", "hiked": "hike", "hiking": "hike",
    "walk": "walk", "walked": "walk", "walking": "walk",
    "strength": "strength", "lift": "strength", "lifted": "strength",
    "cycle": "cycle", "cycling": "cycle", "bike": "cycle", "biked": "cycle",
    "swim": "swim", "swam": "swim", "swimming": "swim",
    "rest": "rest",
}
# Weight: "weight 182", "weigh 181.5 lbs", "weight: 180"
_RE_WEIGHT = re.compile(r"weigh[t]?\s*:?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def _parse_workout(text: str) -> "dict[str, Any] | None":
    """Parse workout text. Returns None if no recognizable fields found."""
    lower = text.lower()

    # Activity type
    activity: "str | None" = None
    for word, canonical in _ACTIVITY_WORDS.items():
        if re.search(r"\b" + re.escape(word) + r"\b", lower):
            activity = canonical
            break

    # Distance
    distance_km: "float | None" = None
    m = _RE_DIST.search(text)
    if m:
        val = float(m.group(1))
        unit = m.group(2).lower()
        distance_km = round(val * 1.60934, 2) if unit.startswith("mi") else round(val, 2)

    # Duration
    duration_min: "int | None" = None
    m = _RE_DUR.search(text)
    if m:
        if m.group(3):  # pure hours: "1h"
            duration_min = int(m.group(3)) * 60
        else:
            hrs = int(m.group(1)) if m.group(1) else 0
            mins = int(m.group(2)) if m.group(2) else 0
            duration_min = hrs * 60 + mins

    # Heart rate
    hr_avg: "int | None" = None
    m = _RE_HR.search(text)
    if m:
        hr_avg = int(m.group(1))

    # Elevation (ft; convert m→ft if value >10 to avoid "8km" false matches)
    elevation_ft: "int | None" = None
    for em in _RE_ELEV.finditer(text):
        unit = em.group(2).lower()
        val = float(em.group(1))
        if unit == "m" and val > 10:
            elevation_ft = int(round(val * 3.28084))
        elif unit == "ft":
            elevation_ft = int(round(val))

    # Weight
    weight_lbs: "float | None" = None
    m = _RE_WEIGHT.search(text)
    if m:
        weight_lbs = float(m.group(1))

    # Must have at least one meaningful field to be a valid workout
    has_data = any(x is not None for x in [activity, distance_km, duration_min, weight_lbs])
    if not has_data:
        return None

    return {
        "activity": activity,
        "distance_km": distance_km,
        "duration_min": duration_min,
        "hr_avg": hr_avg,
        "elevation_ft": elevation_ft,
        "weight_lbs": weight_lbs,
    }


def _workout_already_logged(sender_id: str, message_id: str) -> bool:
    """Scan last N entries in workouts.jsonl for (sender_id, message_id) dedup."""
    if not _WORKOUTS_FILE.exists():
        return False
    try:
        lines = _WORKOUTS_FILE.read_text(encoding="utf-8").splitlines()
        for line in lines[-_WORKOUT_DEDUP_WINDOW:]:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if (entry.get("sender_id") == sender_id
                        and entry.get("message_id") == message_id):
                    return True
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return False


def _build_workout_ack(parsed: "dict[str, Any]") -> str:
    """Build multi-line ack from parsed fields + optional Mailbox Peak goal line."""
    lines: list[str] = []

    activity = parsed.get("activity") or "Activity"
    lines.append(f"✅ {activity.capitalize()} logged")

    if parsed.get("distance_km") is not None:
        dist = parsed["distance_km"]
        dist_mi = round(dist * 0.621371, 1)
        lines.append(f"• Distance: {dist} km ({dist_mi} mi)")

    if parsed.get("duration_min") is not None:
        mins = parsed["duration_min"]
        if mins >= 60:
            h, dm = divmod(mins, 60)
            lines.append(f"• Duration: {h}h {dm}min" if dm else f"• Duration: {h}h")
        else:
            lines.append(f"• Duration: {mins} min")

    if parsed.get("hr_avg") is not None:
        lines.append(f"• Avg HR: {parsed['hr_avg']} bpm")

    if parsed.get("elevation_ft") is not None:
        lines.append(f"• Elevation: {parsed['elevation_ft']} ft gain")

    if parsed.get("weight_lbs") is not None:
        lines.append(f"• Weight: {parsed['weight_lbs']} lbs")

    # Best-effort goal progress (absent/stale → omit silently)
    try:
        import importlib.util as _ilu  # noqa: PLC0415
        _fc_path = _ARTHA_DIR / "scripts" / "skills" / "fitness_coach.py"
        if _fc_path.exists():
            _spec = _ilu.spec_from_file_location("fitness_coach", _fc_path)
            if _spec and _spec.loader:
                _mod = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
                goal_line = _mod.FitnessCoachSkill().goal_progress_line()
                if goal_line:
                    lines.append(f"• {goal_line}")
    except Exception:
        pass

    return "\n".join(lines)


async def cmd_workout_log(
    args: list[str],
    scope: str,
    *,
    sender_id: str = "",
    message_id: str = "",
) -> tuple[str, str]:
    """Parse workout Telegram message and append to ~/.artha-local/workouts.jsonl.

    Returns (ack_text, "N/A"). On write failure returns ("", "N/A") — caller
    must check for empty string and skip sending the ack.

    Failure mode 1: unrecognized format  → ❓ hint, do NOT append.
    Failure mode 2: write error          → audit log, return ("", "N/A").
    Idempotency key: (sender_id, message_id) — checked in last 50 entries.
    Ref: specs/ac-int.md §7.7, §8.1 tool boundaries.
    """
    raw_text = " ".join(args).strip()
    if not raw_text:
        return "❓ Couldn't parse — try: 8km run 58min HR142", "N/A"

    parsed = _parse_workout(raw_text)
    if parsed is None:
        return "❓ Couldn't parse — try: 8km run 58min HR142", "N/A"

    # Idempotency: skip write (but still ack) on exact duplicate
    if sender_id and message_id and _workout_already_logged(sender_id, message_id):
        log.info("[workout_log] duplicate (sender=%s msg=%s) — skip write", sender_id, message_id)
        return _build_workout_ack(parsed), "N/A"

    entry: "dict[str, Any]" = {
        "sender_id": sender_id,
        "message_id": message_id,
        "logged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **parsed,
        "raw": raw_text[:200],
    }

    _LOCAL_WORKOUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with _WORKOUTS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        _audit_log("WORKOUT_LOG_WRITE_ERROR", sender=sender_id, error=str(exc))
        log.error("[workout_log] write failed: %s", exc)
        return "", "N/A"

    _audit_log("WORKOUT_LOGGED", sender=sender_id, activity=parsed.get("activity"))

    return _build_workout_ack(parsed), "N/A"


# ── _handle_callback_query ──────────────────────────────────────────────

async def _handle_callback_query(
    callback_data: str,
    sender_id: str,
    msg,
    adapter,
) -> None:
    """Handle Telegram inline keyboard button presses for action approval.

    callback_data format: "act:APPROVE:action_id" | "act:REJECT:action_id" | "act:DEFER:action_id"

    Ref: specs/act.md §5.3
    """
    from channels.base import ChannelMessage as _CM  # noqa: PLC0415

    parts = callback_data.split(":", 2)
    if len(parts) != 3 or parts[0] != "act":
        return  # Not an action callback — ignore

    verb = parts[1].upper()
    action_id = parts[2]

    if not action_id:
        return

    try:
        from action_executor import ActionExecutor  # noqa: PLC0415
        executor = ActionExecutor(_ARTHA_DIR)

        if verb == "APPROVE":
            result = executor.approve(action_id, approved_by="user:telegram")
            if result.status == "success":
                reply = f"✅ {result.message}"
            elif result.status == "failure":
                reply = f"❌ Failed: {result.message}"
            else:
                reply = f"ℹ️ {result.status}: {result.message}"

        elif verb == "REJECT":
            executor.reject(action_id, reason="user:telegram:button")
            reply = f"❌ Action rejected."

        elif verb == "DEFER":
            executor.defer(action_id, until="+24h")
            reply = f"⏰ Deferred 24 hours."

        else:
            reply = f"⚠️ Unknown action verb: {verb}"

        _audit_log(
            "CHANNEL_ACTION_CALLBACK",
            sender=sender_id,
            verb=verb,
            action_id=action_id[:16],
        )
        adapter.send_message(_CM(text=reply, recipient_id=sender_id))

    except ImportError:
        adapter.send_message(_CM(
            text="⚠️ Action layer not available.",
            recipient_id=sender_id,
        ))
    except Exception as e:
        log.error("[channel_listener] callback_query handler error: %s", e)
        adapter.send_message(_CM(
            text=f"⚠️ Action handler error: {e}",
            recipient_id=sender_id,
        ))
