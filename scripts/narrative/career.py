"""
scripts/narrative/career.py — Promotion case narrative templates.

Standalone functions (accept NarrativeEngineBase as ``base``):
  generate_promo_case(base, narrative=False)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from narrative._base import NarrativeEngineBase


def generate_promo_case(base: "NarrativeEngineBase", narrative: bool = False) -> str:
    """
    Generate promotion readiness assessment or full narrative draft (§7.11).

    narrative=False: Readiness assessment with scope arc, evidence density,
      visibility events, and gap list.
    narrative=True: Full promotion narrative Markdown written to
      work-promo-narrative.md — thesis, before/after, scope arc,
      milestone evidence, manager voice, visibility events, readiness signal.

    Always returns the generated text draft. Caller handles file writing.
    """
    journeys_fm = base._fm("work-project-journeys")
    journeys_body = base._body("work-project-journeys")
    perf_fm = base._fm("work-performance")
    perf_body = base._body("work-performance")
    people_body = base._body("work-people")
    career_body = base._body("work-career")

    today = datetime.now(timezone.utc)
    role = base.profile.get("profile", {}).get("role", "")
    team = base.profile.get("profile", {}).get("team", "")
    projects_tracked = int(journeys_fm.get("projects_tracked", 0))

    # ── Scope arc: collect all timeline entries ────────────────────────────
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

    # ── Scope trajectory signal ────────────────────────────────────────────
    trajectory = "stable"
    scope_events_90d = 0
    try:
        cutoff_90 = today - timedelta(days=90)
        _MONTH_MAP = {m: i for i, m in enumerate(
            ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
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

    # ── Connect goals + evidence density ──────────────────────────────────
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

    # ── Visibility events ──────────────────────────────────────────────────
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

    # ── Manager voice ──────────────────────────────────────────────────────
    manager_voice: list[str] = []
    for line in perf_body.split("\n"):
        if ('"' in line or "says" in line.lower() or "quote" in line.lower()
                or "verbatim" in line.lower()) and len(line) > 20:
            manager_voice.append(line.strip("- ").strip())
    manager_voice = [v for v in manager_voice if len(v) > 10][:4]

    # ── Readiness signal ──────────────────────────────────────────────────
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
        # ── Readiness Assessment (brief) ──────────────────────────────────
        lines.append("# Promotion Readiness Assessment\n\n")
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
            lines.append(
                f"- First milestone: {scope_entries[0]['date']} — "
                f"{scope_entries[0]['milestone'][:60]}\n"
            )
            lines.append(
                f"- Latest milestone: {scope_entries[-1]['date']} — "
                f"{scope_entries[-1]['milestone'][:60]}\n"
            )
        lines.append("\n")

        lines.append("## Evidence Density\n\n")
        if goals:
            for goal_name, status, ev_count in goals:
                stars = "★" * min(ev_count, 5) + "☆" * max(0, 5 - ev_count)
                gap = " ← **gap**" if ev_count < 3 else ""
                short_name = goal_name[:50]
                lines.append(f"- {short_name}: {stars} ({ev_count} evidence items){gap}\n")
        else:
            lines.append(
                "- _No goals found — run `/work bootstrap` or populate work-performance.md_\n"
            )
        lines.append("\n")

        lines.append("## Visibility Events\n\n")
        if vis_events:
            lines.append(f"- **{len(vis_events)} events recorded** in work-people.md\n")
            for ev in vis_events[:5]:
                lines.append(f"  {ev}\n")
        else:
            lines.append(
                "- _No visibility events captured yet — they are collected automatically "
                "on REFRESH_\n"
            )
        lines.append("\n")

        lines.append("## Readiness Signal\n\n")
        signal_icon = "✅" if readiness == "ready" else ("⚠" if "quarters" in readiness else "🔴")
        lines.append(f"**{signal_icon} {readiness}**\n\n")
        if thin_goals:
            lines.append(
                f"Thin goals (< 3 evidence items): "
                f"{', '.join(g[:40] for g in thin_goals[:3])}\n\n"
            )
        if not manager_voice:
            lines.append(
                "⚠ Manager voice not yet captured — populate via `/work notes` post-1:1.\n\n"
            )

        lines.append("## Next Actions\n\n")
        lines.append(
            "- Run `/work promo-case --narrative` to generate the full promotion document\n"
        )
        lines.append(
            "- Run `/work connect-prep --calibration` to generate the calibration defense brief\n"
        )
        if thin_goals:
            lines.append(
                f"- Add evidence to thin goals: "
                f"{', '.join(g[:30] for g in thin_goals[:2])}\n"
            )
        lines.append("\n")

    else:
        # ── Full Narrative (§7.11 /work promo-case --narrative) ──────────
        lines.append("# Promotion Narrative\n\n")
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
            lines.append(
                "_[Insert: one paragraph — what the case is, at what level, "
                "why evidence supports it]_\n\n"
            )

        lines.append("## Before / After\n\n")
        lines.append(
            "_[Insert: domain state before joining vs. current state — "
            "the transformation story]_\n\n"
        )
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
                lines.append(
                    f"| {e['date']} | {e['milestone'][:60]} "
                    f"| {e['evidence'][:40]} | {e['impact'][:40]} |\n"
                )
        else:
            lines.append(
                "_[Populate work-project-journeys.md to auto-generate this section]_\n"
            )
        lines.append("\n")

        lines.append("## Connect Goals — Evidence Summary\n\n")
        if goals:
            for goal_name, status, ev_count in goals:
                stars = "★" * min(ev_count, 5) + "☆" * max(0, 5 - ev_count)
                lines.append(f"### {goal_name}\n\n")
                lines.append(f"- Status: {status or '_[fill in]_'}\n")
                lines.append(f"- Evidence density: {stars} ({ev_count} items)\n")
                lines.append(
                    "- _[Add: specific milestone references, artifact links, delivery dates]_\n\n"
                )
        else:
            lines.append(
                "_[Populate work-performance.md Connect Goals section]_\n\n"
            )

        lines.append("## Manager and Peer Voice\n\n")
        if manager_voice:
            for mv in manager_voice:
                lines.append(f"- {mv}\n")
        else:
            lines.append(
                "_[Add: verbatim quotes from 1:1 pivots, Connect submissions, peer feedback]_\n"
            )
        lines.append("\n")

        lines.append("## Visibility Events\n\n")
        if vis_events:
            lines.append("| Date | Stakeholder | Type | Context | Source |\n")
            lines.append("| --- | --- | --- | --- | --- |\n")
            for ev in vis_events:
                lines.append(f"{ev}\n")
        else:
            lines.append(
                "_[Visibility events are captured automatically on each /work refresh — "
                "run it to populate]_\n"
            )
        lines.append("\n")

        lines.append("## Evidence Gaps\n\n")
        if thin_goals:
            for tg in thin_goals:
                lines.append(
                    f"- **{tg[:50]}** — fewer than 3 evidence items captured. "
                    "Recommended: log milestone artifacts via `/work notes`.\n"
                )
        if not vis_events:
            lines.append(
                "- **Visibility events** — not yet captured. "
                "Run `/work refresh` to begin collection.\n"
            )
        if not manager_voice:
            lines.append(
                "- **Manager voice** — no verbatim signals captured yet. "
                "Log via `/work notes` after each 1:1.\n"
            )
        lines.append("\n")

        lines.append("## Readiness Signal\n\n")
        signal_icon = "✅" if readiness == "ready" else ("⚠" if "quarters" in readiness else "🔴")
        lines.append(f"**{signal_icon} {readiness}**\n\n")
        lines.append(
            "_[Validate this signal with your manager and by cross-checking "
            "the evidence gap list above]_\n\n"
        )

    lines.append(base._freshness_footer())
    lines.append(
        "\n> **DRAFT** — Review, edit, and validate before using in any formal process.\n"
    )
    return "".join(lines)
