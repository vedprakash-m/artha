"""
scripts/narrative_engine.py — Narrative Engine for Work OS (§7.10).

Reads domain state files from state/work/ and generates structured
narrative drafts for human review. Never sends output — always returns
text for the user to review, edit, and distribute.

Supported templates (§7.10 table):
  weekly_memo     — /work memo --weekly  (Phase 1)
  talking_points  — /work talking-points <topic>  (Phase 3, basic)
  boundary_report — internal component for /work pulse
  newsletter      — /work newsletter [period]  (Phase 2)
  deck            — /work deck <topic>  (Phase 2)

Architecture:
  state/work/*.md → NarrativeEngine → draft text

CLI:
  python scripts/narrative_engine.py --template weekly_memo
  python scripts/narrative_engine.py --template talking_points --topic "ADO sprint"
  python scripts/narrative_engine.py --template boundary_report

Rules (§7.10):
  1. Never sends output — drafts only.
  2. Every output includes a data-freshness footer.
  3. Templates are configurable but shipped defaults work without editing.
  4. Reuses Canonical Work Objects — no separate data pipeline.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("artha.narrative_engine")

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_WORK_STATE_DIR = _REPO_ROOT / "state" / "work"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dt(dt_str: str) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _age_str(dt: Optional[datetime]) -> str:
    if not dt:
        return "unknown"
    delta = datetime.now(timezone.utc) - dt
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() / 60)}m ago"
    if hours < 24:
        return f"{int(hours)}h ago"
    return f"{int(hours / 24)}d ago"


def _read_frontmatter(path: Path) -> dict[str, Any]:
    """Read YAML frontmatter from a Markdown state file."""
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore[import]
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                return yaml.safe_load(text[3:end]) or {}
    except Exception:
        pass
    return {}


def _read_body(path: Path) -> str:
    """Read Markdown body (after frontmatter) from a state file."""
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                return text[end + 3:].strip()
        return text.strip()
    except Exception:
        return ""


def _extract_section(body: str, heading: str) -> str:
    """Extract content under a specific ## heading until next ## heading."""
    lines = body.split("\n")
    capture = False
    result = []
    target = heading.strip("# ").lower()
    for line in lines:
        if line.startswith("## "):
            section_name = line.strip("# ").lower()
            if target in section_name:
                capture = True
                continue
            elif capture:
                break  # next section found
        elif capture:
            result.append(line)
    return "\n".join(result).strip()


def _load_profile() -> dict[str, Any]:
    """Load user profile work section."""
    try:
        import yaml  # type: ignore[import]
        path = _REPO_ROOT / "config" / "user_profile.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data.get("work", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# NarrativeEngine
# ---------------------------------------------------------------------------

class NarrativeEngine:
    """
    Template-based narrative generator for Work OS.

    All outputs are Markdown drafts for human review.
    No LLM calls — pure text synthesis from structured state.
    """

    def __init__(
        self,
        state_dir: Optional[Path] = None,
        profile: Optional[dict[str, Any]] = None,
    ) -> None:
        self.state_dir = state_dir or _WORK_STATE_DIR
        self.profile = profile or _load_profile()
        self._cache: dict[str, Any] = {}

    def _fm(self, domain: str) -> dict[str, Any]:
        """Cached frontmatter read for a domain."""
        if domain not in self._cache:
            self._cache[domain] = _read_frontmatter(self.state_dir / f"{domain}.md")
        return self._cache[domain]

    def _body(self, domain: str) -> str:
        """Cached body read for a domain."""
        key = f"_body_{domain}"
        if key not in self._cache:
            self._cache[key] = _read_body(self.state_dir / f"{domain}.md")
        return self._cache[key]

    def _freshness_footer(self) -> str:
        """Build §3.8 data freshness footer from state file timestamps."""
        domains_checked = ["work-calendar", "work-comms", "work-projects", "work-performance"]
        ages = []
        for d in domains_checked:
            fm = self._fm(d)
            last_updated = fm.get("last_updated")
            if last_updated:
                dt = _parse_dt(str(last_updated))
                ages.append((d.split("-")[1], _age_str(dt)))

        if ages:
            age_parts = " | ".join(f"{name}: {age}" for name, age in ages[:4])
            return f"\n---\n_Data freshness: {age_parts}_\n"
        return "\n---\n_Data freshness: unknown — run `/work refresh`_\n"

    # -----------------------------------------------------------------------
    # Template: Weekly Status Memo (Phase 1, §7.10)
    # -----------------------------------------------------------------------

    def generate_weekly_memo(self, period: Optional[str] = None) -> str:
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
        cal_fm = self._fm("work-calendar")
        proj_fm = self._fm("work-projects")
        comms_fm = self._fm("work-comms")
        boundary_fm = self._fm("work-boundary")
        perf_fm = self._fm("work-performance")

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
        goals = self.profile.get("profile", {}).get("goals", [])

        # Extract body sections
        proj_body = self._body("work-projects")
        completed_section = _extract_section(proj_body, "Recently Completed")
        blocked_section = _extract_section(proj_body, "Blocked Items")

        role = self.profile.get("profile", {}).get("role", "")
        team = self.profile.get("profile", {}).get("team", "")
        manager = self.profile.get("profile", {}).get("manager", "")

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

        lines.append(self._freshness_footer())
        lines.append(
            "\n> **Draft** — Review, edit, and send via your preferred channel. "
            "Do not distribute without review.\n"
        )

        return "".join(lines)

    # -----------------------------------------------------------------------
    # Template: Talking Points (Phase 3 preview, §7.10)
    # -----------------------------------------------------------------------

    def generate_talking_points(self, topic: str) -> str:
        """
        Generate concise talking points for a meeting or topic.

        Searches work-projects, work-decisions, and work-people for
        content related to the topic. Returns a structured talking-
        points format suitable for meeting preparation.
        """
        topic_lower = topic.lower()

        # Search for relevant content across state files
        relevant_projects = []
        proj_body = self._body("work-projects")
        for line in proj_body.split("\n"):
            if topic_lower in line.lower() and line.strip():
                relevant_projects.append(line.strip("- ").strip())

        decisions_body = self._body("work-decisions")
        relevant_decisions = []
        for line in decisions_body.split("\n"):
            if topic_lower in line.lower() and line.strip() and not line.startswith("#"):
                relevant_decisions.append(line.strip("- ").strip())

        people_body = self._body("work-people")
        relevant_stakeholders = []
        for line in people_body.split("\n"):
            if topic_lower in line.lower() and "stakeholder" not in line.lower() and line.strip():
                relevant_stakeholders.append(line.strip("- ").strip())

        # Connect goals
        goals = self.profile.get("profile", {}).get("goals", [])
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

        lines.append(self._freshness_footer())
        lines.append("\n> **Draft** — Review before the meeting.\n")

        return "".join(lines)

    # -----------------------------------------------------------------------
    # Template: Boundary Report (internal, used by /work pulse)
    # -----------------------------------------------------------------------

    def generate_boundary_report(self) -> str:
        """Generate a concise boundary health summary for /work pulse."""
        boundary_fm = self._fm("work-boundary")
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

    # -----------------------------------------------------------------------
    # Template: Connect Prep Summary (used by /work connect-prep)
    # -----------------------------------------------------------------------

    def generate_connect_summary(self) -> str:
        """Generate a Connect cycle preparation summary with auto-evidence matching.

        Reads Connect goals from work-performance.md, auto-matches milestones
        from work-project-journeys.md by keyword, and surfaces gap analysis
        (Phase 3 item 8, §7.6).
        """
        perf_fm = self._fm("work-performance")
        perf_body = self._body("work-performance")
        journeys_body = self._body("work-project-journeys")
        proj_fm = self._fm("work-projects")
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
            for pg in self.profile.get("profile", {}).get("goals", []):
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

        lines.append("## Manager 1:1 Pivot Log\n\n")
        pivot_section = _extract_section(perf_body, "Manager 1:1 Pivot Log")
        if pivot_section and len(pivot_section) > 30:
            lines.append(pivot_section + "\n\n")
        else:
            lines.append("_Not yet populated. Use `/work notes` after each 1:1 to log pivots._\n\n")

        lines.append(self._freshness_footer())
        return "".join(lines)

    # -----------------------------------------------------------------------
    # Template: Team Newsletter Draft (Phase 2, §7.8)
    # -----------------------------------------------------------------------

    def generate_newsletter(self, period: Optional[str] = None) -> str:
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

        # ── Template customization (Phase 3 item 13, §7.8) ────────────────
        nl_config = self.profile.get("work", {}).get("newsletter", {})
        tone = nl_config.get("tone", "standard").lower()
        template_type = nl_config.get("template", "standard").lower()
        # Section ordering: user can configure which sections to include and order
        _default_sections = ["highlights", "decisions", "accomplishments", "blockers", "next_steps"]
        _leadership_sections = ["executive_summary", "highlights", "decisions", "blockers", "asks"]
        _team_morale_sections = ["highlights", "accomplishments", "shoutouts", "next_steps"]
        if nl_config.get("sections"):
            section_order = [s.lower() for s in nl_config["sections"]]
        elif template_type == "leadership":
            section_order = _leadership_sections
        elif template_type == "team_morale":
            section_order = _team_morale_sections
        else:
            section_order = _default_sections

        proj_fm = self._fm("work-projects")
        proj_body = self._body("work-projects")
        career_body = self._body("work-career")
        decisions_body = self._body("work-decisions")
        perf_fm = self._fm("work-performance")

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
                kw in line.lower() for kw in ["shipped", "launched", "completed", "delivered", "fixed", "improved"]
            ):
                highlights.append(line.strip("- ").strip())
        highlights = highlights[:4]

        role = self.profile.get("profile", {}).get("role", "")
        team = self.profile.get("profile", {}).get("team", "the team")

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

        # Section builder map
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
            summary_parts = []
            if completed:
                summary_parts.append(f"{completed} items completed this period")
            if blocked:
                summary_parts.append(f"{blocked} blocked")
            if active:
                summary_parts.append(f"{active} active")
            if summary_parts:
                s += f"This period: {', '.join(summary_parts)}. "
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

        _section_map = {
            "highlights": _section_highlights,
            "decisions": _section_decisions,
            "accomplishments": _section_accomplishments,
            "blockers": _section_blockers,
            "next_steps": _section_next_steps,
            "executive_summary": _section_executive_summary,
            "shoutouts": _section_shoutouts,
            "asks": _section_asks,
        }

        for section_key in section_order:
            fn = _section_map.get(section_key)
            if fn:
                lines.append(fn())

        lines.append(self._freshness_footer())

        if tone == "concise":
            lines.append("\n> **DRAFT** — Review before distributing.\n")
        else:
            lines.append(
                "\n> **DRAFT** — Review, edit, and distribute via your team channel. "
                "Do not send without review.\n"
            )
        return "".join(lines)

    # -----------------------------------------------------------------------
    # Template: LT Deck Content Assembly (Phase 2, §7.8)
    # -----------------------------------------------------------------------

    def generate_deck(self, topic: str = "") -> str:
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

        # ── Deck personalization (Phase 3 item 13, §7.8) ──────────────────
        deck_config = self.profile.get("work", {}).get("deck", {})
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
        _risk_review_sections = ["executive_summary", "status", "risks", "dependencies", "asks", "next_steps"]
        _program_status_sections = ["executive_summary", "status", "metrics", "decisions", "risks", "next_steps"]
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

        proj_fm = self._fm("work-projects")
        proj_body = self._body("work-projects")
        sources_body = self._body("work-sources")
        career_body = self._body("work-career")
        perf_fm = self._fm("work-performance")
        decisions_body = self._body("work-decisions")

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

        role = self.profile.get("profile", {}).get("role", "")
        team = self.profile.get("profile", {}).get("team", "")

        lines = []
        lines.append(f"# LT Deck Content — {topic_label}\n\n")
        lines.append(f"_Generated: {today.strftime('%Y-%m-%d')} | For: {role} @ {team}_\n\n")
        if template_type != "standard":
            lines.append(f"_Template: {template_type} | Audience: {audience}_\n\n")
        lines.append("---\n\n")

        def _section_executive_summary() -> str:
            label = a_labels[0]
            s = f"## {label}\n\n"
            if dfs is not None:
                dfs_label = "on track" if dfs >= 80 else ("at risk" if dfs >= 60 else "behind")
                s += (
                    f"Delivery is **{dfs_label}** (DFS: {dfs}/100). "
                    f"{active} items active, {completed} completed recently."
                )
                if blocked:
                    s += f" **{blocked} item(s) blocked** — see Risks section."
                s += "\n\n"
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
            if relevant_sources:
                s += "".join(f"- {src}\n" for src in relevant_sources) + "\n"
            else:
                s += "_[Dashboard links and metric trends — run `/work sources`]_\n\n"
            return s

        def _section_risks() -> str:
            s = f"## {a_labels[2]}\n\n"
            if blocked_section:
                s += blocked_section + "\n\n"
            elif blocked:
                s += f"- **{blocked} blocked item(s)** — details in work-projects.\n\n"
            else:
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

        lines.append(self._freshness_footer())
        lines.append(
            "\n> **DRAFT** — Review, supplement with live data, and adapt for your audience "
            "before presenting.\n"
        )
        return "".join(lines)

    # -----------------------------------------------------------------------
    # Template: Promotion OS (Phase 3, §7.11)
    # -----------------------------------------------------------------------

    def generate_promo_case(self, narrative: bool = False) -> str:
        """
        Generate promotion readiness assessment or full narrative draft (§7.11).

        narrative=False: Readiness assessment with scope arc, evidence density,
          visibility events, and gap list.
        narrative=True: Full promotion narrative Markdown written to
          work-promo-narrative.md — thesis, before/after, scope arc,
          milestone evidence, manager voice, visibility events, readiness signal.

        Always returns the generated text draft. Caller handles file writing.
        """
        journeys_fm = self._fm("work-project-journeys")
        journeys_body = self._body("work-project-journeys")
        perf_fm = self._fm("work-performance")
        perf_body = self._body("work-performance")
        people_body = self._body("work-people")
        career_body = self._body("work-career")

        today = datetime.now(timezone.utc)
        role = self.profile.get("profile", {}).get("role", "")
        team = self.profile.get("profile", {}).get("team", "")
        projects_tracked = int(journeys_fm.get("projects_tracked", 0))

        # ── Scope arc: collect all timeline entries ────────────────────────
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

        # ── Scope trajectory signal ────────────────────────────────────────
        trajectory = "stable"
        scope_events_90d = 0
        try:
            cutoff_90 = today - timedelta(days=90)
            _MONTH_MAP = {m: i for i, m in enumerate(
                ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], 1
            )}
            for e in scope_entries:
                raw_date = e["date"]
                for month_abbr, mnum in _MONTH_MAP.items():
                    if month_abbr in raw_date.lower():
                        year_parts = [p for p in raw_date.split() if p.isdigit() and len(p) == 4]
                        if year_parts:
                            try:
                                ev_dt = datetime(int(year_parts[0]), mnum, 1, tzinfo=timezone.utc)
                                if ev_dt >= cutoff_90:
                                    scope_events_90d += 1
                            except ValueError:
                                pass
                        break
                # Also handle YYYY-MM format
                if "-" in raw_date and len(raw_date) >= 7:
                    try:
                        ev_dt = datetime.fromisoformat(raw_date[:7] + "-01").replace(tzinfo=timezone.utc)
                        if ev_dt >= cutoff_90:
                            scope_events_90d += 1
                    except ValueError:
                        pass
            if scope_events_90d >= 2:
                trajectory = "expanding"
            elif projects_tracked >= 3:
                trajectory = "expanding"
        except Exception:
            pass

        # Deduplicate scope_events_90d (could be double-counted above)
        scope_events_90d = min(scope_events_90d, len(scope_entries))

        # ── Connect goals + evidence density ──────────────────────────────
        goals: list[tuple[str, str, int]] = []  # (name, status, evidence_count)
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
        vis_events = vis_events[:10]  # cap at 10 most recent

        # ── Manager voice ─────────────────────────────────────────────────
        manager_voice: list[str] = []
        for line in perf_body.split("\n"):
            if ('"' in line or "says" in line.lower() or "quote" in line.lower()
                    or "verbatim" in line.lower()) and len(line) > 20:
                manager_voice.append(line.strip("- ").strip())
        manager_voice = [v for v in manager_voice if len(v) > 10][:4]

        # ── Readiness signal ──────────────────────────────────────────────
        evidence_total = sum(e for _, _, e in goals)
        evidence_avg = evidence_total / len(goals) if goals else 0
        thin_goals = [g for g, _, e in goals if e < 3]
        if evidence_avg >= 5 and len(vis_events) >= 3 and trajectory == "expanding":
            readiness = "ready"
        elif evidence_avg >= 3 and len(vis_events) >= 1:
            readiness = "1-2 quarters away"
        else:
            readiness = "critical gaps blocking"

        lines = []

        if not narrative:
            # ── Readiness Assessment (brief) ─────────────────────────────
            lines.append(f"# Promotion Readiness Assessment\n\n")
            lines.append(f"_Generated: {today.strftime('%Y-%m-%d')} | {role} @ {team}_\n\n")
            lines.append("---\n\n")

            lines.append("## Scope Arc\n\n")
            if projects_tracked:
                lines.append(f"- **{projects_tracked} projects-tracked** in work-project-journeys\n")
            lines.append(f"- Trajectory: **{trajectory}**")
            if scope_events_90d:
                lines.append(f" ({scope_events_90d} milestones in last 90 days)")
            lines.append("\n")
            if scope_entries:
                # Show earliest and most recent milestone
                lines.append(f"- First milestone: {scope_entries[0]['date']} — {scope_entries[0]['milestone'][:60]}\n")
                lines.append(f"- Latest milestone: {scope_entries[-1]['date']} — {scope_entries[-1]['milestone'][:60]}\n")
            lines.append("\n")

            lines.append("## Evidence Density\n\n")
            if goals:
                for goal_name, status, ev_count in goals:
                    stars = "★" * min(ev_count, 5) + "☆" * max(0, 5 - ev_count)
                    gap = " ← **gap**" if ev_count < 3 else ""
                    short_name = goal_name[:50]
                    lines.append(f"- {short_name}: {stars} ({ev_count} evidence items){gap}\n")
            else:
                lines.append("- _No goals found — run `/work bootstrap` or populate work-performance.md_\n")
            lines.append("\n")

            lines.append("## Visibility Events\n\n")
            if vis_events:
                lines.append(f"- **{len(vis_events)} events recorded** in work-people.md\n")
                for ev in vis_events[:5]:
                    lines.append(f"  {ev}\n")
            else:
                lines.append("- _No visibility events captured yet — they are collected automatically on REFRESH_\n")
            lines.append("\n")

            lines.append("## Readiness Signal\n\n")
            signal_icon = "✅" if readiness == "ready" else ("⚠" if "quarters" in readiness else "🔴")
            lines.append(f"**{signal_icon} {readiness}**\n\n")
            if thin_goals:
                lines.append(f"Thin goals (< 3 evidence items): {', '.join(g[:40] for g in thin_goals[:3])}\n\n")
            if not manager_voice:
                lines.append("⚠ Manager voice not yet captured — populate via `/work notes` post-1:1.\n\n")

            lines.append("## Next Actions\n\n")
            lines.append("- Run `/work promo-case --narrative` to generate the full promotion document\n")
            lines.append("- Run `/work connect-prep --calibration` to generate the calibration defense brief\n")
            if thin_goals:
                lines.append(f"- Add evidence to thin goals: {', '.join(g[:30] for g in thin_goals[:2])}\n")
            lines.append("\n")

        else:
            # ── Full Narrative (§7.11 /work promo-case --narrative) ───────
            lines.append(f"# Promotion Narrative\n\n")
            lines.append(f"_Generated: {today.strftime('%Y-%m-%d')} | {role} @ {team}_\n\n")
            lines.append("> **DRAFT** — Review, edit, and validate with your manager before using.\n\n")
            lines.append("---\n\n")

            lines.append("## Thesis\n\n")
            if scope_entries and trajectory == "expanding":
                first_project = scope_entries[0]["project"] if scope_entries else "the program"
                latest = scope_entries[-1]["milestone"][:80] if scope_entries else ""
                lines.append(
                    f"_[Auto-draft — edit before using]_ "
                    f"{role or 'Candidate'} drove scope expansion from {first_project} "
                    f"to {projects_tracked} workstreams over {len(scope_entries)} milestones, "
                    f"delivering measurable P0/P1 outcomes and demonstrating principal-level "
                    f"ownership. Most recent: {latest}.\n\n"
                )
            else:
                lines.append("_[Insert: one paragraph — what the case is, at what level, why evidence supports it]_\n\n")

            lines.append("## Before / After\n\n")
            lines.append("_[Insert: domain state before joining vs. current state — the transformation story]_\n\n")
            lines.append("| Dimension | Before | After |\n")
            lines.append("| --- | --- | --- |\n")
            lines.append("| Scope | _[area]_ | _[expanded scope]_ |\n")
            lines.append("| Org impact | _[limited]_ | _[cross-team]_ |\n")
            lines.append("| Process maturity | _[none]_ | _[formalized]_ |\n\n")

            lines.append("## Scope Expansion Arc\n\n")
            if scope_entries:
                lines.append("| Date | Milestone | Evidence | Impact |\n")
                lines.append("| --- | --- | --- | --- |\n")
                for e in scope_entries[-15:]:  # most recent 15
                    lines.append(f"| {e['date']} | {e['milestone'][:60]} | {e['evidence'][:40]} | {e['impact'][:40]} |\n")
            else:
                lines.append("_[Populate work-project-journeys.md to auto-generate this section]_\n")
            lines.append("\n")

            lines.append("## Connect Goals — Evidence Summary\n\n")
            if goals:
                for goal_name, status, ev_count in goals:
                    stars = "★" * min(ev_count, 5) + "☆" * max(0, 5 - ev_count)
                    lines.append(f"### {goal_name}\n\n")
                    lines.append(f"- Status: {status or '_[fill in]_'}\n")
                    lines.append(f"- Evidence density: {stars} ({ev_count} items)\n")
                    lines.append(f"- _[Add: specific milestone references, artifact links, delivery dates]_\n\n")
            else:
                lines.append("_[Populate work-performance.md Connect Goals section]_\n\n")

            lines.append("## Manager and Peer Voice\n\n")
            if manager_voice:
                for mv in manager_voice:
                    lines.append(f"- {mv}\n")
            else:
                lines.append("_[Add: verbatim quotes from 1:1 pivots, Connect submissions, peer feedback]_\n")
            lines.append("\n")

            lines.append("## Visibility Events\n\n")
            if vis_events:
                lines.append("| Date | Stakeholder | Type | Context | Source |\n")
                lines.append("| --- | --- | --- | --- | --- |\n")
                for ev in vis_events:
                    lines.append(f"{ev}\n")
            else:
                lines.append("_[Visibility events are captured automatically on each /work refresh — run it to populate]_\n")
            lines.append("\n")

            lines.append("## Evidence Gaps\n\n")
            if thin_goals:
                for tg in thin_goals:
                    lines.append(f"- **{tg[:50]}** — fewer than 3 evidence items captured. Recommended: log milestone artifacts via `/work notes`.\n")
            if not vis_events:
                lines.append("- **Visibility events** — not yet captured. Run `/work refresh` to begin collection.\n")
            if not manager_voice:
                lines.append("- **Manager voice** — no verbatim signals captured yet. Log via `/work notes` after each 1:1.\n")
            lines.append("\n")

            lines.append("## Readiness Signal\n\n")
            signal_icon = "✅" if readiness == "ready" else ("⚠" if "quarters" in readiness else "🔴")
            lines.append(f"**{signal_icon} {readiness}**\n\n")
            lines.append("_[Validate this signal with your manager and by cross-checking the evidence gap list above]_\n\n")

        lines.append(self._freshness_footer())
        lines.append(
            "\n> **DRAFT** — Review, edit, and validate before using in any formal process.\n"
        )
        return "".join(lines)

    # -----------------------------------------------------------------------
    # Template: Calibration Defense Brief (Phase 3, §7.6)
    # -----------------------------------------------------------------------

    def generate_calibration_brief(self) -> str:
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
        journeys_fm = self._fm("work-project-journeys")
        journeys_body = self._body("work-project-journeys")
        perf_body = self._body("work-performance")
        people_body = self._body("work-people")
        career_body = self._body("work-career")

        today = datetime.now(timezone.utc)
        role = self.profile.get("profile", {}).get("role", "Candidate")
        team = self.profile.get("profile", {}).get("team", "")
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

        lines.append(self._freshness_footer())
        lines.append("\n> **DRAFT** — Manager: review and tailor before the calibration session.\n")
        return "".join(lines)

    # -----------------------------------------------------------------------
    # Template: Escalation Memo (Phase 3, §7.10)
    # -----------------------------------------------------------------------

    def generate_escalation_memo(self, context: str) -> str:
        """
        Generate an escalation note with options framing (§7.10, Phase 3).

        Structure:
          - Situation (context + active blockers from work-projects)
          - Impact if not resolved (delivery risk)
          - Options (structured A / B / Recommended)
          - What I need (specific ask)
          - Timeline
        """
        proj_body = self._body("work-projects")
        people_body = self._body("work-people")
        perf_body = self._body("work-performance")
        today = datetime.now(timezone.utc)
        role = self.profile.get("profile", {}).get("role", "")

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

        lines.append(self._freshness_footer())
        lines.append("> **DRAFT** — Escalation notes require careful human review before sending.\n")
        return "".join(lines)

    # -----------------------------------------------------------------------
    # Template: Decision Memo (Phase 3, §7.10)
    # -----------------------------------------------------------------------

    def generate_decision_memo(self, decision_id: str = "") -> str:
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
        dec_body = self._body("work-decisions")
        people_body = self._body("work-people")
        today = datetime.now(timezone.utc)
        role = self.profile.get("profile", {}).get("role", "")

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

        lines.append(self._freshness_footer())
        lines.append("> **DRAFT** — Decision memos require review by the decision owner before distribution.\n")
        return "".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Artha Work OS Narrative Engine — generate draft narratives from state files"
    )
    parser.add_argument(
        "--template",
        choices=["weekly_memo", "talking_points", "boundary_report", "connect_summary",
                 "newsletter", "deck", "promo_case", "promo_narrative",
                 "calibration_brief", "escalation_memo", "decision_memo"],
        required=True,
        help="Narrative template to generate",
    )
    parser.add_argument(
        "--topic",
        default="",
        help="Topic for talking_points or deck templates",
    )
    parser.add_argument(
        "--period",
        default="",
        help="Period label for weekly_memo or newsletter templates (e.g. 'Week of March 25, 2026')",
    )
    parser.add_argument(
        "--context",
        default="",
        help="Context description for escalation_memo template",
    )
    parser.add_argument(
        "--decision-id",
        default="",
        dest="decision_id",
        help="Decision ID (D-NNN) for decision_memo template",
    )
    parser.add_argument(
        "--state-dir",
        default=str(_WORK_STATE_DIR),
        help="Path to state/work/ directory",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Write output to this file path (default: stdout)",
    )
    args = parser.parse_args(argv)

    engine = NarrativeEngine(state_dir=Path(args.state_dir))

    if args.template == "weekly_memo":
        result = engine.generate_weekly_memo(period=args.period or None)
    elif args.template == "talking_points":
        if not args.topic:
            parser.error("--topic is required for talking_points template")
        result = engine.generate_talking_points(topic=args.topic)
    elif args.template == "boundary_report":
        result = engine.generate_boundary_report()
    elif args.template == "connect_summary":
        result = engine.generate_connect_summary()
    elif args.template == "newsletter":
        result = engine.generate_newsletter(period=args.period or None)
    elif args.template == "deck":
        result = engine.generate_deck(topic=args.topic)
    elif args.template == "promo_case":
        result = engine.generate_promo_case(narrative=False)
    elif args.template == "promo_narrative":
        result = engine.generate_promo_case(narrative=True)
    elif args.template == "calibration_brief":
        result = engine.generate_calibration_brief()
    elif args.template == "escalation_memo":
        if not args.context:
            parser.error("--context is required for escalation_memo template")
        result = engine.generate_escalation_memo(context=args.context)
    elif args.template == "decision_memo":
        result = engine.generate_decision_memo(decision_id=args.decision_id)
    else:
        result = "Unknown template"

    if args.output:
        Path(args.output).write_text(result, encoding="utf-8")
        print(f"[narrative_engine] wrote {len(result)} chars to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
