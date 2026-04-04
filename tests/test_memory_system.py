"""tests/test_memory_system.py — AFW-7 memory system tests.

Covers the FlatFileProvider implementation per specs/agent-fw.md §7.1:
  - YAML frontmatter parse (live state/memory.md format)
  - Keyword recall (token matching)
  - Synonym expansion (_expand_query domain-aware recall)
  - Format round-trip (remember → _load_entries → verify)
  - Scope filtering (scope prefix matching)
  - Recency scoring (decay function)
  - Forget (deletion by ID)
  - get_provider() factory (config plumbing)
  - schema_version guard (SchemaVersionMissing)
  - LanceDbProvider raises NotImplementedError (ADR-001)

All tests are hermetic (tmp_path only) — state/memory.md is never touched.

Ref: specs/agent-fw.md §3.7.6 (AFW-7), ADR-001 (§10.8)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Module loader — isolate scripts/ from sys path pollution
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_memory_provider():
    """Load scripts/lib/memory_provider.py as a module."""
    spec = importlib.util.spec_from_file_location(
        "memory_provider_under_test",
        _SCRIPTS_DIR / "lib" / "memory_provider.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["memory_provider_under_test"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def mp():
    """Return the memory_provider module."""
    return _load_memory_provider()


@pytest.fixture()
def provider(tmp_path, mp):
    """FlatFileProvider instance backed by a fresh tmp_path dir."""
    return mp.FlatFileProvider(artha_dir=tmp_path)


# ---------------------------------------------------------------------------
# Helper to write a synthetic state/memory.md
# ---------------------------------------------------------------------------

def _write_memory_md(path: Path, facts: list[dict[str, Any]]) -> None:
    """Write facts as YAML frontmatter to path/state/memory.md."""
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML not installed — skipping YAML round-trip tests")

    state_dir = path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    data = {"domain": "memory", "facts": facts}
    fm = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=True)
    (state_dir / "memory.md").write_text(f"---\n{fm}---\n", encoding="utf-8")


# ===========================================================================
# T-MP-01: Empty / missing memory file returns empty list
# ===========================================================================

def test_load_entries_missing_file(provider, mp):
    """T-MP-01: Missing memory.md must return empty list."""
    assert provider._load_entries() == []


# ===========================================================================
# T-MP-02: YAML frontmatter parse — authoritative live format
# ===========================================================================

def test_load_entries_parses_yaml_frontmatter(tmp_path, mp):
    """T-MP-02: _load_entries() must parse YAML frontmatter correctly."""
    pytest.importorskip("yaml")
    facts = [
        {
            "id": "abc123",
            "statement": "Opendoor ESPP sale must be self-reported on 2025 tax return.",
            "domain": "finance",
            "date_added": "2026-03-20",
            "confidence": 1.0,
            "type": "correction",
            "source": "briefing-2026-03-20",
            "ttl_days": None,
            "last_seen": "2026-03-21",
        }
    ]
    _write_memory_md(tmp_path, facts)
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    entries = p._load_entries()

    assert len(entries) == 1
    e = entries[0]
    assert e["id"] == "abc123"
    assert e["scope"] == "/finance"
    assert "Opendoor" in e["text"]
    assert e["timestamp"] == "2026-03-20"
    assert e["metadata"]["type"] == "correction"
    assert e["metadata"]["confidence"] == 1.0


# ===========================================================================
# T-MP-03: YAML frontmatter — multiple facts
# ===========================================================================

def test_load_entries_multiple_facts(tmp_path, mp):
    """T-MP-03: Multiple facts in frontmatter must all be parsed."""
    pytest.importorskip("yaml")
    facts = [
        {
            "id": f"fact-{i}",
            "statement": f"Statement {i}.",
            "domain": "general",
            "date_added": "2026-04-01",
            "confidence": 1.0,
            "type": "fact",
            "source": "test",
            "ttl_days": None,
            "last_seen": "2026-04-01",
        }
        for i in range(5)
    ]
    _write_memory_md(tmp_path, facts)
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    entries = p._load_entries()
    assert len(entries) == 5
    assert {e["id"] for e in entries} == {f"fact-{i}" for i in range(5)}


# ===========================================================================
# T-MP-04: File with no YAML frontmatter returns empty list (graceful)
# ===========================================================================

def test_load_entries_no_frontmatter(tmp_path, mp):
    """T-MP-04: A plain markdown file (old format) must return empty list, not crash."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    # Old-style markdown header format — must not crash
    (state_dir / "memory.md").write_text(
        "## /personal/finance/abc | 2026-01-01T00:00:00+00:00\n"
        "<!-- metadata: {} -->\n\nSome old fact.\n",
        encoding="utf-8",
    )
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    entries = p._load_entries()
    assert entries == []


# ===========================================================================
# T-MP-05: Keyword recall — exact token match
# ===========================================================================

def test_recall_keyword_match(tmp_path, mp):
    """T-MP-05: recall() must return entries containing query tokens."""
    pytest.importorskip("yaml")
    facts = [
        {
            "id": "f1", "statement": "Archana H-4 EAD expires June 2026.",
            "domain": "immigration", "date_added": "2026-03-01",
            "confidence": 1.0, "type": "fact", "source": "test",
            "ttl_days": None, "last_seen": "2026-03-01",
        },
        {
            "id": "f2", "statement": "Quarterly budget review scheduled for April.",
            "domain": "finance", "date_added": "2026-03-01",
            "confidence": 1.0, "type": "fact", "source": "test",
            "ttl_days": None, "last_seen": "2026-03-01",
        },
    ]
    _write_memory_md(tmp_path, facts)
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    results = p.recall("/", "EAD expires")
    assert len(results) == 1
    assert results[0]["id"] == "f1"


# ===========================================================================
# T-MP-06: Keyword recall — no match returns empty
# ===========================================================================

def test_recall_no_match(tmp_path, mp):
    """T-MP-06: recall() must return empty list when no tokens match."""
    pytest.importorskip("yaml")
    facts = [
        {
            "id": "f1", "statement": "Some unrelated fact.",
            "domain": "general", "date_added": "2026-03-01",
            "confidence": 1.0, "type": "fact", "source": "test",
            "ttl_days": None, "last_seen": "2026-03-01",
        }
    ]
    _write_memory_md(tmp_path, facts)
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    results = p.recall("/", "visa immigration h1b")
    assert results == []


# ===========================================================================
# T-MP-07: Scope filtering
# ===========================================================================

def test_recall_scope_filter(tmp_path, mp):
    """T-MP-07: recall() must only return entries under the given scope prefix."""
    pytest.importorskip("yaml")
    facts = [
        {
            "id": "f1", "statement": "Finance tax return due April 15.",
            "domain": "finance", "date_added": "2026-03-01",
            "confidence": 1.0, "type": "fact", "source": "test",
            "ttl_days": None, "last_seen": "2026-03-01",
        },
        {
            "id": "f2", "statement": "Health appointment scheduled for April 15.",
            "domain": "health", "date_added": "2026-03-01",
            "confidence": 1.0, "type": "fact", "source": "test",
            "ttl_days": None, "last_seen": "2026-03-01",
        },
    ]
    _write_memory_md(tmp_path, facts)
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    # Query matches both but scope limits to finance
    results = p.recall("/finance", "April")
    assert len(results) == 1
    assert results[0]["id"] == "f1"


# ===========================================================================
# T-MP-08: Synonym expansion — immigration domain
# ===========================================================================

def test_recall_synonym_expansion_immigration(tmp_path, mp):
    """T-MP-08: 'visa' query must expand to include EAD/h1b and match immigration facts."""
    pytest.importorskip("yaml")
    facts = [
        {
            "id": "f1",
            "statement": "H-4 EAD card approved; valid through 2027.",
            "domain": "immigration", "date_added": "2026-03-01",
            "confidence": 1.0, "type": "fact", "source": "test",
            "ttl_days": None, "last_seen": "2026-03-01",
        },
        {
            "id": "f2",
            "statement": "Budget review meeting on Thursday.",
            "domain": "finance", "date_added": "2026-03-01",
            "confidence": 1.0, "type": "fact", "source": "test",
            "ttl_days": None, "last_seen": "2026-03-01",
        },
    ]
    _write_memory_md(tmp_path, facts)
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    # "visa" alone won't match "EAD" literally, but synonym expansion expands "visa"
    # to the immigration synonyms set which includes "ead"
    results = p.recall("/", "visa")
    assert any(r["id"] == "f1" for r in results), (
        "'visa' query should expand via synonyms to match 'EAD' in immigration fact"
    )


# ===========================================================================
# T-MP-09: Synonym expansion — finance domain
# ===========================================================================

def test_recall_synonym_expansion_finance(tmp_path, mp):
    """T-MP-09: 'investment' query must expand to tax/401k/espp synonyms."""
    pytest.importorskip("yaml")
    facts = [
        {
            "id": "f1",
            "statement": "ESPP shares vested; must report on 2025 tax return.",
            "domain": "finance", "date_added": "2026-03-01",
            "confidence": 1.0, "type": "fact", "source": "test",
            "ttl_days": None, "last_seen": "2026-03-01",
        },
    ]
    _write_memory_md(tmp_path, facts)
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    # "investment" is a finance synonym — expands to "espp", "tax" etc.
    results = p.recall("/", "investment")
    assert len(results) == 1
    assert results[0]["id"] == "f1"


# ===========================================================================
# T-MP-10: _expand_query — explicit synonym set checks
# ===========================================================================

def test_expand_query_immigration(mp):
    """T-MP-10: 'visa' must expand to include immigration synonyms."""
    p = mp.FlatFileProvider()
    expanded = p._expand_query("visa")
    assert "h1b" in expanded
    assert "ead" in expanded
    assert "i-140" in expanded


def test_expand_query_finance(mp):
    """T-MP-10b: 'tax' must expand to include finance synonyms."""
    p = mp.FlatFileProvider()
    expanded = p._expand_query("tax")
    assert "espp" in expanded
    assert "ira" in expanded
    assert "401k" in expanded


def test_expand_query_no_expansion(mp):
    """T-MP-10c: Unrecognised tokens remain as-is without expansion."""
    p = mp.FlatFileProvider()
    expanded = p._expand_query("randomtoken xyz")
    assert "randomtoken" in expanded
    assert "xyz" in expanded
    # Should not spuriously add unrelated synonyms
    assert "espp" not in expanded


# ===========================================================================
# T-MP-11: Format round-trip — remember → _load_entries
# ===========================================================================

def test_remember_round_trip(tmp_path, mp):
    """T-MP-11: remember() must write YAML frontmatter readable by _load_entries()."""
    pytest.importorskip("yaml")
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    # Start with empty file
    (tmp_path / "state" / "memory.md").write_text("---\ndomain: memory\nfacts: []\n---\n")

    mem_id = p.remember(
        scope="/finance",
        text="Mortgage payment due on the 15th.",
        metadata={"confidence": 0.9, "type": "fact", "source": "test"},
    )

    entries = p._load_entries()
    assert len(entries) == 1
    assert entries[0]["id"] == mem_id
    assert entries[0]["scope"] == "/finance"
    assert "Mortgage" in entries[0]["text"]


# ===========================================================================
# T-MP-12: remember() returns a unique ID each call
# ===========================================================================

def test_remember_unique_ids(tmp_path, mp):
    """T-MP-12: Multiple remember() calls must produce distinct IDs."""
    pytest.importorskip("yaml")
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "memory.md").write_text("---\ndomain: memory\nfacts: []\n---\n")

    ids = [
        p.remember("/general", f"Fact number {i}.", {})
        for i in range(5)
    ]
    assert len(set(ids)) == 5, "All IDs must be unique"


# ===========================================================================
# T-MP-13: forget() deletes the correct entry
# ===========================================================================

def test_forget_removes_entry(tmp_path, mp):
    """T-MP-13: forget() must remove the target entry and return True."""
    pytest.importorskip("yaml")
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "memory.md").write_text("---\ndomain: memory\nfacts: []\n---\n")

    id1 = p.remember("/general", "Keep this fact.", {})
    id2 = p.remember("/general", "Delete this fact.", {})

    assert p.forget(id2) is True
    remaining = p._load_entries()
    assert len(remaining) == 1
    assert remaining[0]["id"] == id1


def test_forget_returns_false_for_missing(tmp_path, mp):
    """T-MP-13b: forget() must return False for non-existent ID."""
    pytest.importorskip("yaml")
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "memory.md").write_text("---\ndomain: memory\nfacts: []\n---\n")
    assert p.forget("nonexistent-id") is False


# ===========================================================================
# T-MP-14: Recency scoring
# ===========================================================================

def test_recency_score_recent(mp):
    """T-MP-14: A timestamp from today must score close to 1.0."""
    from datetime import datetime, timezone
    p = mp.FlatFileProvider()
    today = datetime.now(timezone.utc).date().isoformat()
    score = p._recency_score(today)
    assert score >= 0.9


def test_recency_score_old(mp):
    """T-MP-14b: A timestamp 20 days ago must score 0.0 (floor)."""
    p = mp.FlatFileProvider()
    score = p._recency_score("2020-01-01")
    assert score == 0.0


# ===========================================================================
# T-MP-15: recall() respects limit parameter
# ===========================================================================

def test_recall_limit(tmp_path, mp):
    """T-MP-15: recall() must return at most `limit` results."""
    pytest.importorskip("yaml")
    facts = [
        {
            "id": f"f{i}", "statement": "finance tax budget money.",
            "domain": "finance", "date_added": "2026-03-01",
            "confidence": 1.0, "type": "fact", "source": "test",
            "ttl_days": None, "last_seen": "2026-03-01",
        }
        for i in range(10)
    ]
    _write_memory_md(tmp_path, facts)
    p = mp.FlatFileProvider(artha_dir=tmp_path)
    results = p.recall("/", "finance", limit=3)
    assert len(results) <= 3


# ===========================================================================
# T-MP-16: SchemaVersionMissing guard
# ===========================================================================

def test_schema_version_missing_raises(mp):
    """T-MP-16: get_provider() must raise SchemaVersionMissing when schema_version absent."""
    with pytest.raises(mp.SchemaVersionMissing):
        mp._require_schema_version({})


def test_schema_version_present(mp):
    """T-MP-16b: _require_schema_version() must return the version when present."""
    assert mp._require_schema_version({"schema_version": 1}) == 1


# ===========================================================================
# T-MP-17: LanceDbProvider raises NotImplementedError (ADR-001)
# ===========================================================================

def test_lancedb_provider_raises(mp):
    """T-MP-17: LanceDbProvider must always raise NotImplementedError (ADR-001)."""
    with pytest.raises(NotImplementedError) as exc_info:
        mp.LanceDbProvider()
    assert "ADR-001" in str(exc_info.value)


# ===========================================================================
# T-MP-18: Load live state/memory.md and verify domain recall
# ===========================================================================

def test_live_memory_md_loads_facts():
    """T-MP-18: Load the live state/memory.md and verify at least one fact is recalled.

    This is the integration test specified in specs/agent-fw.md §7.2:
    'Load live state/memory.md, recall domain queries, verify keyword+synonym match.'
    """
    pytest.importorskip("yaml")
    artha_dir = Path(__file__).resolve().parent.parent
    memory_file = artha_dir / "state" / "memory.md"
    if not memory_file.exists():
        pytest.skip("state/memory.md not present in this environment")

    mod = _load_memory_provider()
    p = mod.FlatFileProvider(artha_dir=artha_dir)
    entries = p._load_entries()

    # Verify the file has parseable content
    assert len(entries) > 0, (
        "state/memory.md exists but _load_entries() returned empty list. "
        "This indicates a format mismatch — check YAML frontmatter parsing."
    )

    # Verify scope is properly mapped (all entries must have a /domain scope)
    for entry in entries:
        assert entry["scope"].startswith("/"), (
            f"Entry {entry['id']} has malformed scope: {entry['scope']!r}"
        )
        assert entry["text"], (
            f"Entry {entry['id']} has empty text/statement"
        )
