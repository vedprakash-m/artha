"""scripts/skills/ai_trend_radar.py — AI Trend Radar Skill (PR-3).

INGEST → DISTILL stage of the AI Trend Radar pipeline.

Reads AI newsletter emails + RSS feed items from the pipeline JSONL output,
applies keyword-based relevance scoring with Topic Interest Graph boosts,
deduplicates across sources and weeks, ranks signals, and writes:
  - tmp/ai_trend_signals.json      (current week's ranked signals)
  - tmp/ai_trend_signals_prev.json (previous week, for cross-week dedup)
  - tmp/ai_trend_metrics.json      (run metrics for PAT-PR-004 health check)

Also scans experiments in state/ai_trend_radar.md for completed experiments
(status: done, verdict: great|useful, moment_emitted: false) and emits
ScoredMoment objects that flow into the Content Stage pipeline.

Design constraints (per specs/ai-posts.md §2):
  - DP-3: Deterministic only — no LLM calls in this stage
  - DP-6: Employer safety gate — blocked terms from user_profile.yaml
  - DP-8: Zero new dependencies — stdlib only
  - BaseSkill.pull() takes NO arguments (ARCH-1)

Ref: specs/ai-posts.md PR-3 v1.0.6
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .base_skill import BaseSkill

_log = logging.getLogger("ai_trend_radar")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WARM_START_TIMELINESS_CUTOFF_DAYS = 14
_WARM_START_TIMELINESS_PENALTY = 0.3
_CROSS_WEEK_DEMOTION_FACTOR = 0.5

# Relevance scoring table (§5.3)
_SCORE_TRYABLE_ARTIFACT = 0.30    # CLI tool, API, library, extension
_SCORE_EMPLOYER_STACK = 0.20      # Azure, Microsoft, cloud
_SCORE_HOWTO = 0.20               # how-to, tip, tutorial
_SCORE_MODEL_RELEASE = 0.15       # GPT, Claude, Gemini, Llama, Qwen
_SCORE_OPEN_SOURCE = 0.10         # GitHub-linked project
_SCORE_MULTI_SOURCE = 0.10        # seen_in >= 2
_SCORE_TOPIC_MATCH = 0.25         # Interest Graph topic match (user-configured = high intent)
_SCORE_COMMUNITY_HIGH = 0.15      # HN score>100, dev.to reactions>50, GitHub stars>500
_SCORE_COMMUNITY_MED = 0.05       # HN score>50, dev.to reactions>20, GitHub stars>100
_PENALTY_ACADEMIC = -0.20         # research paper only
_PENALTY_ENTERPRISE = -0.10       # enterprise-only
_PENALTY_HARDWARE = -0.10         # hardware/datacenter/policy

# Patterns for relevance scoring
_PAT_TRYABLE = re.compile(
    r"\b(cli|command.line|library|sdk|package|api|extension|plugin|tool|app"
    r"|install|pip install|npm install|brew install|launch|release|v\d+\.\d+)\b",
    re.IGNORECASE,
)
_PAT_EMPLOYER = re.compile(r"\b(azure|microsoft|copilot|m365|office 365)\b", re.IGNORECASE)
_PAT_HOWTO = re.compile(
    r"\b(how to|how-to|tutorial|guide|tips?|technique|walkthrough|step.by.step"
    r"|getting started|quickstart)\b",
    re.IGNORECASE,
)
_PAT_MODEL = re.compile(
    # Versioned forms first, then bare company/model names (for newsletter subject matching)
    r"\b(gpt-?\d|gpt-5|gpt-4|claude(?:[\s-]\d)?|gemini(?:\s?\d)?|llama[\s-]\d"
    r"|qwen[\s-]?\d|mistral|deepseek|perplexity|openai|anthropic|xai|grok"
    r"|o\d-model|o1|o3)\b",
    re.IGNORECASE,
)
_PAT_GITHUB = re.compile(r"github\.com/[\w\-]+/[\w\-]+", re.IGNORECASE)
_PAT_PAPER_ONLY = re.compile(
    r"\b(arxiv|preprint|paper|research paper|abstract)\b", re.IGNORECASE
)
_PAT_ENTERPRISE = re.compile(
    r"\b(enterprise|org.level|corporate.deployment|organization-wide|b2b"
    r"|microsoft 365 business)\b",
    re.IGNORECASE,
)
_PAT_HARDWARE = re.compile(
    r"\b(data center|datacenter|chip|gpu.supply|server.farm|policy|regulation"
    r"|compliance|legislation)\b",
    re.IGNORECASE,
)
_PAT_OPINION = re.compile(
    r"\b(quoting|says\b|argues|claims|thinks|believes|contends|reckons"
    r"|commentary|in my view|perspective|thoughts on|my take|hot take"
    r"|essay|musing|reflection|rant|thread|lot of fun)\b",
    re.IGNORECASE,
)
_PENALTY_OPINION = -0.15           # opinion-only (no artifact evidence)

# Hands-on artifact evidence — used to gate topic bonus on opinion content
def _has_artifact_evidence(text: str) -> bool:
    """True if the text contains concrete hands-on signals (install, howto, GitHub)."""
    return bool(
        _PAT_TRYABLE.search(text)
        or _PAT_HOWTO.search(text)
        or _PAT_GITHUB.search(text)
    )

# Category detection
_PAT_CAT_TOOL = re.compile(
    r"\b(releases?|launches?|cli|command.line|install|extension|plugin"
    r"|v\d+\.\d+|new.version)\b",
    re.IGNORECASE,
)
_PAT_CAT_TECHNIQUE = re.compile(
    r"\b(how to|how-to|technique|tip|trick|pattern|method|approach|strategy"
    r"|prompt engineering|fine.tun)\b",
    re.IGNORECASE,
)
_PAT_CAT_MODEL = re.compile(
    r"\b(model release|new model|gpt-\d|claude.?\d|gemini.?\d|llama.?\d"
    r"|qwen.?\d|mistral.?\d)\b",
    re.IGNORECASE,
)
_PAT_CAT_RESEARCH = re.compile(r"\b(paper|research|arxiv|study|benchmark)\b", re.IGNORECASE)
_PAT_CAT_TUTORIAL = re.compile(
    r"\b(tutorial|guide|walkthrough|course|getting started|quickstart)\b", re.IGNORECASE
)
_PAT_CAT_FRAMEWORK = re.compile(
    r"\b(framework|library|sdk|platform|update|upgrade|version)\b", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AISignal:
    """A ranked AI signal extracted from newsletters + RSS feeds."""

    id: str                      # SHA-256(topic_normalized) — source-independent
    topic: str
    category: str                # tool_release | technique | model_release | research | tutorial | framework_update
    sources: list[str]           # all contributing source tags
    best_source_url: str
    summary: str                 # ≤200 chars
    detected_at: str             # ISO date of first detection
    relevance_score: float       # 0.0–1.0 after clamping
    try_worthy: bool
    seen_in: int                 # number of distinct sources
    topic_match: str | None      # matched Interest Graph topic name
    warm_start: bool = False     # True when signal came from warm-start corpus

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AISignal":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _signal_id(topic: str) -> str:
    """Stable SHA-256 based signal ID from normalised topic string (ARCH-2)."""
    normalised = re.sub(r"\s+", " ", topic.lower().strip())
    return hashlib.sha256(normalised.encode()).hexdigest()[:12]


def _detect_category(text: str) -> str:
    if _PAT_CAT_MODEL.search(text):
        return "model_release"
    if _PAT_CAT_FRAMEWORK.search(text):
        return "framework_update"
    if _PAT_CAT_TOOL.search(text):
        return "tool_release"
    if _PAT_CAT_TUTORIAL.search(text):
        return "tutorial"
    if _PAT_CAT_TECHNIQUE.search(text):
        return "technique"
    if _PAT_CAT_RESEARCH.search(text):
        return "research"
    return "technique"  # default for anything that doesn't clearly fit


def _score_signal(text: str) -> float:
    score = 0.0
    if _PAT_TRYABLE.search(text):
        score += _SCORE_TRYABLE_ARTIFACT
    if _PAT_EMPLOYER.search(text):
        score += _SCORE_EMPLOYER_STACK
    if _PAT_HOWTO.search(text):
        score += _SCORE_HOWTO
    if _PAT_MODEL.search(text):
        score += _SCORE_MODEL_RELEASE
    if _PAT_GITHUB.search(text):
        score += _SCORE_OPEN_SOURCE
    if _PAT_PAPER_ONLY.search(text) and not _PAT_GITHUB.search(text):
        score += _PENALTY_ACADEMIC
    if _PAT_ENTERPRISE.search(text):
        score += _PENALTY_ENTERPRISE
    if _PAT_HARDWARE.search(text):
        score += _PENALTY_HARDWARE
    # Opinion penalty: only applied when no hands-on artifact evidence exists
    if _PAT_OPINION.search(text) and not _has_artifact_evidence(text):
        score += _PENALTY_OPINION
    return score


def _is_try_worthy(signal: AISignal, try_worthy_threshold: float) -> bool:
    if signal.relevance_score < try_worthy_threshold:
        return False
    # Inherently hands-on categories — auto-qualify
    if signal.category in ("tool_release", "tutorial", "framework_update"):
        return True
    # "technique" requires hands-on evidence in topic+summary (not just opinions)
    if signal.category == "technique":
        text = f"{signal.topic} {signal.summary}"
        return _has_artifact_evidence(text)
    # Research needs a linked artifact (GitHub)
    if signal.category == "research":
        return bool(_PAT_GITHUB.search(signal.summary))
    return False


def _apply_topic_boost(
    text: str,
    topics: list[dict],
) -> tuple[float, str | None]:
    """Max-wins boost from Topic Interest Graph (§9.3). Returns (boost, matched_topic_name)."""
    best_boost = 0.0
    best_topic: str | None = None
    text_lower = text.lower()
    for topic_row in topics:
        for keyword in topic_row.get("keywords", []):
            if keyword.lower() in text_lower:
                if topic_row.get("boost", 0.0) > best_boost:
                    best_boost = topic_row["boost"]
                    best_topic = topic_row["name"]
                break
    return best_boost, best_topic


def _extract_topic_from_item(item: dict) -> str:
    """Extract a topic string from a pipeline item (email or RSS)."""
    # Prefer subject for emails, title for RSS
    topic = item.get("subject") or item.get("title") or ""
    # Truncate subject to ~80 chars to prevent hash instability from long senders
    return topic[:100].strip()


def _extract_summary(item: dict) -> str:
    """Extract ≤200 char summary from a pipeline item."""
    body = item.get("body") or item.get("description") or item.get("summary") or ""
    # Strip HTML tags rudimentarily
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:200]


def _is_ai_relevant(text: str, relevance_keywords: list[str]) -> bool:
    """Quick gate: does this item mention at least one AI relevance keyword?"""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in relevance_keywords)


def _parse_state_frontmatter(state_path: Path) -> dict:
    """Parse YAML frontmatter from a Markdown state file."""
    try:
        import yaml
        text = state_path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}
        return yaml.safe_load(parts[1]) or {}
    except Exception as e:
        _log.warning("Failed to parse state frontmatter %s: %s", state_path, e)
        return {}


def _write_state_frontmatter(state_path: Path, data: dict) -> None:
    """Write updated YAML frontmatter back to a Markdown state file."""
    try:
        import yaml
        text = state_path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            _log.warning("State file has no valid frontmatter block: %s", state_path)
            return
        body = parts[2]
        new_fm = yaml.safe_dump(data, default_flow_style=False, allow_unicode=True)
        state_path.write_text("---\n" + new_fm + "---" + body, encoding="utf-8")
    except Exception as e:
        _log.error("Failed to write state frontmatter %s: %s", state_path, e)


# ---------------------------------------------------------------------------
# AITrendRadarSkill
# ---------------------------------------------------------------------------

class AITrendRadarSkill(BaseSkill):
    """Distills AI signals from newsletters and RSS feeds.

    Reads pipeline JSONL for newsletter-tagged emails + RSS items.
    Applies keyword extraction, topic interest boost, dedup.
    Outputs ranked signals to tmp/ai_trend_signals.json.

    Warm-start mode: if meta.warm_start_file is set in state/ai_trend_radar.md,
    reads from that JSONL instead of the live pipeline output (one-shot, §4.3).
    """

    def __init__(self, artha_dir: Path) -> None:
        super().__init__(name="ai_trend_radar", priority="P2")
        self._artha_dir = artha_dir
        self._state_file = artha_dir / "state" / "ai_trend_radar.md"
        self._signals_file = artha_dir / "tmp" / "ai_trend_signals.json"
        self._prev_signals_file = artha_dir / "tmp" / "ai_trend_signals_prev.json"
        self._metrics_file = artha_dir / "tmp" / "ai_trend_metrics.json"
        self._config: dict = {}
        self._state_data: dict = {}
        self._run_metrics: dict = {}

    # ── Config helpers ────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        """Load ai_trend_radar config block from artha_config.yaml."""
        try:
            from lib.config_loader import load_config  # noqa: PLC0415
            raw = load_config("artha_config")
            return (
                raw.get("enhancements", {})
                   .get("pr_manager", {})
                   .get("ai_trend_radar", {})
            )
        except Exception as e:
            _log.warning("Could not load artha_config.yaml: %s", e)
            return {}

    def _load_employer_blocked_terms(self) -> frozenset:
        """Load employer blocked terms from user_profile.yaml (never hardcoded, DP-6)."""
        try:
            from lib.config_loader import load_config  # noqa: PLC0415
            raw = load_config("user_profile", str(self._artha_dir / "config"))
            terms = raw.get("employment", {}).get("confidential_terms", []) or []
            return frozenset(t.lower() for t in terms if t)
        except Exception:
            return frozenset()

    # ── Pull ──────────────────────────────────────────────────────────────

    def pull(self) -> dict[str, Any]:
        """Collect raw newsletter emails + RSS items.

        Returns dict with keys:
          items: list[dict]       — raw pipeline records
          warm_start: bool        — True if reading from armed JSONL
          config: dict            — radar config block
          state: dict             — parsed state frontmatter
          blocked_terms: frozenset
        """
        self._config = self._load_config()
        self._state_data = _parse_state_frontmatter(self._state_file)
        blocked_terms = self._load_employer_blocked_terms()

        newsletter_senders = set(
            s.lower() for s in self._config.get("newsletter_senders", [])
        )
        relevance_keywords = self._config.get("relevance_keywords", [])

        warm_start_file = (self._state_data.get("meta") or {}).get("warm_start_file") or ""
        is_warm_start = bool(warm_start_file)

        items: list[dict] = []

        if is_warm_start:
            ws_path = Path(warm_start_file)
            if not ws_path.is_absolute():
                ws_path = self._artha_dir / ws_path
            _log.info("Warm-start mode: reading from %s", ws_path)
            items = self._read_jsonl(ws_path, newsletter_senders, relevance_keywords)
        else:
            # Normal mode: read from live pipeline JSONL output
            pipeline_dir = self._artha_dir / "tmp"
            items = self._read_pipeline_output(pipeline_dir, newsletter_senders, relevance_keywords)

        return {
            "items": items,
            "warm_start": is_warm_start,
            "warm_start_file": warm_start_file,
            "config": self._config,
            "state": self._state_data,
            "blocked_terms": blocked_terms,
        }

    def _read_jsonl(
        self, path: Path, newsletter_senders: set, relevance_keywords: list
    ) -> list[dict]:
        """Read JSONL file and filter to AI-relevant newsletter + RSS items."""
        if not path.exists():
            _log.warning("JSONL path does not exist: %s", path)
            return []
        items = []
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if self._is_relevant_item(record, newsletter_senders, relevance_keywords):
                        items.append(record)
        except OSError as e:
            _log.error("Could not read JSONL %s: %s", path, e)
        return items

    def _read_pipeline_output(
        self, tmp_dir: Path, newsletter_senders: set, relevance_keywords: list
    ) -> list[dict]:
        """Scan tmp/ for pipeline JSONL outputs and filter AI-relevant items."""
        items = []
        if not tmp_dir.exists():
            return items
        for jsonl_file in sorted(tmp_dir.glob("*.jsonl")):
            items.extend(self._read_jsonl(jsonl_file, newsletter_senders, relevance_keywords))
        return items

    def _is_relevant_item(
        self, record: dict, newsletter_senders: set, relevance_keywords: list
    ) -> bool:
        """True if this pipeline record is an AI-relevant newsletter, RSS, or API discovery item."""
        source = record.get("source", "").lower()
        from_addr = record.get("from", "").lower()
        marketing_cat = record.get("marketing_category", "").lower()

        is_rss = source == "rss" or record.get("feed_url") is not None
        is_newsletter = (
            marketing_cat == "newsletter"
            or any(sender in from_addr for sender in newsletter_senders)
        )
        # API discovery items are pre-filtered for AI relevance by the connector
        is_api_discovery = source == "api_discovery"

        if not (is_rss or is_newsletter or is_api_discovery):
            return False

        # API discovery items already passed AI keyword filtering at source
        if is_api_discovery:
            return True

        # AI relevance gate
        text = " ".join(filter(None, [
            record.get("subject", ""),
            record.get("title", ""),
            record.get("body", "")[:500],
        ]))
        return _is_ai_relevant(text, relevance_keywords)

    # ── Parse ─────────────────────────────────────────────────────────────

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Extract, score, deduplicate, and rank AI signals. Also emit ScoredMoments."""
        items: list[dict] = raw_data["items"]
        is_warm_start: bool = raw_data["warm_start"]
        warm_start_file: str = raw_data.get("warm_start_file", "")
        config: dict = raw_data["config"]
        state: dict = raw_data["state"]
        blocked_terms: frozenset = raw_data["blocked_terms"]

        relevance_keywords = config.get("relevance_keywords", [])
        topics = state.get("topics_of_interest", []) or []
        try_worthy_threshold = config.get("try_worthy_threshold", 0.6)
        surface_threshold = config.get("surface_threshold", 0.5)
        max_signals = config.get("max_signals_per_week", 5)

        # --- Build signal index ---
        signal_index: dict[str, AISignal] = {}
        raw_count = len(items)
        filtered_count = 0
        empty_feeds: set[str] = set()

        # Track which feed tags produced items
        seen_feed_tags: set[str] = set()
        for item in items:
            tag = item.get("tag") or item.get("source_tag") or item.get("source", "")
            if tag:
                seen_feed_tags.add(tag)

        # Check configured RSS feeds for empties
        for feed in config.get("newsletter_senders", []):
            pass  # newsletter senders are checked differently
        rss_cfg_path = self._artha_dir / "config" / "connectors.yaml"
        configured_feed_tags = self._get_configured_feed_tags(rss_cfg_path)
        empty_feeds = configured_feed_tags - seen_feed_tags

        for item in items:
            topic = _extract_topic_from_item(item)
            if not topic:
                continue

            # Employer safety gate (DP-6)
            full_text = " ".join(filter(None, [
                topic,
                item.get("body", "")[:500],
                item.get("description", "")[:500],
            ])).lower()
            if any(term in full_text for term in blocked_terms):
                _log.info("RADAR_EMPLOYER_BLOCKED | topic=%s", topic[:60])
                continue

            filtered_count += 1
            sig_id = _signal_id(topic)
            source_tag = (
                item.get("tag")
                or item.get("source_tag")
                or item.get("source", "")
                or (item.get("from", "").split("@")[-1].split(".")[0] if item.get("from") else "")
                or "unknown"
            )
            url = item.get("link") or item.get("feed_url") or item.get("url") or ""
            detected = item.get("date_iso") or item.get("date") or date.today().isoformat()
            text_for_scoring = " ".join(filter(None, [topic, _extract_summary(item)]))

            if sig_id in signal_index:
                # Merge duplicate (same topic from another source)
                existing = signal_index[sig_id]
                if source_tag not in existing.sources:
                    existing.sources.append(source_tag)
                existing.seen_in += 1
                # Keep earliest detection date
                if detected < existing.detected_at:
                    existing.detected_at = detected
                    existing.best_source_url = url or existing.best_source_url
            else:
                base_score = _score_signal(text_for_scoring)
                boost, topic_match = _apply_topic_boost(text_for_scoring, topics)
                # Topic match bonus only applies when the signal has hands-on
                # evidence — prevents opinion pieces from riding topic keywords
                # into try-worthy territory.
                has_artifact = _has_artifact_evidence(text_for_scoring)
                topic_match_bonus = _SCORE_TOPIC_MATCH if (topic_match and has_artifact) else 0.0

                # Community validation bonus (HN score, dev.to reactions, GH stars)
                community = item.get("community_score", 0)
                if community >= 100:
                    community_bonus = _SCORE_COMMUNITY_HIGH
                elif community >= 30:
                    community_bonus = _SCORE_COMMUNITY_MED
                else:
                    community_bonus = 0.0

                signal = AISignal(
                    id=sig_id,
                    topic=topic[:120],
                    category=_detect_category(text_for_scoring),
                    sources=[source_tag] if source_tag else [],
                    best_source_url=url,
                    summary=_extract_summary(item),
                    detected_at=detected,
                    relevance_score=base_score + boost + topic_match_bonus + community_bonus,
                    try_worthy=False,  # computed after merge
                    seen_in=1,
                    topic_match=topic_match,
                    warm_start=is_warm_start,
                )
                signal_index[sig_id] = signal

        # --- Post-processing pass ---
        # Add multi-source bonus, clamp scores, set try_worthy
        for sig in signal_index.values():
            if sig.seen_in >= 2:
                sig.relevance_score += _SCORE_MULTI_SOURCE
            sig.relevance_score = max(0.0, min(1.0, sig.relevance_score))
            sig.try_worthy = _is_try_worthy(sig, try_worthy_threshold)

        deduped_count = len(signal_index)

        # --- Cross-week demotion ---
        prev_signal_ids = self._load_prev_signal_ids()
        existing_experiment_signal_ids = {
            exp.get("signal_id", "") for exp in (state.get("experiments") or [])
        }
        final_signals: list[AISignal] = []
        for sig in signal_index.values():
            if sig.id in existing_experiment_signal_ids:
                continue  # Already in experiment — don't re-surface
            if sig.id in prev_signal_ids and not sig.warm_start:
                sig.relevance_score *= _CROSS_WEEK_DEMOTION_FACTOR

            # Warm-start timeliness penalty (§4.3)
            # Store pre-penalty score for surface threshold check — penalty is
            # for ranking/ordering only, not for filtering during warm-start.
            pre_penalty_score = sig.relevance_score
            if is_warm_start and sig.detected_at:
                try:
                    detected_date = date.fromisoformat(sig.detected_at[:10])
                    age_days = (date.today() - detected_date).days
                    if age_days > _WARM_START_TIMELINESS_CUTOFF_DAYS:
                        sig.relevance_score *= _WARM_START_TIMELINESS_PENALTY
                except (ValueError, TypeError):
                    pass

            sig.relevance_score = max(0.0, min(1.0, sig.relevance_score))
            check_score = pre_penalty_score if is_warm_start else sig.relevance_score
            # During warm-start, use a lower threshold (historical subjects score lower)
            ws_surface_threshold = config.get(
                "warm_start_surface_threshold",
                surface_threshold * _WARM_START_TIMELINESS_PENALTY,
            )
            effective_threshold = ws_surface_threshold if is_warm_start else surface_threshold
            if check_score >= effective_threshold:
                final_signals.append(sig)

        # Sort by relevance descending, cap at max_signals
        final_signals.sort(key=lambda s: s.relevance_score, reverse=True)
        surfaced = final_signals[:max_signals]
        surfaced_count = len(surfaced)

        # --- Organic topic discovery (§9.4) ---
        organic_suggestions: list[str] = []
        topic_names = {t.get("name", "").lower() for t in topics}
        for sig in surfaced:
            if sig.topic_match is None and sig.relevance_score > 0.7:
                top_word = sig.topic.split()[0] if sig.topic.split() else ""
                if top_word.lower() not in topic_names:
                    organic_suggestions.append(
                        f'💡 New topic? "{sig.topic[:40]}" appeared in '
                        f'{sig.seen_in} source(s). /radar topic add "{sig.topic[:40]}"'
                    )

        # --- Write output files ---
        self._rotate_signals_file()
        self._write_signals_file(surfaced, surfaced_count)
        self._write_metrics_file(
            raw_count=raw_count,
            filtered_count=filtered_count,
            deduped_count=deduped_count,
            surfaced_count=surfaced_count,
            empty_feeds=sorted(empty_feeds),
            warm_start_active=is_warm_start,
        )

        # --- Warm-start lifecycle: one-shot cleanup (§4.3) ---
        if is_warm_start and warm_start_file:
            self._complete_warm_start(state, warm_start_file)

        # --- Emit ScoredMoments for completed experiments (§8.1) ---
        scored_moments = self._emit_experiment_moments(state)

        # --- Audit log ---
        _log.info(
            "RADAR_DISTILL_RUN | raw=%d | filtered=%d | deduped=%d | surfaced=%d"
            " | feeds_empty=%s | duration_ms=N/A | warm_start=%s",
            raw_count, filtered_count, deduped_count, surfaced_count,
            sorted(empty_feeds), is_warm_start,
        )

        self._run_metrics = {
            "raw_count": raw_count,
            "filtered_count": filtered_count,
            "deduped_count": deduped_count,
            "surfaced_count": surfaced_count,
            "empty_feeds": sorted(empty_feeds),
        }

        return {
            "signals": [s.to_dict() for s in surfaced],
            "organic_suggestions": organic_suggestions,
            "scored_moments": [m.as_dict() if hasattr(m, "as_dict") else m for m in scored_moments],
            "warm_start": is_warm_start,
            "metrics": self._run_metrics,
        }

    # ── ScoredMoment emission ─────────────────────────────────────────────

    def _emit_experiment_moments(self, state: dict) -> list:
        """Scan experiments and emit ScoredMoments for done/great|useful experiments
        where moment_emitted is False (§8.1). Sets moment_emitted=True after emission."""
        try:
            # Import ScoredMoment from pr_manager — graceful if unavailable
            scripts_dir = self._artha_dir / "scripts"
            if str(scripts_dir) not in sys.path:
                sys.path.insert(0, str(scripts_dir))
            from pr_manager import ScoredMoment, _MOMENT_WEIGHTS  # type: ignore
        except ImportError:
            _log.warning("pr_manager not importable; moment emission skipped")
            return []

        experiments = state.get("experiments") or []
        moments = []
        state_modified = False

        for exp in experiments:
            if exp.get("status") != "done":
                continue
            verdict = exp.get("verdict", "pending")
            if verdict not in ("great", "useful"):
                continue
            if exp.get("moment_emitted", False):
                continue  # Already emitted — skip (GAP-2 guard)

            completed_date = exp.get("completed_date")
            if not completed_date:
                _log.warning("Experiment %s missing completed_date — skipping moment", exp.get("id"))
                continue

            try:
                event_date_obj = date.fromisoformat(str(completed_date))
            except (ValueError, TypeError):
                _log.warning("Experiment %s has invalid completed_date: %s", exp.get("id"), completed_date)
                continue

            days_until = (event_date_obj - date.today()).days
            signal_weight = _MOMENT_WEIGHTS.get("ai_experiment_complete", 0.85)
            signal_magnitude = 1.0 if verdict == "great" else 0.7
            timeliness = max(0.0, 1.0 - abs(days_until) * 0.1)  # recency matters
            relevance = 1.0  # NT-1 primary match
            convergence = signal_weight * relevance * signal_magnitude * timeliness

            try:
                moment = ScoredMoment(
                    moment_type="ai_experiment_complete",
                    label=f"Tried: {exp.get('topic', 'AI experiment')}",
                    event_date=completed_date,
                    days_until=days_until,
                    signal_weight=signal_weight,
                    relevance=relevance,
                    signal_magnitude=signal_magnitude,
                    timeliness=timeliness,
                    convergence_score=round(convergence, 4),
                    primary_thread="NT-1",
                    alt_threads=["NT-5"],
                    platforms=["linkedin"],
                    above_daily_threshold=convergence >= 0.8,
                    above_weekly_threshold=convergence >= 0.6,
                    source="ai_trend_radar",
                )
                moments.append(moment)
                # Mark as emitted (GAP-2)
                exp["moment_emitted"] = True
                state_modified = True
                _log.info(
                    "RADAR_CARD_SEEDED | exp=%s | topic=%s | verdict=%s | score=%.2f",
                    exp.get("id", "?"), exp.get("topic", "?")[:40], verdict, convergence,
                )
            except Exception as e:
                _log.error("Failed to create ScoredMoment for %s: %s", exp.get("id"), e)

        # Persist moment_emitted flag back to state
        if state_modified:
            _write_state_frontmatter(self._state_file, state)

        return moments

    # ── File I/O helpers ──────────────────────────────────────────────────

    def _rotate_signals_file(self) -> None:
        """Rename current signals JSON to _prev before overwriting (§5.6)."""
        if self._signals_file.exists():
            try:
                os.replace(self._signals_file, self._prev_signals_file)
            except OSError as e:
                _log.warning("Could not rotate signals file: %s", e)

    def _write_signals_file(self, signals: list[AISignal], surfaced_count: int) -> None:
        """Write ranked signals to tmp/ai_trend_signals.json."""
        self._signals_file.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        payload = {
            "generated_at": now.isoformat(),
            "week_start": week_start,
            "week_end": date.today().isoformat(),
            "signal_count": surfaced_count,
            "signals": [s.to_dict() for s in signals],
        }
        try:
            self._signals_file.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            _log.error("Could not write signals file: %s", e)

    def _write_metrics_file(
        self,
        *,
        raw_count: int,
        filtered_count: int,
        deduped_count: int,
        surfaced_count: int,
        empty_feeds: list[str],
        warm_start_active: bool,
    ) -> None:
        """Write runtime metrics to tmp/ai_trend_metrics.json (for PAT-PR-004)."""
        self._metrics_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_run_at": datetime.now(timezone.utc).isoformat(),
            "last_run_status": "ok",
            "raw_count": raw_count,
            "filtered_count": filtered_count,
            "deduped_count": deduped_count,
            "surfaced_count": surfaced_count,
            "empty_feeds": empty_feeds,
            "warm_start_active": warm_start_active,
        }
        try:
            self._metrics_file.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            _log.error("Could not write metrics file: %s", e)

    def _load_prev_signal_ids(self) -> set[str]:
        """Load signal IDs from previous week for cross-week demotion."""
        if not self._prev_signals_file.exists():
            return set()
        try:
            data = json.loads(self._prev_signals_file.read_text(encoding="utf-8"))
            return {s["id"] for s in data.get("signals", []) if "id" in s}
        except (OSError, json.JSONDecodeError, KeyError):
            return set()

    def _get_configured_feed_tags(self, connectors_path: Path) -> set[str]:
        """Extract configured RSS feed tags from connectors.yaml."""
        try:
            from lib.config_loader import load_config  # noqa: PLC0415
            data = load_config("connectors", str(connectors_path.parent))
            feeds = (
                data.get("rss_feed", {})
                .get("fetch", {})
                .get("feeds") or []
            )
            return {f["tag"] for f in feeds if isinstance(f, dict) and "tag" in f}
        except Exception:
            return set()

    def _complete_warm_start(self, state: dict, warm_start_file: str) -> None:
        """One-shot warm-start completion (§4.3): clear flag, set timestamp, rename file."""
        ws_path = Path(warm_start_file)
        if not ws_path.is_absolute():
            ws_path = self._artha_dir / ws_path

        # 1. Rename JSONL to .processed (non-destructive)
        if ws_path.exists():
            processed = ws_path.with_suffix(".processed.jsonl")
            try:
                os.replace(ws_path, processed)
                _log.info("Warm-start JSONL renamed to %s", processed)
            except OSError as e:
                _log.warning("Could not rename warm-start JSONL: %s", e)

        # 2. Update state frontmatter: clear warm_start_file, set consumed timestamp
        meta = state.setdefault("meta", {})
        meta["warm_start_file"] = ""
        meta["warm_start_consumed_at"] = datetime.now(timezone.utc).isoformat()
        _write_state_frontmatter(self._state_file, state)
        _log.info("Warm-start lifecycle complete — meta.warm_start_file cleared")

    # ── BaseSkill interface ───────────────────────────────────────────────

    @property
    def compare_fields(self) -> list:
        return ["signals"]

    def to_dict(self) -> dict[str, Any]:
        if not self._signals_file.exists():
            return {"signals": [], "metrics": {}}
        try:
            data = json.loads(self._signals_file.read_text(encoding="utf-8"))
            return {"signals": data.get("signals", []), "metrics": self._run_metrics}
        except (OSError, json.JSONDecodeError):
            return {"signals": [], "metrics": self._run_metrics}


# ---------------------------------------------------------------------------
# Factory function (consistent with other skills)
# ---------------------------------------------------------------------------

def get_skill(artha_dir: Path) -> AITrendRadarSkill:
    """Return an AITrendRadarSkill instance rooted at artha_dir."""
    return AITrendRadarSkill(artha_dir)
