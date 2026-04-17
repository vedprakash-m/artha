"""scripts/skills/fitness_coach.py — Fitness Coach Skill (P6.3)

Reads:
  ~/.artha-local/workouts.jsonl   — append-only local workout log
  state/goals.md                  — via goals_writer._split_frontmatter()
                                    (do NOT read state/goals.md directly —
                                     use goals_writer parse to avoid YAML drift)

Goal fields (from YAML frontmatter):
  G-001.current_value  — cumulative distance logged (km)
  G-001.target_value   — Mailbox Peak total distance target
  G-002.current_value  — cumulative elevation gain (ft)

Best-effort lookup: if state/goals.md absent, stale, or field path missing,
populate goal fields with None and emit ack without goal section. Log
internally; never surface error to user.

Ref: specs/ac-int.md §7.7, P10 declaration, §8.1 tool boundaries.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from skills.base_skill import BaseSkill  # type: ignore[import]

# ── Paths ─────────────────────────────────────────────────────────────────────

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_WORKOUTS_FILE = Path.home() / ".artha-local" / "workouts.jsonl"
_GOALS_FILE = _ARTHA_DIR / "state" / "goals.md"


# ── Goal reader ───────────────────────────────────────────────────────────────


def _read_goal_fields() -> dict[str, Any]:
    """Parse goals frontmatter and return G-001/G-002 fields.

    Uses goals_writer._split_frontmatter() — not a direct YAML read.
    Returns a dict with keys: g001_current, g001_target, g002_current.
    All values default to None on any lookup failure (best-effort).
    """
    result: dict[str, Any] = {
        "g001_current": None,
        "g001_target": None,
        "g002_current": None,
    }

    if not _GOALS_FILE.exists():
        return result

    try:
        sys.path.insert(0, str(_ARTHA_DIR / "scripts"))
        from goals_writer import _split_frontmatter  # type: ignore[import]

        content = _GOALS_FILE.read_text(encoding="utf-8")
        fm, _ = _split_frontmatter(content)
        goals: list[dict] = fm.get("goals", [])

        for goal in goals:
            gid = goal.get("id", "")
            if gid == "G-001":
                result["g001_current"] = goal.get("current_value")
                result["g001_target"] = goal.get("target_value")
            elif gid == "G-002":
                result["g002_current"] = goal.get("current_value")

    except Exception as exc:
        print(
            f"[fitness_coach] goal lookup failed (ack will omit goal section): {exc}",
            file=sys.stderr,
        )

    return result


# ── Skill class ───────────────────────────────────────────────────────────────


class FitnessCoachSkill(BaseSkill):
    """Summarise recent workout log and Mailbox Peak goal progress."""

    def __init__(self, artha_dir: Path | None = None) -> None:
        super().__init__(name="fitness_coach", priority="P1")
        self.artha_dir = artha_dir or _ARTHA_DIR

    @property
    def compare_fields(self) -> list[str]:
        return ["workout_count", "g001_current", "g002_current"]

    # ── BaseSkill interface ───────────────────────────────────────────────

    def pull(self) -> dict[str, Any]:
        """Load workouts.jsonl and goal fields."""
        workouts: list[dict] = []
        if _WORKOUTS_FILE.exists():
            for line in _WORKOUTS_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    workouts.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

        goal_fields = _read_goal_fields()
        return {"workouts": workouts, "goal_fields": goal_fields}

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Compute summary stats."""
        workouts: list[dict] = raw_data["workouts"]
        gf: dict[str, Any] = raw_data["goal_fields"]

        return {
            "workout_count": len(workouts),
            "g001_current": gf["g001_current"],
            "g001_target": gf["g001_target"],
            "g002_current": gf["g002_current"],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "workouts_file": str(_WORKOUTS_FILE),
            "goals_file": str(_GOALS_FILE),
        }

    # ── Public helper ─────────────────────────────────────────────────────

    def goal_progress_line(self) -> str | None:
        """Return a one-line Mailbox Peak progress string, or None if data unavailable.

        Used by workout_log handler ack format.
        Example: 'Mailbox Peak: 9.6mi, 3800ft gain (goal: Aug 2026) 🏔️'
        """
        try:
            gf = _read_goal_fields()
            dist_km = gf["g001_current"]
            dist_target = gf["g001_target"]
            elev_ft = gf["g002_current"]

            if dist_km is None and elev_ft is None:
                return None

            # Convert km to miles for display (round to 1 dp)
            parts: list[str] = []
            if dist_km is not None:
                try:
                    dist_mi = round(float(dist_km) * 0.621371, 1)
                    parts.append(f"{dist_mi}mi")
                except (TypeError, ValueError):
                    pass

            if elev_ft is not None:
                try:
                    parts.append(f"{int(float(elev_ft))}ft gain")
                except (TypeError, ValueError):
                    pass

            if not parts:
                return None

            return "Mailbox Peak: " + ", ".join(parts) + " (goal: Aug 2026) 🏔️"

        except Exception as exc:
            print(
                f"[fitness_coach] goal_progress_line failed: {exc}",
                file=sys.stderr,
            )
            return None


# ── Factory ───────────────────────────────────────────────────────────────────


def get_skill(artha_dir: Path | None = None) -> FitnessCoachSkill:
    """Factory function — entry point for skill_runner.py."""
    return FitnessCoachSkill(artha_dir=artha_dir)
