"""scripts/backfill/cross_reference.py — Backfill cross-reference enrichment.

Phase 1b: Enriches parsed scrape weeks by matching against:
  - state/work/work-project-journeys.md (project milestones)
  - state/work/work-career.md (career evidence entries)
  - state/work/work-performance.md (Connect cycle goals/evidence)

Ref: specs/reflection-loop.md §6.3 Phase 1b
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backfill.scrape_parser import ParsedScrapeWeek


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class EnrichedWeek(ParsedScrapeWeek):
    """ParsedScrapeWeek enriched with cross-referenced metadata."""
    project_refs: list[str] = field(default_factory=list)   # project names matched
    goal_refs: list[str] = field(default_factory=list)       # goal IDs matched
    career_evidence_ids: list[str] = field(default_factory=list)  # evidence entry IDs


# ---------------------------------------------------------------------------
# State file readers
# ---------------------------------------------------------------------------

def _read_text_safe(path: Path) -> str:
    """Read a file, returning empty string on any error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def load_project_journeys(state_dir: Path) -> dict[str, Any]:
    """Load project milestone data from work-project-journeys.md.

    Returns a dict: {project_name: [milestone_text, ...]}
    """
    path = state_dir / "work-project-journeys.md"
    text = _read_text_safe(path)
    if not text:
        return {}

    projects: dict[str, list[str]] = {}
    current_project: str | None = None

    for line in text.splitlines():
        # H2 heading = project name
        h2 = re.match(r"^##\s+(.+)", line)
        if h2:
            current_project = h2.group(1).strip()
            projects[current_project] = []
            continue

        if current_project is None:
            continue

        # Bullet lines = milestones / artifacts
        stripped = line.strip()
        if stripped.startswith("-") and len(stripped) > 5:
            projects[current_project].append(stripped.lstrip("- ").strip())

    return projects


def load_career_evidence(state_dir: Path) -> list[dict[str, Any]]:
    """Load career evidence entries from work-career.md.

    Returns a list of {id, text, week} dicts parsed from bullet lines.
    """
    path = state_dir / "work-career.md"
    text = _read_text_safe(path)
    if not text:
        return []

    evidence: list[dict[str, Any]] = []
    _EVIDENCE_BULLET = re.compile(
        r"^-\s+(?:\*\*)?([^\n*]{10,300})(?:\*\*)?(?:\s+\[([^\]]+)\])?$",
        re.MULTILINE,
    )
    for i, m in enumerate(_EVIDENCE_BULLET.finditer(text)):
        ev_text = m.group(1).strip()
        tag = (m.group(2) or "").strip()
        if len(ev_text) < 10:
            continue
        evidence.append({
            "id": f"CE-{i + 1:04d}",
            "text": ev_text,
            "tag": tag,
        })
    return evidence


# ---------------------------------------------------------------------------
# Title similarity helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> set[str]:
    """Return lowercase word tokens, excluding stopwords."""
    _STOPWORDS = {
        "a", "an", "the", "and", "or", "for", "in", "on", "at", "to", "with",
        "of", "is", "was", "are", "be", "by", "from", "as", "we", "i",
        "this", "that", "it", "its",
    }
    words = re.findall(r"\b[a-z]{3,}\b", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _similarity(a: str, b: str) -> float:
    """Jaccard similarity of word token sets."""
    ta = _normalise(a)
    tb = _normalise(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def dedup_items(items: list[dict[str, Any]], key: str = "title") -> list[dict[str, Any]]:
    """Remove near-duplicate items based on title similarity (Jaccard ≥ 0.7).

    Keeps the first occurrence in the list.
    Input dicts must have a 'title' field (or the specified key).
    """
    kept: list[dict[str, Any]] = []
    for item in items:
        text = item.get(key, item.get("subject", item.get("channel", "")))
        is_dup = any(
            _similarity(text, k.get(key, k.get("subject", k.get("channel", ""))))
            >= 0.7
            for k in kept
        )
        if not is_dup:
            kept.append(item)
    return kept


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def enrich_week(
    parsed: ParsedScrapeWeek,
    state_dir: Path,
) -> EnrichedWeek:
    """Enrich a ParsedScrapeWeek with cross-referenced metadata.

    Cross-references:
    - Meetings/email titles vs project-journeys.md milestones
    - Meeting/email titles vs career evidence entries
    - Matched project names and evidence IDs annotated on the EnrichedWeek
    """
    projects = load_project_journeys(state_dir)
    career = load_career_evidence(state_dir)

    # Build corpus of all meeting + email text for matching
    all_titles: list[str] = (
        [m.get("title", "") for m in parsed.meetings]
        + [e.get("subject", "") for e in parsed.email_items]
        + parsed.key_highlights
        + parsed.key_decisions
    )
    all_text = " ".join(t for t in all_titles if t)

    matched_projects: list[str] = []
    for proj_name, milestones in projects.items():
        # If the week contains text similar to the project name or any milestone
        proj_sim = _similarity(all_text, proj_name)
        milestone_sim = max(
            (_similarity(all_text, ms) for ms in milestones), default=0.0
        )
        if proj_sim >= 0.15 or milestone_sim >= 0.2:
            matched_projects.append(proj_name)

    matched_evidence: list[str] = []
    for ev in career:
        ev_sim = _similarity(all_text, ev["text"])
        if ev_sim >= 0.25:
            matched_evidence.append(ev["id"])

    # Build EnrichedWeek by copying all fields from parsed
    return EnrichedWeek(
        week_id=parsed.week_id,
        date_range=parsed.date_range,
        format_family=parsed.format_family,
        source_path=parsed.source_path,
        meetings=parsed.meetings,
        email_items=parsed.email_items,
        chat_items=parsed.chat_items,
        people_signals=parsed.people_signals,
        key_highlights=parsed.key_highlights,
        key_decisions=parsed.key_decisions,
        authored_docs=parsed.authored_docs,
        extraction_rate=parsed.extraction_rate,
        project_refs=matched_projects,
        goal_refs=[],               # populated in Phase 2 (WorkIQ gap-fill)
        career_evidence_ids=matched_evidence,
    )
