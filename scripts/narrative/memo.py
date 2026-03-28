"""
scripts/narrative/memo.py — Memo narrative templates.

Standalone functions (accept NarrativeEngineBase as ``base``):
  generate_weekly_memo(base, period=None)
  generate_escalation_memo(base, context)
  generate_decision_memo(base, decision_id="")
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional, TYPE_CHECKING

from work.helpers import _extract_section  # noqa: E402

if TYPE_CHECKING:
    from narrative._base import NarrativeEngineBase


def generate_weekly_memo(base: "NarrativeEngineBase", period: Optional[str] = None) -> str:
    """
    Generate a weekly status memo draft from operating data.

    Template structure (§7.10):
      - Header: week period + key stats
      - Status: projects health summary
      - Highlights: completed work, decisions, key milestones
      - Risks: blocked items, boundary score, at-risk commitments
      - Next week: active items preview
      - Asks: items needing escalation or support
      - Freshness footer
    """
    # Determine period
    if not period:
        today = datetime.now(timezone.utc)
        week_start = today - timedelta(days=today.weekday())
        period = f"Week of {week_start.strftime('%B')} {week_start.day}, {week_start.year}"

    # Load all domain data
    cal_fm = base._fm("work-calendar")
    proj_fm = base._fm("work-projects")
    comms_fm = base._fm("work-comms")
    boundary_fm = base._fm("work-boundary")
    perf_fm = base._fm("work-performance")

    # Extract key metrics
    meetings_today = cal_fm.get("meetings_today", "—")
    hours_today = cal_fm.get("hours_today", "—")
    active_items = proj_fm.get("active_count", "—")
    blocked_items = proj_fm.get("blocked_count", 0)
    completed_recent = proj_fm.get("completed_recent_count", 0)
    action_required = comms_fm.get("action_required_count", 0)
    boundary_score = boundary_fm.get("boundary_score")
    after_hours = boundary_fm.get("after_hours_count", 0)

    # Extract Connect goals from performance state
    goals = base.profile.get("profile", {}).get("goals", [])

    # Extract body sections
    proj_body = base._body("work-projects")
    completed_section = _extract_section(proj_body, "Recently Completed")
    blocked_section = _extract_section(proj_body, "Blocked Items")

    role = base.profile.get("profile", {}).get("role", "")
    team = base.profile.get("profile", {}).get("team", "")
    manager = base.profile.get("profile", {}).get("manager", "")

    lines = []
    lines.append(f"# Weekly Status Memo — {period}\n\n")

    if role or team:
        lines.append(f"**{role}** | {team}\n\n")

    # Header stats block
    lines.append("## At a Glance\n\n")
    lines.append("| Dimension | Status |\n|-----------|--------|\n")
    if active_items != "—":
        lines.append(f"| Active work items | {active_items} |\n")
    if blocked_items:
        lines.append(f"| Blocked items | **{blocked_items}** ⚠ |\n")
    if completed_recent:
        lines.append(f"| Completed (14 days) | {completed_recent} |\n")
    if action_required:
        lines.append(f"| Comms needing response | {action_required} |\n")
    if boundary_score is not None:
        score_label = "✅ healthy" if boundary_score >= 80 else ("⚠ elevated" if boundary_score >= 60 else "🔴 at-risk")
        lines.append(f"| Boundary score | {boundary_score}/100 — {score_label} |\n")
    lines.append("\n")

    # Status — project health
    lines.append("## Delivery Status\n\n")
    if completed_section:
        lines.append(completed_section + "\n\n")
    elif completed_recent:
        lines.append(f"_{completed_recent} work items completed in the last 14 days._\n\n")
    else:
        lines.append("_[Add highlights of work completed this week]_\n\n")

    # Risks
    if blocked_items or after_hours >= 3:
        lines.append("## Risks & Flags\n\n")
        if blocked_section:
            lines.append(blocked_section + "\n\n")
        elif blocked_items:
            lines.append(f"- **{blocked_items} blocked item(s)** — see work-projects for details.\n\n")
        if after_hours >= 3:
            lines.append(f"- **Boundary risk**: {after_hours} after-hours meetings this week.\n\n")

    # Connect goals summary (from profile)
    if goals:
        lines.append("## Connect Goals — Progress This Week\n\n")
        for goal in goals[:5]:
            title = goal.get("title", "Goal")
            status = goal.get("status", "in_progress")
            status_icon = {"on_track": "✅", "in_progress": "🔄", "at_risk": "⚠", "completed": "✅"}.get(
                status.lower().replace("-", "_"), "🔄"
            )
            lines.append(f"- {status_icon} **{title}** — status: {status}\n")
            lines.append("  - _[Add evidence / progress note from this week]_\n")
        lines.append("\n")

    # Next steps
    lines.append("## Next Week\n\n")
    if active_items != "—" and int(active_items) > 0:  # type: ignore[arg-type]
        lines.append(f"_{active_items} active items carry forward. Top priorities:_\n\n")
    lines.append("- _[Add your top 3 priorities for next week]_\n\n")

    # Asks
    lines.append("## Asks / Escalations\n\n")
    lines.append("_[Add any items needing manager attention, unblocking, or escalation]_\n\n")

    if manager:
        lines.append(f"_For: {manager}_\n\n")

    lines.append(base._freshness_footer())
    lines.append(
        "\n> **Draft** — Review, edit, and send via your preferred channel. "
        "Do not distribute without review.\n"
    )

    return "".join(lines)


def generate_escalation_memo(base: "NarrativeEngineBase", context: str) -> str:
    """
    Generate an escalation note with options framing (§7.10, Phase 3).

    Structure:
      - Situation (context + active blockers from work-projects)
      - Impact if not resolved (delivery risk)
      - Options (structured A / B / Recommended)
      - What I need (specific ask)
      - Timeline
    """
    proj_body = base._body("work-projects")
    people_body = base._body("work-people")
    perf_body = base._body("work-performance")
    today = datetime.now(timezone.utc)
    role = base.profile.get("profile", {}).get("role", "")

    # ── Extract blocked items from work-projects ───────────────────────
    blocked_items: list[str] = []
    for line in proj_body.split("\n"):
        if "blocked" in line.lower() or "at risk" in line.lower():
            if line.strip().startswith("|") or line.strip().startswith("- "):
                blocked_items.append(line.strip()[:100])
    blocked_items = blocked_items[:5]

    # ── Extract key stakeholders ───────────────────────────────────────
    stakeholders: list[str] = []
    in_mgr = False
    for line in people_body.split("\n"):
        if "## Manager Chain" in line:
            in_mgr = True
            continue
        if in_mgr and line.startswith("## "):
            break
        if in_mgr and line.strip().startswith("- "):
            stakeholders.append(line.strip()[2:].split("—")[0].strip())
    stakeholders = stakeholders[:3]

    lines = []
    lines.append(f"# Escalation: {context[:80]}\n\n")
    lines.append(f"_From: {role or '_[author]_'} | {today.strftime('%Y-%m-%d')}_\n\n")
    lines.append("> **DRAFT** — Review before escalating.\n\n")
    lines.append("---\n\n")

    lines.append("## Situation\n\n")
    lines.append(f"**Context:** {context}\n\n")
    if blocked_items:
        lines.append("**Active blockers from work state:**\n\n")
        for b in blocked_items:
            lines.append(f"- {b}\n")
        lines.append("\n")
    else:
        lines.append("_[Describe the specific situation requiring escalation]_\n\n")

    lines.append("## Impact If Not Resolved\n\n")
    lines.append("_[Fill in]: Delivery impact, timeline slip, dependency chain effect, stakeholder commitments at risk_\n\n")
    lines.append("| Risk | Severity | Timeline |\n")
    lines.append("| --- | --- | --- |\n")
    lines.append("| _[Delivery milestone]_ | High | _[date]_ |\n")
    lines.append("| _[Dependency]_ | Medium | _[date]_ |\n\n")

    lines.append("## Options\n\n")
    lines.append("### Option A — Do Nothing\n")
    lines.append("- _[Consequence of inaction]_\n\n")
    lines.append("### Option B — _[Alternative approach]_\n")
    lines.append("- _[What this involves, cost, timeline, risk]_\n\n")
    lines.append("### ✅ Recommended: _[Describe recommended path]_\n")
    lines.append("- _[Why this is optimal given constraints]_\n\n")

    lines.append("## What I Need\n\n")
    if stakeholders:
        for s in stakeholders:
            lines.append(f"- **{s}**: _[specific ask]_\n")
    else:
        lines.append("- _[Name, role]_: _[specific ask — decision, resource, unblock]_\n")
    lines.append("\n")

    lines.append("## Timeline\n\n")
    lines.append("| Milestone | Date | Status |\n")
    lines.append("| --- | --- | --- |\n")
    lines.append("| _[Blocking deadline]_ | _[date]_ | At risk |\n")
    lines.append("| _[Downstream dependency]_ | _[date]_ | Pending |\n\n")

    lines.append(base._freshness_footer())
    lines.append("> **DRAFT** — Escalation notes require careful human review before sending.\n")
    return "".join(lines)


def generate_decision_memo(base: "NarrativeEngineBase", decision_id: str = "") -> str:
    """
    Generate a decision memo from work-decisions.md (§7.10, Phase 3).

    If decision_id provided, looks up that specific D-NNN entry.
    Otherwise generates a template for a new decision.

    Structure:
      - Decision (what was decided)
      - Context (background)
      - Alternatives considered
      - Evidence / rationale
      - Next steps
      - Distribution
    """
    dec_body = base._body("work-decisions")
    people_body = base._body("work-people")
    today = datetime.now(timezone.utc)
    role = base.profile.get("profile", {}).get("role", "")

    # ── Find the specific decision if ID given ────────────────────────
    found_decision: dict[str, str] = {}
    if decision_id:
        _did_upper = decision_id.upper()
        for line in dec_body.split("\n"):
            if _did_upper in line.upper() and line.startswith("|"):
                cols = [c.strip() for c in line.split("|") if c.strip()]
                if len(cols) >= 3:
                    found_decision = {
                        "id": cols[0] if cols else decision_id,
                        "date": cols[1] if len(cols) > 1 else today.strftime("%Y-%m-%d"),
                        "summary": cols[2] if len(cols) > 2 else "",
                        "owner": cols[3] if len(cols) > 3 else "",
                        "rationale": cols[4] if len(cols) > 4 else "",
                    }
                    break

    # ── Extract stakeholders ───────────────────────────────────────────
    stakeholders: list[str] = []
    in_mgr = False
    for line in people_body.split("\n"):
        if "## Manager Chain" in line:
            in_mgr = True
            continue
        if in_mgr and line.startswith("## "):
            break
        if in_mgr and line.strip().startswith("- "):
            stakeholders.append(line.strip()[2:].split("—")[0].strip())
    stakeholders = stakeholders[:4]

    title = found_decision.get("summary", decision_id or "_[Decision Title]_")
    did = found_decision.get("id", decision_id or "_[D-NNN]_")
    dec_date = found_decision.get("date", today.strftime("%Y-%m-%d"))
    owner = found_decision.get("owner", role or "_[owner]_")
    rationale = found_decision.get("rationale", "")

    lines = []
    lines.append(f"# Decision Memo: {title[:80]}\n\n")
    lines.append(f"_ID: {did} | Owner: {owner} | Date: {dec_date}_\n\n")
    lines.append("> **DRAFT** — Review and distribute to relevant stakeholders.\n\n")
    lines.append("---\n\n")

    lines.append("## Decision\n\n")
    if found_decision:
        lines.append(f"**{found_decision.get('summary', '')}**\n\n")
    else:
        lines.append("_[State the decision clearly in one sentence]_\n\n")

    lines.append("## Context\n\n")
    if rationale:
        lines.append(f"_{rationale}_\n\n")
    lines.append("_[Add: why this decision was needed, what problem it solves, what constraints applied]_\n\n")

    lines.append("## Alternatives Considered\n\n")
    lines.append("| Alternative | Pros | Cons | Why Rejected |\n")
    lines.append("| --- | --- | --- | --- |\n")
    lines.append("| _[Option A]_ | _[pros]_ | _[cons]_ | _[reason]_ |\n")
    lines.append("| _[Option B]_ | _[pros]_ | _[cons]_ | _[reason]_ |\n")
    lines.append("| ✅ Chosen approach | _[pros]_ | _[cons]_ | — |\n\n")

    lines.append("## Evidence and Rationale\n\n")
    lines.append("_[Add: data, analysis, prior decisions, stakeholder input that supported this]_\n\n")
    lines.append("| Data Point | Source | Implication |\n")
    lines.append("| --- | --- | --- |\n")
    lines.append("| _[metric or finding]_ | _[source]_ | _[what it means]_ |\n\n")

    lines.append("## Next Steps\n\n")
    lines.append("| Action | Owner | Due |\n")
    lines.append("| --- | --- | --- |\n")
    lines.append("| _[Communicate decision]_ | _[owner]_ | _[date]_ |\n")
    lines.append("| _[Begin implementation]_ | _[owner]_ | _[date]_ |\n")
    lines.append("| _[Track outcome]_ | _[owner]_ | _[date]_ |\n\n")

    lines.append("## Distribution\n\n")
    if stakeholders:
        for s in stakeholders:
            lines.append(f"- {s} (for awareness)\n")
    else:
        lines.append("_[List who should receive this memo]_\n")
    lines.append("\n")

    lines.append(base._freshness_footer())
    lines.append("> **DRAFT** — Decision memos require review by the decision owner before distribution.\n")
    return "".join(lines)
