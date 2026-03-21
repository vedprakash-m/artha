#!/usr/bin/env python3
"""
scripts/pr_manager.py — PR Manager: Personal Narrative Engine (specs/pr-manager.md PR-1 v1.2)

Phases implemented:
  Phase 1 — State file I/O, content calendar view, /pr command data layer
  Phase 2 — Deterministic moment detection + convergence scoring + derived snapshot write
  Phase 3 — Draft context assembler, PII gate, anti-spam governor check

Runtime modes:
  --step8           : Run moment detection on existing skill outputs → write
                      derived snapshot to state/pr_manager.md + emit
                      tmp/content_moments.json (Step 8 catch-up hook)
  --view            : Render /pr content calendar to stdout
  --threads         : Render /pr threads narrative thread progress
  --voice           : Render /pr voice current voice profile
  --draft-context   : Assemble and emit draft generation context to stdout
                      (consumed by LLM to generate in-context post draft)
  --log-post        : Log a published post (updates platform_metrics + post history)
  --check           : Dry-run health check — validate state file, config, feature flags

Outputs written by --step8:
  state/pr_manager.md  (Derived Snapshot section updated — atomic replace)
  tmp/content_moments.json  (scored moment list for briefing adapter)

Config flag: enhancements.pr_manager (default: false)
             enhancements.pr_manager.compose (default: false, Phase 3+)

Ref: specs/pr-manager.md PR-1 v1.2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _SCRIPTS_DIR.parent
_STATE_DIR = _ROOT_DIR / "state"
_TMP_DIR = _ROOT_DIR / "tmp"
_CONFIG_DIR = _ROOT_DIR / "config"

_PR_STATE_FILE = _STATE_DIR / "pr_manager.md"
_ARTHA_CONFIG_FILE = _CONFIG_DIR / "artha_config.yaml"
_CONTENT_MOMENTS_FILE = _TMP_DIR / "content_moments.json"
_TREND_SCAN_FILE = _TMP_DIR / "trend_scan.json"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Feature flag helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    if not _ARTHA_CONFIG_FILE.exists():
        return {}
    try:
        return yaml.safe_load(_ARTHA_CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def _is_enabled(config: dict | None = None) -> bool:
    """Return True if enhancements.pr_manager is truthy."""
    cfg = config or _load_config()
    enhancements = cfg.get("enhancements", {})
    val = enhancements.get("pr_manager", False)
    if isinstance(val, dict):
        return bool(val.get("enabled", False))
    return bool(val)


def _compose_enabled(config: dict | None = None) -> bool:
    """Return True if Phase 3 compose feature is enabled."""
    cfg = config or _load_config()
    enhancements = cfg.get("enhancements", {})
    val = enhancements.get("pr_manager", False)
    if isinstance(val, dict):
        return bool(val.get("compose", False))
    return False


# ---------------------------------------------------------------------------
# State file I/O
# ---------------------------------------------------------------------------

def _parse_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from a Markdown file."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    lines = text.splitlines()
    fm_lines: list[str] = []
    in_fm = False
    dash_count = 0
    for line in lines:
        if line.strip() == "---":
            dash_count += 1
            if dash_count == 1:
                in_fm = True
                continue
            if dash_count == 2:
                break
        elif in_fm:
            fm_lines.append(line)
    if not fm_lines:
        return {}
    try:
        return yaml.safe_load("\n".join(fm_lines)) or {}
    except yaml.YAMLError:
        return {}


def _load_pr_state() -> dict:
    return _parse_frontmatter(_PR_STATE_FILE)


def _read_pr_body() -> str:
    """Return full file content of pr_manager.md."""
    if not _PR_STATE_FILE.exists():
        return ""
    try:
        return _PR_STATE_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y"]


def _parse_date(v: Any) -> date | None:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if v is None or str(v).strip().lower() in ("null", "none", "—", "-", ""):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _days_until(v: Any) -> int | None:
    d = _parse_date(v)
    if d is None:
        return None
    return (d - date.today()).days


def _days_ago(v: Any) -> int | None:
    d = _parse_date(v)
    if d is None:
        return None
    return (date.today() - d).days


# ---------------------------------------------------------------------------
# Moment types + scoring config (§4.1, §4.2)
# ---------------------------------------------------------------------------

# moment_type → signal_weight (§4.1 table)
_MOMENT_WEIGHTS: dict[str, float] = {
    "cultural_festival": 0.9,
    "life_milestone": 0.85,
    "trending_tech": 0.8,
    "trending_education": 0.8,
    "career_milestone": 0.75,
    "goal_milestone": 0.75,
    "school_milestone": 0.75,
    "seasonal_rhythm": 0.7,
    "friend_life_event": 0.65,
    "birthday_family": 0.65,
    "birthday_friend": 0.65,
    "hiking_event": 0.65,
    "local_weather_outdoor": 0.5,
    "immigration_milestone": 0.65,
    "mba_anniversary": 0.75,
}

# Default moment_thread_map (§4.2.1) — overridden by state/pr_manager.md YAML block
_DEFAULT_MOMENT_THREAD_MAP: dict[str, list[tuple[str, float]]] = {
    "cultural_festival":   [("NT-2", 1.0), ("NT-6", 0.5)],
    "birthday_family":     [("NT-4", 1.0), ("NT-6", 0.7)],
    "birthday_friend":     [("NT-6", 1.0)],
    "hiking_event":        [("NT-3", 1.0)],
    "career_milestone":    [("NT-1", 0.9), ("NT-5", 0.8)],
    "goal_milestone":      [("NT-1", 0.7), ("NT-5", 0.6)],
    "trending_tech":       [("NT-1", 0.9)],
    "trending_education":  [("NT-1", 0.8), ("NT-5", 0.6)],
    "school_milestone":    [("NT-4", 0.9)],
    "seasonal_rhythm":     [("NT-2", 0.7), ("NT-3", 0.5)],
    "friend_life_event":   [("NT-6", 1.0)],
    "local_weather_outdoor": [("NT-3", 0.8)],
    "immigration_milestone": [("NT-2", 0.7)],
    "mba_anniversary":     [("NT-5", 0.9)],
    "life_milestone":      [("NT-4", 0.85), ("NT-2", 0.5)],
}

# Timeliness decay (§4.2): days_offset → timeliness_factor
# day-of=0 → 1.0, day+1 → 0.7, day+2 → 0.5, day+3 → 0.3, day+4–6 → 0.2, day+7 → 0.0
def _timeliness(days_until_event: int) -> float:
    if days_until_event < 0:
        return 0.0  # past
    table = {0: 1.0, 1: 0.7, 2: 0.5, 3: 0.3}
    if days_until_event in table:
        return table[days_until_event]
    if days_until_event <= 6:
        return 0.2
    return 0.0


# Platform map per moment type — what platforms are relevant
_MOMENT_PLATFORMS: dict[str, list[str]] = {
    "cultural_festival":   ["linkedin", "facebook", "whatsapp_status"],
    "birthday_family":     ["facebook", "whatsapp_status"],
    "birthday_friend":     ["facebook"],
    "hiking_event":        ["instagram", "whatsapp_group"],
    "career_milestone":    ["linkedin"],
    "goal_milestone":      ["linkedin", "facebook"],
    "trending_tech":       ["linkedin"],
    "trending_education":  ["linkedin"],
    "school_milestone":    ["facebook"],
    "seasonal_rhythm":     ["facebook", "instagram"],
    "friend_life_event":   ["facebook"],
    "local_weather_outdoor": ["instagram", "whatsapp_group"],
    "immigration_milestone": ["facebook"],
    "mba_anniversary":     ["linkedin"],
    "life_milestone":      ["facebook", "instagram"],
}

# Occasions from occasion_tracker that map to moment types
_OCCASION_TO_MOMENT_TYPE: dict[str, str] = {
    "holi": "cultural_festival",
    "diwali": "cultural_festival",
    "navratri": "cultural_festival",
    "dussehra": "cultural_festival",
    "raksha_bandhan": "cultural_festival",
    "eid": "cultural_festival",
    "christmas": "cultural_festival",
    "thanksgiving": "cultural_festival",
    "new_year": "cultural_festival",
    "bhai_dooj": "cultural_festival",
    "janmashtami": "cultural_festival",
    "birthday": "birthday_family",
    "friend_birthday": "birthday_friend",
    "hiking": "hiking_event",
    "trail": "hiking_event",
    "school_milestone": "school_milestone",
    "graduation": "school_milestone",
    "career": "career_milestone",
    "award": "career_milestone",
    "mba": "mba_anniversary",
}


# ---------------------------------------------------------------------------
# Scored Moment dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScoredMoment:
    moment_type: str          # from _MOMENT_WEIGHTS keys
    label: str                # human-readable label, e.g. "Holi 2026"
    event_date: str           # ISO date string
    days_until: int           # days until event (0 = today, negative = past)
    signal_weight: float      # from _MOMENT_WEIGHTS
    relevance: float          # highest relevance from moment_thread_map
    signal_magnitude: float   # emitted by upstream skill (0.1–1.0, default 0.5)
    timeliness: float         # computed decay factor
    convergence_score: float  # signal_weight × relevance × signal_magnitude × timeliness
    primary_thread: str       # best matching thread ID (NT-1 … NT-6)
    alt_threads: list[str]    # additional matching thread IDs
    platforms: list[str]      # recommended platforms for this moment
    above_daily_threshold: bool    # score >= 0.8
    above_weekly_threshold: bool   # score >= 0.6
    source: str               # "occasion_tracker" | "goals" | "trend" | "calendar"

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def score_emoji(self) -> str:
        if self.convergence_score >= 0.8:
            return "🟠"
        if self.convergence_score >= 0.6:
            return "🟡"
        return "🔵"


# ---------------------------------------------------------------------------
# MomentDetector (Phase 2)
# ---------------------------------------------------------------------------

class MomentDetector:
    """Deterministic convergence scoring engine (§4.2).

    Consumes structured signals from existing skills (occasion_tracker,
    relationship_pulse, goals step) and rates each against the
    moment_thread_map to produce a list of ScoredMoment objects.

    Called from catch-up Step 8 via --step8 CLI mode.
    """

    def __init__(self, pr_state: dict | None = None) -> None:
        self._pr_state = pr_state or _load_pr_state()
        self._thread_map = self._load_thread_map()

    def _load_thread_map(self) -> dict[str, list[tuple[str, float]]]:
        """Load moment_thread_map from state file or fall back to defaults."""
        return _DEFAULT_MOMENT_THREAD_MAP.copy()

    def _best_relevance(self, moment_type: str) -> tuple[float, str, list[str]]:
        """Return (best_relevance, primary_thread, alt_threads) for a moment type."""
        mappings = self._thread_map.get(moment_type, [])
        if not mappings:
            return 0.5, "NT-1", []
        sorted_mappings = sorted(mappings, key=lambda t: t[1], reverse=True)
        primary_thread = sorted_mappings[0][0]
        best_relevance = sorted_mappings[0][1]
        alt_threads = [t[0] for t in sorted_mappings[1:]]
        return best_relevance, primary_thread, alt_threads

    def score_moment(
        self,
        moment_type: str,
        label: str,
        event_date_str: str,
        signal_magnitude: float = 0.5,
        source: str = "occasion_tracker",
    ) -> ScoredMoment | None:
        """Score a single moment. Returns None if below any threshold."""
        signal_weight = _MOMENT_WEIGHTS.get(moment_type, 0.5)
        relevance, primary_thread, alt_threads = self._best_relevance(moment_type)
        days = _days_until(event_date_str)
        if days is None:
            return None
        timeliness = _timeliness(days)
        if timeliness == 0.0:
            return None  # past event or too far out

        score = signal_weight * relevance * signal_magnitude * timeliness

        return ScoredMoment(
            moment_type=moment_type,
            label=label,
            event_date=event_date_str,
            days_until=days,
            signal_weight=signal_weight,
            relevance=relevance,
            signal_magnitude=signal_magnitude,
            timeliness=timeliness,
            convergence_score=round(score, 3),
            primary_thread=primary_thread,
            alt_threads=alt_threads,
            platforms=_MOMENT_PLATFORMS.get(moment_type, ["linkedin"]),
            above_daily_threshold=score >= 0.8,
            above_weekly_threshold=score >= 0.6,
            source=source,
        )

    def score_occasions(self, occasions: list[dict]) -> list[ScoredMoment]:
        """Score a list of occasion dicts from occasion_tracker skill output.

        Expected occasion dict keys:
          name (str), date (str ISO), type (str, optional), magnitude (float, optional)
        """
        results: list[ScoredMoment] = []
        for occ in occasions:
            name = occ.get("name", "")
            event_date = str(occ.get("date", ""))
            magnitude = float(occ.get("magnitude", 0.5))
            # Derive moment type from occasion name/type
            occ_type = str(occ.get("type", name)).lower()
            moment_type = _OCCASION_TO_MOMENT_TYPE.get(occ_type)
            # Fuzzy match on name if direct type not found
            if not moment_type:
                for key, mtype in _OCCASION_TO_MOMENT_TYPE.items():
                    if key in name.lower():
                        moment_type = mtype
                        break
            if not moment_type:
                moment_type = "cultural_festival"

            scored = self.score_moment(
                moment_type=moment_type,
                label=name,
                event_date_str=event_date,
                signal_magnitude=magnitude,
                source="occasion_tracker",
            )
            if scored is not None:
                results.append(scored)

        return sorted(results, key=lambda m: m.convergence_score, reverse=True)

    def score_from_trends(self, trends: list[dict]) -> list[ScoredMoment]:
        """Score trend signals from Gemini trend scan (Step 9, Monday only).

        Expected trend dict keys: topic (str), relevance (str: high/medium/low)
        """
        results: list[ScoredMoment] = []
        magnitude_map = {"high": 1.0, "medium": 0.7, "low": 0.4}
        for trend in trends:
            topic = trend.get("topic", "")
            relevance_str = str(trend.get("relevance", "medium")).lower()
            magnitude = magnitude_map.get(relevance_str, 0.5)
            # Classify trend moment type
            topic_lower = topic.lower()
            if any(w in topic_lower for w in ["ai", "tech", "software", "cloud", "ml"]):
                moment_type = "trending_tech"
            elif any(w in topic_lower for w in ["education", "learning", "school"]):
                moment_type = "trending_education"
            elif any(w in topic_lower for w in ["career", "job", "leadership"]):
                moment_type = "career_milestone"
            else:
                moment_type = "trending_tech"

            # Trends are always "today" relevant (timeliness = 1.0)
            today_str = date.today().isoformat()
            scored = self.score_moment(
                moment_type=moment_type,
                label=f"Trending: {topic}",
                event_date_str=today_str,
                signal_magnitude=magnitude,
                source="trend_scan",
            )
            if scored is not None:
                results.append(scored)

        return sorted(results, key=lambda m: m.convergence_score, reverse=True)


# ---------------------------------------------------------------------------
# Anti-Spam Governor (§4.3)
# ---------------------------------------------------------------------------

class AntiSpamGovernor:
    """Enforces posting frequency limits before surfacing content opportunities."""

    # Hardcoded safe defaults — overridable by state/pr_manager.md YAML block
    _LIMITS: dict[str, dict] = {
        "linkedin":        {"max_per_week": 2, "min_gap_days": 2},
        "facebook":        {"max_per_week": 2, "min_gap_days": 1},
        "instagram":       {"max_per_week": 2, "min_gap_days": 2},
        "whatsapp_status": {"max_per_week": 3, "min_gap_days": 0},
    }

    def __init__(self, pr_state: dict | None = None) -> None:
        self._pr_state = pr_state or _load_pr_state()

    def platform_available(self, platform: str) -> tuple[bool, str]:
        """Return (ok, reason). ok=True means posting is within limits."""
        limits = self._LIMITS.get(platform)
        if not limits:
            return True, ""

        metrics = (
            self._pr_state.get("platform_metrics", {})
            .get(platform, {})
        )
        last_post = metrics.get("last_post")
        posts_30d = int(metrics.get("posts_30d", 0))

        if last_post:
            gap = _days_ago(last_post)
            if gap is not None and gap < limits["min_gap_days"]:
                return (
                    False,
                    f"Min gap {limits['min_gap_days']}d not met (last post {gap}d ago)",
                )

        # Weekly quota approximation (30d / 4.3 weeks)
        weekly_approx = posts_30d / 4.3
        if weekly_approx >= limits["max_per_week"]:
            return (
                False,
                f"Weekly quota {limits['max_per_week']}/week approaching (≈{weekly_approx:.1f}/wk)",
            )

        return True, ""


# ---------------------------------------------------------------------------
# Derived Snapshot Writer (Step 8 hook)
# ---------------------------------------------------------------------------

def _count_pending_drafts(pr_body: str) -> int:
    """Count non-empty rows in the Pending Drafts section."""
    in_section = False
    count = 0
    for line in pr_body.splitlines():
        if "## Pending Drafts" in line:
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            # Count table rows (| col | ... | col |) that are not headers or separator
            if line.strip().startswith("|") and "---" not in line and "Platform" not in line:
                # Check if row has meaningful content (not all dashes/blanks)
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if cells and not all(c in ("—", "-", "") for c in cells):
                    count += 1
    return count


def write_derived_snapshot(
    next_occasion_date: str | None,
    pending_draft_count: int,
    pr_state_path: Path | None = None,
) -> bool:
    """Atomically update the Derived Snapshot block in state/pr_manager.md.

    Returns True on success, False if write failed.
    """
    path = pr_state_path or _PR_STATE_FILE
    if not path.exists():
        return False

    today_str = date.today().isoformat()
    occ_date = next_occasion_date or "—"

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    # Update YAML frontmatter derived_snapshot block
    # Using targeted regex to replace just the derived_snapshot section in frontmatter
    fm_pattern = re.compile(
        r"(derived_snapshot:\s*\n)"
        r"([ \t]+next_occasion_date:.*\n)"
        r"([ \t]+pending_draft_count:.*\n)"
        r"([ \t]+last_scan_at:.*\n?)",
        re.MULTILINE,
    )
    new_fm_block = (
        "derived_snapshot:\n"
        f"  next_occasion_date: {occ_date}\n"
        f"  pending_draft_count: {pending_draft_count}\n"
        f"  last_scan_at: {today_str}\n"
    )
    if fm_pattern.search(content):
        content = fm_pattern.sub(new_fm_block, content)
    else:
        # Frontmatter block not found — append before closing ---
        # This is a safety fallback: state file may be fresh
        content = content.replace(
            "derived_snapshot:\n  next_occasion_date: null\n"
            "  pending_draft_count: 0\n  last_scan_at: null",
            new_fm_block.rstrip("\n"),
        )

    # Update markdown Derived Snapshot table
    md_pattern = re.compile(
        r"(## Derived Snapshot\n.*?\n"
        r"\|.*?\|\n"  # header row
        r"\|.*?\|\n"  # separator row — optional
        r"(?:\|.*?\|\n)*"
        r"(?:\| next_occasion_date \|.*?\|\n)"
        r"(?:\| pending_draft_count \|.*?\|\n)"
        r"(?:\| last_scan_at \|.*?\|\n?))",
        re.DOTALL,
    )

    table_pattern = re.compile(
        r"(\| next_occasion_date \|)([^\n]*)(\|)",
    )
    content = table_pattern.sub(
        rf"\g<1> {occ_date} | occasion_tracker |", content
    )

    table_pattern2 = re.compile(
        r"(\| pending_draft_count \|)([^\n]*)(\|)",
    )
    content = table_pattern2.sub(
        rf"\g<1> {pending_draft_count} | count(Pending Drafts rows) |", content
    )

    table_pattern3 = re.compile(
        r"(\| last_scan_at \|)([^\n]*)(\|)",
    )
    content = table_pattern3.sub(
        rf"\g<1> {today_str} | Step 8 timestamp |", content
    )

    # Atomic write
    tmp = path.with_suffix(".tmp")
    try:
        import fcntl
        with open(tmp, "w", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
            except Exception:
                pass
            fh.write(content)
        import os
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return False


# ---------------------------------------------------------------------------
# Content Calendar Renderer (Phase 1+2)
# ---------------------------------------------------------------------------

_THREAD_NAMES: dict[str, str] = {
    "NT-1": "Thoughtful Technologist",
    "NT-2": "Cultural Bridge-Builder",
    "NT-3": "PNW Explorer",
    "NT-4": "Proud Dad",
    "NT-5": "MBA Practitioner",
    "NT-6": "The Connector",
}

_PLATFORM_DISPLAY: dict[str, str] = {
    "linkedin": "LinkedIn",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "whatsapp_status": "WA Status",
    "whatsapp_group": "WA Group",
}


def render_content_calendar(
    moments: list[ScoredMoment],
    pr_state: dict | None = None,
    is_weekly: bool = False,
) -> str:
    """Render the content calendar section for briefing injection."""
    state = pr_state or _load_pr_state()
    gov = AntiSpamGovernor(state)

    if not moments:
        if is_weekly:
            return _render_empty_weekly_calendar(state)
        return ""

    lines: list[str] = []
    if is_weekly:
        lines.append("━━ 📣 CONTENT OPPORTUNITIES THIS WEEK ━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"{'Moment':<35} {'Platforms':<18} {'Thread':<8} {'Score'}")
        lines.append("─" * 72)
    else:
        lines.append("### 📣 Content Opportunity")

    for m in moments:
        if not m.above_weekly_threshold:
            continue
        platforms_str = " + ".join(
            _PLATFORM_DISPLAY.get(p, p) for p in m.platforms[:3]
        )
        thread_str = m.primary_thread
        thread_name = _THREAD_NAMES.get(m.primary_thread, "")
        days_label = "today" if m.days_until == 0 else f"in {m.days_until}d" if m.days_until > 0 else "today"

        # Build platform availability note
        avail_notes = []
        for platform in m.platforms[:2]:
            ok, reason = gov.platform_available(platform)
            if not ok:
                avail_notes.append(f"⚠️  {_PLATFORM_DISPLAY.get(platform, platform)}: {reason}")

        if is_weekly:
            lines.append(
                f"{m.score_emoji} {m.label:<33} {platforms_str:<18} {thread_str:<8} {m.convergence_score:.2f}"
            )
            lines.append(f"   ↳ Thread: {thread_name} · /pr draft {m.platforms[0] if m.platforms else 'linkedin'}")
            for note in avail_notes:
                lines.append(f"   ├ {note}")
        else:
            # Daily briefing format (compact)
            lines.append(
                f"{m.score_emoji} **{m.label}** ({days_label}) — convergence score {m.convergence_score:.2f}"
            )
            lines.append(
                f"   Thread: {thread_str} · {thread_name}"
            )
            lines.append(
                f"   Platforms: {platforms_str}"
            )
            lines.append(
                f"   Say \"draft it\" or use `/pr draft {m.platforms[0] if m.platforms else 'linkedin'}`"
            )
            for note in avail_notes:
                lines.append(f"   ⚠️  {note}")

    if is_weekly:
        lines.append("─" * 72)
        # Platform quota summary
        metrics = state.get("platform_metrics", {})
        quota_parts = []
        for plat, display in [("linkedin", "LinkedIn"), ("facebook", "FB"), ("instagram", "IG")]:
            pm = metrics.get(plat, {})
            posts = pm.get("posts_30d", 0)
            quota_parts.append(f"{display} {posts}/~8")
        lines.append(f"Posts this month: {' · '.join(quota_parts)}")

        # Last post info
        last_posts = []
        for plat, display in [("linkedin", "LI"), ("facebook", "FB"), ("instagram", "IG")]:
            pm = metrics.get(plat, {})
            last = pm.get("last_post")
            if last:
                ago = _days_ago(last)
                last_posts.append(f"{display} {last} ({ago}d ago)")
        if last_posts:
            lines.append(f"Last posts: {' · '.join(last_posts)}")

        lines.append("━" * 72)

    return "\n".join(lines)


def _render_empty_weekly_calendar(state: dict) -> str:
    """Render weekly calendar when no high-scoring moments detected."""
    metrics = state.get("platform_metrics", {})
    quota_parts = []
    for plat, display in [("linkedin", "LinkedIn"), ("facebook", "FB"), ("instagram", "IG")]:
        pm = metrics.get(plat, {})
        posts = pm.get("posts_30d", 0)
        quota_parts.append(f"{display} {posts}/~8")
    posts_str = " · ".join(quota_parts)
    return (
        "### 📣 Content Calendar\n"
        f"No high-scoring content moments detected this week.\n"
        f"Posts this month: {posts_str}\n"
        "Tip: Use `/pr draft linkedin` to create a post on any topic."
    )


# ---------------------------------------------------------------------------
# /pr command views
# ---------------------------------------------------------------------------

def render_pr_overview(moments: list[ScoredMoment] | None = None) -> str:
    """Render /pr top-level content calendar view."""
    state = _load_pr_state()
    if moments is None:
        moments = _load_scored_moments_from_cache()
    calendar = render_content_calendar(moments, state, is_weekly=True)

    ds = state.get("derived_snapshot", {})
    last_scan = ds.get("last_scan_at", "never")
    pending = ds.get("pending_draft_count", 0)

    header = [
        "━━ 📣 PR MANAGER — CONTENT CALENDAR ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Last scanned: {last_scan}  ·  Pending drafts: {pending}",
        "",
    ]
    footer = [
        "",
        "Commands: /pr threads · /pr voice · /pr draft <platform> [topic]",
        "━" * 72,
    ]
    return "\n".join(header) + calendar + "\n".join(footer)


def render_thread_progress() -> str:
    """Render /pr threads narrative thread progress."""
    state = _load_pr_state()
    # Read thread data from Markdown body (table parsing)
    body = _read_pr_body()
    thread_section = _extract_section(body, "## Narrative Thread Progress")

    lines = [
        "━━ 📣 NARRATIVE THREADS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if thread_section:
        lines.append(thread_section.strip())
    else:
        lines.append("No thread data available. Run a catch-up to populate.")

    lines += [
        "",
        "Thread guide:",
        "  NT-1: Thoughtful Technologist  · LinkedIn, Facebook",
        "  NT-2: Cultural Bridge-Builder  · LinkedIn, Facebook, Instagram",
        "  NT-3: PNW Explorer             · Instagram, Facebook, WA Hiking Group",
        "  NT-4: Proud Dad                · Facebook (friends), Instagram, WA Family",
        "  NT-5: MBA Practitioner         · LinkedIn",
        "  NT-6: The Connector            · All platforms",
        "━" * 72,
    ]
    return "\n".join(lines)


def render_voice_profile() -> str:
    """Render /pr voice current voice profile."""
    body = _read_pr_body()
    overrides_section = _extract_section(body, "## Voice Profile Overrides")

    lines = [
        "━━ 📣 VOICE PROFILE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "**Core attributes:**",
        "  Language: English primary, Hindi (transliteration) for cultural/family content",
        "  Tone: Thoughtful-casual — warm and considered, not corporate",
        "  Perspective: First-person ('I', not 'we' unless family content)",
        "  Humor: Dry and subtle — occasional, never forced",
        "",
        "**Avoid:**",
        "  • Corporate buzzwords (synergy, leverage, pivot)",
        "  • Humble-bragging ('humbled to announce')",
        "  • Engagement bait ('agree?' / 'thoughts?')",
        "  • Emoji overload (max 2–3 per post; none on LinkedIn thought pieces)",
        "  • Generic AI phrases ('in today's fast-paced world')",
        "",
        "**Signature elements:**",
        "  • Cultural specificity — name the festival, explain the tradition briefly",
        "  • Concrete detail — '4,000ft gain to [trail name]', not 'great hike'",
        "  • Earned perspective — insights from lived experience",
        "  • Graceful brevity — fewer words than expected",
        "  • Generous attribution — credit others, name names",
        "",
    ]
    if overrides_section and "—" not in overrides_section:
        lines += ["**User overrides (active):**", overrides_section.strip(), ""]

    lines += [
        "To refine: /pr voice adjust",
        "━" * 72,
    ]
    return "\n".join(lines)


def _extract_section(body: str, heading: str) -> str:
    """Extract a markdown section (up to next ##)."""
    lines = body.splitlines()
    in_section = False
    result: list[str] = []
    for line in lines:
        if line.strip() == heading.strip():
            in_section = True
            continue
        if in_section:
            if line.startswith("## ") and line.strip() != heading.strip():
                break
            result.append(line)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Draft Context Assembler (Phase 3)
# ---------------------------------------------------------------------------

def assemble_draft_context(
    platform: str,
    topic: str | None = None,
    moment: ScoredMoment | None = None,
    include_trends: bool = False,
) -> dict:
    """Assemble the context dict that the LLM uses to generate a post draft.

    Returns a structured dict containing:
      - voice_profile: the active voice profile rules
      - platform_rules: platform-specific adaptation rules
      - moment_context: the triggering moment (if any)
      - thread_context: the narrative thread to target
      - privacy_gates: PII constraints to enforce
      - trend_context: optional trend data (if available + include_trends=True)
      - output_spec: format instructions for the draft

    The caller (LLM / catch-up workflow) feeds this as context for in-context
    generation. No separate LLM call is made by this function.
    """
    state = _load_pr_state()
    thread_id = moment.primary_thread if moment else _pick_default_thread(platform)
    thread_name = _THREAD_NAMES.get(thread_id, thread_id)

    # Platform-specific rules
    platform_rules = _platform_rules(platform)

    # Trend context (optional, Phase 2+)
    trend_text = ""
    if include_trends and _TREND_SCAN_FILE.exists():
        try:
            trends = json.loads(_TREND_SCAN_FILE.read_text(encoding="utf-8"))
            if isinstance(trends, list) and trends:
                trend_text = "Trending topics (LinkedIn, this week): " + ", ".join(
                    t.get("topic", "") for t in trends[:3] if t.get("topic")
                )
        except Exception:  # noqa: BLE001
            pass

    # Build context
    ctx: dict[str, Any] = {
        "platform": platform,
        "platform_display": _PLATFORM_DISPLAY.get(platform, platform),
        "topic": topic or (moment.label if moment else "general"),
        "thread_id": thread_id,
        "thread_name": thread_name,
        "voice_profile": {
            "tone": "thoughtful-casual — warm, considered, not corporate",
            "language": "English primary; Hindi transliteration for cultural content",
            "perspective": "first-person (I)",
            "humor": "dry, subtle — occasional, never forced",
            "avoid": [
                "corporate buzzwords", "humble-bragging", "engagement bait",
                "emoji overload (max 2-3)", "generic AI filler phrases", "virtue signaling",
            ],
            "signature": [
                "cultural specificity (name the festival/tradition)",
                "concrete detail (specifics beat generalities)",
                "earned perspective (lived experience > abstract opinion)",
                "graceful brevity (fewer words than expected)",
                "generous attribution (credit others by name)",
            ],
        },
        "platform_rules": platform_rules,
        "privacy_gates": {
            "gate1_context_sanitization": [
                "NEVER include: financial data, immigration case details, health info, SSN/EIN, salary",
                "ALLOWED: first names, public relationships, cultural context, Microsoft/MBA (as employer/credential)",
            ],
            "childrens_privacy": _childrens_privacy_rules(platform),
            "employer_guard": {
                "rule": "Never imply speaking for Microsoft. Never share internal info.",
                "safe": ["general tech trends", "leadership insights", "industry commentary"],
                "forbidden": ["internal projects", "unreleased products", "MSFT stock", "competitor criticism"],
                "flag": "Add ⚠️ EMPLOYER MENTION if Microsoft named in post",
            },
        },
        "output_spec": {
            "variants": 1,
            "format": "plain text, ready to copy-paste",
            "include": ["post body", "suggested hashtags (0-3 max)", "optimal posting time"],
            "length_guide": platform_rules.get("length_guide", "150-300 words"),
        },
    }

    if moment:
        ctx["moment_context"] = {
            "label": moment.label,
            "date": moment.event_date,
            "type": moment.moment_type,
            "convergence_score": moment.convergence_score,
            "days_until": moment.days_until,
        }

    if trend_text:
        ctx["trend_context"] = trend_text

    # Voice profile overrides from state file
    body = _read_pr_body()
    overrides = _extract_section(body, "## Voice Profile Overrides")
    if overrides and "—" not in overrides:
        ctx["voice_profile_overrides"] = overrides.strip()

    return ctx


def _pick_default_thread(platform: str) -> str:
    """Pick sensible default thread when no moment is specified."""
    defaults = {
        "linkedin": "NT-1",
        "facebook": "NT-2",
        "instagram": "NT-3",
        "whatsapp_status": "NT-2",
    }
    return defaults.get(platform, "NT-1")


def _platform_rules(platform: str) -> dict:
    rules: dict[str, Any] = {
        "linkedin": {
            "audience": "Professional network, MBA alumni, tech industry",
            "tone": "Insightful, authoritative, approachable",
            "content_type": "Thought leadership, career milestones, industry commentary, cultural bridge",
            "optimal_frequency": "1-2x/week max",
            "best_time_pt": "Tue-Thu 8-10 AM",
            "length_guide": "150-250 words optimal; avoid >400 words",
            "hashtags": "0-3 only; professional; no trending hashtag spam",
            "emoji": "0 or 1 at most for thought-leadership pieces; 1-2 for cultural moments",
        },
        "facebook": {
            "audience": "Family (US + India), friends, community, MBA cohort",
            "tone": "Warm, personal, celebratory",
            "content_type": "Family milestones, cultural celebrations, life updates",
            "optimal_frequency": "2-4x/month",
            "best_time_pt": "Evenings and weekends",
            "length_guide": "80-150 words; shorter = better for social feed",
            "hashtags": "0-2 at most; rarely used on Facebook",
            "emoji": "2-4 ok; warm and expressive",
        },
        "instagram": {
            "audience": "Close friends (private account, ~269 followers)",
            "tone": "Authentic, visual, casual",
            "content_type": "Photos with stories (hiking, family, food, travel)",
            "optimal_frequency": "1-3x/month",
            "best_time_pt": "Fri-Sun evenings",
            "length_guide": "50-120 words; caption supplements photo",
            "hashtags": "5-10 relevant; niche > generic",
            "emoji": "2-5; casual and expressive",
            "note": "Private account — content should feel personal, not broadcast-y",
        },
        "whatsapp_status": {
            "audience": "All contacts (broad distribution — friends, family, colleagues)",
            "tone": "Informal, warm",
            "content_type": "Quick moments, festival greetings, family photos",
            "optimal_frequency": "Occasion-driven",
            "best_time_pt": "Context-dependent",
            "length_guide": "1-3 sentences max; or image-as-content",
            "hashtags": "None",
            "emoji": "2-5; warm and casual",
        },
    }
    return rules.get(platform, rules["linkedin"])


def _childrens_privacy_rules(platform: str) -> dict:
    rules_by_platform = {
        "linkedin": "NEVER name children. Reference as 'my son' or 'my daughter' only.",
        "facebook": "First names OK in friends-only posts (privacy: friends). Never full names.",
        "instagram": "First names OK (private account, curated followers). Never school name.",
        "whatsapp_status": "First names only — visible to all contacts.",
        "whatsapp_group": "Full names OK in family group only. First names in other groups.",
    }
    return {
        "rule": rules_by_platform.get(
            platform,
            "Use first names only. Never full name + school + location together.",
        ),
        "never": ["children's school name in public posts", "identifiable location of children"],
    }


# ---------------------------------------------------------------------------
# Post logging (Phase 3)
# ---------------------------------------------------------------------------

def log_post(
    platform: str,
    topic: str,
    thread_id: str,
    convergence_score: float = 0.0,
    notes: str = "",
) -> bool:
    """Append a post record to Post History and update platform_metrics.

    Safe: reads then atomically writes. Does NOT touch derived_snapshot.
    Phase 3+ only (requires Post History section in state file).
    """
    if not _PR_STATE_FILE.exists():
        return False

    today_str = date.today().isoformat()

    try:
        content = _PR_STATE_FILE.read_text(encoding="utf-8")
    except OSError:
        return False

    # Append row to Post History section (Phase 3+ section)
    post_row = (
        f"| {today_str} | {platform} | {thread_id} | {topic} "
        f"| {convergence_score:.2f} | — | {notes} |"
    )

    if "## Post History" in content:
        # Insert after header rows (find last | row in section and append after)
        pattern = re.compile(
            r"(## Post History\n(?:.*\n)*?"
            r"(?:\|[-| ]+\|\n))"  # separator row
        )
        match = pattern.search(content)
        if match:
            insert_pos = match.end()
            content = content[:insert_pos] + post_row + "\n" + content[insert_pos:]

    tmp = _PR_STATE_FILE.with_suffix(".tmp")
    try:
        import fcntl, os
        with open(tmp, "w", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
            except Exception:
                pass
            fh.write(content)
        os.replace(tmp, _PR_STATE_FILE)
        return True
    except Exception:  # noqa: BLE001
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return False


# ---------------------------------------------------------------------------
# Cache helpers (tmp/content_moments.json)
# ---------------------------------------------------------------------------

def _save_scored_moments(moments: list[ScoredMoment]) -> None:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    data = [m.as_dict() for m in moments]
    _CONTENT_MOMENTS_FILE.write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )


def _load_scored_moments_from_cache() -> list[ScoredMoment]:
    if not _CONTENT_MOMENTS_FILE.exists():
        return []
    try:
        data = json.loads(_CONTENT_MOMENTS_FILE.read_text(encoding="utf-8"))
        return [ScoredMoment(**d) for d in data]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Step 8 execution (catch-up hook)
# ---------------------------------------------------------------------------

def run_step8(
    occasions_file: Path | None = None,
    trends_file: Path | None = None,
    verbose: bool = False,
) -> list[ScoredMoment]:
    """Execute Step 8 moment detection.

    Reads:
      tmp/occasion_tracker_output.json  (if exists)
      tmp/trend_scan.json               (if exists + Monday only)

    Writes:
      tmp/content_moments.json
      state/pr_manager.md (Derived Snapshot)
    """
    config = _load_config()
    if not _is_enabled(config):
        if verbose:
            print("[pr_manager] Feature disabled (enhancements.pr_manager: false)")
        return []

    detector = MomentDetector()
    all_moments: list[ScoredMoment] = []

    # Load occasions from skill output
    occ_path = occasions_file or (_TMP_DIR / "occasion_tracker_output.json")
    if occ_path.exists():
        try:
            occasions_data = json.loads(occ_path.read_text(encoding="utf-8"))
            if isinstance(occasions_data, list):
                occasions = occasions_data
            elif isinstance(occasions_data, dict):
                occasions = occasions_data.get("occasions", [])
            else:
                occasions = []
            scored = detector.score_occasions(occasions)
            all_moments.extend(scored)
            if verbose:
                print(f"[pr_manager] Scored {len(scored)} occasions from {occ_path.name}")
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"[pr_manager] Warning: could not load occasions: {e}")

    # Load trends (Monday only or if explicitly provided)
    t_path = trends_file or _TREND_SCAN_FILE
    if t_path.exists():
        try:
            trends_data = json.loads(t_path.read_text(encoding="utf-8"))
            if isinstance(trends_data, list):
                trends = trends_data
            elif isinstance(trends_data, dict):
                trends = trends_data.get("trends", [])
            else:
                trends = []
            scored_trends = detector.score_from_trends(trends)
            all_moments.extend(scored_trends)
            if verbose:
                print(f"[pr_manager] Scored {len(scored_trends)} trends from {t_path.name}")
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"[pr_manager] Warning: could not load trends: {e}")

    # Deduplicate by label (keep highest score)
    seen: dict[str, ScoredMoment] = {}
    for m in all_moments:
        if m.label not in seen or m.convergence_score > seen[m.label].convergence_score:
            seen[m.label] = m
    unique_moments = sorted(seen.values(), key=lambda x: x.convergence_score, reverse=True)

    # Save to cache
    _save_scored_moments(unique_moments)

    # Compute derived snapshot values
    next_occasion_date = None
    for m in unique_moments:
        if m.days_until >= 0:
            next_occasion_date = m.event_date
            break

    pending_count = _count_pending_drafts(_read_pr_body())

    # Write derived snapshot
    ok = write_derived_snapshot(next_occasion_date, pending_count)
    if verbose:
        print(
            f"[pr_manager] Derived snapshot written: "
            f"next_occasion={next_occasion_date}, "
            f"pending_drafts={pending_count}, ok={ok}"
        )

    if verbose:
        above_daily = [m for m in unique_moments if m.above_daily_threshold]
        above_weekly = [m for m in unique_moments if m.above_weekly_threshold]
        print(
            f"[pr_manager] Step 8 complete: {len(unique_moments)} moments scored, "
            f"{len(above_daily)} high-opportunity (≥0.8), "
            f"{len(above_weekly)} weekly calendar (≥0.6)"
        )

    return unique_moments


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def run_health_check() -> tuple[bool, list[str]]:
    """Validate PR Manager state and config. Returns (ok, issues)."""
    issues: list[str] = []

    # State file
    if not _PR_STATE_FILE.exists():
        issues.append("MISSING: state/pr_manager.md — run catch-up to generate")
    else:
        fm = _parse_frontmatter(_PR_STATE_FILE)
        if not fm:
            issues.append("WARN: state/pr_manager.md frontmatter could not be parsed")
        else:
            schema = fm.get("schema_version")
            if schema != "1.0":
                issues.append(f"WARN: schema_version={schema} (expected 1.0)")
            if "derived_snapshot" not in fm:
                issues.append("WARN: derived_snapshot missing from frontmatter — will be written on next Step 8")

    # Config flag
    cfg = _load_config()
    enabled = _is_enabled(cfg)
    if not enabled:
        issues.append("INFO: enhancements.pr_manager is disabled — activate via artha_config.yaml")

    # Pattern engine entries
    patterns_file = _CONFIG_DIR / "patterns.yaml"
    if patterns_file.exists():
        try:
            pdata = yaml.safe_load(patterns_file.read_text(encoding="utf-8")) or {}
            pattern_ids = {p.get("id") for p in pdata.get("patterns", [])}
            for pid in ("PAT-PR-001", "PAT-PR-002"):
                if pid not in pattern_ids:
                    issues.append(f"MISSING: {pid} not found in config/patterns.yaml")
        except Exception:  # noqa: BLE001
            issues.append("WARN: config/patterns.yaml could not be parsed")

    # tmp dir
    _TMP_DIR.mkdir(parents=True, exist_ok=True)

    ok = not any(i.startswith(("MISSING", "ERROR")) for i in issues)
    return ok, issues


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pr_manager.py",
        description="Artha PR Manager — Personal Narrative Engine (PR-1 v1.2)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--step8", action="store_true", help="Run Step 8 moment detection")
    group.add_argument("--view", action="store_true", help="Render /pr content calendar")
    group.add_argument("--threads", action="store_true", help="Render /pr threads view")
    group.add_argument("--voice", action="store_true", help="Render /pr voice view")
    group.add_argument(
        "--draft-context",
        metavar="PLATFORM",
        help="Emit draft context JSON for a platform (e.g. linkedin)",
    )
    group.add_argument(
        "--log-post",
        nargs="+",
        metavar="ARG",
        help="Log a post: PLATFORM TOPIC THREAD_ID [SCORE] [NOTES]",
    )
    group.add_argument("--check", action="store_true", help="Health check")

    parser.add_argument("--topic", help="Optional topic for --draft-context")
    parser.add_argument("--trending", action="store_true", help="Include trend context in draft")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args(argv)

    if args.step8:
        moments = run_step8(verbose=args.verbose)
        # Output JSON summary for catch-up workflow consumption
        output = {
            "moments_scanned": len(moments),
            "above_daily_threshold": sum(1 for m in moments if m.above_daily_threshold),
            "above_weekly_threshold": sum(1 for m in moments if m.above_weekly_threshold),
            "top_moment": moments[0].as_dict() if moments else None,
        }
        print(json.dumps(output, indent=2, default=str))
        return 0

    if args.view:
        moments = _load_scored_moments_from_cache()
        print(render_pr_overview(moments))
        return 0

    if args.threads:
        print(render_thread_progress())
        return 0

    if args.voice:
        print(render_voice_profile())
        return 0

    if args.draft_context:
        platform = args.draft_context.lower().replace("-", "_")
        moments = _load_scored_moments_from_cache()
        # Find best moment for this platform
        moment = next(
            (m for m in moments if platform in m.platforms and m.above_weekly_threshold),
            None,
        )
        ctx = assemble_draft_context(
            platform=platform,
            topic=args.topic,
            moment=moment,
            include_trends=args.trending,
        )
        print(json.dumps(ctx, indent=2, default=str))
        return 0

    if args.log_post:
        lp_args = args.log_post
        if len(lp_args) < 3:
            print("Usage: --log-post PLATFORM TOPIC THREAD_ID [SCORE] [NOTES]", file=sys.stderr)
            return 1
        platform_arg = lp_args[0]
        topic_arg = lp_args[1]
        thread_arg = lp_args[2]
        score_arg = float(lp_args[3]) if len(lp_args) > 3 else 0.0
        notes_arg = " ".join(lp_args[4:]) if len(lp_args) > 4 else ""
        ok = log_post(platform_arg, topic_arg, thread_arg, score_arg, notes_arg)
        print(f"Post logged: {'✅' if ok else '❌'}")
        return 0 if ok else 1

    if args.check:
        ok, issues = run_health_check()
        if issues:
            for issue in issues:
                print(issue)
        if ok:
            print("✅ PR Manager health check passed")
        else:
            print("❌ PR Manager health check found issues")
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
