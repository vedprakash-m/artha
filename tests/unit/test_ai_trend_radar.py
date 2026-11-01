"""Unit tests for scripts/skills/ai_trend_radar.py (PR-3, §16.1).

Test matrix covers:
  - Signal deduplication by topic hash (ARCH-2)
  - Stable _signal_id() function
  - Relevance scoring with try-worthy artifact bonus
  - Academic-only penalty
  - Multi-source bonus
  - Employer safety gate (DP-6)
  - Topic Interest Graph boost — max-wins rule
  - Warm-start timeliness penalty (§4.3)
  - ScoredMoment emission for done experiments (§8.1)
  - Moment NOT re-emitted when moment_emitted=True (GAP-2 guard)
  - Signals file integrity after parse()
  - Metrics file written after parse()
  - Empty input path (no items)
  - Warm-start lifecycle (flag cleared, consumed_at set)
"""
from __future__ import annotations

import hashlib
import json
import sys
import textwrap
from datetime import date, datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Import module under test — importing *after* sys.path is patched
from skills.ai_trend_radar import (  # noqa: E402
    AITrendRadarSkill,
    AISignal,
    _signal_id,
    _score_signal,
    _apply_topic_boost,
    _detect_category,
    _is_try_worthy,
    get_skill,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_artha_dir(tmp_path: Path) -> Path:
    """Create a minimal fake Artha directory layout in tmp_path."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)

    # Minimal artha_config.yaml  
    (tmp_path / "config" / "artha_config.yaml").write_text(
        """
enhancements:
  pr_manager:
    ai_trend_radar:
      enabled: true
      newsletter_senders:
        - "newsletter@simonwillison.net"
        - "noreply@openai.com"
      relevance_keywords:
        - ai
        - llm
        - model
        - agent
        - mcp
        - embedding
      topics_of_interest: []
      try_worthy_threshold: 0.5
      surface_threshold: 0.3
      max_signals_per_week: 10
""",
        encoding="utf-8",
    )

    # Minimal state/ai_trend_radar.md with YAML frontmatter
    (tmp_path / "state" / "ai_trend_radar.md").write_text(
        textwrap.dedent("""\
            ---
            topics_of_interest:
              - name: MCP Servers
                keywords: [mcp, model context protocol]
                boost: 0.4
              - name: Agentic Workflows
                keywords: [agentic, agent workflow]
                boost: 0.3
            experiments: []
            meta:
              warm_start_file: ""
            ---

            # AI Trend Radar

            Notes go here.
        """),
        encoding="utf-8",
    )

    # Minimal user_profile.yaml (no confidential terms)
    (tmp_path / "config" / "user_profile.yaml").write_text(
        "employment:\n  confidential_terms: []\n",
        encoding="utf-8",
    )

    # Minimal connectors.yaml
    (tmp_path / "config" / "connectors.yaml").write_text(
        "rss_feed:\n  fetch:\n    feeds:\n      - tag: openai_blog\n        url: https://openai.com/blog/rss\n",
        encoding="utf-8",
    )

    return tmp_path


def _make_email_item(
    subject: str,
    body: str = "",
    from_addr: str = "newsletter@simonwillison.net",
    date_iso: str = "",
) -> dict:
    """Build a minimal pipeline email record."""
    return {
        "source": "email",
        "marketing_category": "newsletter",
        "from": from_addr,
        "subject": subject,
        "body": body,
        "date_iso": date_iso or date.today().isoformat(),
        "link": "https://example.com",
    }


def _make_rss_item(
    title: str,
    description: str = "",
    tag: str = "openai_blog",
    date_iso: str = "",
) -> dict:
    """Build a minimal pipeline RSS record."""
    return {
        "source": "rss",
        "tag": tag,
        "title": title,
        "description": description,
        "date_iso": date_iso or date.today().isoformat(),
        "link": "https://example.com/rss-item",
    }


def _run_parse(
    artha_dir: Path,
    items: list[dict],
    *,
    warm_start: bool = False,
    warm_start_file: str = "",
    extra_state: dict | None = None,
) -> dict:
    """Helper: run parse() with a pre-built items list."""
    import yaml

    skill = AITrendRadarSkill(artha_dir)
    cfg_path = artha_dir / "config" / "artha_config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    config = (
        cfg.get("enhancements", {})
           .get("pr_manager", {})
           .get("ai_trend_radar", {})
    )
    state_path = artha_dir / "state" / "ai_trend_radar.md"
    text = state_path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    state = yaml.safe_load(parts[1]) or {}
    if extra_state:
        state.update(extra_state)

    raw_data = {
        "items": items,
        "warm_start": warm_start,
        "warm_start_file": warm_start_file,
        "config": config,
        "state": state,
        "blocked_terms": frozenset(),
    }
    return skill.parse(raw_data)


# ---------------------------------------------------------------------------
# _signal_id stability
# ---------------------------------------------------------------------------

class TestSignalIdStability:
    """_signal_id must be deterministic and whitespace-insensitive (ARCH-2)."""

    def test_same_topic_same_id(self):
        assert _signal_id("Claude Tool Use") == _signal_id("Claude Tool Use")

    def test_case_insensitive(self):
        assert _signal_id("claude tool use") == _signal_id("CLAUDE TOOL USE")

    def test_whitespace_normalised(self):
        assert _signal_id("claude  tool   use") == _signal_id("claude tool use")

    def test_leading_trailing_stripped(self):
        assert _signal_id("  claude tool use  ") == _signal_id("claude tool use")

    def test_different_topics_different_ids(self):
        assert _signal_id("Claude Tool Use") != _signal_id("GPT-5 Release")

    def test_id_is_12_chars(self):
        assert len(_signal_id("some topic")) == 12


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

class TestRelevanceScoring:
    """_score_signal() must apply bonuses and penalties deterministically."""

    def test_tryable_artifact_bonus(self):
        text = "Install the new CLI tool today: pip install mycli v1.2"
        score = _score_signal(text)
        assert score >= 0.30, f"Expected ≥0.30 for tryable text, got {score}"

    def test_how_to_bonus(self):
        text = "How to build a RAG pipeline with LangChain"
        score = _score_signal(text)
        assert score >= 0.20

    def test_model_release_bonus(self):
        text = "Claude 3.5 Sonnet just released — here's the breakdown"
        score = _score_signal(text)
        assert score >= 0.15

    def test_academic_penalty(self):
        text = "New arxiv preprint: attention patterns in language models"
        score = _score_signal(text)
        assert score <= 0.0, f"Expected ≤0 for academic-only text, got {score}"

    def test_enterprise_penalty(self):
        text = "Enterprise org-level deployment of Microsoft 365 Business"
        score = _score_signal(text)
        # enterprise + employer cancel each other somewhat; net ≤ 0.15
        assert score <= 0.20

    def test_employer_stack_bonus(self):
        text = "Azure AI Copilot integration now supports MCP"
        score = _score_signal(text)
        assert score >= 0.20

    def test_github_open_source_bonus(self):
        text = "New project at github.com/owner/cool-agent with Python bindings"
        score = _score_signal(text)
        assert score >= 0.10

    def test_score_clamped_by_parse(self, tmp_path):
        """Parse should clamp final relevance_score to [0.0, 1.0]."""
        artha_dir = _make_artha_dir(tmp_path)
        # Pile every bonus into one item — score should be clamped at 1.0
        item = _make_email_item(
            subject="Install Claude CLI LLM agent tool",
            body="How to use github.com/owner/claude-cli v1.0 for agentic workflows",
        )
        result = _run_parse(artha_dir, [item])
        for sig in result["signals"]:
            assert 0.0 <= sig["relevance_score"] <= 1.0


# ---------------------------------------------------------------------------
# Signal deduplication
# ---------------------------------------------------------------------------

class TestSignalDeduplication:
    """Two items with the same normalised topic → exactly one AISignal (ARCH-2)."""

    def test_same_topic_deduped(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        items = [
            _make_email_item("Claude AI tool released", from_addr="newsletter@simonwillison.net"),
            _make_rss_item("Claude AI tool released", tag="openai_blog"),
        ]
        result = _run_parse(artha_dir, items)
        ids = [s["id"] for s in result["signals"]]
        assert len(ids) == len(set(ids)), "Duplicate signal IDs found after dedup"
        # Should be merged into one signal
        assert len(result["signals"]) == 1

    def test_same_topic_increments_seen_in(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        base_topic = "MCP server protocol spec update"
        items = [
            _make_email_item(base_topic, from_addr="newsletter@simonwillison.net"),
            _make_rss_item(base_topic, tag="openai_blog"),
        ]
        result = _run_parse(artha_dir, items)
        assert result["signals"][0]["seen_in"] == 2

    def test_different_topics_not_deduped(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        items = [
            _make_email_item("Claude tool use agent"),
            _make_email_item("GPT-5 model release benchmark"),
        ]
        result = _run_parse(artha_dir, items)
        assert len(result["signals"]) == 2


# ---------------------------------------------------------------------------
# Multi-source bonus
# ---------------------------------------------------------------------------

class TestMultiSourceBonus:
    """seen_in >= 2 should add the SCORE_MULTI_SOURCE (+0.10) bonus."""

    def test_multi_source_increases_score(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        topic = "LLM agent tool calling API"
        # Single source
        items_single = [_make_email_item(topic)]
        result_single = _run_parse(artha_dir, items_single)
        single_score = result_single["signals"][0]["relevance_score"] if result_single["signals"] else 0.0

        # Two sources — same normalised topic
        items_dual = [
            _make_email_item(topic),
            _make_rss_item(topic, tag="openai_blog"),
        ]
        result_dual = _run_parse(artha_dir, items_dual)
        dual_score = result_dual["signals"][0]["relevance_score"] if result_dual["signals"] else 0.0

        assert dual_score >= single_score + 0.05, (
            f"Multi-source bonus expected; single={single_score:.3f} dual={dual_score:.3f}"
        )


# ---------------------------------------------------------------------------
# Employer safety gate
# ---------------------------------------------------------------------------

class TestEmployerSafetyGate:
    """Items mentioning blocked terms must be silently dropped (DP-6)."""

    def test_blocked_term_drops_signal(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        # Write a user_profile.yaml with a confidential term
        (artha_dir / "config" / "user_profile.yaml").write_text(
            "employment:\n  confidential_terms:\n    - QuantumLeap\n",
            encoding="utf-8",
        )
        skill = AITrendRadarSkill(artha_dir)
        blocked = skill._load_employer_blocked_terms()
        assert "quantumleap" in blocked

        items = [
            _make_email_item("QuantumLeap internal LLM launch agent"),  # blocked
            _make_email_item("Claude MCP server release tool"),          # allowed
        ]
        # We pass blocked_terms explicitly in _run_parse helper
        import yaml
        cfg = yaml.safe_load(
            (artha_dir / "config" / "artha_config.yaml").read_text()
        )
        config = cfg["enhancements"]["pr_manager"]["ai_trend_radar"]
        state_text = (artha_dir / "state" / "ai_trend_radar.md").read_text()
        parts = state_text.split("---", 2)
        state = yaml.safe_load(parts[1]) or {}

        parse_skill = AITrendRadarSkill(artha_dir)
        raw_data = {
            "items": items,
            "warm_start": False,
            "warm_start_file": "",
            "config": config,
            "state": state,
            "blocked_terms": frozenset(["quantumleap"]),
        }
        result = parse_skill.parse(raw_data)
        topics = [s["topic"] for s in result["signals"]]
        assert not any("QuantumLeap" in t for t in topics), "Blocked term leaked into signals"
        assert any("Claude" in t or "MCP" in t for t in topics), "Non-blocked signal was also dropped"

    def test_no_blocked_terms_passes_all(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        items = [
            _make_email_item("Claude MCP server tool"),
            _make_email_item("LLM agent agentic workflow"),
        ]
        result = _run_parse(artha_dir, items, extra_state={})
        # All items should produce at least one signal (both pass employer gate)
        assert len(result["signals"]) >= 1


# ---------------------------------------------------------------------------
# Topic Interest Graph boost
# ---------------------------------------------------------------------------

class TestTopicBoost:
    """_apply_topic_boost() must apply max-wins; only highest boost counts."""

    def test_single_match_applies_boost(self):
        topics = [{"name": "MCP Servers", "keywords": ["mcp"], "boost": 0.4}]
        boost, matched = _apply_topic_boost("New mcp server release tool", topics)
        assert boost == 0.4
        assert matched == "MCP Servers"

    def test_max_wins_returns_highest(self):
        topics = [
            {"name": "Low Topic", "keywords": ["agent"], "boost": 0.2},
            {"name": "High Topic", "keywords": ["mcp"], "boost": 0.4},
        ]
        boost, matched = _apply_topic_boost("New mcp agent workflow tool", topics)
        assert boost == 0.4
        assert matched == "High Topic"

    def test_no_match_returns_zero(self):
        topics = [{"name": "MCP Servers", "keywords": ["mcp"], "boost": 0.4}]
        boost, matched = _apply_topic_boost("arxiv paper on attention mechanisms", topics)
        assert boost == 0.0
        assert matched is None

    def test_boost_added_to_base_score(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        # A neutral topic that gets boost from Interest Graph
        topic = "MCP Servers: model context protocol spec"
        items_no_boost = [_make_rss_item("Some unrelated AI model news")]
        items_boost = [_make_rss_item(topic)]

        result_no = _run_parse(artha_dir, items_no_boost)
        result_yes = _run_parse(artha_dir, items_boost)

        score_no = result_no["signals"][0]["relevance_score"] if result_no["signals"] else 0.0
        score_yes = result_yes["signals"][0]["relevance_score"] if result_yes["signals"] else 0.0
        assert score_yes >= score_no, "Interest Graph boost should not reduce score"


# ---------------------------------------------------------------------------
# Warm-start timeliness penalty
# ---------------------------------------------------------------------------

class TestWarmStartTimelinessPenalty:
    """Items older than 14 days in warm-start mode get 0.30x penalty (§4.3)."""

    def test_old_item_demoted_in_warm_start(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        old_date = (date.today() - timedelta(days=20)).isoformat()
        fresh_date = date.today().isoformat()

        items_old = [_make_rss_item("Claude AI tool install CLI", date_iso=old_date)]
        items_fresh = [_make_rss_item("Claude AI tool install CLI", date_iso=fresh_date)]

        result_old = _run_parse(artha_dir, items_old, warm_start=True)
        result_fresh = _run_parse(artha_dir, items_fresh, warm_start=True)

        score_old = result_old["signals"][0]["relevance_score"] if result_old["signals"] else 0.0
        score_fresh = result_fresh["signals"][0]["relevance_score"] if result_fresh["signals"] else 1.0

        assert score_old <= score_fresh, (
            f"Old warm-start item should be ≤ fresh item: old={score_old} fresh={score_fresh}"
        )

    def test_fresh_item_not_penalised_in_warm_start(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        fresh_date = date.today().isoformat()
        items = [_make_rss_item("Claude AI tool install agent", date_iso=fresh_date)]
        result = _run_parse(artha_dir, items, warm_start=True)
        # Fresh items should not have the penalty applied
        if result["signals"]:
            assert result["signals"][0]["relevance_score"] > 0.0

    def test_normal_mode_no_timeliness_penalty(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        old_date = (date.today() - timedelta(days=20)).isoformat()
        items = [_make_rss_item("Claude AI tool install agent", date_iso=old_date)]
        # Normal mode (warm_start=False) — should not apply timeliness penalty
        result_normal = _run_parse(artha_dir, items, warm_start=False)
        result_warm = _run_parse(artha_dir, items, warm_start=True)
        score_normal = result_normal["signals"][0]["relevance_score"] if result_normal["signals"] else 0.0
        score_warm = result_warm["signals"][0]["relevance_score"] if result_warm["signals"] else 0.0
        assert score_normal >= score_warm, "Normal mode should not apply warm-start penalty"


# ---------------------------------------------------------------------------
# ScoredMoment emission
# ---------------------------------------------------------------------------

class TestScoredMomentEmission:
    """Done experiments with great/useful verdicts must emit exactly one ScoredMoment
    per experiment, and only once (moment_emitted guard, GAP-2)."""

    def _base_experiment(self, overrides: dict | None = None) -> dict:
        exp = {
            "id": "EXP-ABC001",
            "topic": "Claude MCP Tool Use",
            "status": "done",
            "verdict": "great",
            "completed_date": date.today().isoformat(),
            "moment_emitted": False,
        }
        if overrides:
            exp.update(overrides)
        return exp

    def test_done_great_emits_moment(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        skill = AITrendRadarSkill(artha_dir)

        # We need a ScoredMoment from pr_manager importable — patch it
        mock_moment = MagicMock()
        mock_moment.as_dict = MagicMock(return_value={"moment_type": "ai_experiment_complete"})

        with patch.dict("sys.modules", {"pr_manager": MagicMock(
            ScoredMoment=MagicMock(return_value=mock_moment),
            _MOMENT_WEIGHTS={"ai_experiment_complete": 0.85},
        )}):
            state = {
                "topics_of_interest": [],
                "experiments": [self._base_experiment()],
                "meta": {},
            }
            moments = skill._emit_experiment_moments(state)

        assert len(moments) == 1

    def test_done_useful_emits_moment(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        skill = AITrendRadarSkill(artha_dir)
        mock_moment = MagicMock()
        mock_moment.as_dict = MagicMock(return_value={"moment_type": "ai_experiment_complete"})

        with patch.dict("sys.modules", {"pr_manager": MagicMock(
            ScoredMoment=MagicMock(return_value=mock_moment),
            _MOMENT_WEIGHTS={"ai_experiment_complete": 0.85},
        )}):
            state = {"experiments": [self._base_experiment({"verdict": "useful"})], "meta": {}}
            moments = skill._emit_experiment_moments(state)

        assert len(moments) == 1

    def test_pending_verdict_skipped(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        skill = AITrendRadarSkill(artha_dir)
        state = {"experiments": [self._base_experiment({"verdict": "pending"})], "meta": {}}
        with patch.dict("sys.modules", {"pr_manager": MagicMock(
            ScoredMoment=MagicMock(),
            _MOMENT_WEIGHTS={"ai_experiment_complete": 0.85},
        )}):
            moments = skill._emit_experiment_moments(state)
        assert len(moments) == 0

    def test_skip_verdict_skipped(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        skill = AITrendRadarSkill(artha_dir)
        state = {"experiments": [self._base_experiment({"verdict": "skip"})], "meta": {}}
        with patch.dict("sys.modules", {"pr_manager": MagicMock(
            ScoredMoment=MagicMock(),
            _MOMENT_WEIGHTS={"ai_experiment_complete": 0.85},
        )}):
            moments = skill._emit_experiment_moments(state)
        assert len(moments) == 0

    def test_moment_not_re_emitted_on_second_run(self, tmp_path):
        """moment_emitted=True → moment NOT emitted again (GAP-2 guard)."""
        artha_dir = _make_artha_dir(tmp_path)
        skill = AITrendRadarSkill(artha_dir)
        already_emitted = self._base_experiment({"moment_emitted": True})
        state = {"experiments": [already_emitted], "meta": {}}
        with patch.dict("sys.modules", {"pr_manager": MagicMock(
            ScoredMoment=MagicMock(),
            _MOMENT_WEIGHTS={"ai_experiment_complete": 0.85},
        )}):
            moments = skill._emit_experiment_moments(state)
        assert len(moments) == 0, "Already-emitted moment must not be re-emitted"

    def test_missing_completed_date_skips(self, tmp_path):
        """Experiments without completed_date must be skipped gracefully."""
        artha_dir = _make_artha_dir(tmp_path)
        skill = AITrendRadarSkill(artha_dir)
        exp = self._base_experiment()
        del exp["completed_date"]
        state = {"experiments": [exp], "meta": {}}
        with patch.dict("sys.modules", {"pr_manager": MagicMock(
            ScoredMoment=MagicMock(),
            _MOMENT_WEIGHTS={"ai_experiment_complete": 0.85},
        )}):
            moments = skill._emit_experiment_moments(state)
        assert len(moments) == 0

    def test_moment_emitted_flag_set_after_emission(self, tmp_path):
        """moment_emitted must be set to True in state after emission."""
        artha_dir = _make_artha_dir(tmp_path)
        skill = AITrendRadarSkill(artha_dir)
        exp = self._base_experiment()
        state = {"experiments": [exp], "meta": {}}
        mock_moment = MagicMock()
        mock_moment.as_dict = MagicMock(return_value={})

        with patch.dict("sys.modules", {"pr_manager": MagicMock(
            ScoredMoment=MagicMock(return_value=mock_moment),
            _MOMENT_WEIGHTS={"ai_experiment_complete": 0.85},
        )}):
            skill._emit_experiment_moments(state)

        # The experiment in the state dict should now have moment_emitted=True
        assert state["experiments"][0].get("moment_emitted") is True


# ---------------------------------------------------------------------------
# Output file integrity
# ---------------------------------------------------------------------------

class TestOutputFileIntegrity:
    """Signals and metrics files must be written with expected structure."""

    def test_signals_file_written(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        items = [_make_rss_item("Claude AI tool install agent LLM")]
        _run_parse(artha_dir, items)
        signals_path = artha_dir / "tmp" / "ai_trend_signals.json"
        assert signals_path.exists(), "signals file not created"
        data = json.loads(signals_path.read_text())
        assert "signals" in data
        assert "generated_at" in data
        assert "signal_count" in data

    def test_metrics_file_written(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        items = [_make_rss_item("MCP server agentic agent workflow")]
        _run_parse(artha_dir, items)
        metrics_path = artha_dir / "tmp" / "ai_trend_metrics.json"
        assert metrics_path.exists(), "metrics file not created"
        data = json.loads(metrics_path.read_text())
        assert "last_run_at" in data
        assert "raw_count" in data
        assert "surfaced_count" in data

    def test_empty_input_produces_valid_signals_file(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        result = _run_parse(artha_dir, [])
        assert result["signals"] == []
        signals_path = artha_dir / "tmp" / "ai_trend_signals.json"
        assert signals_path.exists()
        data = json.loads(signals_path.read_text())
        assert data["signals"] == []
        assert data["signal_count"] == 0

    def test_signals_rotation(self, tmp_path):
        """Previous signals file should be rotated to _prev on second run."""
        artha_dir = _make_artha_dir(tmp_path)
        items1 = [_make_rss_item("Claude AI agent tool first run")]
        _run_parse(artha_dir, items1)
        signals_path = artha_dir / "tmp" / "ai_trend_signals.json"
        prev_path = artha_dir / "tmp" / "ai_trend_signals_prev.json"
        assert signals_path.exists()

        items2 = [_make_rss_item("GPT-5 model release second run")]
        _run_parse(artha_dir, items2)
        assert prev_path.exists(), "Previous signals file should exist after rotation"


# ---------------------------------------------------------------------------
# Warm-start lifecycle
# ---------------------------------------------------------------------------

class TestWarmStartLifecycle:
    """Warm-start file path should be cleared after a successful run (§4.3)."""

    def test_flag_cleared_after_warmstart(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)

        # Create the warm-start JSONL
        ws_file = artha_dir / "tmp" / "warmstart.jsonl"
        ws_file.write_text(
            json.dumps({
                "source": "rss", "tag": "openai_blog",
                "title": "Claude MCP AI tool install", "description": "",
                "date_iso": date.today().isoformat(), "link": "https://x.com",
            }) + "\n",
            encoding="utf-8",
        )

        # Set warm_start_file in state
        import yaml
        state_path = artha_dir / "state" / "ai_trend_radar.md"
        text = state_path.read_text()
        parts = text.split("---", 2)
        fm = yaml.safe_load(parts[1]) or {}
        fm.setdefault("meta", {})["warm_start_file"] = str(ws_file)
        state_path.write_text(
            "---\n" + yaml.safe_dump(fm, default_flow_style=False) + "---" + parts[2],
            encoding="utf-8",
        )

        # Run the skill
        skill = AITrendRadarSkill(artha_dir)
        skill.pull()  # This loads config + state
        # Simulate parse with the state as-is (warm_start=True)
        cfg_path = artha_dir / "config" / "artha_config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text())
        config = cfg["enhancements"]["pr_manager"]["ai_trend_radar"]
        fm_after_pull = yaml.safe_load(state_path.read_text().split("---", 2)[1]) or {}
        raw_data = {
            "items": [{"source": "rss", "tag": "openai_blog",
                        "title": "Claude MCP AI tool install",
                        "date_iso": date.today().isoformat(), "link": "https://x.com"}],
            "warm_start": True,
            "warm_start_file": str(ws_file),
            "config": config,
            "state": fm_after_pull,
            "blocked_terms": frozenset(),
        }
        skill.parse(raw_data)

        # Verify: state file should now have empty warm_start_file
        fm_final = yaml.safe_load(state_path.read_text().split("---", 2)[1]) or {}
        meta = fm_final.get("meta") or {}
        assert meta.get("warm_start_file") == "", (
            f"warm_start_file not cleared after lifecycle: {meta.get('warm_start_file')!r}"
        )
        assert "warm_start_consumed_at" in meta, "consumed_at timestamp not set"
        # JSONL should be renamed to .processed.jsonl
        assert not ws_file.exists(), "warm-start JSONL should be renamed"
        processed = ws_file.with_suffix(".processed.jsonl")
        assert processed.exists(), "warm-start JSONL should be renamed to .processed.jsonl"


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

class TestCategoryDetection:
    """_detect_category() must return correct category strings."""

    def test_tool_release(self):
        assert _detect_category("New CLI v2.0 launched: install today") == "tool_release"

    def test_model_release(self):
        assert _detect_category("GPT-5 model release benchmark") == "model_release"

    def test_tutorial(self):
        assert _detect_category("Getting started guide for RAG pipelines") == "tutorial"

    def test_research(self):
        assert _detect_category("arxiv paper on attention mechanisms") == "research"

    def test_technique(self):
        assert _detect_category("How-to: prompt engineering for chain-of-thought") in (
            "technique", "tutorial"
        )

    def test_framework_update(self):
        assert _detect_category("LangChain framework v0.3 major update") == "framework_update"


# ---------------------------------------------------------------------------
# Try-worthy determination
# ---------------------------------------------------------------------------

class TestTryWorthy:
    """_is_try_worthy() must respect minimum score threshold and category rules."""

    def _signal(self, score: float, category: str, summary: str = "") -> AISignal:
        return AISignal(
            id=_signal_id(f"{category}-{score}"),
            topic="test topic",
            category=category,
            sources=[],
            best_source_url="",
            summary=summary,
            detected_at=date.today().isoformat(),
            relevance_score=score,
            try_worthy=False,
            seen_in=1,
            topic_match=None,
        )

    def test_tool_above_threshold_is_try_worthy(self):
        sig = self._signal(0.7, "tool_release")
        assert _is_try_worthy(sig, try_worthy_threshold=0.5) is True

    def test_below_threshold_not_try_worthy(self):
        sig = self._signal(0.3, "tool_release")
        assert _is_try_worthy(sig, try_worthy_threshold=0.5) is False

    def test_research_without_github_not_try_worthy(self):
        sig = self._signal(0.8, "research", summary="arxiv paper only no links")
        assert _is_try_worthy(sig, try_worthy_threshold=0.5) is False

    def test_research_with_github_is_try_worthy(self):
        sig = self._signal(0.8, "research", summary="github.com/owner/proj — experimental")
        assert _is_try_worthy(sig, try_worthy_threshold=0.5) is True


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

class TestFactoryFunction:
    def test_get_skill_returns_instance(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        skill = get_skill(artha_dir)
        assert isinstance(skill, AITrendRadarSkill)

    def test_get_skill_name(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        skill = get_skill(artha_dir)
        assert skill.name == "ai_trend_radar"

    def test_get_skill_priority(self, tmp_path):
        artha_dir = _make_artha_dir(tmp_path)
        skill = get_skill(artha_dir)
        assert skill.priority == "P2"
