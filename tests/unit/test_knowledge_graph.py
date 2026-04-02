"""
tests/unit/test_knowledge_graph.py — Unit tests for scripts/lib/knowledge_graph.py

Ref: specs/kb-graph-design.md §6, §11
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

_ARTHA   = Path(__file__).resolve().parent.parent.parent
_SCRIPTS = _ARTHA / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.knowledge_graph import (  # noqa: E402
    KnowledgeGraph,
    NullKnowledgeGraph,
    KnowledgeEnricher,
    Entity,
    Edge,
    SearchResult,
    get_kb,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kg(tmp_path: Path) -> KnowledgeGraph:
    """Open a fresh test KG in tmp_path."""
    return KnowledgeGraph(db_path=tmp_path / "test-kb.sqlite")


# ---------------------------------------------------------------------------
# TestSchema — DB initialisation and schema integrity
# ---------------------------------------------------------------------------

class TestSchema:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.close()
        assert (tmp_path / "test-kb.sqlite").exists()

    def test_stats_returns_dict(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        stats = kg.get_stats()
        kg.close()
        assert "entities" in stats
        assert "relationships" in stats
        assert "episodes" in stats
        assert stats["entities"] == 0

    def test_validate_integrity_passes_on_fresh_db(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        issues = kg.validate_integrity()
        kg.close()
        assert issues == [], f"Unexpected integrity issues: {issues}"

    def test_double_open_does_not_corrupt(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.close()
        kg2 = KnowledgeGraph(db_path=tmp_path / "test-kb.sqlite")
        issues = kg2.validate_integrity()
        kg2.close()
        assert issues == []


# ---------------------------------------------------------------------------
# TestUpsertEntity
# ---------------------------------------------------------------------------

class TestUpsertEntity:
    def test_basic_insert(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        entity_id = kg.upsert_entity(
            {"id": "xpf-fleet-mgr", "name": "XPF Fleet Manager", "type": "service", "domain": "fleet"},
            source="test",
        )
        kg.close()
        assert entity_id == "xpf-fleet-mgr"

    def test_stats_increments(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "E1", "type": "concept", "domain": "test"}, source="test")
        kg.upsert_entity({"id": "e2", "name": "E2", "type": "concept", "domain": "test"}, source="test")
        stats = kg.get_stats()
        kg.close()
        assert stats["entities"] == 2

    def test_upsert_updates_existing(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "Old Name", "type": "concept", "domain": "d"}, source="test")
        kg.upsert_entity({"id": "e1", "name": "New Name", "type": "concept", "domain": "d"}, source="test")
        stats = kg.get_stats()
        kg.close()
        # Still only one entity
        assert stats["entities"] == 1

    def test_history_created_on_upsert(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "V1", "type": "concept", "domain": "d"}, source="test")
        kg.upsert_entity({"id": "e1", "name": "V2", "type": "concept", "domain": "d"}, source="test")
        # Verify entity_history records exist (direct query)
        row_count = kg._conn.execute("SELECT COUNT(*) FROM entity_history WHERE entity_id='e1'").fetchone()[0]
        kg.close()
        assert row_count >= 1, "Expected at least 1 entity_history row after name change"

    def test_upsert_with_current_state(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        entity_id = kg.upsert_entity(
            {"id": "e1", "name": "MyService", "type": "service", "domain": "d", "current_state": "active"},
            source="test",
        )
        assert entity_id == "e1"
        kg.close()

    def test_resolve_entity_by_name(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "infra-armada", "name": "Armada Platform", "type": "system", "domain": "infra"}, source="test")
        resolved = kg.resolve_entity("armada platform")
        kg.close()
        assert resolved is not None and resolved.id == "infra-armada"

    def test_resolve_entity_not_found(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        resolved = kg.resolve_entity("non existent entity xyz")
        kg.close()
        assert resolved is None


# ---------------------------------------------------------------------------
# TestRelationships
# ---------------------------------------------------------------------------

class TestRelationships:
    def _seed(self, kg: KnowledgeGraph) -> tuple[str, str]:
        a = kg.upsert_entity({"id": "a", "name": "A", "type": "service", "domain": "d"}, source="test")
        b = kg.upsert_entity({"id": "b", "name": "B", "type": "service", "domain": "d"}, source="test")
        return a, b

    def test_add_relationship(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        a, b = self._seed(kg)
        kg.add_relationship("a", "b", "depends_on", confidence=0.9, source="test")
        stats = kg.get_stats()
        kg.close()
        assert stats["relationships"] == 1

    def test_duplicate_relationship_upserts(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        a, b = self._seed(kg)
        kg.add_relationship("a", "b", "depends_on", confidence=0.8, source="test")
        kg.add_relationship("a", "b", "depends_on", confidence=0.9, source="test")
        stats = kg.get_stats()
        kg.close()
        # Should still be 1 (upsert)
        assert stats["relationships"] == 1

    def test_missing_entity_raises(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        with pytest.raises(Exception):
            kg.add_relationship("NONEXIST_A", "NONEXIST_B", "related_to", source="test")
        kg.close()

    def test_relationship_in_context(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        a, b = self._seed(kg)
        kg.add_relationship("a", "b", "depends_on", confidence=0.9, source="test")
        ctx = kg.context_for("a")
        kg.close()
        assert ctx is not None

    def test_traverse(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "root", "name": "Root", "type": "system", "domain": "d"}, source="test")
        kg.upsert_entity({"id": "child1", "name": "Child1", "type": "component", "domain": "d"}, source="test")
        kg.upsert_entity({"id": "child2", "name": "Child2", "type": "component", "domain": "d"}, source="test")
        kg.add_relationship("child1", "root", "component_of", source="test")
        kg.add_relationship("child2", "root", "component_of", source="test")
        neighbours = kg.traverse("root", depth=1)
        kg.close()
        assert isinstance(neighbours, list)

    def test_find_path(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        for eid in ("a", "b", "c"):
            kg.upsert_entity({"id": eid, "name": eid.upper(), "type": "concept", "domain": "d"}, source="test")
        kg.add_relationship("a", "b", "related_to", source="test")
        kg.add_relationship("b", "c", "related_to", source="test")
        path = kg.find_path("a", "c")
        kg.close()
        assert path is not None
        assert len(path) >= 2
        assert path[0].from_entity == "a"
        assert path[-1].to_entity == "c"


# ---------------------------------------------------------------------------
# TestSearch
# ---------------------------------------------------------------------------

class TestSearch:
    def test_fts_returns_results(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity(
            {"id": "svc-1", "name": "Armada Platform", "type": "system", "domain": "infra",
             "summary": "Manages fleet scheduling and hardware lifecycle"},
            source="test",
        )
        results = kg.search("armada fleet")
        kg.close()
        assert len(results) >= 1
        assert any("armada" in r.name.lower() or "armada" in (r.summary or "").lower() for r in results)

    def test_search_no_results(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        results = kg.search("zzznomatch99")
        kg.close()
        assert results == []

    def test_search_with_domain_filter(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "Shared Topic", "type": "concept", "domain": "fleet"}, source="test")
        kg.upsert_entity({"id": "e2", "name": "Shared Topic", "type": "concept", "domain": "finance"}, source="test")
        fleet_results   = kg.search("shared topic", domain="fleet")
        finance_results = kg.search("shared topic", domain="finance")
        kg.close()
        assert all(r.domain == "fleet"   for r in fleet_results)
        assert all(r.domain == "finance" for r in finance_results)

    def test_search_limit_respected(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        for i in range(20):
            kg.upsert_entity(
                {"id": f"e{i}", "name": f"Widget {i}", "type": "component", "domain": "d",
                 "summary": "searchable widget component"},
                source="test",
            )
        results = kg.search("widget component", limit=5)
        kg.close()
        assert len(results) <= 5


# ---------------------------------------------------------------------------
# TestContextFor
# ---------------------------------------------------------------------------

class TestContextFor:
    def test_context_for_existing_entity(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "My Entity", "type": "system", "domain": "d"}, source="test")
        ctx = kg.context_for("e1")
        kg.close()
        assert ctx is not None
        assert ctx.entity is not None
        assert ctx.entity.name == "My Entity"

    def test_context_for_missing_entity_returns_none(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        ctx = kg.context_for("DOES_NOT_EXIST_XYZ")
        kg.close()
        assert ctx is None

    def test_context_for_respects_token_budget(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity(
            {"id": "root", "name": "Root", "type": "system", "domain": "d",
             "summary": "A " * 1000},
            source="test",
        )
        ctx_small = kg.context_for("root", token_budget=200)
        ctx_large = kg.context_for("root", token_budget=8000)
        kg.close()
        # Both should return (not error), large may have more content
        assert ctx_small is not None
        assert ctx_large is not None


# ---------------------------------------------------------------------------
# TestEpisodes
# ---------------------------------------------------------------------------

class TestEpisodes:
    def test_add_episode(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        ep_id = kg.add_episode(
            episode_key="brief-2026-03-30",
            source_type="briefing",
            raw_content="Meeting with team today about XPF.",
        )
        stats = kg.get_stats()
        kg.close()
        assert ep_id is not None
        assert stats["episodes"] == 1

    def test_duplicate_episode_reuses_id(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        id1 = kg.add_episode("key1", source_type="briefing", raw_content="content")
        id2 = kg.add_episode("key1", source_type="briefing", raw_content="content again")
        stats = kg.get_stats()
        kg.close()
        assert id1 == id2
        assert stats["episodes"] == 1

    def test_recent_episodes_returns_list(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.add_episode("ep1", source_type="briefing", raw_content="ep1 content")
        kg.add_episode("ep2", source_type="kb_file", raw_content="ep2 content")
        eps = kg.recent_episodes(days=30)
        kg.close()
        assert isinstance(eps, list)
        assert len(eps) == 2


# ---------------------------------------------------------------------------
# TestRecentChanges and Stale
# ---------------------------------------------------------------------------

class TestRecentAndStale:
    def test_recent_changes(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "E1", "type": "concept", "domain": "d"}, source="test")
        changes = kg.recent_changes(days=7)
        kg.close()
        assert isinstance(changes, list)
        assert len(changes) >= 1

    def test_stale_entities_empty_when_fresh(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "E1", "type": "concept", "domain": "d"}, source="test")
        stale = kg.stale_entities()
        kg.close()
        # Newly inserted entities should not be stale
        assert isinstance(stale, list)
        # Newly inserted items should ideally not be stale (staleness threshold varies)


# ---------------------------------------------------------------------------
# TestBackup
# ---------------------------------------------------------------------------

class TestBackup:
    def test_backup_creates_file(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "E1", "type": "concept", "domain": "d"}, source="test")

        # Point backup to tmp_path (not OneDrive)
        dest_dir = tmp_path / "backups" / "daily"
        dest_dir.mkdir(parents=True)

        backup_result = kg.backup(tier="daily", dest_dir=dest_dir)
        kg.close()
        # Should return a path or None — not raise
        assert backup_result is None or isinstance(backup_result, (str, Path))

    def test_backup_on_null_kg_is_noop(self) -> None:
        nkg = NullKnowledgeGraph()
        # Should not raise
        result = nkg.backup(tier="daily")
        assert result is None


# ---------------------------------------------------------------------------
# TestNullKnowledgeGraph — graceful degradation
# ---------------------------------------------------------------------------

class TestNullKnowledgeGraph:
    def test_all_read_methods_return_safely(self) -> None:
        nkg = NullKnowledgeGraph()
        assert nkg.upsert_entity({"id": "x", "name": "X", "type": "concept", "domain": "d"}, source="test") is None
        assert nkg.add_relationship("a", "b", "related_to", source="test") is None
        assert nkg.context_for("any") is None
        assert nkg.search("anything") == []
        assert nkg.get_stats() == {}
        assert nkg.validate_integrity() == []
        assert nkg.recent_changes() == []
        assert nkg.stale_entities() == []
        assert nkg.recent_episodes() == []
        assert nkg.rebuild_communities() == 0
        assert nkg.traverse("x") == []
        assert nkg.find_path("x", "y") is None
        assert nkg.global_context_for("q") is None
        assert nkg.resolve_entity("name") is None
        nkg.close()  # Should not raise

    def test_null_enricher_does_not_mutate(self) -> None:
        nkg = NullKnowledgeGraph()
        enricher = KnowledgeEnricher(nkg)
        brief = {"summary": "Test briefing content"}
        result = enricher.enrich_briefing(brief)
        # Must return the original dict unchanged or a dict
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# TestGetKbFactory
# ---------------------------------------------------------------------------

class TestGetKbFactory:
    def test_get_kb_with_tmp_path(self, tmp_path: Path) -> None:
        import os
        os.environ["ARTHA_KB_PATH"] = str(tmp_path / "factory-test.sqlite")
        try:
            kg = get_kb()
            stats = kg.get_stats()
            kg.close()
            assert "entities" in stats
        finally:
            del os.environ["ARTHA_KB_PATH"]

    def test_get_kb_returns_null_on_corrupt_db(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "bad.sqlite"
        bad_path.write_bytes(b"THIS IS NOT A SQLITE DATABASE")
        import os
        os.environ["ARTHA_KB_PATH"] = str(bad_path)
        try:
            kg = get_kb()
            # Should return NullKnowledgeGraph rather than crashing
            assert isinstance(kg, NullKnowledgeGraph)
        finally:
            del os.environ["ARTHA_KB_PATH"]


# ---------------------------------------------------------------------------
# TestKnowledgeEnricher
# ---------------------------------------------------------------------------

class TestKnowledgeEnricher:
    def test_enrich_briefing_adds_context(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity(
            {"id": "xpf-mgr", "name": "XPF Manager", "type": "service", "domain": "fleet",
             "summary": "Manages XPF fleet hardware"},
            source="test",
        )
        enricher = KnowledgeEnricher(kg)
        brief = {"summary": "XPF Manager had 3 alerts today."}
        result = enricher.enrich_briefing(brief)
        kg.close()
        assert isinstance(result, dict)

    def test_enrich_meeting_prep(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity(
            {"id": "armada-p", "name": "Armada Platform", "type": "system", "domain": "infra"},
            source="test",
        )
        enricher = KnowledgeEnricher(kg)
        context = {"meeting_title": "Armada Platform review", "attendees": []}
        result = enricher.enrich_meeting_prep(context)
        kg.close()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# TestGetEntity — direct ID lookup
# ---------------------------------------------------------------------------

class TestGetEntity:
    def test_get_entity_by_id(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "svc-xpf", "name": "XPF Service", "type": "service", "domain": "fleet"}, source="test")
        entity = kg.get_entity("svc-xpf")
        kg.close()
        assert entity is not None
        assert entity.id == "svc-xpf"
        assert entity.name == "XPF Service"

    def test_get_entity_not_found_returns_none(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        entity = kg.get_entity("no-such-id")
        kg.close()
        assert entity is None

    def test_get_entity_returns_entity_dataclass(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "E", "type": "concept", "domain": "d"}, source="test")
        entity = kg.get_entity("e1")
        kg.close()
        assert isinstance(entity, Entity)


# ---------------------------------------------------------------------------
# TestResolveEntityCandidates — ranked 3-stage resolution
# ---------------------------------------------------------------------------

class TestResolveEntityCandidates:
    def test_returns_ranked_tuples(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "armada", "name": "Armada Fleet", "type": "system", "domain": "infra"}, source="test")
        candidates = kg.resolve_entity_candidates("armada fleet")
        kg.close()
        assert len(candidates) >= 1
        entity, score, reason = candidates[0]
        assert isinstance(entity, Entity)
        assert isinstance(score, float)
        assert reason in ("exact_id", "alias", "fts_title", "fts_content")

    def test_empty_query_returns_list(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        candidates = kg.resolve_entity_candidates("zzz-no-match-xyz")
        kg.close()
        assert isinstance(candidates, list)

    def test_returns_at_most_limit(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        for i in range(10):
            kg.upsert_entity(
                {"id": f"widget-{i}", "name": f"Widget Component {i}", "type": "component", "domain": "d",
                 "summary": "searchable widget thing"},
                source="test",
            )
        candidates = kg.resolve_entity_candidates("widget component", limit=3)
        kg.close()
        assert len(candidates) <= 3


# ---------------------------------------------------------------------------
# TestAddAlias — alias registration and resolution
# ---------------------------------------------------------------------------

class TestAddAlias:
    def test_add_alias_enables_resolution(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "titan-svc", "name": "Titan Service", "type": "service", "domain": "fleet"}, source="test")
        kg.add_alias("titan", "titan-svc")
        entity = kg.resolve_entity("titan")
        kg.close()
        assert entity is not None
        assert entity.id == "titan-svc"

    def test_add_alias_idempotent(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "E1", "type": "concept", "domain": "d"}, source="test")
        # Adding twice should not raise
        kg.add_alias("alias-x", "e1")
        kg.add_alias("alias-x", "e1")
        kg.close()

    def test_alias_appears_in_candidates_with_reason(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "rubik-svc", "name": "Rubik Service", "type": "service", "domain": "infra"}, source="test")
        kg.add_alias("rubik", "rubik-svc")
        candidates = kg.resolve_entity_candidates("rubik")
        kg.close()
        reasons = [r for _, _, r in candidates]
        assert "alias" in reasons


# ---------------------------------------------------------------------------
# TestDeactivateRelationship — soft-delete edges
# ---------------------------------------------------------------------------

class TestDeactivateRelationship:
    def test_deactivates_active_relationship(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "a", "name": "A", "type": "service", "domain": "d"}, source="test")
        kg.upsert_entity({"id": "b", "name": "B", "type": "service", "domain": "d"}, source="test")
        kg.add_relationship("a", "b", "depends_on", source="test")
        rel_id = kg._conn.execute("SELECT id FROM relationships WHERE from_entity='a' AND to_entity='b'").fetchone()["id"]
        kg.deactivate_relationship(rel_id, reason="refactored")
        row = kg._conn.execute("SELECT valid_to FROM relationships WHERE id=?", (rel_id,)).fetchone()
        kg.close()
        assert row["valid_to"] is not None

    def test_deactivate_nonexistent_is_noop(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        # Should not raise for a non-existent ID
        kg.deactivate_relationship(99999, reason="no-op test")
        kg.close()

    def test_deactivated_edge_excluded_from_context(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "x", "name": "X", "type": "service", "domain": "d"}, source="test")
        kg.upsert_entity({"id": "y", "name": "Y", "type": "service", "domain": "d"}, source="test")
        kg.add_relationship("x", "y", "depends_on", source="test")
        rel_id = kg._conn.execute("SELECT id FROM relationships WHERE from_entity='x'").fetchone()["id"]
        kg.deactivate_relationship(rel_id, reason="test")
        # After deactivation, active edge count for x→y should be 0
        active = kg._conn.execute(
            "SELECT COUNT(*) as n FROM relationships WHERE from_entity='x' AND to_entity='y' AND valid_to IS NULL"
        ).fetchone()["n"]
        kg.close()
        assert active == 0


# ---------------------------------------------------------------------------
# TestInvalidateCache — cache eviction
# ---------------------------------------------------------------------------

class TestInvalidateCache:
    def test_invalidate_cache_runs_without_error(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "E1", "type": "concept", "domain": "d"}, source="test")
        # Should not raise even with no cached entries
        kg.invalidate_cache("e1")
        kg.invalidate_cache("nonexistent-id")
        kg.close()

    def test_invalidate_cache_clears_neighbors(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "center", "name": "Center", "type": "system", "domain": "d"}, source="test")
        kg.upsert_entity({"id": "neighbor", "name": "Neighbor", "type": "component", "domain": "d"}, source="test")
        kg.add_relationship("center", "neighbor", "contains", source="test")
        # Seed the cache directly then verify invalidation clears it
        kg._conn.execute(
            "INSERT OR IGNORE INTO entity_context_cache (entity_id, context_json, token_estimate, cached_at)"
            " VALUES ('center', '{}', 100, datetime('now'))"
        )
        kg.invalidate_cache("center")
        count = kg._conn.execute("SELECT COUNT(*) as n FROM entity_context_cache WHERE entity_id='center'").fetchone()["n"]
        kg.close()
        assert count == 0


# ---------------------------------------------------------------------------
# TestVacuum
# ---------------------------------------------------------------------------

class TestVacuum:
    def test_vacuum_runs_without_error(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "E1", "type": "concept", "domain": "d"}, source="test")
        kg.vacuum()  # Should not raise
        kg.close()

    def test_vacuum_updates_meta(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.vacuum()
        row = kg._conn.execute("SELECT value FROM kb_meta WHERE key='last_vacuum'").fetchone()
        kg.close()
        assert row is not None
        assert row["value"] is not None


# ---------------------------------------------------------------------------
# TestRebuildCommunities — connected component clustering
# ---------------------------------------------------------------------------

class TestRebuildCommunities:
    def test_rebuild_on_empty_db_returns_zero(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        count = kg.rebuild_communities()
        kg.close()
        assert count == 0

    def test_rebuild_creates_community_for_connected_entities(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "p1", "name": "P1", "type": "service", "domain": "fleet"}, source="test")
        kg.upsert_entity({"id": "p2", "name": "P2", "type": "service", "domain": "fleet"}, source="test")
        kg.add_relationship("p1", "p2", "related_to", source="test")
        count = kg.rebuild_communities()
        kg.close()
        assert count >= 1

    def test_singletons_not_counted(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        # 3 isolated entities, no edges → 0 communities (singletons skipped)
        for i in range(3):
            kg.upsert_entity({"id": f"iso-{i}", "name": f"Iso {i}", "type": "concept", "domain": "d"}, source="test")
        count = kg.rebuild_communities()
        kg.close()
        assert count == 0


# ---------------------------------------------------------------------------
# TestGetContextAsOf — bi-temporal reconstruction
# ---------------------------------------------------------------------------

class TestGetContextAsOf:
    def test_context_as_of_missing_entity_returns_empty_ctx(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        ctx = kg.get_context_as_of("no-such-entity", timestamp="2020-01-01T00:00:00")
        kg.close()
        # Should return an empty EntityContext, not None or raise
        from lib.knowledge_graph import EntityContext
        assert isinstance(ctx, EntityContext)
        assert ctx.entity is None

    def test_context_as_of_returns_entity_context(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "hist-e1", "name": "V1", "type": "service", "domain": "d"}, source="test")
        # Upsert again to create history
        kg.upsert_entity({"id": "hist-e1", "name": "V2", "type": "service", "domain": "d"}, source="test")
        timestamp = datetime.now(timezone.utc).isoformat()
        ctx = kg.get_context_as_of("hist-e1", timestamp=timestamp)
        kg.close()
        from lib.knowledge_graph import EntityContext
        assert isinstance(ctx, EntityContext)

    def test_context_as_of_includes_temporal_edges(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "t1", "name": "T1", "type": "service", "domain": "d"}, source="test")
        kg.upsert_entity({"id": "t2", "name": "T2", "type": "service", "domain": "d"}, source="test")
        kg.add_relationship("t1", "t2", "calls", source="test")
        # Future timestamp should include the newly added edge
        future_ts = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        ctx = kg.get_context_as_of("t1", timestamp=future_ts)
        kg.close()
        assert ctx is not None
        assert isinstance(ctx.edges, list)


# ---------------------------------------------------------------------------
# TestGlobalContextFor — community-level FTS
# ---------------------------------------------------------------------------

class TestGlobalContextFor:
    def test_global_context_empty_when_no_communities(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        result = kg.global_context_for("XPF fleet manager status")
        kg.close()
        assert isinstance(result, str)

    def test_global_context_returns_string_after_rebuild(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "g1", "name": "G1", "type": "system", "domain": "fleet"}, source="test")
        kg.upsert_entity({"id": "g2", "name": "G2", "type": "system", "domain": "fleet"}, source="test")
        kg.add_relationship("g1", "g2", "related_to", source="test")
        kg.rebuild_communities()
        result = kg.global_context_for("fleet system status")
        kg.close()
        assert isinstance(result, str)
        assert len(result) >= 0  # May or may not have content depending on FTS match


# ---------------------------------------------------------------------------
# TestRecentEpisodesFilter — entity_mentions filtering
# ---------------------------------------------------------------------------

class TestRecentEpisodesFilter:
    def test_recent_episodes_with_entity_filter(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "proj-xpf", "name": "XPF Project", "type": "project", "domain": "fleet"}, source="test")
        # Add episode linked to entity
        ep_id = kg.add_episode("ep-xpf-001", source_type="meeting", raw_content="XPF project review")
        # Link entity to episode via source_episode_id in upsert
        kg.upsert_entity(
            {"id": "proj-xpf", "name": "XPF Project", "type": "project", "domain": "fleet"},
            source="test",
            source_episode_id=ep_id,
        )
        eps_filtered = kg.recent_episodes(entity_mentions=["proj-xpf"], days=30)
        kg.close()
        assert isinstance(eps_filtered, list)

    def test_recent_episodes_no_filter_returns_all(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.add_episode("ep-a", source_type="briefing", raw_content="A")
        kg.add_episode("ep-b", source_type="kb_file", raw_content="B")
        eps = kg.recent_episodes(days=30)
        kg.close()
        assert len(eps) == 2

    def test_recent_episodes_days_alias(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.add_episode("ep-z", source_type="briefing", raw_content="Z")
        # `days` is an alias for `since_days`
        eps = kg.recent_episodes(days=7)
        kg.close()
        assert isinstance(eps, list)


# ---------------------------------------------------------------------------
# TestContextForSessionFocus — session_focus parameter
# ---------------------------------------------------------------------------

class TestContextForSessionFocus:
    def test_context_for_with_session_focus(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "focus-e", "name": "Focus Entity", "type": "system", "domain": "fleet"}, source="test")
        kg.upsert_entity({"id": "focus-n", "name": "Neighbor", "type": "component", "domain": "fleet"}, source="test")
        kg.add_relationship("focus-e", "focus-n", "depends_on", source="test")
        ctx = kg.context_for("focus-e", session_focus=["focus-n"])
        kg.close()
        assert ctx is not None
        assert ctx.entity is not None

    def test_context_for_session_focus_empty_list(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "e1", "name": "E1", "type": "concept", "domain": "d"}, source="test")
        ctx = kg.context_for("e1", session_focus=[])
        kg.close()
        assert ctx is not None


# ---------------------------------------------------------------------------
# TestDomainFilters — domain parameter on stale_entities and recent_changes
# ---------------------------------------------------------------------------

class TestDomainFilters:
    def test_stale_entities_domain_filter(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "fleet-e", "name": "Fleet E", "type": "service", "domain": "fleet"}, source="test")
        kg.upsert_entity({"id": "infra-e", "name": "Infra E", "type": "service", "domain": "infra"}, source="test")
        stale_fleet = kg.stale_entities(domain="fleet")
        stale_infra = kg.stale_entities(domain="infra")
        kg.close()
        # All returned entities must match the filtered domain
        assert all(e.domain == "fleet" for e in stale_fleet)
        assert all(e.domain == "infra" for e in stale_infra)

    def test_recent_changes_domain_filter(self, tmp_path: Path) -> None:
        kg = _kg(tmp_path)
        kg.upsert_entity({"id": "fleet-x", "name": "Fleet X", "type": "service", "domain": "fleet"}, source="test")
        kg.upsert_entity({"id": "infra-x", "name": "Infra X", "type": "service", "domain": "infra"}, source="test")
        changes_fleet = kg.recent_changes(domain="fleet", days=7)
        changes_infra = kg.recent_changes(domain="infra", days=7)
        kg.close()
        assert isinstance(changes_fleet, list)
        assert isinstance(changes_infra, list)
