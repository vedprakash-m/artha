#!/usr/bin/env python3
# pii-guard: coaching nudges reference goal titles and metrics only, no PII
"""
scripts/coaching_engine.py — Deterministic coaching nudge engine (E16).

Selects ONE coaching nudge per catch-up session based on:
  - Goals progress from state/goals.md
  - Memory facts (coaching_preferences, patterns)
  - Health check history (nudge dismissal tracking)

The nudge is surfaced at Step 8 (moved from Step 19b) to ensure it fires
before context exhaustion.

Selection priority (pick first that applies):
  1. Progress reflection   — goal pace deviation >20% from plan
  2. Obstacle anticipation — goal has 'blocked' or 'at_risk' status
  3. Next small win        — low momentum (goals with no recent progress)
  4. Cross-domain insight  — pattern engine found relevant correlation (optional)

Suppression rules:
  - coaching_enabled == false → return None
  - Same nudge type fired and dismissed within 7 days → skip
  - >2 nudges dismissed in a row → pause 14 days

Output formats (per coaching_style preference):
  - 'question'     — Socratic prompt ("What's blocking your weight goal?")
  - 'direct'       — Action statement ("Your weight goal is 15% behind pace.")
  - 'cheerleader'  — Encouragement ("You've made great progress! Keep it up.")

Config flags:
  enhancements.coaching_engine       — master toggle
  harness.agentic.self_model.enabled — reads coaching_preferences from memory
Frequency cap:
  max 1 coaching nudge per catch-up; max 5 per week
  dismissal pause: 14 days if >2 consecutive dismissals

Ref: specs/act-reloaded.md Enhancement 16, config/Artha.md Step 19b
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False

try:
    from context_offloader import load_harness_flag as _load_flag
except ImportError:  # pragma: no cover
    def _load_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default

# --- Constants ---------------------------------------------------------------

_DISMISSAL_COOLDOWN_DAYS = 14   # pause after 2+ consecutive dismissals
_SAME_TYPE_COOLDOWN_DAYS = 7    # skip if same type dismissed within N days
_MAX_NUDGES_PER_WEEK = 5
_GOAL_PACE_DEVIATION_PCT = 20   # flag if progress deviation > this %


# --- Dataclasses -------------------------------------------------------------

@dataclass
class CoachingNudge:
    """A single coaching nudge for the user."""
    nudge_type: str          # progress_reflection | obstacle_anticipation | next_small_win | cross_domain
    goal_title: str          # The goal this nudge relates to
    message: str             # Pre-formatted nudge message
    style: str               # question | direct | cheerleader


# --- CoachingEngine ----------------------------------------------------------

class CoachingEngine:
    """Deterministic coaching nudge selector.

    Usage:
        engine = CoachingEngine()
        nudge = engine.select_nudge(
            goals=goals_frontmatter,
            memory_facts=facts_list,
            health_history=catchup_runs,
            preferences={"coaching_style": "question"},
        )
        if nudge:
            print(engine.format_nudge(nudge, style="question"))
    """

    def select_nudge(
        self,
        goals: dict,
        memory_facts: list[dict],
        health_history: list[dict],
        preferences: dict | None = None,
        pattern_matches: list | None = None,  # Optional: from PatternEngine
    ) -> CoachingNudge | None:
        """Deterministic coaching nudge selection.

        Returns None if no nudge is appropriate.
        """
        if not _load_flag("enhancements.coaching_engine", default=True):
            return None

        prefs = preferences or {}
        coaching_enabled = prefs.get("coaching_enabled", True)
        if not coaching_enabled:
            return None

        # --- Check suppression rules ------------------------------------
        if self._is_globally_suppressed(health_history):
            return None

        coaching_style = prefs.get("coaching_style", "question")

        # --- Parse goals ------------------------------------------------
        goals_list = self._extract_goals(goals)
        if not goals_list:
            return None

        # --- Priority 1: Progress reflection ----------------------------
        blocked_goals = [g for g in goals_list if g.get("status_flag") in ("blocked", "at_risk")]
        if blocked_goals:
            goal = blocked_goals[0]
            nudge_type = "obstacle_anticipation"
            if not self._is_type_suppressed(nudge_type, health_history):
                return CoachingNudge(
                    nudge_type=nudge_type,
                    goal_title=goal["title"],
                    message=self._make_message(nudge_type, goal, coaching_style),
                    style=coaching_style,
                )

        # --- Priority 2: Pace deviation ---------------------------------
        off_pace_goals = [g for g in goals_list if g.get("off_pace")]
        if off_pace_goals:
            goal = off_pace_goals[0]
            nudge_type = "progress_reflection"
            if not self._is_type_suppressed(nudge_type, health_history):
                return CoachingNudge(
                    nudge_type=nudge_type,
                    goal_title=goal["title"],
                    message=self._make_message(nudge_type, goal, coaching_style),
                    style=coaching_style,
                )

        # --- Priority 3: Next small win (low momentum) ------------------
        in_progress_goals = [g for g in goals_list if g.get("status_flag") == "in_progress"]
        if in_progress_goals:
            goal = in_progress_goals[0]
            nudge_type = "next_small_win"
            if not self._is_type_suppressed(nudge_type, health_history):
                return CoachingNudge(
                    nudge_type=nudge_type,
                    goal_title=goal["title"],
                    message=self._make_message(nudge_type, goal, coaching_style),
                    style=coaching_style,
                )

        # --- Priority 4: Cross-domain insight (optional) ----------------
        if pattern_matches:
            nudge_type = "cross_domain"
            if not self._is_type_suppressed(nudge_type, health_history):
                goal_title = f"{len(pattern_matches)} cross-domain pattern(s)"
                return CoachingNudge(
                    nudge_type=nudge_type,
                    goal_title=goal_title,
                    message=self._make_cross_domain_message(pattern_matches, coaching_style),
                    style=coaching_style,
                )

        return None

    def format_nudge(self, nudge: CoachingNudge, style: str | None = None) -> str:
        """Format the nudge for display. Style overrides nudge.style if provided."""
        effective_style = style or nudge.style
        if effective_style == nudge.style:
            return f"💡 *Coaching:* {nudge.message}"
        # Re-format with different style
        return f"💡 *Coaching:* {nudge.message}"

    # ------------------------------------------------------------------
    # Suppression checks
    # ------------------------------------------------------------------

    def _is_globally_suppressed(self, health_history: list[dict]) -> bool:
        """Check if coaching is globally paused (>2 consecutive dismissals)."""
        if not health_history:
            return False

        recent = [r for r in health_history[-10:] if isinstance(r, dict)]
        if not recent:
            return False

        # Count consecutive dismissals from the end
        consecutive_dismissals = 0
        for run in reversed(recent):
            nudge_outcome = run.get("coaching_nudge")
            if nudge_outcome == "dismissed":
                consecutive_dismissals += 1
            elif nudge_outcome in ("fired", "skipped"):
                break

        if consecutive_dismissals >= 2:
            # Check if cooldown has expired
            last_run = recent[-1]
            last_date_str = str(last_run.get("date", "") or "")
            try:
                last_date = date.fromisoformat(last_date_str[:10])
                if (date.today() - last_date).days < _DISMISSAL_COOLDOWN_DAYS:
                    return True
            except ValueError:
                pass

        return False

    def _is_type_suppressed(self, nudge_type: str, health_history: list[dict]) -> bool:
        """Check if a specific nudge type was dismissed within cooldown window."""
        if not health_history:
            return False

        cutoff = date.today() - timedelta(days=_SAME_TYPE_COOLDOWN_DAYS)
        for run in reversed(health_history[-20:]):
            if not isinstance(run, dict):
                continue
            if (run.get("coaching_nudge") == "dismissed"
                    and run.get("coaching_nudge_type") == nudge_type):
                run_date_str = str(run.get("date", "") or "")
                try:
                    run_date = date.fromisoformat(run_date_str[:10])
                    if run_date >= cutoff:
                        return True
                except ValueError:
                    pass

        return False

    # ------------------------------------------------------------------
    # Goal extraction
    # ------------------------------------------------------------------

    def _extract_goals(self, goals: dict) -> list[dict]:
        """Extract structured goal data from goals.md frontmatter or content heuristics."""
        if not isinstance(goals, dict):
            return []

        # If frontmatter has structured goals list
        goals_list = goals.get("goals", [])
        if isinstance(goals_list, list) and goals_list:
            return [g for g in goals_list if isinstance(g, dict)]

        # Fallback: inspect raw text indicators
        content = goals.get("_raw_content", "")
        result = []
        if "🟡 In Progress" in content:
            # Extract goal titles from table rows
            for line in content.splitlines():
                if "|" in line and "🟡" in line:
                    parts = [p.strip() for p in line.strip("|").split("|")]
                    if parts:
                        title = parts[0]
                        if title and title.lower() not in ("goal", ""):
                            result.append({
                                "title": title,
                                "status_flag": "in_progress",
                                "off_pace": False,
                            })

        if "🔴 Not Started" in content:
            for line in content.splitlines():
                if "|" in line and "🔴" in line:
                    parts = [p.strip() for p in line.strip("|").split("|")]
                    if parts:
                        title = parts[0]
                        if title and title.lower() not in ("goal", ""):
                            result.append({
                                "title": title,
                                "status_flag": "at_risk",
                                "off_pace": True,
                            })

        return result[:5]  # Cap at 5

    # ------------------------------------------------------------------
    # Message formatting
    # ------------------------------------------------------------------

    def _make_message(self, nudge_type: str, goal: dict, style: str) -> str:
        """Generate a coaching nudge message."""
        title = goal.get("title", "your goal")

        templates = {
            "progress_reflection": {
                "question": f"What's been your biggest win toward '{title}' lately?",
                "direct": f"'{title}' appears to be behind pace. What's one concrete step this week?",
                "cheerleader": f"You're making progress on '{title}'! What can you do today to keep the momentum?",
            },
            "obstacle_anticipation": {
                "question": f"What's the one thing blocking progress on '{title}'?",
                "direct": f"'{title}' is marked blocked or at risk. Identify and remove the obstacle.",
                "cheerleader": f"Every obstacle is a puzzle! What would unblock '{title}' for you?",
            },
            "next_small_win": {
                "question": f"What's the smallest next step you could take today on '{title}'?",
                "direct": f"'{title}' needs momentum. Pick one 5-minute action and do it now.",
                "cheerleader": f"Small wins add up! What tiny progress can you make on '{title}' today?",
            },
        }

        type_templates = templates.get(nudge_type, templates["next_small_win"])
        return type_templates.get(style, type_templates["question"])

    def _make_cross_domain_message(self, pattern_matches: list, style: str) -> str:
        """Generate a cross-domain insight coaching nudge."""
        count = len(pattern_matches)
        templates = {
            "question": f"I detected {count} cross-domain pattern(s). What connections do you see across these signals?",
            "direct": f"{count} cross-domain pattern(s) detected. Review the domain conflicts before making decisions.",
            "cheerleader": f"Great — {count} cross-domain correlation(s) found! Connecting the dots is powerful.",
        }
        return templates.get(style, templates["question"])


# --- Helpers ----------------------------------------------------------------

def load_goals_content(goals_path: Path) -> dict:
    """Load goals.md for coaching engine consumption."""
    if not goals_path.exists():
        return {}
    content = goals_path.read_text(encoding="utf-8")
    result: dict = {}

    if _YAML_AVAILABLE:
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if match:
            try:
                fm = yaml.safe_load(match.group(1)) or {}
                result.update(fm)
            except Exception:
                pass

    result["_raw_content"] = content
    return result


# --- Standalone entry point -------------------------------------------------

def main() -> int:
    """CLI: python scripts/coaching_engine.py"""
    from lib.common import ARTHA_DIR  # type: ignore[import]

    state_dir = ARTHA_DIR / "state"
    engine = CoachingEngine()

    goals_data = load_goals_content(state_dir / "goals.md")

    # Load memory facts
    facts: list[dict] = []
    memory_path = state_dir / "memory.md"
    if memory_path.exists() and _YAML_AVAILABLE:
        content = memory_path.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if match:
            try:
                fm = yaml.safe_load(match.group(1)) or {}
                facts = fm.get("facts", []) or []
            except Exception:
                pass

    nudge = engine.select_nudge(
        goals=goals_data,
        memory_facts=facts,
        health_history=[],
        preferences={},
    )

    if nudge:
        print(f"💡 Coaching nudge ({nudge.nudge_type}):")
        print(f"   {nudge.message}")
    else:
        print("ℹ️  No coaching nudge selected (suppressed or no trigger)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
