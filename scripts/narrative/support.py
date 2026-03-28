"""
scripts/narrative/support.py — Support narrative templates.

Standalone functions (accept NarrativeEngineBase as ``base``):
  generate_talking_points(base, topic)
  generate_boundary_report(base)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from narrative._base import NarrativeEngineBase


def generate_talking_points(base: "NarrativeEngineBase", topic: str) -> str:
    """
    Generate concise talking points for a meeting or topic.

    Searches work-projects, work-decisions, and work-people for
    content related to the topic. Returns a structured talking-
    points format suitable for meeting preparation.
    """
    topic_lower = topic.lower()

    # Search for relevant content across state files
    relevant_projects = []
    proj_body = base._body("work-projects")
    for line in proj_body.split("\n"):
        if topic_lower in line.lower() and line.strip():
            relevant_projects.append(line.strip("- ").strip())

    decisions_body = base._body("work-decisions")
    relevant_decisions = []
    for line in decisions_body.split("\n"):
        if topic_lower in line.lower() and line.strip() and not line.startswith("#"):
            relevant_decisions.append(line.strip("- ").strip())

    people_body = base._body("work-people")
    relevant_stakeholders = []
    for line in people_body.split("\n"):
        if topic_lower in line.lower() and "stakeholder" not in line.lower() and line.strip():
            relevant_stakeholders.append(line.strip("- ").strip())

    # Connect goals
    goals = base.profile.get("profile", {}).get("goals", [])
    aligned_goals = [
        g.get("title", "") for g in goals
        if topic_lower in g.get("title", "").lower()
    ]

    lines = []
    lines.append(f"# Talking Points — {topic}\n\n")
    lines.append(f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n\n")

    lines.append("## Opening Frame (30 sec)\n\n")
    lines.append(f"_[Start with: why {topic} matters right now, in one sentence]_\n\n")

    if relevant_projects[:3]:
        lines.append("## Delivery Status\n\n")
        for item in relevant_projects[:3]:
            lines.append(f"- {item}\n")
        lines.append("\n")
    else:
        lines.append("## Delivery Status\n\n")
        lines.append("_[Add: what shipped, what is in-flight, what is at risk]_\n\n")

    if relevant_decisions[:3]:
        lines.append("## Key Decisions\n\n")
        for d in relevant_decisions[:3]:
            lines.append(f"- {d}\n")
        lines.append("\n")

    if aligned_goals:
        lines.append("## Connect Goal Alignment\n\n")
        for g in aligned_goals[:2]:
            lines.append(f"- {g}\n")
        lines.append("\n")

    lines.append("## Risks / Open Items\n\n")
    lines.append("_[Add: blockers, dependencies, asks for the room]_\n\n")

    lines.append("## Ask / Next Step\n\n")
    lines.append("_[End with a clear ask or proposed next action]_\n\n")

    lines.append(base._freshness_footer())
    lines.append("\n> **Draft** — Review before the meeting.\n")

    return "".join(lines)


def generate_boundary_report(base: "NarrativeEngineBase") -> str:
    """Generate a concise boundary health summary for /work pulse."""
    boundary_fm = base._fm("work-boundary")
    score = boundary_fm.get("boundary_score")
    after_hours = boundary_fm.get("after_hours_count", 0)
    total_hours = boundary_fm.get("total_hours_today", 0.0)
    focus_pct = boundary_fm.get("focus_availability_pct", 1.0)

    if score is None:
        return "Boundary data not yet available — run `/work refresh`."

    trend = "✅ healthy" if score >= 80 else ("⚠ elevated" if score >= 60 else "🔴 at-risk")
    lines = [f"Boundary score: **{score}/100** — {trend}"]
    if after_hours:
        lines.append(f"After-hours meetings: {after_hours}")
    if total_hours:
        lines.append(f"Meeting load: {total_hours:.1f}h today")
    focus_label = f"{int(focus_pct * 100)}%" if focus_pct is not None else "—"
    lines.append(f"Focus availability: {focus_label}")
    return "\n".join(lines)
