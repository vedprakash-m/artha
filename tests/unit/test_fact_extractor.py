"""tests/unit/test_fact_extractor.py — Unit tests for scripts/fact_extractor.py

Phase 5 verification suite (specs/agentic-improve.md).

Coverage:
  - Fact model construction and validation
  - TTL expiry logic
  - extract_facts_from_summary() with correction/pattern/preference signals
  - extract_facts_from_summary() with empty or missing summary
  - load_existing_facts() from memory.md with v2.0 schema
  - load_existing_facts() backward compat (old v1.0 schema without facts key)
  - deduplicate_facts() by ID
  - persist_facts() creates / updates memory.md
  - persist_facts() preserves markdown body
  - persist_facts() expires stale facts
  - Feature flag disabled: no extraction, no writes
  - PII stripped from fact statements
"""
from __future__ import annotations

import json
import textwrap
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest adds scripts/ to sys.path
from fact_extractor import (
    Fact,
    _make_id,
    _strip_pii,
    deduplicate_facts,
    extract_facts_from_summary,
    load_existing_facts,
    persist_facts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_summary(tmp_path: Path, content: str, n: int = 1) -> Path:
    """Write a fake session summary markdown file."""
    p = tmp_path / "tmp" / f"session_history_{n}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _write_memory(tmp_path: Path, facts: list[dict]) -> Path:
    """Write a memory.md with the given facts in frontmatter."""
    import yaml
    fm = {
        "domain": "memory",
        "last_updated": "never",
        "schema_version": "2.0",
        "facts": facts,
    }
    fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True)
    content = f"---\n{fm_text}---\n\n## Memory\n"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    p = state_dir / "memory.md"
    p.write_text(content, encoding="utf-8")
    return p


def _enabled(enabled: bool = True):
    """Patch _load_harness_flag to return a fixed value."""
    return patch("fact_extractor._load_harness_flag", return_value=enabled)


# ---------------------------------------------------------------------------
# Fact model
# ---------------------------------------------------------------------------


class TestFactModel:
    def test_basic_construction(self):
        f = Fact(id="test-1", type="correction", domain="finance",
                 statement="Costco is not anomalous")
        assert f.id == "test-1"
        assert f.type == "correction"

    def test_date_added_defaults_to_today(self):
        f = Fact(id="x", type="pattern", domain="general", statement="recurring bill")
        assert f.date_added == date.today().isoformat()

    def test_last_seen_defaults_to_today(self):
        f = Fact(id="x", type="preference", domain="general", statement="prefers flash")
        assert f.last_seen == date.today().isoformat()

    def test_confidence_default(self):
        f = Fact(id="x", type="pattern", domain="general", statement="test")
        assert f.confidence == 0.8

    def test_ttl_none_never_expires(self):
        f = Fact(id="x", type="correction", domain="general", statement="test",
                 ttl_days=None, date_added="2020-01-01")
        assert not f.is_expired()

    def test_expired_fact(self):
        old_date = (date.today() - timedelta(days=200)).isoformat()
        f = Fact(id="x", type="pattern", domain="general", statement="test",
                 ttl_days=180, date_added=old_date)
        assert f.is_expired()

    def test_not_expired_fact(self):
        recent_date = (date.today() - timedelta(days=10)).isoformat()
        f = Fact(id="x", type="pattern", domain="general", statement="test",
                 ttl_days=180, date_added=recent_date)
        assert not f.is_expired()

    def test_to_dict_has_required_keys(self):
        f = Fact(id="x", type="correction", domain="finance", statement="test")
        d = f.to_dict()
        required = {"id", "type", "domain", "statement", "source", "date_added",
                    "ttl_days", "confidence", "last_seen"}
        assert required <= set(d.keys())


# ---------------------------------------------------------------------------
# PII stripping
# ---------------------------------------------------------------------------


class TestStripPii:
    def test_strips_phone_number(self):
        result = _strip_pii("Call at 425-555-1234 anytime")
        assert "425-555-1234" not in result
        assert "[PHONE-REDACTED]" in result

    def test_strips_email(self):
        result = _strip_pii("Contact user@example.com for details")
        assert "user@example.com" not in result
        assert "[EMAIL-REDACTED]" in result

    def test_strips_ssn(self):
        result = _strip_pii("SSN: 123-45-6789 on file")
        assert "123-45-6789" not in result

    def test_clean_text_unchanged(self):
        text = "Costco purchases are normal bulk shopping"
        assert _strip_pii(text) == text


# ---------------------------------------------------------------------------
# extract_facts_from_summary
# ---------------------------------------------------------------------------

CORRECTION_SUMMARY = """\
# Session Summary — 2026-03-15T09:00:00Z

**Command:** `/catch-up`
**Trigger:** post_command

## Key Findings
1. Finance: Costco purchases are not anomalous — user noted this is expected bulk shopping
2. Immigration: I-131 renewal due in 45 days
3. Calendar: Dr. appointment on March 20 confirmed

## State Mutations
- state/finance.md
- state/immigration.md

## Open Threads
- Recurring bill from PSE typically arrives on the 15th
"""

PREFERENCE_SUMMARY = """\
# Session Summary — 2026-03-15

**Command:** `/catch-up flash`
**Trigger:** post_command

## Key Findings
1. User prefers flash briefings on weekday mornings
2. Goals: sprint check-in complete

## State Mutations
- state/goals.md

## Open Threads
"""

EMPTY_SUMMARY = """\
# Session Summary — 2026-03-15

**Command:** `/catch-up`

## Key Findings

## State Mutations

## Open Threads
"""


class TestExtractFactsFromSummary:
    def test_extracts_correction_fact(self, tmp_path):
        p = _write_summary(tmp_path, CORRECTION_SUMMARY)
        with _enabled():
            facts = extract_facts_from_summary(p, tmp_path)
        correction_facts = [f for f in facts if f.type == "correction"]
        assert len(correction_facts) >= 1

    def test_correction_fact_high_confidence(self, tmp_path):
        p = _write_summary(tmp_path, CORRECTION_SUMMARY)
        with _enabled():
            facts = extract_facts_from_summary(p, tmp_path)
        corrections = [f for f in facts if f.type == "correction"]
        for c in corrections:
            assert c.confidence >= 0.9

    def test_extracts_pattern_fact(self, tmp_path):
        p = _write_summary(tmp_path, CORRECTION_SUMMARY)
        with _enabled():
            facts = extract_facts_from_summary(p, tmp_path)
        pattern_facts = [f for f in facts if f.type == "pattern"]
        assert len(pattern_facts) >= 1

    def test_extracts_preference_fact(self, tmp_path):
        p = _write_summary(tmp_path, PREFERENCE_SUMMARY)
        with _enabled():
            facts = extract_facts_from_summary(p, tmp_path)
        pref_facts = [f for f in facts if f.type == "preference"]
        assert len(pref_facts) >= 1

    def test_empty_summary_returns_empty(self, tmp_path):
        p = _write_summary(tmp_path, EMPTY_SUMMARY)
        with _enabled():
            facts = extract_facts_from_summary(p, tmp_path)
        assert facts == []

    def test_missing_file_returns_empty(self, tmp_path):
        with _enabled():
            facts = extract_facts_from_summary(tmp_path / "nonexistent.md", tmp_path)
        assert facts == []

    def test_feature_flag_disabled_returns_empty(self, tmp_path):
        p = _write_summary(tmp_path, CORRECTION_SUMMARY)
        with _enabled(False):
            facts = extract_facts_from_summary(p, tmp_path)
        assert facts == []

    def test_fact_source_includes_session_date(self, tmp_path):
        p = _write_summary(tmp_path, CORRECTION_SUMMARY)
        with _enabled():
            facts = extract_facts_from_summary(p, tmp_path)
        if facts:
            assert "session-" in facts[0].source

    def test_correction_ttl_is_none(self, tmp_path):
        p = _write_summary(tmp_path, CORRECTION_SUMMARY)
        with _enabled():
            facts = extract_facts_from_summary(p, tmp_path)
        corrections = [f for f in facts if f.type == "correction"]
        for c in corrections:
            assert c.ttl_days is None


# ---------------------------------------------------------------------------
# load_existing_facts
# ---------------------------------------------------------------------------


class TestLoadExistingFacts:
    def test_returns_empty_when_no_memory_file(self, tmp_path):
        (tmp_path / "state").mkdir()
        facts = load_existing_facts(tmp_path)
        assert facts == []

    def test_loads_facts_from_frontmatter(self, tmp_path):
        today = date.today().isoformat()
        raw = [{"id": "fact-1", "type": "correction", "domain": "finance",
                "statement": "Costco is not anomalous", "source": "session-2026-03-15",
                "date_added": today, "ttl_days": None, "confidence": 1.0, "last_seen": today}]
        _write_memory(tmp_path, raw)
        facts = load_existing_facts(tmp_path)
        assert len(facts) == 1
        assert facts[0].id == "fact-1"

    def test_backwards_compat_no_facts_key(self, tmp_path):
        """Old memory.md without facts: key still loads (returns empty)."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "memory.md").write_text(
            "---\ndomain: memory\nsome: value\n---\n## Memory\n",
            encoding="utf-8",
        )
        facts = load_existing_facts(tmp_path)
        assert facts == []

    def test_skips_malformed_entries(self, tmp_path):
        """Malformed fact entries are skipped silently."""
        raw = [
            {"id": "ok", "type": "correction", "domain": "finance",
             "statement": "valid", "confidence": 1.0},
            "not_a_dict",
            None,
        ]
        _write_memory(tmp_path, raw)
        facts = load_existing_facts(tmp_path)
        assert len(facts) == 1
        assert facts[0].id == "ok"


# ---------------------------------------------------------------------------
# deduplicate_facts
# ---------------------------------------------------------------------------


class TestDeduplicateFacts:
    def _fact(self, fact_id: str, confidence: float = 0.8) -> Fact:
        return Fact(id=fact_id, type="pattern", domain="finance",
                    statement="test statement", confidence=confidence)

    def test_dedup_removes_existing_id(self):
        existing = [self._fact("dupe-1")]
        new_facts = [self._fact("dupe-1"), self._fact("new-1")]
        result = deduplicate_facts(new_facts, existing)
        assert len(result) == 1
        assert result[0].id == "new-1"

    def test_all_new_returns_all(self):
        existing = [self._fact("old-1")]
        new_facts = [self._fact("new-a"), self._fact("new-b")]
        result = deduplicate_facts(new_facts, existing)
        assert len(result) == 2

    def test_empty_new_returns_empty(self):
        existing = [self._fact("old-1")]
        result = deduplicate_facts([], existing)
        assert result == []

    def test_empty_existing_accepts_all(self):
        new_facts = [self._fact("fact-a"), self._fact("fact-b")]
        result = deduplicate_facts(new_facts, [])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# persist_facts
# ---------------------------------------------------------------------------


class TestPersistFacts:
    def _new_fact(self, fact_id: str, fact_type: str = "pattern") -> Fact:
        return Fact(id=fact_id, type=fact_type, domain="finance",
                    statement=f"Fact: {fact_id}")

    def test_creates_memory_file_if_missing(self, tmp_path):
        (tmp_path / "state").mkdir()
        facts = [self._new_fact("fact-1")]
        with _enabled():
            count = persist_facts(facts, tmp_path)
        assert count == 1
        assert (tmp_path / "state" / "memory.md").exists()

    def test_returns_count_of_added_facts(self, tmp_path):
        (tmp_path / "state").mkdir()
        facts = [self._new_fact("a"), self._new_fact("b"), self._new_fact("c")]
        with _enabled():
            count = persist_facts(facts, tmp_path)
        assert count == 3

    def test_duplicate_not_re_added(self, tmp_path):
        today = date.today().isoformat()
        _write_memory(tmp_path, [
            {"id": "fact-1", "type": "correction", "domain": "finance",
             "statement": "existing fact", "confidence": 1.0,
             "date_added": today, "last_seen": today, "source": "", "ttl_days": None}
        ])
        new_facts = [
            self._new_fact("fact-1"),   # duplicate
            self._new_fact("fact-2"),   # new
        ]
        with _enabled():
            count = persist_facts(new_facts, tmp_path)
        assert count == 1
        # fact-1 should NOT be re-added
        loaded = load_existing_facts(tmp_path)
        ids = [f.id for f in loaded]
        assert ids.count("fact-1") == 1

    def test_preserves_markdown_body(self, tmp_path):
        (tmp_path / "state").mkdir()
        body_marker = "## My Custom Section\nSome text I wrote manually."
        (tmp_path / "state" / "memory.md").write_text(
            f"---\ndomain: memory\nschema_version: '2.0'\nfacts: []\n---\n\n{body_marker}",
            encoding="utf-8",
        )
        with _enabled():
            persist_facts([self._new_fact("new-1")], tmp_path)
        content = (tmp_path / "state" / "memory.md").read_text()
        assert body_marker in content

    def test_expires_stale_facts(self, tmp_path):
        old_date = (date.today() - timedelta(days=200)).isoformat()
        _write_memory(tmp_path, [
            {"id": "stale-pattern", "type": "pattern", "domain": "general",
             "statement": "stale recurring bill", "confidence": 0.8,
             "date_added": old_date, "last_seen": old_date, "source": "", "ttl_days": 180},
            {"id": "permanent-correction", "type": "correction", "domain": "finance",
             "statement": "corrections last forever", "confidence": 1.0,
             "date_added": old_date, "last_seen": old_date, "source": "", "ttl_days": None},
        ])
        with _enabled():
            persist_facts([self._new_fact("new-1")], tmp_path)
        loaded = load_existing_facts(tmp_path)
        ids = [f.id for f in loaded]
        assert "stale-pattern" not in ids   # expired and evicted
        assert "permanent-correction" in ids  # corrections don't expire

    def test_feature_flag_disabled_noop(self, tmp_path):
        (tmp_path / "state").mkdir()
        facts = [self._new_fact("fact-1")]
        with _enabled(False):
            count = persist_facts(facts, tmp_path)
        assert count == 0
        assert not (tmp_path / "state" / "memory.md").exists()

    def test_schema_version_set_to_v2(self, tmp_path):
        (tmp_path / "state").mkdir()
        with _enabled():
            persist_facts([self._new_fact("f")], tmp_path)
        loaded_facts = load_existing_facts(tmp_path)
        memory_text = (tmp_path / "state" / "memory.md").read_text()
        assert "schema_version: '2.0'" in memory_text or '2.0' in memory_text


# ---------------------------------------------------------------------------
# make_id helper
# ---------------------------------------------------------------------------


class TestMakeId:
    def test_id_format(self):
        fid = _make_id("correction", "finance", "Costco is not anomalous spending")
        assert fid.startswith("correction-finance-")

    def test_id_normalized(self):
        fid = _make_id("pattern", "calendar", "Weekly on TUESDAY at 4PM")
        assert fid == fid.lower()
        assert " " not in fid


# ---------------------------------------------------------------------------
# AR-1: Memory capacity enforcement (_consolidate_facts)
# ---------------------------------------------------------------------------

from fact_extractor import _consolidate_facts, MAX_MEMORY_CHARS, MAX_FACTS_COUNT  # noqa: E402


def _make_fact(fact_id: str, fact_type: str = "pattern", confidence: float = 0.8) -> Fact:
    """Helper: create a minimal Fact for consolidation tests."""
    return Fact(
        id=fact_id,
        type=fact_type,
        domain="general",
        statement=f"Statement for {fact_id}",
        confidence=confidence,
    )


class TestConsolidateFacts:
    def test_within_limits_unchanged(self):
        """When within both limits, facts are returned as-is."""
        facts = [_make_fact(f"f{i}") for i in range(5)]
        result = _consolidate_facts(facts, max_facts=30, max_chars=3000)
        assert len(result) == 5

    def test_drops_ttl_expired_facts(self):
        """TTL-expired facts are removed in the first pass."""
        old = (date.today() - timedelta(days=200)).isoformat()
        expired = Fact(
            id="expired-fact", type="pattern", domain="general",
            statement="old fact", confidence=0.9,
            date_added=old, last_seen=old, ttl_days=100,
        )
        fresh = _make_fact("fresh")
        result = _consolidate_facts([expired, fresh], max_facts=30, max_chars=3000)
        ids = [f.id for f in result]
        assert "expired-fact" not in ids
        assert "fresh" in ids

    def test_evicts_lowest_confidence_when_over_count(self):
        """When count > max_facts, lowest-confidence non-protected facts are evicted."""
        facts = [_make_fact(f"f{i}", confidence=float(i) / 10) for i in range(10)]
        result = _consolidate_facts(facts, max_facts=5, max_chars=99999)
        # Should keep at most 5 facts
        assert len(result) <= 5
        # The surviving facts should be higher-confidence ones
        surviving_ids = {f.id for f in result}
        # f0 (lowest confidence) should have been evicted
        assert "f0" not in surviving_ids

    def test_never_evicts_corrections(self):
        """Correction-type facts are pinned and never evicted regardless of confidence."""
        correction = _make_fact("correction-1", fact_type="correction", confidence=0.1)
        low_others = [_make_fact(f"p{i}", confidence=0.5) for i in range(10)]
        all_facts = [correction] + low_others
        result = _consolidate_facts(all_facts, max_facts=5, max_chars=99999)
        ids = [f.id for f in result]
        assert "correction-1" in ids

    def test_never_evicts_preferences(self):
        """Preference-type facts are pinned and never evicted regardless of confidence."""
        pref = _make_fact("pref-1", fact_type="preference", confidence=0.1)
        low_others = [_make_fact(f"q{i}", confidence=0.5) for i in range(10)]
        result = _consolidate_facts([pref] + low_others, max_facts=5, max_chars=99999)
        ids = [f.id for f in result]
        assert "pref-1" in ids

    def test_empty_input_returns_empty(self):
        """Empty input returns empty list without error."""
        result = _consolidate_facts([], max_facts=30, max_chars=3000)
        assert result == []

    def test_persist_within_limits_no_consolidation(self, tmp_path):
        """persist_facts() stays within limits — no eviction needed."""
        (tmp_path / "state").mkdir()
        facts = [_make_fact(f"f{i}") for i in range(3)]
        with _enabled():
            count = persist_facts(facts, tmp_path)
        assert count == 3
        loaded = load_existing_facts(tmp_path)
        assert len(loaded) == 3

    def test_persist_over_count_limit_consolidates(self, tmp_path):
        """persist_facts() evicts surplus low-confidence facts when over MAX_FACTS_COUNT."""
        # Pre-populate with MAX_FACTS_COUNT low-confidence facts
        today = date.today().isoformat()
        existing = [
            {"id": f"existing-{i}", "type": "pattern", "domain": "general",
             "statement": f"Existing fact {i}", "confidence": 0.3,
             "date_added": today, "last_seen": today, "source": "", "ttl_days": None}
            for i in range(MAX_FACTS_COUNT)
        ]
        _write_memory(tmp_path, existing)

        new_high_conf = [_make_fact("high-conf-new", confidence=0.95)]
        with patch("fact_extractor._load_harness_config", return_value={
            "agentic": {"memory_capacity": {"enabled": True, "max_chars": 999999, "max_facts": MAX_FACTS_COUNT}}
        }):
            with _enabled():
                persist_facts(new_high_conf, tmp_path)

        loaded = load_existing_facts(tmp_path)
        assert len(loaded) <= MAX_FACTS_COUNT

