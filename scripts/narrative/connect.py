"""
scripts/narrative/connect.py — Connect-cycle narrative templates.

Standalone functions (accept NarrativeEngineBase as ``base``):
  generate_connect_summary(base)
  generate_calibration_brief(base)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from work.helpers import _extract_section  # noqa: E402

if TYPE_CHECKING:
    from narrative._base import NarrativeEngineBase


def generate_connect_summary(base: "NarrativeEngineBase") -> str:
    """Generate a Connect cycle preparation summary with auto-evidence matching.

    Reads Connect goals from work-performance.md, auto-matches milestones
    from work-project-journeys.md by keyword, and surfaces gap analysis
    (Phase 3 item 8, §7.6).
    """
    perf_fm = base._fm("work-performance")
    perf_body = base._body("work-performance")
    journeys_body = base._body("work-project-journeys")
    proj_fm = base._fm("work-projects")
    completed = proj_fm.get("completed_recent_count", 0)

    # ── Parse Connect goals from work-performance.md ──────────────────
    goals: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in perf_body.split("\n"):
        if line.startswith("### Goal "):
            if current.get("title"):
                goals.append(current)
            title = line.strip("# ").strip()
            # Strip "Goal N: " prefix for display
            title = re.sub(r"^Goal \d+:\s*", "", title)
            current = {"title": title, "status": "", "priority": "", "target": ""}
        elif "Status:" in line and current:
            current["status"] = line.split(":", 1)[1].strip()[:100] if ":" in line else ""
        elif "Priority:" in line and current:
            current["priority"] = line.split(":", 1)[1].strip()[:20] if ":" in line else ""
        elif "Target:" in line and current and not current["target"]:
            current["target"] = line.split(":", 1)[1].strip()[:120] if ":" in line else ""
    if current.get("title"):
        goals.append(current)

    # Fall back to profile goals if work-performance.md has none
    if not goals:
        for pg in base.profile.get("profile", {}).get("goals", []):
            goals.append({
                "title": pg.get("title", "Goal"),
                "status": pg.get("status", ""),
                "priority": "",
                "target": pg.get("target", ""),
            })

    # ── Parse milestone rows from work-project-journeys.md ────────────
    milestone_rows: list[dict[str, str]] = []
    for line in journeys_body.split("\n"):
        if line.startswith("| **") and "---" not in line and "Date" not in line:
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if len(cols) >= 3:
                milestone_rows.append({
                    "date": cols[0].strip("*").strip(),
                    "milestone": cols[1],
                    "evidence": cols[2] if len(cols) > 2 else "",
                    "impact": cols[3] if len(cols) > 3 else "",
                })

    def _keywords_for_goal(goal: dict[str, str]) -> list[str]:
        """Extract meaningful keywords from goal title and target."""
        stop = {"the", "a", "an", "and", "or", "for", "in", "on", "of",
                "to", "by", "with", "from", "at", "is", "are", "was",
                "be", "by", "via", "its", "has"}
        text = f"{goal['title']} {goal['target']}"
        words = re.findall(r"[A-Za-z][A-Za-z0-9\-]+", text)
        return [w.lower() for w in words if w.lower() not in stop and len(w) >= 3]

    def _match_score(keywords: list[str], row: dict[str, str]) -> int:
        text = " ".join([row["milestone"], row["evidence"], row["impact"]]).lower()
        return sum(1 for kw in keywords if kw in text)

    lines = []
    lines.append("# Connect Cycle Preparation\n\n")

    connect_period = perf_fm.get("connect_period", "")
    if connect_period:
        lines.append(f"_Period: {connect_period}_\n\n")

    if goals:
        lines.append("## Goals Evidence Map\n\n")
        gap_goals: list[str] = []

        for goal in goals:
            title = goal["title"]
            status = goal["status"]
            priority = goal["priority"]
            target = goal.get("target", "")

            keywords = _keywords_for_goal(goal)
            matched = [r for r in milestone_rows if _match_score(keywords, r) >= 1]
            scored = sorted(matched, key=lambda r: _match_score(keywords, r), reverse=True)
            top = scored[:5]

            density = len(top)
            if density >= 5:
                stars = "★★★"
            elif density >= 3:
                stars = "★★☆"
            elif density >= 1:
                stars = "★☆☆"
            else:
                stars = "☆☆☆"
                gap_goals.append(title[:50])

            priority_tag = f" [{priority}]" if priority else ""
            gap_flag = " ⚠ **GAP — no evidence found**" if density == 0 else ""

            lines.append(f"### {title}{priority_tag}\n\n")
            if target:
                lines.append(f"- Target: {target}\n")
            if status:
                lines.append(f"- Status: {status}\n")
            lines.append(f"- Evidence Density: {stars} ({density} milestone matches){gap_flag}\n")

            if top:
                lines.append("- Auto-matched evidence from work-project-journeys.md:\n")
                for row in top:
                    lines.append(
                        f"  - **{row['date']}**: {row['milestone'][:80]}"
                        + (f" — {row['impact'][:60]}" if row.get('impact') else "")
                        + "\n"
                    )
            else:
                lines.append(
                    "- Evidence: _Add milestones to work-project-journeys.md or run `/work refresh`_\n"
                )
            lines.append("\n")

        if gap_goals:
            lines.append("## Evidence Gaps\n\n")
            for g in gap_goals:
                lines.append(f"- ⚠ **{g}** — no milestone evidence matched. "
                             "Add entries to work-project-journeys.md.\n")
            lines.append("\n")
    else:
        lines.append("_No Connect goals found. Run `/work bootstrap` or populate work-performance.md._\n\n")

    if completed:
        lines.append("## Recent Delivery (auto-linked)\n\n")
        lines.append(
            f"_{completed} items completed in the last 14 days — "
            "cross-reference with goals above._\n\n"
        )

    # Kusto-validated program metrics as quantitative evidence
    pm = base._load_program_metrics()
    if pm["workstreams"]:
        lines.append("## Quantitative Evidence (Kusto-validated)\n\n")
        lines.append(
            "Metrics below are auto-extracted from xpf-program-structure.md. "
            "Use as supporting evidence for goals related to program execution.\n\n"
        )
        # Green metrics = wins to cite
        greens = [
            km for ws in pm["workstreams"]
            for km in base._extract_ws_metrics(ws["id"], "🟢")
        ]
        if greens:
            lines.append("**Wins (Green Metrics):**\n")
            for g in greens[:6]:
                lines.append(f"- ✅ {g['name']}: {g['value']}\n")
            lines.append("\n")

        sig = pm["signal_summary"]
        lines.append(
            f"**Program Scale:** {sig['red']+sig['yellow']+sig['green']} tracked metrics, "
            f"Risk Posture: {pm['risk_posture'] or 'N/A'}\n\n"
        )

    lines.append("## Manager 1:1 Pivot Log\n\n")
    pivot_section = _extract_section(perf_body, "Manager 1:1 Pivot Log")
    if pivot_section and len(pivot_section) > 30:
        lines.append(pivot_section + "\n\n")
    else:
        lines.append("_Not yet populated. Use `/work notes` after each 1:1 to log pivots._\n\n")

    lines.append(base._freshness_footer())
    return "".join(lines)


def generate_calibration_brief(base: "NarrativeEngineBase") -> str:
    """
    Generate a calibration defense brief (§7.6, Phase 3).

    Third-person brief optimized for the calibration room — where the
    manager advocates for the user without them present.

    Structure:
      - The Case In One Sentence (auto-thesis)
      - Impact Summary table (program | outcome | evidence | visibility)
      - Evidence Density per goal (stars rating, gaps flagged)
      - Cross-Team Visibility (visibility events, stakeholder count)
      - Manager Risk Signal (what to say if challenged)
      - Readiness Signal
    """
    journeys_fm = base._fm("work-project-journeys")
    journeys_body = base._body("work-project-journeys")
    perf_body = base._body("work-performance")
    people_body = base._body("work-people")
    career_body = base._body("work-career")

    today = datetime.now(timezone.utc)
    role = base.profile.get("profile", {}).get("role", "Candidate")
    team = base.profile.get("profile", {}).get("team", "")
    projects_tracked = int(journeys_fm.get("projects_tracked", 0))

    # ── Scope entries from journeys ────────────────────────────────────
    scope_entries: list[dict[str, str]] = []
    current_project = ""
    for line in journeys_body.split("\n"):
        if line.startswith("## ") and not line.startswith("### "):
            current_project = line[3:].strip()
        if line.startswith("| **") and "---" not in line and "Date" not in line:
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if len(cols) >= 2:
                scope_entries.append({
                    "project": current_project,
                    "date": cols[0].strip("*"),
                    "milestone": cols[1] if len(cols) > 1 else "",
                    "evidence": cols[2] if len(cols) > 2 else "",
                    "impact": cols[3] if len(cols) > 3 else "",
                })

    # ── Goals with evidence density ────────────────────────────────────
    goals: list[tuple[str, str, int]] = []
    current_goal = ""
    current_status = ""
    current_evidence = 0
    for line in perf_body.split("\n"):
        if line.startswith("### Goal "):
            if current_goal:
                goals.append((current_goal, current_status, current_evidence))
            current_goal = line.strip("# ").strip()
            current_status = ""
            current_evidence = 0
        elif "Status:" in line and current_goal:
            current_status = line.split(":", 1)[1].strip()[:80] if ":" in line else ""
        elif line.strip().startswith("- ") and current_goal:
            current_evidence += 1
    if current_goal:
        goals.append((current_goal, current_status, current_evidence))

    # ── Visibility events ──────────────────────────────────────────────
    vis_events: list[str] = []
    in_vis = False
    for line in people_body.split("\n"):
        if line.startswith("## Visibility Events"):
            in_vis = True
            continue
        if in_vis and line.startswith("## "):
            break
        if in_vis and line.startswith("| ") and "---" not in line and "Date" not in line:
            vis_events.append(line.strip())

    unique_stakeholders = len({
        cols[1].strip()
        for ev in vis_events
        for cols in [[c.strip() for c in ev.split("|") if c.strip()]]
        if len(cols) >= 2
    })

    # ── Manager voice from career ──────────────────────────────────────
    manager_voice: list[str] = []
    in_recog = False
    for line in career_body.split("\n"):
        if "## Recognition" in line or "## Manager" in line:
            in_recog = True
            continue
        if in_recog and line.startswith("## "):
            in_recog = False
        if in_recog and line.strip().startswith("- "):
            manager_voice.append(line.strip()[2:])
    manager_voice = manager_voice[:4]

    # ── Readiness ─────────────────────────────────────────────────────
    thin_goals = [g for g, _, ev in goals if ev < 3]
    total_evidence = sum(ev for _, _, ev in goals)
    if goals and len(thin_goals) == 0 and vis_events and scope_entries:
        readiness = "ready"
    elif len(thin_goals) <= 1 and total_evidence >= 5:
        readiness = "1-2 quarters away"
    else:
        readiness = "critical gaps blocking"

    lines = []
    lines.append("# Calibration Defense Brief\n\n")
    lines.append(f"_For manager use only. Third-person format. {today.strftime('%Y-%m-%d')}_\n\n")
    lines.append("> **DRAFT** — For use by manager in calibration discussions only.\n\n")
    lines.append("---\n\n")

    lines.append("## The Case In One Sentence\n\n")
    if scope_entries and projects_tracked:
        lines.append(
            f"_{role} has led {projects_tracked} high-priority programs with "
            f"{len(scope_entries)} documented milestones, "
            f"{unique_stakeholders} senior stakeholders with direct visibility, "
            f"and {total_evidence} evidence-bearing goals "
            f"— delivering principal-level scope and impact in {team}._\n\n"
        )
    else:
        lines.append(
            f"_{role} contributions and impact — evidence below. "
            f"Run `/work bootstrap` to populate work-project-journeys.md for auto-thesis._\n\n"
        )

    lines.append("## Impact Summary\n\n")
    if scope_entries:
        lines.append("| Program | Most Recent Milestone | Evidence | Impact |\n")
        lines.append("| --- | --- | --- | --- |\n")
        seen_projects: set[str] = set()
        for e in reversed(scope_entries):
            if e["project"] not in seen_projects:
                seen_projects.add(e["project"])
                lines.append(
                    f"| {e['project'][:40]} | {e['milestone'][:50]} "
                    f"| {e['evidence'][:35]} | {e['impact'][:35]} |\n"
                )
    else:
        lines.append("_[Populate work-project-journeys.md via `/work bootstrap` or `/work notes`]_\n")
    lines.append("\n")

    lines.append("## Evidence Density\n\n")
    if goals:
        lines.append("| Goal | Status | Evidence | Gaps |\n")
        lines.append("| --- | --- | --- | --- |\n")
        for goal_name, status, ev_count in goals:
            stars = "★" * min(ev_count, 5) + "☆" * max(0, 5 - ev_count)
            gap_flag = "⚠ THIN" if ev_count < 3 else "✅"
            lines.append(
                f"| {goal_name[:45]} | {(status or '—')[:30]} "
                f"| {stars} ({ev_count}) | {gap_flag} |\n"
            )
    else:
        lines.append("_[No Connect goals found — populate work-performance.md]_\n")
    lines.append("\n")

    lines.append("## Cross-Team Visibility\n\n")
    if vis_events:
        lines.append(f"**{len(vis_events)} visibility events** | **{unique_stakeholders} unique stakeholders**\n\n")
        lines.append("| Date | Stakeholder | Event Type | Context |\n")
        lines.append("| --- | --- | --- | --- |\n")
        for ev in vis_events[:6]:
            cols = [c.strip() for c in ev.split("|") if c.strip()]
            if len(cols) >= 4:
                lines.append(f"| {cols[0]} | {cols[1]} | {cols[2]} | {cols[3][:50]} |\n")
    else:
        lines.append(
            "⚠ _No visibility events captured yet._\n"
            "Run `/work refresh` to begin automatic collection from email + meetings.\n"
        )
    lines.append("\n")

    lines.append("## Manager Talking Points\n\n")
    if manager_voice:
        lines.append("_Verbatim signals captured from 1:1s and feedback:_\n\n")
        for mv in manager_voice:
            lines.append(f'- "{mv}"\n')
    else:
        lines.append(
            "_[Manager voice not yet captured. Log signals after 1:1s via `/work notes`]_\n"
        )
    lines.append("\n")

    lines.append("## Readiness Signal\n\n")
    signal_icon = "✅" if readiness == "ready" else ("⚠" if "quarters" in readiness else "🔴")
    lines.append(f"**{signal_icon} {readiness}**\n\n")
    if thin_goals:
        lines.append("**Gaps to address before calibration:**\n\n")
        for tg in thin_goals[:3]:
            lines.append(f"- Add evidence to: _{tg[:50]}_\n")
    if not vis_events:
        lines.append("- Capture visibility events via `/work refresh`\n")
    lines.append("\n")

    lines.append(base._freshness_footer())
    lines.append("\n> **DRAFT** — Manager: review and tailor before the calibration session.\n")
    return "".join(lines)
