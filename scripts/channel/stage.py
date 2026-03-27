"""channel/stage.py — Content Stage and AI Trend Radar commands."""
from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from channel.formatters import _clean_for_telegram, _trim_to_cap, _strip_frontmatter
from channel.state_readers import (
    _read_state_file, _apply_scope_filter, _get_latest_briefing_path,
    _READABLE_STATE_FILES,
)
from channel.audit import _audit_log

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_STATE_DIR = _ARTHA_DIR / "state"

log = logging.getLogger("channel_listener")



# ── cmd_stage ───────────────────────────────────────────────────

async def cmd_stage(args: list[str], scope: str) -> tuple[str, str]:
    """Show Content Stage card list or card detail (PR-2, read-only).

    Usage:
      /stage            — list active cards
      /stage list       — same as above
      /stage preview <ID>  — show card details

    Write operations (approve, draft, posted, dismiss) are not available via
    channel — use the AI assistant directly for those.
    """
    try:
        import yaml as _yaml  # noqa: PLC0415
        gallery_path = _READABLE_STATE_FILES["gallery"]

        if not gallery_path.exists():
            return (
                "⚠️ Content Stage not initialised. Run a catch-up first.",
                "N/A",
            )

        data = _yaml.safe_load(gallery_path.read_text(encoding="utf-8")) or {}
        cards = data.get("cards", [])

        subcommand = args[0].lower() if args else "list"

        # ── preview <ID> ──────────────────────────────────────────────────
        if subcommand == "preview" and len(args) >= 2:
            card_id = args[1].upper()
            card = next((c for c in cards if str(c.get("id", "")).upper() == card_id), None)
            if not card:
                return f"⚠️ Card {card_id} not found.", "N/A"

            from datetime import date as _date  # noqa: PLC0415
            today = _date.today()
            ev_str = card.get("event_date", "?")
            try:
                ev_date = _date.fromisoformat(str(ev_str))
                days = (ev_date - today).days
                days_label = "today" if days == 0 else (f"in {days}d" if days > 0 else f"{abs(days)}d ago")
            except (ValueError, TypeError):
                days_label = "?"

            drafts = card.get("drafts", {})
            lines = [
                f"📋 CARD {card_id} — {card.get('occasion', '?')}",
                f"Event: {ev_str} ({days_label})",
                f"Status: {card.get('status', '?')}",
                f"Occasion type: {card.get('occasion_type', '?')}",
                "",
            ]
            if drafts:
                lines.append("Drafts:")
                for platform, draft in drafts.items():
                    text = draft.get("text", "") if isinstance(draft, dict) else str(draft)
                    pii_ok = draft.get("pii_scan_passed", "?") if isinstance(draft, dict) else "?"
                    lines.append(f"  {platform}: PII={'✓' if pii_ok is True else '✗' if pii_ok is False else '?'}")
                    if text:
                        lines.append(f"    {text[:200]}{'…' if len(text) > 200 else ''}")
            else:
                lines.append("(No drafts yet — run /stage draft to generate)")

            return "\n".join(lines), "N/A"

        # ── list (default) ────────────────────────────────────────────────
        active_statuses = {"seed", "drafting", "staged", "approved"}
        active = [c for c in cards if c.get("status", "") in active_statuses]

        if not active:
            last_updated = data.get("last_updated", "never")
            return (
                f"📭 Content Stage is empty.\nNo active cards. Last updated: {last_updated}\n"
                "Run a catch-up to populate.",
                "N/A",
        )

        from datetime import date as _date  # noqa: PLC0415
        today = _date.today()

        status_emoji = {
            "seed": "🌱",
            "drafting": "✏️",
            "staged": "📋",
            "approved": "✅",
        }

        lines = [f"📣 CONTENT STAGE ({len(active)} active cards)", ""]
        for c in sorted(active, key=lambda x: str(x.get("event_date", ""))):
            cid      = c.get("id", "?")
            occasion = c.get("occasion", "?")
            ev_str   = c.get("event_date", "?")
            status   = c.get("status", "?")
            emoji    = status_emoji.get(status, "•")

            try:
                ev_date = _date.fromisoformat(str(ev_str))
                days = (ev_date - today).days
                days_label = "today" if days == 0 else (f"+{days}d" if days > 0 else f"{abs(days)}d past")
            except (ValueError, TypeError):
                days_label = "?"

            drafts = c.get("drafts", {})
            platforms = list(drafts.keys()) if drafts else []
            plat_str = "/".join(p[:2].upper() for p in platforms[:3]) if platforms else "none"

            lines.append(f"{emoji} {cid}  {occasion}  {ev_str} ({days_label})  [{status}]  {plat_str}")

        lines += [
            "",
            "Use: stage preview <ID> for draft content",
            "Approve/dismiss: use AI chat (/stage approve <ID>)",
        ]
        return "\n".join(lines), "N/A"

    except Exception as e:  # noqa: BLE001
        return f"⚠️ Stage error: {e}", "N/A"


# ── cmd_radar ───────────────────────────────────────────────────

async def cmd_radar(args: list[str], scope: str) -> tuple[str, str]:
    """Show current AI radar signals or manage the Topic Interest Graph.

    Usage:
      /radar              — list current week's top signals
      /radar list         — same as above
      /radar topic add <name>  — add a topic to the Interest Graph
      /radar topic rm <name>   — remove a topic from the Interest Graph
    """
    from pathlib import Path as _Path  # noqa: PLC0415

    artha_dir = _Path(__file__).parent.parent

    subcommand = args[0].lower() if args else "list"

    # ── topic management ──────────────────────────────────────────────────
    if subcommand == "topic":
        action = args[1].lower() if len(args) > 1 else ""
        topic_name = " ".join(args[2:]).strip() if len(args) > 2 else ""
        if not topic_name:
            return "Usage: /radar topic add <name> OR /radar topic rm <name>", "N/A"
        try:
            import yaml as _yaml  # noqa: PLC0415
            state_path = artha_dir / "state" / "ai_trend_radar.md"
            text = state_path.read_text(encoding="utf-8")
            parts = text.split("---", 2)
            if len(parts) < 3:
                return "⚠️ ai_trend_radar.md frontmatter missing.", "N/A"
            fm = _yaml.safe_load(parts[1]) or {}
            topics = fm.setdefault("topics_of_interest", []) or []

            if action == "add":
                existing = [t["name"].lower() for t in topics if isinstance(t, dict)]
                if topic_name.lower() in existing:
                    return f"Topic '{topic_name}' already in Interest Graph.", "N/A"
                topics.append({"name": topic_name, "keywords": [topic_name.lower()], "boost": 0.3})
                fm["topics_of_interest"] = topics
                new_fm = _yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True)
                state_path.write_text("---\n" + new_fm + "---" + parts[2], encoding="utf-8")
                return f"✅ Added '{topic_name}' to AI Radar Interest Graph.", "N/A"

            elif action in ("rm", "remove"):
                before = len(topics)
                topics = [t for t in topics if isinstance(t, dict) and t.get("name", "").lower() != topic_name.lower()]
                if len(topics) == before:
                    return f"⚠️ Topic '{topic_name}' not found in Interest Graph.", "N/A"
                fm["topics_of_interest"] = topics
                new_fm = _yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True)
                state_path.write_text("---\n" + new_fm + "---" + parts[2], encoding="utf-8")
                return f"🗑 Removed '{topic_name}' from AI Radar Interest Graph.", "N/A"

            else:
                return "Usage: /radar topic add <name> OR /radar topic rm <name>", "N/A"

        except Exception as e:  # noqa: BLE001
            return f"⚠️ Radar topic error: {e}", "N/A"

    # ── list signals (default) ────────────────────────────────────────────
    signals_path = artha_dir / "tmp" / "ai_trend_signals.json"
    if not signals_path.exists():
        return (
            "📡 No radar signals yet. Run a catch-up to populate.\n"
            "Hint: ensure RSS is enabled and at least one AI feed is active.",
            "N/A",
        )
    try:
        data = json.loads(signals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return f"⚠️ Could not read signals: {e}", "N/A"

    signals = data.get("signals") or []
    if not signals:
        return "📡 Radar ran but found no signals this week.", "N/A"

    week_end = data.get("week_end", "")
    lines = [f"🧠 AI RADAR — week of {week_end}" if week_end else "🧠 AI RADAR", ""]
    for i, sig in enumerate(signals, start=1):
        topic = sig.get("topic", "?")[:60]
        cat = sig.get("category", "?")
        score = sig.get("relevance_score", 0.0)
        seen = sig.get("seen_in", 1)
        try_flag = " ✅" if sig.get("try_worthy") else ""
        sig_id = sig.get("id", "?")[:8]
        lines.append(f"{i}. [{sig_id}] {topic}{try_flag}")
        lines.append(f"   {cat} | score={score:.2f} | {seen} source(s)")

    lines += [
        "",
        "✅ = try-worthy. Use: /try <topic> to log an experiment",
        "Use: /radar topic add <name> to track new interests",
    ]
    return "\n".join(lines), "N/A"


# ── cmd_radar_try ───────────────────────────────────────────────

async def cmd_radar_try(args: list[str], scope: str) -> tuple[str, str]:
    """Log an AI topic/tool as an active experiment.

    Usage: /try <topic description>
    Adds an experiment entry to state/ai_trend_radar.md with status: active.
    """
    topic = " ".join(args).strip() if args else ""
    if not topic:
        return "Usage: /try <topic or tool name>", "N/A"
    try:
        import yaml as _yaml  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415
        from datetime import date as _date  # noqa: PLC0415

        artha_dir = _Path(__file__).parent.parent
        state_path = artha_dir / "state" / "ai_trend_radar.md"
        text = state_path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            return "⚠️ ai_trend_radar.md frontmatter missing.", "N/A"
        fm = _yaml.safe_load(parts[1]) or {}
        experiments = fm.setdefault("experiments", []) or []

        # Generate a simple ID
        import hashlib  # noqa: PLC0415
        exp_id = "EXP-" + hashlib.sha256(topic.encode()).hexdigest()[:6].upper()
        if any(e.get("id") == exp_id for e in experiments):
            return f"⚠️ Experiment for '{topic[:40]}' already exists ({exp_id}).", "N/A"

        experiments.append({
            "id": exp_id,
            "topic": topic,
            "status": "active",
            "started_date": _date.today().isoformat(),
            "verdict": "pending",
            "moment_emitted": False,
        })
        fm["experiments"] = experiments
        new_fm = _yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True)
        state_path.write_text("---\n" + new_fm + "---" + parts[2], encoding="utf-8")
        return (
            f"🧪 Experiment logged: {exp_id}\n"
            f"Topic: {topic[:80]}\n"
            "Status: active\n"
            f"Use: /verdict {exp_id} great|useful|skip when done.",
            "N/A",
        )
    except Exception as e:  # noqa: BLE001
        return f"⚠️ /try error: {e}", "N/A"


# ── cmd_radar_skip ──────────────────────────────────────────────

async def cmd_radar_skip(args: list[str], scope: str) -> tuple[str, str]:
    """Mark a radar signal topic as skipped this week.

    Usage: /skip <topic or signal ID>
    Adds the signal ID to the skipped list so it won't resurface next week.
    """
    topic_or_id = " ".join(args).strip() if args else ""
    if not topic_or_id:
        return "Usage: /skip <topic or signal ID>", "N/A"
    try:
        import yaml as _yaml  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415
        from datetime import date as _date  # noqa: PLC0415

        artha_dir = _Path(__file__).parent.parent
        state_path = artha_dir / "state" / "ai_trend_radar.md"
        text = state_path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            return "⚠️ ai_trend_radar.md frontmatter missing.", "N/A"
        fm = _yaml.safe_load(parts[1]) or {}
        skipped = fm.setdefault("skipped_signals", []) or []

        if topic_or_id in skipped:
            return f"Already skipped: '{topic_or_id}'.", "N/A"
        skipped.append(topic_or_id)
        fm["skipped_signals"] = skipped
        new_fm = _yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True)
        state_path.write_text("---\n" + new_fm + "---" + parts[2], encoding="utf-8")
        return f"⏭ Skipped '{topic_or_id}' — won't resurface next week.", "N/A"
    except Exception as e:  # noqa: BLE001
        return f"⚠️ /skip error: {e}", "N/A"
