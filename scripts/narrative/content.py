"""
scripts/narrative/content.py — Newsletter and deck content templates.

Standalone functions (accept NarrativeEngineBase as ``base``):
  generate_newsletter(base, period=None)
  generate_deck(base, topic="")
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional, TYPE_CHECKING

from work.helpers import _extract_section  # noqa: E402

if TYPE_CHECKING:
    from narrative._base import NarrativeEngineBase


def generate_newsletter(base: "NarrativeEngineBase", period: Optional[str] = None) -> str:
    """
    Generate a team newsletter draft from sprint + decision + career data.

    Supports template customization via user_profile.yaml work.newsletter:
      sections: list of section keys (default order shown below)
          highlights | decisions | accomplishments | blockers | next_steps
      tone: standard | formal | concise  (default: standard)
      template: standard | leadership | team_morale  (default: standard)

    Phase 2: core templates. Phase 3: template customization (§7.8).
    DRAFT only — never auto-sent.
    """
    today = datetime.now(timezone.utc)
    if not period:
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        period = (
            f"{week_start.strftime('%B')} {week_start.day}–"
            f"{week_end.day}, {week_start.year}"
        )

    # ── Template customization (Phase 3 item 13, §7.8) ────────────────────
    nl_config = base.profile.get("work", {}).get("newsletter", {})
    tone = nl_config.get("tone", "standard").lower()
    template_type = nl_config.get("template", "standard").lower()
    # Section ordering: user can configure which sections to include and order
    _default_sections = [
        "highlights", "program_metrics", "decisions", "accomplishments", "blockers", "next_steps"
    ]
    _leadership_sections = [
        "executive_summary", "program_metrics", "highlights", "decisions", "blockers", "asks"
    ]
    _team_morale_sections = ["highlights", "accomplishments", "shoutouts", "next_steps"]
    if nl_config.get("sections"):
        section_order = [s.lower() for s in nl_config["sections"]]
    elif template_type == "leadership":
        section_order = _leadership_sections
    elif template_type == "team_morale":
        section_order = _team_morale_sections
    else:
        section_order = _default_sections

    proj_fm = base._fm("work-projects")
    proj_body = base._body("work-projects")
    career_body = base._body("work-career")
    decisions_body = base._body("work-decisions")

    completed = proj_fm.get("completed_recent_count", 0)
    active = proj_fm.get("active_count", 0)
    blocked = proj_fm.get("blocked_count", 0)

    completed_section = _extract_section(proj_body, "Recently Completed")
    blocked_section = _extract_section(proj_body, "Blocked")

    # Recent decisions
    recent_decisions = []
    for line in decisions_body.split("\n"):
        if line.startswith("|") and "D-" in line and "Date" not in line and "---" not in line:
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if len(cols) >= 3:
                recent_decisions.append(f"[{cols[0]}] {cols[2]}" if len(cols) > 2 else cols[0])
    recent_decisions = recent_decisions[:5]

    # Career highlights (accomplishments)
    highlights = []
    for line in career_body.split("\n"):
        if line.strip().startswith("- ") and any(
            kw in line.lower()
            for kw in ["shipped", "launched", "completed", "delivered", "fixed", "improved"]
        ):
            highlights.append(line.strip("- ").strip())
    highlights = highlights[:4]

    role = base.profile.get("profile", {}).get("role", "")
    team = base.profile.get("profile", {}).get("team", "the team")

    # Header label adjusted by tone
    if tone == "concise":
        header = f"# {period} Update\n\n"
    elif template_type == "leadership":
        header = f"# Program Update — {period}\n\n"
    else:
        header = f"# Team Newsletter — {period}\n\n"

    lines = []
    lines.append(header)
    if role or team:
        lines.append(f"_From: {role} | {team}_\n\n")
    lines.append("---\n\n")

    # ── Section builder closures ───────────────────────────────────────────
    def _section_highlights() -> str:
        s = "## Highlights\n\n"
        if completed_section:
            s += completed_section + "\n\n"
        elif highlights:
            s += "".join(f"- {h}\n" for h in highlights) + "\n"
        elif completed:
            s += f"- {completed} work items completed this period.\n\n"
        else:
            s += "- _[Add: shipped features, closed PRs, velocity trends from this period]_\n\n"
        return s

    def _section_decisions() -> str:
        s = "## Key Decisions\n\n"
        if recent_decisions:
            s += "".join(f"- {d}\n" for d in recent_decisions) + "\n"
        else:
            s += "- _[Add: decisions made this period and brief rationale]_\n\n"
        return s

    def _section_accomplishments() -> str:
        s = "## Accomplishments\n\n"
        if highlights:
            s += "".join(f"- {h}\n" for h in highlights[:3]) + "\n"
        else:
            s += "- _[Add: team recognition, achievements, milestones reached]_\n\n"
        return s

    def _section_blockers() -> str:
        label = "## Risks & Blockers\n\n" if tone != "concise" else "## Blockers\n\n"
        s = label
        if blocked:
            s += (blocked_section + "\n\n") if blocked_section else \
                f"- **{blocked} item(s) currently blocked** — see /work sprint for details.\n\n"
        else:
            s += "- _[Add: current blockers, risk items, dependency concerns]_\n\n"
        return s

    def _section_next_steps() -> str:
        s = "## Next Steps\n\n"
        if active:
            s += f"- {active} active items carrying forward.\n"
        s += "- _[Add: upcoming milestones, planned releases, key dates]_\n\n"
        return s

    def _section_executive_summary() -> str:
        s = "## Executive Summary\n\n"
        pm = base._load_program_metrics()
        summary_parts = []
        if pm["risk_posture"]:
            sig = pm["signal_summary"]
            summary_parts.append(
                f"Program risk: **{pm['risk_posture']}** "
                f"(🔴{sig['red']} 🟡{sig['yellow']} 🟢{sig['green']})"
            )
        if completed:
            summary_parts.append(f"{completed} items completed this period")
        if blocked:
            summary_parts.append(f"{blocked} blocked")
        if active:
            summary_parts.append(f"{active} active")
        if summary_parts:
            s += f"{'. '.join(summary_parts)}. "
        s += "_[Add: 2-3 sentence executive summary highlighting business impact.]_\n\n"
        return s

    def _section_shoutouts() -> str:
        s = "## Shoutouts\n\n"
        s += "- _[Add: team member recognition and contributions this period]_\n\n"
        return s

    def _section_asks() -> str:
        s = "## Asks\n\n"
        s += "- _[Add: leadership support needed — decisions required, unblocking needed]_\n\n"
        return s

    def _section_program_metrics() -> str:
        pm = base._load_program_metrics()
        sig = pm["signal_summary"]
        if not sig["red"] and not sig["yellow"] and not sig["green"]:
            return ""  # no program data — skip section silently

        s = "## Program Health\n\n"
        posture = pm["risk_posture"] or "Unknown"
        s += f"**Risk Posture: {posture}** — "
        s += f"🔴 {sig['red']} · 🟡 {sig['yellow']} · 🟢 {sig['green']}\n\n"
        if pm["risk_rationale"]:
            s += f"> {pm['risk_rationale']}\n\n"

        # Per-workstream one-liner
        for ws in pm["workstreams"]:
            sigs = ws["signals"]
            total_signals = sigs["red"] + sigs["yellow"] + sigs["green"]
            if total_signals == 0:
                continue
            tag = "🔴" if sigs["red"] else ("🟡" if sigs["yellow"] else "🟢")
            top = ws.get("top_metric", "")
            line = f"- **{ws['id']} {ws['name']}** {tag}"
            if top:
                line += f" — {top}"
            s += line + "\n"

        # Highlight red metrics
        reds = pm["key_metrics"][:5]
        if reds:
            s += "\n**Top Risks (Red Metrics):**\n"
            for km in reds:
                s += f"- {km['id']} {km['name']}: {km['value']}\n"
            s += "\n"

        return s

    _section_map = {
        "highlights": _section_highlights,
        "decisions": _section_decisions,
        "accomplishments": _section_accomplishments,
        "blockers": _section_blockers,
        "next_steps": _section_next_steps,
        "executive_summary": _section_executive_summary,
        "shoutouts": _section_shoutouts,
        "asks": _section_asks,
        "program_metrics": _section_program_metrics,
    }

    for section_key in section_order:
        fn = _section_map.get(section_key)
        if fn:
            lines.append(fn())

    lines.append(base._freshness_footer())

    if tone == "concise":
        lines.append("\n> **DRAFT** — Review before distributing.\n")
    else:
        lines.append(
            "\n> **DRAFT** — Review, edit, and distribute via your team channel. "
            "Do not send without review.\n"
        )
    return "".join(lines)


def generate_deck(base: "NarrativeEngineBase", topic: str = "") -> str:
    """
    Generate structured LT deck content for a leadership presentation.

    Supports outline personalization via user_profile.yaml work.deck:
      template: standard | risk_review | program_status | exec_brief
      audience: leadership | team | exec  (affects depth and framing)

    Phase 2: core templates. Phase 3: outline personalization (§7.8).
    DRAFT only — not a PowerPoint generator.
    """
    topic_label = topic or "Leadership Update"
    topic_lower = topic.lower()
    today = datetime.now(timezone.utc)

    # ── Deck personalization (Phase 3 item 13, §7.8) ──────────────────────
    deck_config = base.profile.get("work", {}).get("deck", {})
    template_type = deck_config.get("template", "standard").lower()
    audience = deck_config.get("audience", "leadership").lower()

    # Audience-tailored framing labels
    _audience_labels = {
        "exec": ("Executive Summary", "Strategic Direction", "Business Impact"),
        "leadership": ("Executive Summary", "Status", "Risks"),
        "team": ("Overview", "What We Shipped", "Blockers"),
    }
    a_labels = _audience_labels.get(audience, _audience_labels["leadership"])

    # Template-specific section ordering
    _risk_review_sections = [
        "executive_summary", "status", "risks", "dependencies", "asks", "next_steps"
    ]
    _program_status_sections = [
        "executive_summary", "status", "metrics", "decisions", "risks", "next_steps"
    ]
    _exec_brief_sections = ["executive_summary", "key_results", "risks", "asks"]
    _standard_sections = ["executive_summary", "status", "metrics", "risks", "asks", "next_steps"]

    if template_type == "risk_review":
        section_order = _risk_review_sections
    elif template_type == "program_status":
        section_order = _program_status_sections
    elif template_type == "exec_brief":
        section_order = _exec_brief_sections
    else:
        section_order = _standard_sections

    proj_fm = base._fm("work-projects")
    proj_body = base._body("work-projects")
    sources_body = base._body("work-sources")
    career_body = base._body("work-career")
    perf_fm = base._fm("work-performance")
    decisions_body = base._body("work-decisions")

    active = proj_fm.get("active_count", 0)
    blocked = proj_fm.get("blocked_count", 0)
    completed = proj_fm.get("completed_recent_count", 0)
    dfs = proj_fm.get("delivery_feasibility_score")

    # Find relevant projects for the topic
    relevant_lines = []
    for line in proj_body.split("\n"):
        if topic_lower and topic_lower in line.lower() and line.strip():
            relevant_lines.append(line.strip("# ").strip())
        elif line.startswith("## ") and "active" not in line.lower():
            relevant_lines.append(line.strip("# ").strip())
    relevant_lines = relevant_lines[:4]

    # Find relevant data sources
    relevant_sources = []
    for line in sources_body.split("\n"):
        if (topic_lower in line.lower() or "velocity" in line.lower()) and "http" in line.lower():
            relevant_sources.append(line.strip("- ").strip())
    relevant_sources = relevant_sources[:3]

    # Recent decisions for deck context
    recent_decisions = []
    for line in decisions_body.split("\n"):
        if line.startswith("|") and "D-" in line and "Date" not in line and "---" not in line:
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if len(cols) >= 3:
                recent_decisions.append(f"[{cols[0]}] {cols[2]}")
    recent_decisions = recent_decisions[:3]

    blocked_section = _extract_section(proj_body, "Blocked")

    role = base.profile.get("profile", {}).get("role", "")
    team = base.profile.get("profile", {}).get("team", "")

    lines = []
    lines.append(f"# LT Deck Content — {topic_label}\n\n")
    lines.append(f"_Generated: {today.strftime('%Y-%m-%d')} | For: {role} @ {team}_\n\n")
    if template_type != "standard":
        lines.append(f"_Template: {template_type} | Audience: {audience}_\n\n")
    lines.append("---\n\n")

    # ── Section builder closures ───────────────────────────────────────────
    def _section_executive_summary() -> str:
        label = a_labels[0]
        s = f"## {label}\n\n"
        pm = base._load_program_metrics()
        if pm["risk_posture"]:
            sig = pm["signal_summary"]
            s += (
                f"**Program Risk: {pm['risk_posture']}** "
                f"(🔴{sig['red']} 🟡{sig['yellow']} 🟢{sig['green']}). "
            )
        if dfs is not None:
            dfs_label = "on track" if dfs >= 80 else ("at risk" if dfs >= 60 else "behind")
            s += (
                f"Delivery is **{dfs_label}** (DFS: {dfs}/100). "
                f"{active} items active, {completed} completed recently."
            )
            if blocked:
                s += f" **{blocked} item(s) blocked** — see Risks section."
            s += "\n\n"
        elif pm["risk_rationale"]:
            s += f"{pm['risk_rationale']}\n\n"
        else:
            s += "_[2-3 sentence overview: state, trajectory, and recommendation]_\n\n"
        return s

    def _section_status() -> str:
        s = f"## {a_labels[1]}\n\n"
        if relevant_lines:
            s += "".join(f"- {rl}\n" for rl in relevant_lines if rl.strip()) + "\n"
        else:
            s += "_[Project/milestone status with current data]_\n\n"
        return s

    def _section_metrics() -> str:
        s = "## Data & Metrics\n\n"
        pm = base._load_program_metrics()
        if pm["workstreams"]:
            for ws in pm["workstreams"]:
                sigs = ws["signals"]
                total = sigs["red"] + sigs["yellow"] + sigs["green"]
                if total == 0:
                    continue
                tag = "🔴" if sigs["red"] else ("🟡" if sigs["yellow"] else "🟢")
                top = ws.get("top_metric", "")
                ws_line = f"- **{ws['id']} {ws['name']}** {tag}"
                if top:
                    ws_line += f" — {top}"
                s += ws_line + "\n"
            s += "\n"
        if relevant_sources:
            s += "**Data Sources:**\n"
            s += "".join(f"- {src}\n" for src in relevant_sources) + "\n"
        if not pm["workstreams"] and not relevant_sources:
            s += "_[Dashboard links and metric trends — run `/work sources`]_\n\n"
        return s

    def _section_risks() -> str:
        s = f"## {a_labels[2]}\n\n"
        pm = base._load_program_metrics()
        reds = pm.get("key_metrics", [])[:5]
        if reds:
            for km in reds:
                s += f"- **{km['id']} {km['name']}**: {km['value']}\n"
            s += "\n"
        if blocked_section:
            s += blocked_section + "\n\n"
        elif blocked:
            s += f"- **{blocked} blocked item(s)** — details in work-projects.\n\n"
        elif not reds:
            s += "- _[Dependency health, blockers, mitigation plans]_\n\n"
        return s

    def _section_asks() -> str:
        s = "## Asks\n\n"
        s += "- _[What you need from leadership: unblocking, resources, decision]_\n\n"
        return s

    def _section_next_steps() -> str:
        s = "## Next Steps\n\n"
        if active:
            s += f"- {active} active items with delivery commitments.\n"
        s += "- _[Planned actions with timeline and owner]_\n\n"
        return s

    def _section_decisions() -> str:
        s = "## Key Decisions\n\n"
        if recent_decisions:
            s += "".join(f"- {d}\n" for d in recent_decisions) + "\n"
        else:
            s += "- _[Decisions made this period and their rationale]_\n\n"
        return s

    def _section_dependencies() -> str:
        s = "## Dependencies\n\n"
        s += "- _[Cross-team dependencies, external blockers, timeline risks]_\n\n"
        return s

    def _section_key_results() -> str:
        s = "## Key Results\n\n"
        if completed:
            s += f"- {completed} items completed this period.\n"
        s += "- _[Measurable outcomes tied to business goals]_\n\n"
        return s

    _section_map = {
        "executive_summary": _section_executive_summary,
        "status": _section_status,
        "metrics": _section_metrics,
        "risks": _section_risks,
        "asks": _section_asks,
        "next_steps": _section_next_steps,
        "decisions": _section_decisions,
        "dependencies": _section_dependencies,
        "key_results": _section_key_results,
    }

    for section_key in section_order:
        fn = _section_map.get(section_key)
        if fn:
            lines.append(fn())

    lines.append(base._freshness_footer())
    lines.append(
        "\n> **DRAFT** — Review, supplement with live data, and adapt for your audience "
        "before presenting.\n"
    )
    return "".join(lines)
