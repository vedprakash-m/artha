"""scripts/backfill/backfill_runner.py — Backfill Engine orchestrator.

Phases (ref: specs/reflection-loop.md §6.3):
  1a: Parse all scrape files (no API calls)
  1b: Cross-reference enrichment (no API calls)
  2:  WorkIQ gap-fill (session budget: max_workiq_calls_per_session, default 10)
  3:  Interactive review (--backfill-review)

Non-destructive: NEVER modifies existing state files.
Idempotency: checks ReflectionKey.already_exists() before writing any artifact.
Source citation: every backfill artifact includes [source: work-scrape/...] reference.
CF items: tagged carry_forward_status: historical, status: resolved.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("artha.backfill")

# ---------------------------------------------------------------------------
# Path / import resolution
# ---------------------------------------------------------------------------

# Allow running standalone (scripts/ on PYTHONPATH)
import sys as _sys
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS_DIR))

from backfill.scrape_parser import parse_scrape_file, ParsedScrapeWeek
from backfill.cross_reference import enrich_week, EnrichedWeek
from work.helpers import write_state_atomic
from work.reflection_key import ReflectionKey, Horizon


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BackfillSession:
    """Tracks state for one CLI invocation of --backfill."""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    scrape_path: str = ""
    weeks_processed: int = 0
    weeks_failed: int = 0
    calls_used: int = 0
    calls_budget: int = 10


@dataclass
class BackfillResult:
    """Summary returned by run_backfill()."""
    weeks_written: int = 0
    weeks_skipped: int = 0   # already_exists() == True
    weeks_failed: int = 0    # parse error or extraction_rate == 0
    total_items: int = 0
    extraction_rates: list[float] = field(default_factory=list)
    low_fidelity_weeks: list[str] = field(default_factory=list)  # week_ids with rate < 0.33


# ---------------------------------------------------------------------------
# Artifact builders
# ---------------------------------------------------------------------------

def _build_backfill_artifact(
    enriched: EnrichedWeek,
    now: datetime,
) -> str:
    """Build the Tier 2 markdown artifact for a backfill week."""
    import yaml

    total_items = (
        len(enriched.meetings)
        + len(enriched.email_items)
        + len(enriched.chat_items)
    )

    high_count = len([
        m for m in enriched.meetings
        if m.get("ved_organizer") or m.get("attendee_count", 0) >= 5
    ])

    fm: dict[str, Any] = {
        "schema_version": "1.0",
        "horizon": "weekly",
        "period": enriched.week_id,
        "created": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "accomplishment_count": total_items,
        "carry_forward_count": 0,     # historical — all CFs resolved
        "impact_summary": f"{high_count} HIGH, {max(total_items - high_count, 0)} MEDIUM, 0 LOW",
        "generated_by": "work/backfill",
        "source": "backfill",
        "source_file": f"[source: {enriched.source_path}]",
        "carry_forward_status": "historical",
        "format_family": enriched.format_family,
        "extraction_rate": round(enriched.extraction_rate, 2),
        "project_refs": enriched.project_refs or [],
    }
    yaml_header = yaml.dump(fm, default_flow_style=False, allow_unicode=True).rstrip()

    lines = [
        "---",
        yaml_header,
        "---",
        "",
        f"## Weekly Reflection — {enriched.week_id}",
    ]

    if enriched.date_range:
        lines.append(f"*{enriched.date_range}*")

    lines += [
        "",
        f"*[source: {enriched.source_path}]*",
        "",
    ]

    # --- Accomplishments ----
    lines.append("### Accomplishments by Impact")
    lines.append("")
    lines.append("#### HIGH Impact")

    high_meetings = [m for m in enriched.meetings if m.get("ved_organizer")]
    if high_meetings:
        for m in high_meetings:
            proj_tag = ""
            if enriched.project_refs:
                proj_tag = f" [{enriched.project_refs[0]}]"
            lines.append(f"1. **{m['title']}** [HIGH|ORG]{proj_tag}")
    else:
        lines.append("_(none)_")

    lines += ["", "#### MEDIUM Impact"]
    other_meetings = [m for m in enriched.meetings if not m.get("ved_organizer")]
    for m in other_meetings[:5]:
        lines.append(f"- {m['title']} [MED|ATTENDED]")
    for e in enriched.email_items[:3]:
        if e.get("urgency") in ("high", "medium"):
            lines.append(f"- Email: {e['subject']} [MED|EMAIL]")
    if not other_meetings and not enriched.email_items:
        lines.append("_(none)_")

    # --- Carry Forward (historical — all resolved) ---
    lines += [
        "",
        "### Carry Forward",
        "_(all historical carry-forwards are resolved — status: resolved)_",
    ]

    # --- Key Highlights ---
    if enriched.key_highlights:
        lines += ["", "### Key Highlights"]
        for h in enriched.key_highlights[:5]:
            lines.append(f"- {h}")

    # --- Key Decisions ---
    if enriched.key_decisions:
        lines += ["", "### Key Decisions"]
        for d in enriched.key_decisions[:5]:
            lines.append(f"- {d}")

    # --- People Signals ---
    if enriched.people_signals:
        lines += ["", "### People Signals"]
        for p in enriched.people_signals[:5]:
            lines.append(f"- {p['name']}: {p['context'][:80]}")

    # --- Chat Threads ---
    if enriched.chat_items:
        lines += ["", "### Teams Interactions"]
        for c in enriched.chat_items[:5]:
            lines.append(f"- {c['channel']}")

    # --- Cross-reference metadata ---
    if enriched.project_refs or enriched.career_evidence_ids:
        lines += ["", "### Cross-References"]
        if enriched.project_refs:
            lines.append(f"- Projects: {', '.join(enriched.project_refs[:5])}")
        if enriched.career_evidence_ids:
            lines.append(f"- Career evidence: {', '.join(enriched.career_evidence_ids[:5])}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scrape corpus discovery
# ---------------------------------------------------------------------------

def _discover_scrape_files(scrape_path: Path) -> list[Path]:
    """Return sorted list of .md files in the scrape corpus.

    Handles two layouts:
      scrape_path/YYYY/MM-wN.md  (standard)
      scrape_path/MM-wN.md       (flat)
    """
    if not scrape_path.exists():
        log.warning("Scrape path does not exist: %s", scrape_path)
        return []

    files = sorted(scrape_path.rglob("*.md"), key=lambda p: str(p))
    # Exclude README/index files
    files = [f for f in files if re.match(r"\d{2}-w\d+", f.stem, re.IGNORECASE)]
    return files


# ---------------------------------------------------------------------------
# Phase 1a + 1b: parse + enrich
# ---------------------------------------------------------------------------

def _parse_and_enrich(
    scrape_files: list[Path],
    state_dir: Path,
) -> tuple[list[EnrichedWeek], list[str]]:
    """Parse all scrape files and cross-reference with state.

    Returns (enriched_weeks, failed_week_ids).
    """
    enriched: list[EnrichedWeek] = []
    failed: list[str] = []
    # Track best-extraction-rate entry per week_id for dedup
    best_by_week: dict[str, int] = {}  # week_id → index in enriched list

    for f in scrape_files:
        parsed = parse_scrape_file(f)
        if parsed is None:
            log.warning("Failed to parse: %s", f.name)
            failed.append(f.stem)
            continue

        if not parsed.week_id:
            log.warning("No week_id for: %s", f.name)
            failed.append(f.stem)
            continue

        if parsed.extraction_rate < 0.01:
            log.warning("Zero extraction from: %s", f.name)
            failed.append(parsed.week_id)
            continue

        try:
            ew = enrich_week(parsed, state_dir)
        except Exception as exc:
            log.warning("Cross-reference failed for %s: %s", parsed.week_id, exc)
            # Degrade gracefully: wrap parsed in EnrichedWeek with empty refs
            from backfill.cross_reference import EnrichedWeek as EW
            ew = EW(
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
            )

        enriched.append(ew)

    # Deduplicate: when two files map to the same week_id, keep the one
    # with higher extraction_rate (e.g. stub file vs real scrape).
    seen: dict[str, int] = {}
    deduped: list[EnrichedWeek] = []
    for ew in enriched:
        if ew.week_id in seen:
            idx = seen[ew.week_id]
            if ew.extraction_rate > deduped[idx].extraction_rate:
                log.info(
                    "Replacing duplicate %s (rate %.2f → %.2f)",
                    ew.week_id, deduped[idx].extraction_rate, ew.extraction_rate,
                )
                deduped[idx] = ew
        else:
            seen[ew.week_id] = len(deduped)
            deduped.append(ew)

    return deduped, failed


# ---------------------------------------------------------------------------
# Phase 2: WorkIQ gap-fill (session budget)
# ---------------------------------------------------------------------------

def _workiq_gap_fill(
    enriched_weeks: list[EnrichedWeek],
    session: BackfillSession,
) -> list[EnrichedWeek]:
    """Phase 2: call WorkIQ for low-fidelity weeks (within session budget).

    WorkIQ is the ground-truth advisory source (§6.1 advisory note):
    high-fidelity recall window is typically 6–12 months.  We only call
    WorkIQ for recent weeks (≤12 months old) to avoid wasting budget on
    queries where recall is low.

    Budget exhaustion: saves progress, returns updated list with notes.
    """
    # Sort by recency (most recent first)
    sorted_weeks = sorted(
        enriched_weeks, key=lambda w: w.week_id, reverse=True
    )

    # Identify weeks that need gap-fill (extraction_rate < 0.65)
    gap_weeks = [w for w in sorted_weeks if w.extraction_rate < 0.65]

    if not gap_weeks or session.calls_budget <= 0:
        return enriched_weeks

    calls_remaining = session.calls_budget - session.calls_used

    # Filter to recent weeks only (rough cutoff: week_id >= 12 months ago)
    now = datetime.now(timezone.utc)
    cutoff_year = now.year - 1
    cutoff_week = now.isocalendar()[1]
    cutoff_id = f"{cutoff_year}-W{cutoff_week:02d}"

    recent_gaps = [w for w in gap_weeks if w.week_id >= cutoff_id]

    if not recent_gaps:
        log.info("No recent low-fidelity weeks for WorkIQ gap-fill")
        return enriched_weeks

    log.info(
        "WorkIQ gap-fill: %d weeks eligible, %d calls remaining in budget",
        len(recent_gaps), calls_remaining,
    )

    # Gap-fill via WorkIQ would go here in a live implementation.
    # The actual npx workiq call pattern mirrors work/workiq_connector.py.
    # For Phase 1.5 the gap-fill is scaffolded but not executed:
    # - WorkIQ availability is checked at runtime
    # - Each call consumes 1 session budget unit
    # - Results are merged back into the EnrichedWeek
    # This stub returns unchanged weeks and logs the gap-fill opportunity.

    if calls_remaining > 0:
        log.info(
            "WorkIQ gap-fill scaffolded: %d weeks would benefit. "
            "Activate Phase 2 WorkIQ calls when needed.",
            min(len(recent_gaps), calls_remaining),
        )
        session.calls_used += 0  # no actual calls in Phase 1.5

    return enriched_weeks


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def run_backfill(
    scrape_path: Path,
    state_dir: Path,
    base_dir: Path,
    max_workiq_calls: int = 10,
) -> BackfillResult:
    """Run the full backfill pipeline (Phases 1a + 1b + 2).

    Non-destructive: NEVER modifies existing files.
    Idempotent: weeks already written are skipped.

    Args:
        scrape_path:       Root of the work-scrape corpus (Q:/... or local copy).
        state_dir:         state/work/ directory.
        base_dir:          Repository root (for artifact output paths).
        max_workiq_calls:  Session budget for WorkIQ gap-fill.

    Returns:
        BackfillResult summary.
    """
    result = BackfillResult()
    session = BackfillSession(
        scrape_path=str(scrape_path),
        calls_budget=max_workiq_calls,
    )

    # Phase 1a: Discover + parse
    scrape_files = _discover_scrape_files(scrape_path)
    if not scrape_files:
        log.info("No scrape files found at %s", scrape_path)
        return result

    log.info("Found %d scrape files at %s", len(scrape_files), scrape_path)

    enriched_weeks, failed = _parse_and_enrich(scrape_files, state_dir)
    result.weeks_failed = len(failed)

    # Phase 2: WorkIQ gap-fill (session budget)
    enriched_weeks = _workiq_gap_fill(enriched_weeks, session)

    # Write artifacts
    now = datetime.now(timezone.utc)

    for ew in enriched_weeks:
        # Parse week_id → Horizon + period
        if not re.match(r"^\d{4}-W\d{2}$", ew.week_id):
            log.warning("Unexpected week_id format: %s — skipping", ew.week_id)
            result.weeks_failed += 1
            continue

        key = ReflectionKey(horizon=Horizon.WEEKLY, period=ew.week_id)

        # Idempotency gate
        if key.already_exists():
            log.info("[idempotent] %s already present — skipping", key.as_string)
            result.weeks_skipped += 1
            continue

        # Build artifact
        artifact_content = _build_backfill_artifact(ew, now=now)

        # Ensure directory exists
        artifact_path = base_dir / key.artifact_filename
        artifact_path.parent.mkdir(parents=True, exist_ok=True)

        # Write atomically
        write_state_atomic(artifact_path, artifact_content)
        log.info("Written backfill artifact: %s", key.as_string)

        result.weeks_written += 1
        result.total_items += (
            len(ew.meetings) + len(ew.email_items) + len(ew.chat_items)
        )
        result.extraction_rates.append(ew.extraction_rate)
        if ew.extraction_rate < 0.33:
            result.low_fidelity_weeks.append(ew.week_id)

        session.weeks_processed += 1

    return result


# ---------------------------------------------------------------------------
# Phase 3: Interactive review
# ---------------------------------------------------------------------------

def run_backfill_review(
    state_dir: Path,
    base_dir: Path,
) -> str:
    """Phase 3 — Interactive review of backfill output (§6.3 Phase 3).

    For each quarter, presents summary of accomplishments and gaps,
    then asks the user to validate/correct.

    This surface is invoked by /work reflect --backfill-review.
    """
    reflections_dir = base_dir / "state" / "work" / "reflections" / "weekly"
    if not reflections_dir.exists():
        return (
            "No backfill reflections found. "
            "Run '/work reflect --backfill' first to generate historical reflections."
        )

    # Discover all weekly reflections
    weekly_files = sorted(reflections_dir.glob("*.md"))
    backfill_files = []
    for wf in weekly_files:
        try:
            text = wf.read_text(encoding="utf-8")
            if 'source: "backfill"' in text or "source: backfill" in text:
                backfill_files.append(wf)
        except Exception:
            continue

    if not backfill_files:
        return (
            "No backfill reflections found (source: backfill). "
            "Run '/work reflect --backfill' first."
        )

    # Group by quarter
    quarters: dict[str, list[Path]] = {}
    for wf in backfill_files:
        # week_id from filename
        week_id = wf.stem  # e.g. "2025-W14"
        m = re.match(r"(\d{4})-W(\d+)", week_id)
        if not m:
            continue
        year = int(m.group(1))
        week = int(m.group(2))
        # ISO week → approximate quarter
        quarter = (week - 1) // 13 + 1
        q_key = f"{year}-Q{quarter}"
        quarters.setdefault(q_key, []).append(wf)

    lines = [
        "## Backfill Review — Phase 3",
        "",
        f"Found **{len(backfill_files)} backfill reflections** across "
        f"**{len(quarters)} quarters**.",
        "",
        "### Quarter Summary",
        "",
    ]

    for q_key in sorted(quarters.keys()):
        q_files = quarters[q_key]
        lines.append(f"**{q_key}**: {len(q_files)} weeks")

        # Summarise accomplishments from this quarter's files
        all_high: list[str] = []
        for wf in sorted(q_files):
            try:
                text = wf.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if line.startswith("1. **") and "[HIGH|ORG]" in line:
                        title = re.sub(r"\s*\[.*?\]", "", line[5:]).strip().rstrip("*").lstrip("*")
                        all_high.append(title)
            except Exception:
                continue

        if all_high:
            lines.append("  HIGH impact items:")
            for h in all_high[:3]:
                lines.append(f"    - {h}")

        lines.append("")

    lines += [
        "### Next Steps",
        "",
        "Review each quarter and confirm:",
        "1. Are the HIGH impact items correctly identified?",
        "2. Are there accomplishments missing from this period?",
        "3. Are any items misattributed or overstated?",
        "",
        "To update a specific week, edit the file in state/work/reflections/weekly/",
        "and add `reviewed: true` to its YAML frontmatter.",
        "",
        "_This is a Phase 3 interactive review summary. "
        "For detailed edits, open individual weekly reflection files._",
    ]

    return "\n".join(lines)
