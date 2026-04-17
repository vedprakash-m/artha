"""channel/router.py — Command alias resolution and handler dispatch."""
from __future__ import annotations
import re
from channel.handlers import (
    cmd_status, cmd_alerts, cmd_tasks, cmd_quick, cmd_domain,
    cmd_dashboard, cmd_goals, cmd_diff, cmd_items_add, cmd_items_done,
    cmd_remember, cmd_cost, cmd_power, cmd_relationships, cmd_help,
    cmd_queue, cmd_approve, cmd_reject, cmd_undo, cmd_unlock,
    cmd_workout_log,
)
from channel.catchup import cmd_catchup
from channel.stage import cmd_stage, cmd_radar, cmd_radar_try, cmd_radar_skip
from channel.llm_bridge import cmd_ask

# ── Workout trigger detection ─────────────────────────────────────────────────
# Matches messages that start with activity/log keywords so channel_listener
# can route them to cmd_workout_log before the LLM fallback.
_WORKOUT_TRIGGER_RE = re.compile(
    r"^(log(?:ged)?|rest\s*day|weight\s|weigh\s|ran|run|hike[d]?|walk(?:ed)?|"
    r"swim(?:ming)?|cycl(?:e[d]?|ing)|strength|lift(?:ed)?)",
    re.IGNORECASE,
)


def is_workout_trigger(text: str) -> bool:
    """Return True if message text looks like a workout log entry."""
    return bool(_WORKOUT_TRIGGER_RE.match(text.strip()))


_COMMAND_ALIASES: dict[str, str] = {
    "catchup":             "/catchup",
    "catch-up":            "/catchup",
    "catch up":            "/catchup",
    "briefing":            "/catchup",
    "brief":               "/catchup",
    "status":              "/status",
    "s":                   "/status",
    "alerts":              "/alerts",
    "alert":               "/alerts",
    "a":                   "/alerts",
    "tasks":               "/tasks",
    "task":                "/tasks",
    "items":               "/tasks",
    "t":                   "/tasks",
    "quick":               "/quick",
    "q":                   "/quick",
    "domain":              "/domain",
    "d":                   "/domain",
    "dashboard":           "/dashboard",
    "dash":                "/dashboard",
    "db":                  "/dashboard",
    "goals":               "/goals",
    "goal":                "/goals",
    "g":                   "/goals",
    "diff":                "/diff",
    "items add":           "/items_add",
    "item add":            "/items_add",
    "add item":            "/items_add",
    "items done":          "/items_done",
    "item done":           "/items_done",
    "done":                "/items_done",
    "remember":            "/remember",
    "note":                "/remember",
    "inbox":               "/remember",
    "power":               "/power",
    "power half hour":     "/power",
    "relationships":       "/relationships",
    "relationship pulse":  "/relationships",
    "help":                "/help",
    "h":                   "/help",
    "?":                   "/help",
    "unlock":              "/unlock",
    "queue":               "/queue",
    "approve":             "/approve",
    "reject":              "/reject",
    "undo":                "/undo",
    "cost":                "/cost",
    "stage":               "/stage",
    "stage list":          "/stage",
    "radar":               "/radar",
    "ai radar":            "/radar",
    "radar list":          "/radar",
    "try":                 "/radar_try",
    "skip":                "/radar_skip",
    "radar topic":         "/radar",
    "radar topic add":     "/radar",
    "radar topic rm":      "/radar",
}

ALLOWED_COMMANDS = frozenset(_COMMAND_ALIASES.values())

_HANDLERS = {
    "/status": cmd_status,
    "/alerts": cmd_alerts,
    "/tasks": cmd_tasks,
    "/quick": cmd_quick,
    "/domain": cmd_domain,
    "/dashboard": cmd_dashboard,
    "/catchup": cmd_catchup,
    "/goals": cmd_goals,
    "/diff": cmd_diff,
    "/items_add": cmd_items_add,
    "/items_done": cmd_items_done,
    "/remember": cmd_remember,
    "/cost": cmd_cost,
    "/power": cmd_power,
    "/relationships": cmd_relationships,
    "/help": cmd_help,
    "/queue": cmd_queue,
    "/approve": cmd_approve,
    "/reject": cmd_reject,
    "/undo": cmd_undo,
    "/stage": cmd_stage,
    "/radar": cmd_radar,
    "/radar_try": cmd_radar_try,
    "/radar_skip": cmd_radar_skip,
    "/workout_log": cmd_workout_log,
}



# ── _normalise_command ──────────────────────────────────────────

def _normalise_command(raw_text: str) -> tuple[str, list[str]]:
    """Normalise user input to (canonical_command, args).

    Accepts:
      /catchup, catchup, catch-up, "catch up", briefing
      /status, status, s
      /dash, dash, db
      etc.
    Returns ("/catchup", []) or ("", []) if not a recognised command.
    """
    text = raw_text.strip()
    if not text:
        return "", []

    # Try the full text first (handles "catch up" with space)
    lower_full = text.lower().lstrip("/")
    # Check full-text match (e.g. "catch up flash")
    for alias in sorted(_COMMAND_ALIASES, key=len, reverse=True):
        if lower_full == alias or lower_full.startswith(alias + " "):
            cmd = _COMMAND_ALIASES[alias]
            rest = lower_full[len(alias):].strip()
            args = rest.split() if rest else []
            return cmd, args

    # Try first word only (handles "/status" style)
    parts = text.split()
    first = parts[0].lower().lstrip("/")
    if first in _COMMAND_ALIASES:
        return _COMMAND_ALIASES[first], parts[1:]

    return "", []
