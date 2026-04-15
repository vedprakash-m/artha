#!/usr/bin/env python3
# pii-guard: ignore-file — KB graph; entity names are professional/work data, not PII
"""
scripts/lib/knowledge_graph.py — Artha Work OS Knowledge Graph

SQLite-backed directed property graph for work-domain knowledge.
Implements the full API contract from specs/kb-graph-design.md §6.

Architecture:
  - Live DB is machine-local only (%LOCALAPPDATA%\\Artha\\kb.sqlite on Windows)
    because SQLite WAL mode is incompatible with cloud sync agents (OneDrive,
    Dropbox).  See §6.1 OneDrive Safety Rule.
  - Backups go to OneDrive backups/{daily,weekly,monthly}/kb-*.sqlite via
    sqlite3.Connection.backup() which produces a fully checkpointed, WAL-free
    snapshot safe for cloud sync.
  - The LLM never writes SQL.  All graph access is through typed Python methods.
  - This module is an ENHANCEMENT layer — all KB calls are wrapped in
    try/except by pipeline callers so the briefing works without the graph.

Design principles: specs/kb-graph-design.md §0
Ref: specs/kb-graph-design.md §4–§8, §10.1
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sqlite3
import tempfile
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema version — increment on any DDL change
# ---------------------------------------------------------------------------
_SCHEMA_VERSION = "4"

# ---------------------------------------------------------------------------
# Source weight seed data (spec §4.10)
# ---------------------------------------------------------------------------
_SOURCE_WEIGHT_SEEDS = [
    ("ado_sync",          0.90, "ADO work items are authoritative for project ownership"),
    ("kusto_direct",      0.90, "Kusto query results are authoritative for telemetry facts"),
    ("structured_import", 0.85, "Deterministic parser from canonical state files — high confidence"),
    ("kb_file",           0.80, "Bootstrap from KB markdown files — high editorial confidence"),
    ("manual",            0.85, "User manually entered fact — high trust"),
    ("workiq",            0.70, "WorkIQ research output — medium confidence, needs validation"),
    ("meeting",           0.65, "Extracted from meeting notes — medium confidence"),
    ("reflection_yaml",   0.80, "Deterministic YAML frontmatter from weekly reflections"),
    ("reflection_body",   0.60, "Heuristic extraction from reflection body text"),
    ("llm_extract",       0.50, "LLM-extracted from prose, requires human review"),
    ("sharepoint",        0.65, "SharePoint-ingested documents — medium-high confidence"),
    ("inbox",             0.60, "Inbox drop-folder files — medium confidence, user-curated"),
    ("state_md",          0.55, "LLM-synthesised state/*.md files — lower confidence due to hallucination risk"),
]

# ---------------------------------------------------------------------------
# Cloud-sync folder patterns for OneDrive safety check (§6.1)
# ---------------------------------------------------------------------------
_CLOUD_SYNC_MARKERS = (
    "OneDrive",
    "Dropbox",
    "iCloudDrive",
    "Library/Mobile Documents",  # macOS iCloud
    "Google Drive",
)

# ---------------------------------------------------------------------------
# Full DDL — table creation order is MANDATORY (spec §6.3)
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
-- Step 1: episodes must come first — entities/relationships FK into it
CREATE TABLE IF NOT EXISTS episodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_key TEXT UNIQUE NOT NULL,
    source_type TEXT NOT NULL,
    raw_content TEXT,
    ingested_at TEXT NOT NULL,
    quality     TEXT NOT NULL DEFAULT 'raw'
);
CREATE INDEX IF NOT EXISTS idx_episode_source ON episodes(source_type);
CREATE INDEX IF NOT EXISTS idx_episode_date   ON episodes(ingested_at);

-- Step 2a: entities
CREATE TABLE IF NOT EXISTS entities (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    type               TEXT NOT NULL,
    domain             TEXT,
    domains            TEXT,
    summary            TEXT,
    detail             TEXT,
    current_state      TEXT,
    confidence         REAL NOT NULL DEFAULT 0.5,
    last_validated     TEXT,
    staleness_ttl_days      INTEGER NOT NULL DEFAULT 90,
    validation_method       TEXT,
    source                  TEXT,
    source_type             TEXT,
    corroborating_sources   INTEGER NOT NULL DEFAULT 0,
    excerpt_hash            TEXT,
    lifecycle_stage         TEXT NOT NULL DEFAULT 'unknown',
    source_episode_id       INTEGER,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    FOREIGN KEY (source_episode_id) REFERENCES episodes(id)
);
CREATE INDEX IF NOT EXISTS idx_entity_domain ON entities(domain);
CREATE INDEX IF NOT EXISTS idx_entity_type   ON entities(type);

-- Step 2b: entity_history
CREATE TABLE IF NOT EXISTS entity_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id  TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    field      TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    changed_by TEXT,
    session_id        TEXT,
    change_source_ref TEXT,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
CREATE INDEX IF NOT EXISTS idx_history_entity ON entity_history(entity_id, changed_at);
CREATE INDEX IF NOT EXISTS idx_history_field  ON entity_history(field);

-- Step 2c: entity_aliases
CREATE TABLE IF NOT EXISTS entity_aliases (
    alias     TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    added_at  TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
CREATE INDEX IF NOT EXISTS idx_alias_entity ON entity_aliases(entity_id);

-- Step 3: relationships
CREATE TABLE IF NOT EXISTS relationships (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entity       TEXT NOT NULL,
    to_entity         TEXT NOT NULL,
    rel_type          TEXT NOT NULL,
    label             TEXT,
    detail            TEXT,
    strength          TEXT NOT NULL DEFAULT 'strong',
    confidence        REAL NOT NULL DEFAULT 0.5,
    valid_from        TEXT,
    valid_to          TEXT,
    last_validated    TEXT,
    source            TEXT,
    source_episode_id INTEGER,
    created_at        TEXT NOT NULL,
    FOREIGN KEY (from_entity) REFERENCES entities(id),
    FOREIGN KEY (to_entity)   REFERENCES entities(id),
    FOREIGN KEY (source_episode_id) REFERENCES episodes(id)
);
CREATE INDEX IF NOT EXISTS idx_rel_from ON relationships(from_entity);
CREATE INDEX IF NOT EXISTS idx_rel_to   ON relationships(to_entity);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(rel_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rel_unique
    ON relationships(from_entity, to_entity, rel_type)
    WHERE valid_to IS NULL;

-- Step 4: specialized tables
CREATE TABLE IF NOT EXISTS kusto_queries (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    description   TEXT,
    cluster       TEXT,
    database_name TEXT,
    table_name    TEXT,
    query_text    TEXT NOT NULL,
    domain        TEXT,
    tags          TEXT,
    created_at    TEXT NOT NULL,
    source        TEXT
);
CREATE INDEX IF NOT EXISTS idx_kq_domain ON kusto_queries(domain);

CREATE TABLE IF NOT EXISTS decisions (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    domain        TEXT,
    entity_id     TEXT,
    decision_date TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    rationale     TEXT,
    made_by       TEXT,
    impact        TEXT,
    superseded_by TEXT,
    created_at    TEXT NOT NULL,
    source        TEXT,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
CREATE INDEX IF NOT EXISTS idx_decision_entity ON decisions(entity_id);
CREATE INDEX IF NOT EXISTS idx_decision_domain ON decisions(domain);

CREATE TABLE IF NOT EXISTS documents (
    id                 TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    doc_type           TEXT NOT NULL,
    domain             TEXT,
    status             TEXT NOT NULL DEFAULT 'active',
    url                TEXT,
    authors            TEXT,
    date_created       TEXT,
    date_last_modified TEXT,
    summary            TEXT,
    source             TEXT,
    content_hash       TEXT,
    drive_item_id      TEXT,
    etag               TEXT,
    ingestion_status   TEXT NOT NULL DEFAULT 'pending',
    last_ingested      TEXT,
    shared_by          TEXT,
    share_context      TEXT
);
CREATE INDEX IF NOT EXISTS idx_doc_type   ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_doc_domain ON documents(domain);

CREATE TABLE IF NOT EXISTS document_entities (
    document_id TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    relevance   TEXT NOT NULL DEFAULT 'secondary',
    PRIMARY KEY (document_id, entity_id),
    FOREIGN KEY (document_id) REFERENCES documents(id),
    FOREIGN KEY (entity_id)   REFERENCES entities(id)
);
CREATE INDEX IF NOT EXISTS idx_doc_entity ON document_entities(entity_id);

CREATE TABLE IF NOT EXISTS table_schemas (
    id            TEXT PRIMARY KEY,
    cluster       TEXT,
    database_name TEXT,
    table_name    TEXT NOT NULL,
    domain        TEXT,
    columns       TEXT,
    description   TEXT,
    created_at    TEXT NOT NULL,
    source        TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    id            TEXT PRIMARY KEY,
    entity_id     TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    url           TEXT,
    description   TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
CREATE INDEX IF NOT EXISTS idx_artifact_entity ON artifacts(entity_id);
CREATE INDEX IF NOT EXISTS idx_artifact_type   ON artifacts(artifact_type);

CREATE TABLE IF NOT EXISTS entity_context_cache (
    entity_id      TEXT PRIMARY KEY,
    context_json   TEXT NOT NULL,
    token_estimate INTEGER NOT NULL,
    cached_at      TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);

CREATE TABLE IF NOT EXISTS research_archive (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_key TEXT,
    content     TEXT NOT NULL,
    source_type TEXT,
    created_at  TEXT NOT NULL,
    quality     TEXT NOT NULL DEFAULT 'raw'
);

-- Step 4b: accomplishments (kb-population-plan.md Schema Additions)
CREATE TABLE IF NOT EXISTS accomplishments (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    impact      TEXT NOT NULL,
    program     TEXT,
    date        TEXT,
    evidence    TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    week        TEXT,
    created_at  TEXT NOT NULL,
    source      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_accomplishment_program ON accomplishments(program);
CREATE INDEX IF NOT EXISTS idx_accomplishment_impact  ON accomplishments(impact);

-- Step 4c: milestones
CREATE TABLE IF NOT EXISTS milestones (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    title       TEXT NOT NULL,
    date        TEXT,
    impact      TEXT,
    evidence    TEXT,
    created_at  TEXT NOT NULL,
    source      TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES entities(id)
);
CREATE INDEX IF NOT EXISTS idx_milestone_project ON milestones(project_id);
CREATE INDEX IF NOT EXISTS idx_milestone_date    ON milestones(date);

-- Step 4d: entity_identifiers (exact-match lookup: GQ-001, Bug NNNN, IcM-X, xKulfi)
CREATE TABLE IF NOT EXISTS entity_identifiers (
    identifier TEXT PRIMARY KEY,
    entity_id  TEXT NOT NULL,
    id_type    TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
CREATE INDEX IF NOT EXISTS idx_ident_entity ON entity_identifiers(entity_id);

-- Step 5
CREATE TABLE IF NOT EXISTS source_weights (
    source_type TEXT PRIMARY KEY,
    weight      REAL NOT NULL,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS community_summaries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id TEXT NOT NULL,
    entity_ids   TEXT NOT NULL,
    theme        TEXT,
    summary      TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_theme ON community_summaries(theme);

CREATE TABLE IF NOT EXISTS community_members (
    community_id TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    PRIMARY KEY (community_id, entity_id),
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
CREATE INDEX IF NOT EXISTS idx_cm_entity    ON community_members(entity_id);
CREATE INDEX IF NOT EXISTS idx_cm_community ON community_members(community_id);

-- Step 6
CREATE TABLE IF NOT EXISTS kb_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Step 7: FTS virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS kb_search USING fts5(
    entity_id,
    content,
    domain,
    source_type,
    tokenize='porter unicode61'
);

-- Step 8: Triggers (FTS synchronization)
CREATE TRIGGER IF NOT EXISTS trg_entity_fts_insert
    AFTER INSERT ON entities BEGIN
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.name, '') || ' ' || COALESCE(NEW.summary, '') || ' ' || COALESCE(NEW.detail, '') || ' ' || COALESCE(NEW.current_state, ''),
        NEW.domain,
        'entity'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_entity_fts_update
    AFTER UPDATE ON entities BEGIN
    DELETE FROM kb_search WHERE entity_id = OLD.id AND source_type = 'entity';
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.name, '') || ' ' || COALESCE(NEW.summary, '') || ' ' || COALESCE(NEW.detail, '') || ' ' || COALESCE(NEW.current_state, ''),
        NEW.domain,
        'entity'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_entity_fts_delete
    AFTER DELETE ON entities BEGIN
    DELETE FROM kb_search WHERE entity_id = OLD.id AND source_type = 'entity';
END;

CREATE TRIGGER IF NOT EXISTS trg_decision_fts_insert
    AFTER INSERT ON decisions BEGIN
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.rationale, '') || ' ' || COALESCE(NEW.impact, ''),
        NEW.domain,
        'decision'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_decision_fts_update
    AFTER UPDATE ON decisions BEGIN
    DELETE FROM kb_search WHERE entity_id = OLD.id AND source_type = 'decision';
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.rationale, '') || ' ' || COALESCE(NEW.impact, ''),
        NEW.domain,
        'decision'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_kq_fts_insert
    AFTER INSERT ON kusto_queries BEGIN
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.description, '') || ' ' || COALESCE(NEW.query_text, ''),
        NEW.domain,
        'kusto_query'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_kq_fts_update
    AFTER UPDATE ON kusto_queries BEGIN
    DELETE FROM kb_search WHERE entity_id = OLD.id AND source_type = 'kusto_query';
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.description, '') || ' ' || COALESCE(NEW.query_text, ''),
        NEW.domain,
        'kusto_query'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_doc_fts_insert
    AFTER INSERT ON documents BEGIN
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.summary, ''),
        NEW.domain,
        'document'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_doc_fts_update
    AFTER UPDATE ON documents BEGIN
    DELETE FROM kb_search WHERE entity_id = OLD.id AND source_type = 'document';
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.summary, ''),
        NEW.domain,
        'document'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_episode_fts_insert
    AFTER INSERT ON episodes BEGIN
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        CAST(NEW.id AS TEXT),
        COALESCE(NEW.episode_key, '') || ' ' || COALESCE(NEW.raw_content, ''),
        '',
        'episode'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_community_fts_insert
    AFTER INSERT ON community_summaries BEGIN
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.community_id,
        COALESCE(NEW.theme, '') || ' ' || COALESCE(NEW.summary, ''),
        '',
        'community'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_community_fts_update
    AFTER UPDATE ON community_summaries BEGIN
    DELETE FROM kb_search WHERE entity_id = OLD.community_id AND source_type = 'community';
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.community_id,
        COALESCE(NEW.theme, '') || ' ' || COALESCE(NEW.summary, ''),
        '',
        'community'
    );
END;

-- FTS triggers for accomplishments
CREATE TRIGGER IF NOT EXISTS trg_accomplishment_fts_insert
    AFTER INSERT ON accomplishments BEGIN
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.program, '') || ' ' || COALESCE(NEW.evidence, ''),
        NEW.program,
        'accomplishment'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_accomplishment_fts_update
    AFTER UPDATE ON accomplishments BEGIN
    DELETE FROM kb_search WHERE entity_id = OLD.id AND source_type = 'accomplishment';
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.program, '') || ' ' || COALESCE(NEW.evidence, ''),
        NEW.program,
        'accomplishment'
    );
END;

-- FTS triggers for milestones
CREATE TRIGGER IF NOT EXISTS trg_milestone_fts_insert
    AFTER INSERT ON milestones BEGIN
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.evidence, ''),
        '',
        'milestone'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_milestone_fts_update
    AFTER UPDATE ON milestones BEGIN
    DELETE FROM kb_search WHERE entity_id = OLD.id AND source_type = 'milestone';
    INSERT INTO kb_search(entity_id, content, domain, source_type)
    VALUES (
        NEW.id,
        COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.evidence, ''),
        '',
        'milestone'
    );
END;

-- Step 9: Views
CREATE VIEW IF NOT EXISTS relationship_conflicts AS
    SELECT
        eh1.entity_id,
        eh1.field,
        eh1.old_value  AS value_a,
        eh1.new_value  AS value_b,
        eh1.session_id AS episode_a,
        eh2.session_id AS episode_b,
        eh1.changed_at AS changed_at_a,
        eh2.changed_at AS changed_at_b
    FROM entity_history eh1
    JOIN entity_history eh2
        ON  eh1.entity_id = eh2.entity_id
        AND eh1.field     = eh2.field
        AND eh1.id        < eh2.id
    WHERE eh1.changed_at > datetime('now', '-30 days')
      AND eh2.changed_at > datetime('now', '-30 days')
      AND COALESCE(eh1.new_value, '') != COALESCE(eh2.new_value, '')
      AND eh1.changed_by IS NOT eh2.changed_by;
"""

# Fields on entities that can be mutated (logged to entity_history on change)
_MUTABLE_ENTITY_FIELDS = (
    "name", "type", "domain", "domains", "summary", "detail",
    "current_state", "confidence", "last_validated", "staleness_ttl_days",
    "validation_method", "source",
)

# Token cost estimates (spec §6.6)
_TOK_ENTITY_SUMMARY = 100
_TOK_EDGE           = 30
_TOK_DECISION       = 150
_TOK_GAP            = 50
_TOK_ARTIFACT       = 80
_TOK_EPISODE        = 50
_TOK_KUSTO          = 40

# ---------------------------------------------------------------------------
# Data Quality Weights (spec: data-quality-gate.md)
# Priority order (non-negotiable): Accuracy > Freshness > Completeness
# These weights are used by assess_quality() and context_for() to compute
# composite quality scores. Domain-aware: calendar/comms/incidents are
# freshness-dominated; decisions/accomplishments are accuracy-dominated.
# ---------------------------------------------------------------------------
_DQ_WEIGHT_DEFAULT      = {"A": 0.5, "F": 0.3, "C": 0.2}
_DQ_WEIGHT_CALENDAR     = {"A": 0.1, "F": 0.8, "C": 0.1}
_DQ_WEIGHT_COMMS        = {"A": 0.1, "F": 0.8, "C": 0.1}
_DQ_WEIGHT_INCIDENTS    = {"A": 0.2, "F": 0.7, "C": 0.1}
_DQ_WEIGHT_DECISIONS    = {"A": 0.7, "F": 0.1, "C": 0.2}
_DQ_WEIGHT_ACCOMPLISHMENTS = {"A": 0.6, "F": 0.1, "C": 0.3}
_DQ_WEIGHT_GOLDEN_QUERIES  = {"A": 0.6, "F": 0.2, "C": 0.2}
_DQ_WEIGHT_PEOPLE       = {"A": 0.5, "F": 0.3, "C": 0.2}
_DQ_WEIGHT_PRODUCTS     = {"A": 0.6, "F": 0.1, "C": 0.3}

_DQ_DOMAIN_WEIGHTS = {
    "default":          _DQ_WEIGHT_DEFAULT,
    "calendar":         _DQ_WEIGHT_CALENDAR,
    "comms":            _DQ_WEIGHT_COMMS,
    "incidents":        _DQ_WEIGHT_INCIDENTS,
    "decisions":        _DQ_WEIGHT_DECISIONS,
    "accomplishments":  _DQ_WEIGHT_ACCOMPLISHMENTS,
    "golden_queries":   _DQ_WEIGHT_GOLDEN_QUERIES,
    "people":           _DQ_WEIGHT_PEOPLE,
    "products":         _DQ_WEIGHT_PRODUCTS,
}

# Minimum confidence to include in context assembly.
# Entities below this threshold are dropped to preserve accuracy.
_DQ_MIN_CONFIDENCE      = 0.5

# Quality gate thresholds (composite Q score)
_DQ_GATE_PASS           = 0.7   # Serve directly
_DQ_GATE_WARN           = 0.5   # Serve with staleness caveat
_DQ_GATE_STALE_SERVE    = 0.3   # Serve stale + caveat (no blocking heal)
# Below 0.3 = REFUSE: "I don't have reliable data for this"


# ---------------------------------------------------------------------------
# Dataclasses (spec §6.2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Entity:
    id: str
    name: str
    type: str
    domain: str | None
    domains: list
    summary: str | None
    detail: str | None
    current_state: str | None
    confidence: float
    last_validated: str | None
    staleness_ttl_days: int
    effective_staleness: str  # computed: 'fresh' | 'aging' | 'stale' | 'expired'
    source_type: str = ""            # provenance tier (e.g. 'workiq', 'manual')
    corroborating_sources: int = 0   # distinct source count for accuracy scoring
    lifecycle_stage: str = "unknown" # active | inactive | cancelled | archived | superseded
    excerpt_hash: str | None = None  # SHA-256 of source excerpt for change detection


@dataclass(frozen=True)
class Edge:
    from_entity: str
    to_entity: str
    rel_type: str
    label: str | None
    detail: str | None
    asserted_strength: str
    effective_strength: str  # computed from last_validated
    confidence: float
    valid_from: str | None
    valid_to: str | None
    source_episode_key: str | None


@dataclass(frozen=True)
class EntityContext:
    """Pre-assembled context bundle for LLM injection."""
    entity: Entity | None
    edges: list
    neighborhood: list
    decisions: list
    artifacts: list
    documents: list
    gaps: list
    kusto_queries: list
    shadow_refs: list
    recent_episodes: list
    community_context: str | None
    suggested_followups: list
    token_estimate: int
    quality_score: float = 0.0                    # composite Q score (domain-aware)
    quality_caveats: tuple = ()                   # caveat strings for WARN/STALE/REFUSE
    has_conflicts: bool = False                   # True if any entity has provenance conflicts


@dataclass(frozen=True)
class SearchResult:
    entity_id: str
    name: str
    snippet: str
    source_type: str
    relevance_score: float
    domain: str | None = None
    summary: str | None = None


# ---------------------------------------------------------------------------
# Low-level SQLite helpers
# ---------------------------------------------------------------------------

def _open_kb(db_path: Path) -> sqlite3.Connection:
    """Open kb.sqlite with the mandatory pragma sequence.

    Mirrors action_queue._open_db() exactly as required by spec §0 rule 6.
    Every connection to kb.sqlite MUST use this function.
    """
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _compute_staleness(last_validated: str | None, ttl_days: int, updated_at: str) -> str:
    """Compute effective staleness: fresh | aging | stale | expired."""
    ref = last_validated or updated_at
    if not ref:
        return "stale"
    try:
        last_dt = datetime.fromisoformat(ref.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "stale"
    days = (datetime.now(timezone.utc) - last_dt).days
    ttl = max(ttl_days, 1)
    if days < ttl // 2:
        return "fresh"
    if days < ttl:
        return "aging"
    if days < ttl * 2:
        return "stale"
    return "expired"


def _compute_effective_strength(last_validated: str | None) -> str:
    """Compute effective relationship strength from last_validated age."""
    if not last_validated:
        return "weak"
    try:
        last_dt = datetime.fromisoformat(last_validated.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "weak"
    days = (datetime.now(timezone.utc) - last_dt).days
    if days < 30:
        return "strong"
    if days < 90:
        return "moderate"
    if days < 180:
        return "weak"
    return "historical"


def _row_to_entity(row: sqlite3.Row) -> Entity:
    r = dict(row)
    domains_raw = r.get("domains")
    try:
        domains = json.loads(domains_raw) if domains_raw else []
    except (json.JSONDecodeError, TypeError):
        domains = []
    return Entity(
        id=r["id"],
        name=r["name"],
        type=r["type"],
        domain=r.get("domain"),
        domains=domains,
        summary=r.get("summary"),
        detail=r.get("detail"),
        current_state=r.get("current_state"),
        confidence=float(r.get("confidence") or 0.5),
        last_validated=r.get("last_validated"),
        staleness_ttl_days=int(r.get("staleness_ttl_days") or 90),
        effective_staleness=_compute_staleness(
            r.get("last_validated"),
            int(r.get("staleness_ttl_days") or 90),
            r.get("updated_at", ""),
        ),
        source_type=r.get("source_type") or "",
        corroborating_sources=int(r.get("corroborating_sources") or 0),
        lifecycle_stage=r.get("lifecycle_stage") or "unknown",
        excerpt_hash=r.get("excerpt_hash"),
    )


def _row_to_edge(row: sqlite3.Row) -> Edge:
    r = dict(row)
    return Edge(
        from_entity=r["from_entity"],
        to_entity=r["to_entity"],
        rel_type=r["rel_type"],
        label=r.get("label"),
        detail=r.get("detail"),
        asserted_strength=r.get("strength") or "strong",
        effective_strength=_compute_effective_strength(r.get("last_validated")),
        confidence=float(r.get("confidence") or 0.5),
        valid_from=r.get("valid_from"),
        valid_to=r.get("valid_to"),
        source_episode_key=r.get("episode_key"),  # comes from JOIN on episodes
    )


def _token_estimate(text: str) -> int:
    """Fast token estimate: ~4 chars per token (GPT-style)."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# NullKnowledgeGraph — returned when the DB is corrupted or unavailable
# ---------------------------------------------------------------------------

class NullKnowledgeGraph:
    """Returned when the KB is corrupted or unavailable.

    All queries return safe empty values. All writes are no-ops.
    Callers can treat this as a valid KnowledgeGraph — the briefing
    continues without KB context (spec §6.5 graceful degradation).
    """

    def _empty_ctx(self) -> EntityContext:
        return EntityContext(
            entity=None, edges=[], neighborhood=[], decisions=[],
            artifacts=[], documents=[], gaps=[], kusto_queries=[],
            shadow_refs=[], recent_episodes=[], community_context=None,
            suggested_followups=[], token_estimate=0,
            quality_score=0.0,
            quality_caveats=("KB not yet populated — serving from markdown only",),
            has_conflicts=False,
        )

    def get_entity(self, id: str) -> None:                         return None
    def resolve_entity(self, name: str) -> None:                   return None
    def resolve_entity_candidates(self, name: str, limit: int = 5) -> list: return []
    def search(self, query: str, **kwargs) -> list:                return []
    def traverse(self, *args, **kwargs) -> list:                   return []
    def find_path(self, *args, **kwargs) -> None:                  return None
    def context_for(self, entity_id: str, **kwargs) -> None:       return None
    def get_context_as_of(self, *args, **kwargs) -> EntityContext:  return self._empty_ctx()
    def global_context_for(self, *args, **kwargs) -> None:         return None
    def recent_episodes(self, *args, **kwargs) -> list:            return []
    def stale_entities(self, *args, **kwargs) -> list:             return []
    def recent_changes(self, *args, **kwargs) -> list:             return []
    def upsert_entity(self, *args, **kwargs) -> None:              pass
    def add_relationship(self, *args, **kwargs) -> None:           pass
    def deactivate_relationship(self, *args, **kwargs) -> None:    pass
    def add_episode(self, *args, **kwargs) -> int:                 return 0
    def add_alias(self, *args, **kwargs) -> None:                  pass
    def invalidate_cache(self, *args, **kwargs) -> None:           pass
    def validate_integrity(self) -> list:                          return []
    def get_stats(self) -> dict:                                   return {}
    def rebuild_communities(self) -> int:                          return 0
    def god_nodes(self, *args, **kwargs) -> list:                  return []
    def vacuum(self) -> None:                                      pass
    def backup(self, tier: str = "daily", dest_dir: Path | None = None) -> None:
        _log.warning("KG: backup() called on NullKnowledgeGraph — skipped")
        return None
    def close(self) -> None:                                       pass


# ---------------------------------------------------------------------------
# KnowledgeGraph — main class
# ---------------------------------------------------------------------------

class KnowledgeGraph:
    """SQLite-backed knowledge graph for Artha Work OS.

    Usage:
        kg = KnowledgeGraph()
        entity = kg.resolve_entity("xKulfi")
        ctx = kg.context_for(entity.id, token_budget=950)

    Graceful degradation: if the DB is corrupt, __init__ returns a
    NullKnowledgeGraph (via get_kb() factory). Direct instantiation
    propagates the original exception.
    """

    def __init__(self, artha_dir: Path | None = None, db_path: Path | None = None) -> None:
        self._artha_dir = artha_dir or self._default_artha_dir()
        self._db_path = db_path or self._resolve_db_path(self._artha_dir)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = _open_kb(self._db_path)
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _default_artha_dir() -> Path:
        """Infer Artha root from this file's location (scripts/lib/ → project root)."""
        return Path(__file__).resolve().parent.parent.parent

    @staticmethod
    def _resolve_db_path(artha_dir: Path) -> Path:
        """Return the machine-local (non-cloud-synced) DB path.

        Priority:
          1. ARTHA_KB_PATH env var (absolute path override)
          2. Real Artha dirs (config/artha_config.yaml present):
               macOS:   ~/.artha-local/kb.sqlite
               Windows: %LOCALAPPDATA%\\Artha\\kb.sqlite
               Linux:   $XDG_DATA_HOME/artha/kb.sqlite
          3. Test/CI: tmp/test-kb.sqlite (project-local temp, gitignored)

        OneDrive safety rule: if the resolved path is under a cloud sync folder
        it is relocated to the platform default and a warning is logged.
        """
        env_override = os.environ.get("ARTHA_KB_PATH", "").strip()
        if env_override:
            p = Path(env_override)
            KnowledgeGraph._assert_not_cloud_synced(p)
            return p

        if (artha_dir / "config" / "artha_config.yaml").exists():
            system = platform.system()
            if system == "Darwin":
                candidate = Path.home() / ".artha-local" / "kb.sqlite"
            elif system == "Windows":
                local_app = os.environ.get(
                    "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
                )
                candidate = Path(local_app) / "Artha" / "kb.sqlite"
            else:
                xdg = os.environ.get(
                    "XDG_DATA_HOME", str(Path.home() / ".local" / "share")
                )
                candidate = Path(xdg) / "artha" / "kb.sqlite"

            KnowledgeGraph._assert_not_cloud_synced(candidate)
            return candidate

        # Test/CI fallback — project-local tmp/
        return artha_dir / "tmp" / "test-kb.sqlite"

    @staticmethod
    def _assert_not_cloud_synced(path: Path) -> None:
        """Raise RuntimeError if the KB path is under a cloud-sync folder (§6.1 safety rule)."""
        # CI/test environments may set ARTHA_ALLOW_CLOUD_DB=1 to bypass (§4.3).
        # No other bypass is permitted.
        if os.environ.get("ARTHA_ALLOW_CLOUD_DB", "").strip() == "1":
            return
        # DEBT-026: Resolve symlinks before checking — a symlink at ~/.artha-local/kb.sqlite
        # pointing to an OneDrive path bypasses string matching on the unresolved path.
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path  # path may not exist yet; fall back to original
        path_str = str(resolved)
        for marker in _CLOUD_SYNC_MARKERS:
            if marker.lower() in path_str.lower():
                raise RuntimeError(
                    f"KB at {path_str!r} is inside a cloud-sync folder — "
                    f"move to a local-only path. Markers: {_CLOUD_SYNC_MARKERS}"
                )

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Create tables / triggers / views if needed, then migrate.

        Note: executescript() always issues an implicit COMMIT before
        executing the script, so it must NOT be wrapped in an explicit
        BEGIN IMMEDIATE / COMMIT block.  DDL is inherently auto-committed
        by executescript() and SQLite.
        """
        try:
            self._conn.executescript(_SCHEMA_SQL)
        except Exception:
            raise

        # Seed kb_meta if empty
        row = self._conn.execute(
            "SELECT value FROM kb_meta WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = _now_utc()
                meta_seeds = [
                    ("schema_version",      _SCHEMA_VERSION),
                    ("created_at",          now),
                    ("last_vacuum",         None),
                    ("last_integrity_check", None),
                    ("last_backup_daily",   None),
                    ("last_backup_weekly",  None),
                    ("last_backup_monthly", None),
                    ("last_backup_path",    None),
                ]
                for k, v in meta_seeds:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO kb_meta (key, value) VALUES (?, ?)", (k, v)
                    )
                # Seed source_weights
                for source_type, weight, notes in _SOURCE_WEIGHT_SEEDS:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO source_weights (source_type, weight, notes)"
                        " VALUES (?, ?, ?)",
                        (source_type, weight, notes),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        else:
            self._migrate_schema_if_needed(row["value"])

    def _migrate_schema_if_needed(self, current_version: str) -> None:
        """Additive-only migration. Pattern mirrors action_queue._migrate_schema_if_needed()."""
        if current_version == _SCHEMA_VERSION:
            return
        if current_version in ("1", "2"):
            # v2 → v3: add source_type and corroborating_sources to entities.
            # Uses try/except per column for idempotency (SQLite has no IF NOT EXISTS
            # for ALTER TABLE ADD COLUMN).
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for ddl in (
                    "ALTER TABLE entities ADD COLUMN source_type TEXT",
                    "ALTER TABLE entities ADD COLUMN corroborating_sources INTEGER NOT NULL DEFAULT 0",
                ):
                    try:
                        self._conn.execute(ddl)
                    except sqlite3.OperationalError as exc:
                        if "duplicate column" not in str(exc).lower():
                            raise
                self._conn.execute(
                    "UPDATE kb_meta SET value=? WHERE key='schema_version'",
                    (_SCHEMA_VERSION,),
                )
                self._conn.execute("COMMIT")
                _log.info("KB schema migrated: %s → %s", current_version, _SCHEMA_VERSION)
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        elif current_version == "3":
            # v3 → v4: add excerpt_hash, lifecycle_stage, change_source_ref, documents inventory
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for ddl in (
                    "ALTER TABLE entities ADD COLUMN excerpt_hash TEXT",
                    "ALTER TABLE entities ADD COLUMN lifecycle_stage TEXT NOT NULL DEFAULT 'unknown'",
                    "ALTER TABLE entity_history ADD COLUMN change_source_ref TEXT",
                    "ALTER TABLE documents ADD COLUMN content_hash TEXT",
                    "ALTER TABLE documents ADD COLUMN drive_item_id TEXT",
                    "ALTER TABLE documents ADD COLUMN etag TEXT",
                    "ALTER TABLE documents ADD COLUMN ingestion_status TEXT NOT NULL DEFAULT 'pending'",
                    "ALTER TABLE documents ADD COLUMN last_ingested TEXT",
                    "ALTER TABLE documents ADD COLUMN shared_by TEXT",
                    "ALTER TABLE documents ADD COLUMN share_context TEXT",
                ):
                    try:
                        self._conn.execute(ddl)
                    except sqlite3.OperationalError as exc:
                        if "duplicate column" not in str(exc).lower():
                            raise
                # community_members already created by _SCHEMA_SQL IF NOT EXISTS
                self._conn.execute(
                    "UPDATE kb_meta SET value=? WHERE key='schema_version'",
                    (_SCHEMA_VERSION,),
                )
                self._conn.execute("COMMIT")
                _log.info("KB schema migrated: 3 → 4")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        else:
            raise RuntimeError(
                f"Unknown schema version {current_version!r} — cannot safely migrate. "
                "Manual intervention required."
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_entity_with_conn(self, row: sqlite3.Row) -> Entity:
        return _row_to_entity(row)

    def _empty_ctx(self) -> EntityContext:
        return EntityContext(
            entity=None, edges=[], neighborhood=[], decisions=[],
            artifacts=[], documents=[], gaps=[], kusto_queries=[],
            shadow_refs=[], recent_episodes=[], community_context=None,
            suggested_followups=[], token_estimate=0,
            quality_score=0.0,
            quality_caveats=("KB not yet populated — serving from markdown only",),
            has_conflicts=False,
        )

    def entity_has_active_conflicts(self, entity_id: str) -> bool:
        """Return True if the entity has provenance conflicts in entity_history.

        Queries the relationship_conflicts VIEW which only fires for truly
        conflicting provenance (different changed_by authors asserted different
        values within the last 30 days).
        """
        try:
            row = self._conn.execute(
                "SELECT 1 FROM relationship_conflicts WHERE entity_id=? LIMIT 1",
                (entity_id,),
            ).fetchone()
            return row is not None
        except Exception:
            return False

    def _strength_rank(self, strength: str) -> int:
        return {"strong": 0, "moderate": 1, "weak": 2, "historical": 3}.get(strength, 4)

    # ------------------------------------------------------------------
    # Read — core
    # ------------------------------------------------------------------

    def get_entity(self, id: str) -> Entity | None:
        row = self._conn.execute(
            "SELECT * FROM entities WHERE id=?", (id,)
        ).fetchone()
        return _row_to_entity(row) if row else None

    def resolve_entity(self, name: str) -> Entity | None:
        """Return the best-match entity for *name*, or None."""
        candidates = self.resolve_entity_candidates(name, limit=1)
        return candidates[0][0] if candidates else None

    def resolve_entity_candidates(
        self, name: str, limit: int = 5
    ) -> list[tuple[Entity, float, str]]:
        """Return ranked (entity, confidence, match_reason) tuples.

        match_reason: 'exact_id' | 'alias' | 'fts_title' | 'fts_content'
        Handles ambiguity — callers can present multiple candidates to the user.
        """
        results: list[tuple[Entity, float, str]] = []
        seen: set[str] = set()
        norm = name.strip().lower()

        # 1. Exact ID match
        row = self._conn.execute(
            "SELECT * FROM entities WHERE id=?", (norm,)
        ).fetchone()
        if row:
            e = _row_to_entity(row)
            results.append((e, 1.0, "exact_id"))
            seen.add(e.id)

        # 2. Alias match
        if len(results) < limit:
            alias_row = self._conn.execute(
                "SELECT entity_id FROM entity_aliases WHERE alias=?", (norm,)
            ).fetchone()
            if alias_row and alias_row["entity_id"] not in seen:
                row = self._conn.execute(
                    "SELECT * FROM entities WHERE id=?", (alias_row["entity_id"],)
                ).fetchone()
                if row:
                    e = _row_to_entity(row)
                    results.append((e, 0.95, "alias"))
                    seen.add(e.id)

        # 3. FTS search across entities
        if len(results) < limit:
            fts = self.search(name, limit=limit * 2)
            for sr in fts:
                if sr.entity_id in seen:
                    continue
                if sr.source_type != "entity":
                    continue
                row = self._conn.execute(
                    "SELECT * FROM entities WHERE id=?", (sr.entity_id,)
                ).fetchone()
                if row:
                    e = _row_to_entity(row)
                    in_title = norm in e.name.lower()
                    reason = "fts_title" if in_title else "fts_content"
                    score = sr.relevance_score * (0.9 if in_title else 0.7)
                    results.append((e, score, reason))
                    seen.add(e.id)
                    if len(results) >= limit:
                        break

        return sorted(results, key=lambda x: -x[1])[:limit]

    def search(
        self,
        query: str,
        domain: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """FTS5 full-text search across all indexed content."""
        if not query or not query.strip():
            return []
        # Sanitize for FTS5: strip special chars so multi-word queries become
        # implicit AND (the FTS5 default), not a phrase search.  Phrase search
        # ("term1 term2") requires adjacent tokens; AND search finds documents
        # that contain all terms anywhere in the content.
        tokens = " ".join(w for w in query.split() if w.replace('-', '').replace('_', '').isalnum())
        if not tokens:
            return []
        try:
            if domain:
                rows = self._conn.execute(
                    "SELECT entity_id, content, domain, source_type, rank"
                    " FROM kb_search"
                    " WHERE kb_search MATCH ? AND domain=?"
                    " ORDER BY rank LIMIT ?",
                    (tokens, domain, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT entity_id, content, domain, source_type, rank"
                    " FROM kb_search"
                    " WHERE kb_search MATCH ?"
                    " ORDER BY rank LIMIT ?",
                    (tokens, limit),
                ).fetchall()
        except sqlite3.OperationalError:
            return []

        results = []
        for row in rows:
            content = row["content"] or ""
            snippet = content[:200].strip()
            # FTS5 rank is negative (more negative = better match)
            raw_rank = row["rank"] or 0.0
            score = max(0.0, min(1.0, 1.0 + raw_rank / 10.0))
            # Enrich with entity name and summary if available
            entity_name = row["entity_id"]
            entity_summary: str | None = None
            ent_row = self._conn.execute(
                "SELECT name, summary, lifecycle_stage FROM entities WHERE id=?",
                (row["entity_id"],),
            ).fetchone()
            if ent_row:
                ls = ent_row["lifecycle_stage"] or "unknown"
                if ls in ("cancelled", "archived", "superseded"):
                    continue
                entity_name = ent_row["name"]
                entity_summary = ent_row["summary"]
            results.append(SearchResult(
                entity_id=row["entity_id"],
                name=entity_name,
                snippet=snippet,
                source_type=row["source_type"] or "",
                relevance_score=score,
                domain=row["domain"] if row["domain"] else None,
                summary=entity_summary,
            ))
        return results

    def traverse(
        self,
        entity_id: str,
        rel_types: list[str] | None = None,
        direction: str = "both",
        depth: int = 1,
        include_historical: bool = False,
    ) -> list[Edge]:
        """Return edges at depth=1 (or up to depth via BFS)."""
        if depth == 1:
            return self._traverse_one_hop(entity_id, rel_types, direction, include_historical)

        # BFS for depth > 1
        visited_ids = {entity_id}
        frontier = [entity_id]
        all_edges: list[Edge] = []
        seen_rel_ids: set[tuple] = set()

        for _ in range(depth):
            next_frontier: list[str] = []
            for eid in frontier:
                hop_edges = self._traverse_one_hop(eid, rel_types, direction, include_historical)
                for edge in hop_edges:
                    sig = (edge.from_entity, edge.to_entity, edge.rel_type)
                    if sig not in seen_rel_ids:
                        seen_rel_ids.add(sig)
                        all_edges.append(edge)
                    other = edge.to_entity if edge.from_entity == eid else edge.from_entity
                    if other not in visited_ids:
                        visited_ids.add(other)
                        next_frontier.append(other)
            frontier = next_frontier
            if not frontier:
                break

        return all_edges

    def _traverse_one_hop(
        self,
        entity_id: str,
        rel_types: list[str] | None,
        direction: str,
        include_historical: bool,
    ) -> list[Edge]:
        active_filter = "" if include_historical else "AND r.valid_to IS NULL"
        type_filter = ""
        params: list[Any] = []

        if rel_types:
            placeholders = ",".join("?" * len(rel_types))
            type_filter = f"AND r.rel_type IN ({placeholders})"
            params.extend(rel_types)

        if direction == "from":
            where = f"r.from_entity=? {active_filter} {type_filter}"
            params = [entity_id] + params
        elif direction == "to":
            where = f"r.to_entity=? {active_filter} {type_filter}"
            params = [entity_id] + params
        else:  # both
            where = f"(r.from_entity=? OR r.to_entity=?) {active_filter} {type_filter}"
            params = [entity_id, entity_id] + params

        sql = f"""
            SELECT r.*, e.episode_key
            FROM relationships r
            LEFT JOIN episodes e ON r.source_episode_id = e.id
            WHERE {where}
        """
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_edge(row) for row in rows]

    def find_path(
        self, from_id: str, to_id: str, max_depth: int = 4
    ) -> list[Edge] | None:
        """BFS shortest path from from_id to to_id."""
        if from_id == to_id:
            return []

        queue: deque[tuple[str, list[Edge]]] = deque([(from_id, [])])
        visited: set[str] = {from_id}

        while queue:
            curr_id, path = queue.popleft()
            if len(path) >= max_depth:
                continue
            edges = self._traverse_one_hop(curr_id, None, "from", False)
            for edge in edges:
                if edge.to_entity == to_id:
                    return path + [edge]
                if edge.to_entity not in visited:
                    visited.add(edge.to_entity)
                    queue.append((edge.to_entity, path + [edge]))
        return None

    # ------------------------------------------------------------------
    # Read — context materialization (spec §6.6)
    # ------------------------------------------------------------------

    def context_for(
        self,
        entity_id: str,
        token_budget: int = 950,
        depth: int = 2,
        include_historical: bool = False,
        session_focus: list[str] | None = None,
        intent: str = "deep",
    ) -> EntityContext | None:
        """One call → complete context bundle with neighbourhood walk.

        Performs a 2-hop BFS by default. Only traverses active edges
        (valid_to IS NULL) unless include_historical=True.

        intent controls relationship pruning (§4.11b):
          "status" → keep milestone/ownership/blocker; prune meeting/comms
          "prep"   → keep people/decision/open_question; prune historical timeline
          "deep"   → no pruning (default — fully backward-compatible)

        Returns NullEntityContext if entity not found.
        """
        if token_budget > 2000:
            raise ValueError(
                f"context_for: token_budget {token_budget} exceeds safe limit of 2000"
            )

        # ── §4.11b intent-based pruning sets ─────────────────────────────────
        _STATUS_KEEP = {"milestone", "ownership", "blocker"}
        _STATUS_PRUNE = {"meeting", "comms"}
        _PREP_KEEP = {"people", "decision", "open_question"}

        def _keep_edge(edge: "Edge") -> bool:
            rt = (edge.rel_type or "").lower()
            if intent == "status":
                if rt in _STATUS_PRUNE:
                    return False
                return True
            if intent == "prep":
                if edge.effective_strength == "historical":
                    return False
                return rt in _PREP_KEEP or not _PREP_KEEP  # include if type is relevant
            return True  # "deep" — no pruning

        entity = self.get_entity(entity_id)
        if entity is None:
            return None

        used_tokens = _TOK_ENTITY_SUMMARY
        focus_set = set(session_focus or [])
        edges_to_include: list[Edge] = []
        hop1_ids: set[str] = set()
        neighborhood: list[Entity] = []
        gaps: list[Entity] = []
        shadow_refs: list[str] = []
        seen_ids: set[str] = {entity_id}

        # --- Hop 1: direct edges ---
        hop1_edges = self._traverse_one_hop(
            entity_id, None, "both", include_historical
        )
        hop1_edges.sort(key=lambda e: (
            -e.confidence,
            self._strength_rank(e.effective_strength),
        ))
        for edge in hop1_edges:
            if used_tokens + _TOK_EDGE > token_budget:
                break
            if not _keep_edge(edge):
                continue
            edges_to_include.append(edge)
            used_tokens += _TOK_EDGE
            other = edge.to_entity if edge.from_entity == entity_id else edge.from_entity
            hop1_ids.add(other)

        # --- Hop 1: classify neighbours ---
        for h1_id in hop1_ids:
            if h1_id in seen_ids:
                continue
            seen_ids.add(h1_id)
            h1_ent = self.get_entity(h1_id)
            if h1_ent is None:
                continue
            if h1_ent.type == "shadow":
                if h1_ent.source:
                    shadow_refs.append(h1_ent.source)
                continue
            if h1_ent.type == "gap":
                gaps.append(h1_ent)
                used_tokens += _TOK_GAP
                continue
            neighborhood.append(h1_ent)

        # --- Hop 2: edges from hop-1 nodes ---
        if depth >= 2:
            for h1_id in list(hop1_ids):
                h2_edges = self._traverse_one_hop(h1_id, None, "both", include_historical)
                for edge in h2_edges:
                    if used_tokens + _TOK_EDGE > token_budget:
                        break
                    if not _keep_edge(edge):
                        continue
                    other = edge.to_entity if edge.from_entity == h1_id else edge.from_entity
                    if other in seen_ids:
                        continue
                    # Confidence gate: same domain OR conf >= 0.6 (with focus bonus)
                    conf_bonus = 0.2 if other in focus_set else 0.0
                    effective_conf = edge.confidence + conf_bonus
                    other_ent = self.get_entity(other)
                    same_domain = other_ent and other_ent.domain == entity.domain
                    if not same_domain and effective_conf < 0.6:
                        continue
                    if not include_historical and edge.effective_strength == "historical":
                        continue
                    sig = (edge.from_entity, edge.to_entity, edge.rel_type)
                    already = any(
                        (e.from_entity, e.to_entity, e.rel_type) == sig
                        for e in edges_to_include
                    )
                    if not already:
                        edges_to_include.append(edge)
                        used_tokens += _TOK_EDGE
                    if other_ent and other_ent not in neighborhood:
                        seen_ids.add(other)
                        if other_ent.type == "shadow" and other_ent.source:
                            shadow_refs.append(other_ent.source)
                        elif other_ent.type == "gap":
                            gaps.append(other_ent)
                        else:
                            neighborhood.append(other_ent)

        # --- Decisions ---
        decisions_raw: list[dict] = []
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE entity_id=? ORDER BY decision_date DESC, created_at DESC LIMIT 10",
            (entity_id,),
        ).fetchall()
        for row in rows:
            if used_tokens + _TOK_DECISION > token_budget:
                break
            decisions_raw.append(dict(row))
            used_tokens += _TOK_DECISION

        # --- Artifacts ---
        artifacts_raw: list[dict] = []
        rows = self._conn.execute(
            "SELECT * FROM artifacts WHERE entity_id=?",
            (entity_id,),
        ).fetchall()
        for row in rows:
            if used_tokens + _TOK_ARTIFACT > token_budget:
                break
            artifacts_raw.append(dict(row))
            used_tokens += _TOK_ARTIFACT

        # --- Documents ---
        documents_raw: list[dict] = []
        rows = self._conn.execute(
            """SELECT d.*, de.relevance FROM documents d
               JOIN document_entities de ON d.id = de.document_id
               WHERE de.entity_id=?
               ORDER BY CASE de.relevance WHEN 'primary' THEN 1
                                          WHEN 'secondary' THEN 2 ELSE 3 END,
                        d.date_last_modified DESC""",
            (entity_id,),
        ).fetchall()
        for row in rows:
            if used_tokens + _TOK_ARTIFACT > token_budget:
                break
            documents_raw.append(dict(row))
            used_tokens += _TOK_ARTIFACT

        # --- Kusto queries for domain ---
        kusto_raw: list[dict] = []
        if entity.domain and used_tokens + _TOK_KUSTO <= token_budget:
            rows = self._conn.execute(
                "SELECT id, title, description, cluster, database_name, table_name FROM kusto_queries"
                " WHERE domain=? LIMIT 5",
                (entity.domain,),
            ).fetchall()
            for row in rows:
                if used_tokens + _TOK_KUSTO > token_budget:
                    break
                kusto_raw.append(dict(row))
                used_tokens += _TOK_KUSTO

        # --- Recent episodes ---
        recent_eps = self.recent_episodes([entity_id], since_days=90, limit=5)

        # --- Community context (if available) ---
        community_ctx = self._community_context_for_entity(entity_id)

        # --- Suggested followups ---
        followups = self._suggest_followups(entity, edges_to_include, gaps)

        # --- Quality assessment (lazy import — avoids circular dependency N1) ---
        try:
            import lib.dq_gate as _dq  # noqa: PLC0415
            if entity.confidence < _DQ_MIN_CONFIDENCE:
                _quality_score = 0.0
                _quality_caveats: tuple = (
                    f"Entity confidence {entity.confidence:.2f} below minimum "
                    f"{_DQ_MIN_CONFIDENCE} — excluded from reliable serving",
                )
                _has_conflicts = False
            else:
                _qs = _dq.assess_quality(entity, self)
                _quality_score = _qs.composite
                if _qs.verdict == _dq.QualityVerdict.WARN:
                    _quality_caveats = (
                        f"{entity.name} data is aging (Q={_qs.composite:.2f})",
                    )
                elif _qs.verdict in (
                    _dq.QualityVerdict.STALE_SERVE, _dq.QualityVerdict.REFUSE
                ):
                    _quality_caveats = (
                        f"{entity.name}: stale or unreliable data "
                        f"(Q={_qs.composite:.2f}). Run /work refresh to update.",
                    )
                else:
                    _quality_caveats = ()
                _has_conflicts = self.entity_has_active_conflicts(entity.id)
        except Exception:
            _quality_score = 0.0
            _quality_caveats = ()
            _has_conflicts = False

        return EntityContext(
            entity=entity,
            edges=edges_to_include,
            neighborhood=neighborhood,
            decisions=decisions_raw,
            artifacts=artifacts_raw,
            documents=documents_raw,
            gaps=gaps,
            kusto_queries=kusto_raw,
            shadow_refs=shadow_refs,
            recent_episodes=recent_eps[:3],
            community_context=community_ctx,
            suggested_followups=followups[:3],
            token_estimate=used_tokens,
            quality_score=_quality_score,
            quality_caveats=_quality_caveats,
            has_conflicts=_has_conflicts,
        )

    def get_context_as_of(
        self, entity_id: str, timestamp: str, token_budget: int = 4000
    ) -> EntityContext:
        """Reconstruct entity context at a point in time using entity_history.

        Reverses all changes made after *timestamp* to recover historical state.
        Includes only edges where valid_from <= timestamp AND
        (valid_to IS NULL OR valid_to > timestamp).
        """
        entity = self.get_entity(entity_id)
        if entity is None:
            return self._empty_ctx()

        # Reconstruct historical entity state by replaying history backwards
        entity_dict = asdict(entity)
        history_rows = self._conn.execute(
            "SELECT field, old_value, new_value, changed_at"
            " FROM entity_history WHERE entity_id=? AND changed_at > ?"
            " ORDER BY changed_at DESC",
            (entity_id, timestamp),
        ).fetchall()

        for row in history_rows:
            field = row["field"]
            if field in _MUTABLE_ENTITY_FIELDS and field in entity_dict:
                entity_dict[field] = row["old_value"]
        # Recompute effective_staleness with historical values
        entity_dict["effective_staleness"] = _compute_staleness(
            entity_dict.get("last_validated"),
            int(entity_dict.get("staleness_ttl_days") or 90),
            entity_dict.get("updated_at", ""),
        )
        historical_entity = Entity(**entity_dict)

        # Get edges valid at timestamp
        rows = self._conn.execute(
            """SELECT r.*, e.episode_key FROM relationships r
               LEFT JOIN episodes e ON r.source_episode_id = e.id
               WHERE (r.from_entity=? OR r.to_entity=?)
                 AND (r.valid_from IS NULL OR r.valid_from <= ?)
                 AND (r.valid_to IS NULL OR r.valid_to > ?)""",
            (entity_id, entity_id, timestamp, timestamp),
        ).fetchall()
        historical_edges = [_row_to_edge(row) for row in rows]

        # Thin context for point-in-time (no BFS at this depth)
        return EntityContext(
            entity=historical_entity,
            edges=historical_edges[:20],
            neighborhood=[],
            decisions=[],
            artifacts=[],
            documents=[],
            gaps=[],
            kusto_queries=[],
            shadow_refs=[],
            recent_episodes=[],
            community_context=None,
            suggested_followups=[],
            token_estimate=_TOK_ENTITY_SUMMARY + len(historical_edges[:20]) * _TOK_EDGE,
        )

    def global_context_for(self, question: str, max_tokens: int = 800) -> str:
        """Return community summaries most relevant to *question*.

        Uses FTS5 on community_summaries. Falls back to top-N summaries
        if FTS5 unavailable. Enables holistic portfolio questions.
        """
        rows_sql: list[sqlite3.Row] = []
        try:
            safe_q = question.replace('"', '""')
            rows_sql = self._conn.execute(
                "SELECT entity_ids, theme, summary FROM community_summaries"
                " WHERE community_id IN ("
                "   SELECT entity_id FROM kb_search WHERE kb_search MATCH ?"
                "   AND source_type='community' ORDER BY rank LIMIT 5"
                ")",
                (f'"{safe_q}"',),
            ).fetchall()
        except sqlite3.OperationalError:
            pass

        if not rows_sql:
            rows_sql = self._conn.execute(
                "SELECT entity_ids, theme, summary FROM community_summaries ORDER BY generated_at DESC LIMIT 5"
            ).fetchall()

        if not rows_sql:
            return ""

        parts: list[str] = []
        used = 0
        for row in rows_sql:
            text = f"**{row['theme'] or 'Portfolio Context'}:** {row['summary']}"
            cost = _token_estimate(text)
            if used + cost > max_tokens:
                break
            parts.append(text)
            used += cost

        return "\n\n".join(parts)

    def recent_episodes(
        self,
        entity_mentions: list[str] | None = None,
        since_days: int = 30,
        days: int | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Return episodes that mention the given entities, sorted by recency.

        entity_mentions: list of entity IDs to filter by. Pass None (default)
            to return all recent episodes regardless of entity association.
        days: alias for since_days (convenience parameter).
        Enables UC-13: 'When was IcM-XXXXXX last discussed?'
        """
        effective_days = days if days is not None else since_days

        if entity_mentions is None:
            # Return all recent episodes — no entity filter
            rows = self._conn.execute(
                """SELECT id, episode_key, source_type, ingested_at, raw_content
                   FROM episodes
                   WHERE ingested_at >= datetime('now', ? || ' days')
                   ORDER BY ingested_at DESC
                   LIMIT ?""",
                (f"-{int(effective_days)}", limit),
            ).fetchall()
            return [
                {
                    "episode_key": row["episode_key"],
                    "source_type": row["source_type"],
                    "ingested_at": row["ingested_at"],
                    "excerpt": (row["raw_content"] or "")[:300],
                }
                for row in rows
            ]

        if not entity_mentions:
            return []

        # Filter by specific entity mentions via source_episode_id
        placeholders = ",".join("?" * len(entity_mentions))
        rows = self._conn.execute(
            f"""SELECT DISTINCT ep.id, ep.episode_key, ep.source_type,
                       ep.ingested_at, ep.raw_content
                FROM episodes ep
                JOIN entities e ON e.source_episode_id = ep.id
                WHERE e.id IN ({placeholders})
                  AND ep.ingested_at >= datetime('now', ? || ' days')
                ORDER BY ep.ingested_at DESC
                LIMIT ?""",
            entity_mentions + [f"-{int(effective_days)}", limit],
        ).fetchall()
        return [
            {
                "episode_key": row["episode_key"],
                "source_type": row["source_type"],
                "ingested_at": row["ingested_at"],
                "excerpt": (row["raw_content"] or "")[:300],
            }
            for row in rows
        ]

    def _community_context_for_entity(self, entity_id: str) -> str | None:
        """Return the community summary for the entity's community, if any."""
        # Use community_members junction table (O(1) index lookup) instead of LIKE scan.
        rows = self._conn.execute(
            """SELECT cs.theme, cs.summary
               FROM community_members cm
               JOIN community_summaries cs ON cs.community_id = cm.community_id
               WHERE cm.entity_id = ?
               ORDER BY cs.generated_at DESC LIMIT 1""",
            (entity_id,),
        ).fetchall()
        if not rows:
            return None
        row = rows[0]
        return f"{row['theme'] or 'Community'}: {row['summary']}"

    def _suggest_followups(
        self,
        entity: Entity,
        edges: list[Edge],
        gaps: list[Entity],
    ) -> list[str]:
        """Generate proactive follow-up queries from the entity context."""
        followups: list[str] = []
        name = entity.name
        if gaps:
            followups.append(f"What gaps exist for {name}?")
        # Find stale data
        if entity.effective_staleness in ("stale", "expired"):
            followups.append(f"When was {name} last validated?")
        # High-confidence outgoing deps
        outgoing = [e for e in edges if e.from_entity == entity.id and e.rel_type == "depends_on"]
        if outgoing:
            dep_to = outgoing[0].to_entity
            followups.append(f"What does {dep_to} depend on?")
        if entity.type in ("program", "system") and entity.domain:
            followups.append(f"What decisions were made about {name}?")
        return followups[:3]

    # ------------------------------------------------------------------
    # Read — staleness / changes
    # ------------------------------------------------------------------

    def stale_entities(self, domain: str | None = None) -> list[Entity]:
        """Return entities past their staleness TTL."""
        if domain:
            rows = self._conn.execute(
                """SELECT * FROM entities
                   WHERE domain=?
                     AND julianday('now') - julianday(COALESCE(last_validated, updated_at))
                         > staleness_ttl_days
                   ORDER BY
                       julianday('now') - julianday(COALESCE(last_validated, updated_at)) DESC""",
                (domain,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM entities
                   WHERE julianday('now') - julianday(COALESCE(last_validated, updated_at))
                         > staleness_ttl_days
                   ORDER BY
                       julianday('now') - julianday(COALESCE(last_validated, updated_at)) DESC"""
            ).fetchall()
        return [_row_to_entity(row) for row in rows]

    def recent_changes(self, domain: str | None = None, days: int = 30) -> list[dict]:
        """Return entity changes in the last *days* days."""
        if domain:
            rows = self._conn.execute(
                """SELECT eh.entity_id, e.name, e.domain, eh.field,
                          eh.old_value, eh.new_value, eh.changed_at
                   FROM entity_history eh
                   JOIN entities e ON eh.entity_id = e.id
                   WHERE e.domain=?
                     AND eh.changed_at >= datetime('now', ? || ' days')
                   ORDER BY eh.changed_at DESC""",
                (domain, f"-{int(days)}"),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT eh.entity_id, e.name, e.domain, eh.field,
                          eh.old_value, eh.new_value, eh.changed_at
                   FROM entity_history eh
                   JOIN entities e ON eh.entity_id = e.id
                   WHERE eh.changed_at >= datetime('now', ? || ' days')
                   ORDER BY eh.changed_at DESC""",
                (f"-{int(days)}",),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert_entity(
        self,
        entity: dict,
        source: str,
        confidence: float = 0.5,
        source_episode_id: int | None = None,
    ) -> str | None:
        """Insert or update entity. Logs field diffs to entity_history.

        source_episode_id: pass the ID returned by add_episode() for full
        provenance linkage.
        """
        now = _now_utc()
        eid = entity.get("id", "").strip().lower().replace(" ", "-")
        if not eid:
            raise ValueError("entity['id'] is required and must be non-empty")

        # Normalise domains
        domains_val = entity.get("domains")
        if isinstance(domains_val, list):
            domains_json = json.dumps(domains_val)
        elif isinstance(domains_val, str):
            domains_json = domains_val
        else:
            domains_json = None

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self._conn.execute(
                "SELECT * FROM entities WHERE id=?", (eid,)
            ).fetchone()

            if existing is None:
                # Insert new entity
                self._conn.execute(
                    """INSERT INTO entities
                       (id, name, type, domain, domains, summary, detail, current_state,
                        confidence, last_validated, staleness_ttl_days, validation_method,
                        source, source_type, lifecycle_stage, excerpt_hash,
                        source_episode_id, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        eid,
                        entity.get("name", eid),
                        entity.get("type", "system"),
                        entity.get("domain"),
                        domains_json,
                        entity.get("summary"),
                        entity.get("detail"),
                        entity.get("current_state"),
                        float(entity.get("confidence", confidence)),
                        entity.get("last_validated"),
                        int(entity.get("staleness_ttl_days", 90)),
                        entity.get("validation_method"),
                        source,
                        entity.get("source_type") or "",
                        entity.get("lifecycle_stage") or "unknown",
                        entity.get("excerpt_hash"),
                        source_episode_id,
                        now,
                        now,
                    ),
                )
                # Record creation in entity_history so recent_changes() sees new entities
                self._conn.execute(
                    """INSERT INTO entity_history
                       (entity_id, changed_at, field, old_value, new_value, changed_by, session_id)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        eid, now, "created", None, entity.get("name", eid), source,
                        str(source_episode_id) if source_episode_id else None,
                    ),
                )
            else:
                # Update — log diffs to entity_history
                e = dict(existing)
                new_vals: dict[str, Any] = {
                    "name":               entity.get("name", e["name"]),
                    "type":               entity.get("type", e["type"]),
                    "domain":             entity.get("domain", e.get("domain")),
                    "domains":            domains_json or e.get("domains"),
                    "summary":            entity.get("summary", e.get("summary")),
                    "detail":             entity.get("detail", e.get("detail")),
                    "current_state":      entity.get("current_state", e.get("current_state")),
                    "confidence":         float(entity.get("confidence", confidence)),
                    "last_validated":     entity.get("last_validated", e.get("last_validated")),
                    "staleness_ttl_days": int(entity.get("staleness_ttl_days", e.get("staleness_ttl_days", 90))),
                    "validation_method":  entity.get("validation_method", e.get("validation_method")),
                    "source":             source,
                }
                for field in _MUTABLE_ENTITY_FIELDS:
                    old_v = str(e.get(field)) if e.get(field) is not None else None
                    new_v = str(new_vals[field]) if new_vals.get(field) is not None else None
                    if old_v != new_v:
                        self._conn.execute(
                            """INSERT INTO entity_history
                               (entity_id, changed_at, field, old_value, new_value, changed_by, session_id)
                               VALUES (?,?,?,?,?,?,?)""",
                            (
                                eid, now, field, old_v, new_v, source,
                                str(source_episode_id) if source_episode_id else None,
                            ),
                        )

                self._conn.execute(
                    """UPDATE entities SET
                       name=?, type=?, domain=?, domains=?, summary=?, detail=?,
                       current_state=?, confidence=?, last_validated=?,
                       staleness_ttl_days=?, validation_method=?, source=?,
                       source_type=?, lifecycle_stage=?, excerpt_hash=?,
                       source_episode_id=?, updated_at=?
                       WHERE id=?""",
                    (
                        new_vals["name"], new_vals["type"], new_vals["domain"],
                        new_vals["domains"], new_vals["summary"], new_vals["detail"],
                        new_vals["current_state"], new_vals["confidence"],
                        new_vals["last_validated"], new_vals["staleness_ttl_days"],
                        new_vals["validation_method"], source,
                        entity.get("source_type") or e.get("source_type") or "",
                        entity.get("lifecycle_stage") or e.get("lifecycle_stage") or "unknown",
                        entity.get("excerpt_hash") or e.get("excerpt_hash"),
                        source_episode_id, now, eid,
                    ),
                )

            # Invalidate cache
            self._conn.execute(
                "DELETE FROM entity_context_cache WHERE entity_id=?", (eid,)
            )
            self._conn.execute("COMMIT")
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

        return eid

    def add_relationship(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        **kwargs: Any,
    ) -> None:
        """Insert or merge relationship.

        If (from_id, to_id, rel_type) with valid_to IS NULL exists:
          - Take higher confidence, append detail, update last_validated.
        Sets valid_from=now() on new edges.
        Merge logged to entity_history for both endpoints.
        """
        now = _now_utc()
        label     = kwargs.get("label")
        detail    = kwargs.get("detail")
        strength  = kwargs.get("strength", "strong")
        confidence = float(kwargs.get("confidence", 0.5))
        valid_from = kwargs.get("valid_from", now)
        source     = kwargs.get("source")
        source_episode_id = kwargs.get("source_episode_id")

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self._conn.execute(
                "SELECT * FROM relationships"
                " WHERE from_entity=? AND to_entity=? AND rel_type=? AND valid_to IS NULL",
                (from_id, to_id, rel_type),
            ).fetchone()

            if existing is None:
                self._conn.execute(
                    """INSERT INTO relationships
                       (from_entity, to_entity, rel_type, label, detail, strength,
                        confidence, valid_from, valid_to, last_validated, source,
                        source_episode_id, created_at)
                       VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?,?)""",
                    (
                        from_id, to_id, rel_type, label, detail, strength,
                        confidence, valid_from,
                        now, source, source_episode_id, now,
                    ),
                )
            else:
                e = dict(existing)
                merged_conf   = max(confidence, float(e.get("confidence") or 0.5))
                existing_detail = e.get("detail") or ""
                merged_detail   = detail or existing_detail
                if detail and existing_detail and detail != existing_detail:
                    merged_detail = f"{existing_detail}; {detail}"

                self._conn.execute(
                    """UPDATE relationships SET
                       confidence=?, detail=?, last_validated=?, source=?
                       WHERE id=?""",
                    (merged_conf, merged_detail, now, source, e["id"]),
                )
                # Log merge to entity_history for both endpoints
                for ep_id in (from_id, to_id):
                    self._conn.execute(
                        """INSERT INTO entity_history
                           (entity_id, changed_at, field, old_value, new_value, changed_by, session_id)
                           VALUES (?,?,?,?,?,?,?)""",
                        (
                            ep_id, now, "relationship_merged",
                            f"{from_id}->{rel_type}->{to_id}:conf={e.get('confidence')}",
                            f"{from_id}->{rel_type}->{to_id}:conf={merged_conf}",
                            source or "merge",
                            str(source_episode_id) if source_episode_id else None,
                        ),
                    )

            # Invalidate context cache for both endpoints
            self._conn.execute(
                "DELETE FROM entity_context_cache WHERE entity_id IN (?,?)",
                (from_id, to_id),
            )
            self._conn.execute("COMMIT")
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def deactivate_relationship(self, rel_id: int, reason: str) -> None:
        """Set valid_to=now() on an active relationship.

        Used when a dependency changes. Logs to entity_history for both
        endpoints. Never deletes — bi-temporal soft-delete.
        """
        now = _now_utc()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT * FROM relationships WHERE id=?", (rel_id,)
            ).fetchone()
            if row is None:
                self._conn.execute("ROLLBACK")
                return

            r = dict(row)
            self._conn.execute(
                "UPDATE relationships SET valid_to=? WHERE id=?",
                (now, rel_id),
            )
            for ep_id in (r["from_entity"], r["to_entity"]):
                self._conn.execute(
                    """INSERT INTO entity_history
                       (entity_id, changed_at, field, old_value, new_value, changed_by)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        ep_id, now, "relationship_deactivated",
                        f"{r['from_entity']}->{r['rel_type']}->{r['to_entity']}",
                        f"deactivated:{reason}",
                        "deactivate_relationship",
                    ),
                )
            self._conn.execute(
                "DELETE FROM entity_context_cache WHERE entity_id IN (?,?)",
                (r["from_entity"], r["to_entity"]),
            )
            self._conn.execute("COMMIT")
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def add_episode(
        self,
        episode_key: str,
        source_type: str,
        raw_content: str | None = None,
    ) -> int:
        """Record a new ingestion episode. Returns the episode ID."""
        now = _now_utc()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            # Idempotent: return existing ID if key already exists
            existing = self._conn.execute(
                "SELECT id FROM episodes WHERE episode_key=?", (episode_key,)
            ).fetchone()
            if existing:
                self._conn.execute("COMMIT")
                return existing["id"]

            cur = self._conn.execute(
                """INSERT INTO episodes (episode_key, source_type, raw_content, ingested_at)
                   VALUES (?,?,?,?)""",
                (episode_key, source_type, raw_content, now),
            )
            ep_id = cur.lastrowid
            self._conn.execute("COMMIT")
            return ep_id
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def add_alias(self, alias: str, entity_id: str) -> None:
        """Register an alias for an entity. Idempotent."""
        norm = alias.strip().lower()
        now = _now_utc()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO entity_aliases (alias, entity_id, added_at) VALUES (?,?,?)",
                (norm, entity_id, now),
            )
            self._conn.execute("COMMIT")
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def invalidate_cache(self, entity_id: str) -> None:
        """Clear entity_context_cache for this entity and its neighbours."""
        neighbours = self._conn.execute(
            "SELECT from_entity, to_entity FROM relationships WHERE from_entity=? OR to_entity=?",
            (entity_id, entity_id),
        ).fetchall()
        ids = {entity_id}
        for row in neighbours:
            ids.add(row["from_entity"])
            ids.add(row["to_entity"])

        for eid in ids:
            self._conn.execute(
                "DELETE FROM entity_context_cache WHERE entity_id=?", (eid,)
            )

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def validate_integrity(self) -> list[str]:
        """Run integrity checks. Returns list of issue strings (empty = healthy).

        Checks:
          - PRAGMA integrity_check
          - Orphan edges (entity referenced doesn't exist)
          - FTS drift (count mismatch between source tables and kb_search)
          - Episode orphans (episodes with no entity references)
          - Entities with no domain assignment
        """
        issues: list[str] = []

        # 1. SQLite structural integrity
        row = self._conn.execute("PRAGMA integrity_check").fetchone()
        if row and row[0] != "ok":
            issues.append(f"PRAGMA integrity_check failed: {row[0]}")

        # 2. Orphan edges
        orphan_from = self._conn.execute(
            "SELECT COUNT(*) FROM relationships r"
            " WHERE r.valid_to IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM entities e WHERE e.id=r.from_entity)"
        ).fetchone()[0]
        orphan_to = self._conn.execute(
            "SELECT COUNT(*) FROM relationships r"
            " WHERE r.valid_to IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM entities e WHERE e.id=r.to_entity)"
        ).fetchone()[0]
        if orphan_from:
            issues.append(f"Orphan edges (from_entity missing): {orphan_from}")
        if orphan_to:
            issues.append(f"Orphan edges (to_entity missing): {orphan_to}")

        # 3. FTS drift checks
        for tbl, source_type in [
            ("entities", "entity"),
            ("decisions", "decision"),
            ("kusto_queries", "kusto_query"),
            ("documents", "document"),
        ]:
            count_tbl = self._conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            count_fts = self._conn.execute(
                "SELECT COUNT(*) FROM kb_search WHERE source_type=?",
                (source_type,),
            ).fetchone()[0]
            if abs(count_tbl - count_fts) > 0:
                issues.append(
                    f"FTS drift: {tbl} has {count_tbl} rows but kb_search has"
                    f" {count_fts} '{source_type}' entries"
                )

        # 4. Entities with no domain
        no_domain = self._conn.execute(
            "SELECT COUNT(*) FROM entities WHERE domain IS NULL AND type NOT IN ('shadow', 'gap')"
        ).fetchone()[0]
        if no_domain:
            issues.append(f"Entities missing domain assignment: {no_domain}")

        # Log integrity check timestamp
        now = _now_utc()
        self._conn.execute(
            "INSERT OR REPLACE INTO kb_meta (key, value) VALUES ('last_integrity_check', ?)",
            (now,),
        )

        return issues

    def get_stats(self) -> dict:
        """Return live counts for all major tables."""
        def count(tbl: str) -> int:
            return self._conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]

        def count_where(tbl: str, where: str) -> int:
            return self._conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {where}").fetchone()[0]

        db_size = 0
        try:
            db_size = self._db_path.stat().st_size
        except OSError:
            pass

        last_updated_row = self._conn.execute(
            "SELECT MAX(updated_at) FROM entities"
        ).fetchone()
        last_updated = last_updated_row[0] if last_updated_row else None

        return {
            "entities":               count("entities"),
            "relationships":          count("relationships"),
            "active_relationships":   count_where("relationships", "valid_to IS NULL"),
            "historical_relationships": count_where("relationships", "valid_to IS NOT NULL"),
            "episodes":               count("episodes"),
            "decisions":              count("decisions"),
            "kusto_queries":          count("kusto_queries"),
            "documents":              count("documents"),
            "artifacts":              count("artifacts"),
            "aliases":                count("entity_aliases"),
            "communities":            count("community_summaries"),
            "research_archive":       count("research_archive"),
            "db_size_bytes":          db_size,
            "last_updated":           last_updated,
        }

    def god_nodes(self, degree_threshold: int = 10, limit: int = 20) -> list[dict]:
        """Return high-degree (hub) entities that may be over-connected. §8.3

        Returns entities linked to >= *degree_threshold* active relationships.
        Useful for identifying god-nodes that should be split or filtered from
        context windows to avoid token budget bloat.
        """
        rows = self._conn.execute(
            """SELECT e.id, e.name, e.type, e.domain, e.confidence,
                      COUNT(r.id) AS degree
               FROM entities e
               JOIN relationships r
                 ON (r.from_entity = e.id OR r.to_entity = e.id)
                AND r.valid_to IS NULL
               WHERE (e.lifecycle_stage NOT IN ('cancelled', 'archived', 'superseded')
                      OR e.lifecycle_stage IS NULL)
               GROUP BY e.id
               HAVING degree >= ?
               ORDER BY degree DESC
               LIMIT ?""",
            (degree_threshold, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def rebuild_communities(self) -> int:
        """Re-cluster entities via Leiden algorithm (graspologic) with Union-Find fallback.

        Leiden produces higher-quality communities by optimising modularity globally.
        Falls back to connected-components (Union-Find) when graspologic is not installed.
        Returns number of non-singleton communities written to community_summaries.
        Full LLM-generated narrative summaries are a v1.1 feature.
        """
        # Fetch all entities and active edges
        entity_rows = self._conn.execute("SELECT id, name, domain FROM entities").fetchall()
        edge_rows   = self._conn.execute(
            "SELECT from_entity, to_entity FROM relationships WHERE valid_to IS NULL"
        ).fetchall()

        entity_info: dict[str, dict] = {
            row["id"]: {"name": row["name"], "domain": row["domain"]}
            for row in entity_rows
        }
        entity_ids: list[str] = [row["id"] for row in entity_rows]

        # ── Leiden clustering (graspologic optional) ──────────────────────────
        communities: dict[str, list[str]] = {}
        _leiden_used = False
        if len(entity_ids) >= 2 and edge_rows:
            try:
                import networkx as nx  # graspologic depends on networkx
                from graspologic.partition import hierarchical_leiden

                G = nx.Graph()
                G.add_nodes_from(entity_ids)
                for row in edge_rows:
                    fe, te = row["from_entity"], row["to_entity"]
                    if fe in entity_info and te in entity_info:
                        G.add_edge(fe, te)

                # hierarchical_leiden returns list of PartitionedGraph objects;
                # the last level gives finest-grain communities.
                partition_levels = hierarchical_leiden(G, max_cluster_size=max(10, len(entity_ids) // 10))
                # Use the final (finest) partition level
                final_partition = partition_levels[-1] if partition_levels else None
                if final_partition is not None:
                    for node, community_id in final_partition.final_level_hierarchical_community_id.items():
                        communities.setdefault(str(community_id), []).append(node)
                    _leiden_used = True
            except Exception:
                communities = {}  # fall through to Union-Find

        # ── Union-Find fallback (connected components) ────────────────────────
        if not _leiden_used:
            parent: dict[str, str] = {eid: eid for eid in entity_ids}

            def find(x: str) -> str:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]  # path compression
                    x = parent[x]
                return x

            def union(x: str, y: str) -> None:
                rx, ry = find(x), find(y)
                if rx != ry:
                    parent[rx] = ry

            for row in edge_rows:
                fe, te = row["from_entity"], row["to_entity"]
                if fe in parent and te in parent:
                    union(fe, te)

            for eid in parent:
                root = find(eid)
                communities.setdefault(root, []).append(eid)

        now = _now_utc()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute("DELETE FROM community_members")
            self._conn.execute("DELETE FROM community_summaries")
            num = 0
            for i, (root, members) in enumerate(communities.items()):
                if len(members) < 2:
                    continue  # skip singletons
                community_id = f"community-{i:04d}"
                domains = {entity_info[m]["domain"] for m in members if entity_info[m].get("domain")}
                domain_label = ", ".join(sorted(d for d in domains if d)) or "mixed"
                names = sorted(entity_info[m]["name"] for m in members)
                summary = (
                    f"Cluster of {len(members)} entities in domain(s): {domain_label}. "
                    f"Members include: {', '.join(names[:10])}"
                    + (" …" if len(names) > 10 else ".")
                )
                theme = domain_label
                self._conn.execute(
                    """INSERT INTO community_summaries
                       (community_id, entity_ids, theme, summary, generated_at)
                       VALUES (?,?,?,?,?)""",
                    (community_id, json.dumps(members), theme, summary, now),
                )
                for member_id in members:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO community_members"
                        " (community_id, entity_id) VALUES (?,?)",
                        (community_id, member_id),
                    )
                num += 1
            self._conn.execute("COMMIT")
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

        return num

    def vacuum(self) -> None:
        """Run VACUUM to reclaim space. Updates kb_meta.last_vacuum."""
        self._conn.execute("VACUUM")
        self._conn.execute(
            "INSERT OR REPLACE INTO kb_meta (key, value) VALUES ('last_vacuum', ?)",
            (_now_utc(),),
        )

    def backup(self, tier: str = "daily", dest_dir: Path | None = None) -> Path:
        """Create a clean backup snapshot. See spec §10.1 for full contract.

        Step 1: Open a temp connection to OS temp file.
        Step 2: sqlite3.Connection.backup() — fully checkpointed WAL-free snapshot.
        Step 3: Close dest connection.
        Step 4: Atomic move temp → final OneDrive path.
        Step 5: Enforce retention (7 daily / 4 weekly / 6 monthly).
        Step 6: UPDATE kb_meta last_backup_<tier>.
        Step 7: UPDATE kb_meta last_backup_path.

        dest_dir: override the backup directory (used in tests to avoid OneDrive).
        """
        if tier not in ("daily", "weekly", "monthly"):
            raise ValueError(f"Invalid backup tier '{tier}'. Must be daily|weekly|monthly.")

        now = datetime.now(timezone.utc)

        # Determine filename
        if tier == "daily":
            fname = f"kb-{now.strftime('%Y-%m-%d')}.sqlite"
        elif tier == "weekly":
            fname = f"kb-{now.strftime('%G-W%V')}.sqlite"  # ISO week
        else:
            fname = f"kb-{now.strftime('%Y-%m')}.sqlite"

        # Resolve backup destination
        if dest_dir is not None:
            tier_dir = dest_dir
        else:
            backup_dir_override = os.environ.get("ARTHA_KB_BACKUP_DIR", "").strip()
            if backup_dir_override:
                backup_root = Path(backup_dir_override)
            else:
                backup_root = self._artha_dir / "backups"
            tier_dir = backup_root / tier
        tier_dir.mkdir(parents=True, exist_ok=True)
        dest_path = tier_dir / fname

        # Step 1: Create temp file in OS temp dir (never on OneDrive)
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            prefix=f"kb-backup-{os.getpid()}-",
            suffix=".sqlite",
        )
        tmp_path = Path(tmp_path_str)
        os.close(tmp_fd)

        try:
            # DEBT-035: Checkpoint WAL before backup to ensure consistent snapshot.
            # TRUNCATE mode folds WAL into the main DB and discards the sidecar.
            # If checkpoint is incomplete (busy_log > 0), skip backup this cycle.
            wal_result = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if wal_result and len(wal_result) >= 3:
                _blocked, _checkpointed, _remaining = wal_result
                if _remaining > 0:
                    _log.warning(
                        "KB backup: WAL checkpoint incomplete (%d frames remaining) — "
                        "skipping backup to avoid inconsistent snapshot",
                        _remaining,
                    )
                    if tmp_path.exists():
                        tmp_path.unlink(missing_ok=True)
                    return dest_path  # non-fatal skip

            # Step 2: Backup via Python stdlib — WAL-clean snapshot
            dest_conn = sqlite3.connect(str(tmp_path))
            try:
                self._conn.backup(dest_conn)
            finally:
                dest_conn.close()

            # Verify: no .sqlite-wal sidecar was created for the backup file
            wal_sidecar = tmp_path.with_suffix(".sqlite-wal")
            if wal_sidecar.exists():
                _log.warning("KB backup: unexpected WAL sidecar at %s — removing", wal_sidecar)
                wal_sidecar.unlink(missing_ok=True)

            # Step 4: Atomic move to final path
            try:
                shutil.move(str(tmp_path), str(dest_path))
            except OSError:
                # Cross-device (tmp on C:\, backup on D:\) — copy + unlink
                shutil.copy2(str(tmp_path), str(dest_path))
                tmp_path.unlink(missing_ok=True)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

        # Step 5: Enforce retention
        retention = {"daily": 7, "weekly": 4, "monthly": 6}
        keep_n = retention[tier]
        existing = sorted(tier_dir.glob("kb-*.sqlite"), key=lambda p: p.stat().st_mtime)
        while len(existing) > keep_n:
            oldest = existing.pop(0)
            try:
                oldest.unlink()
            except OSError as exc:
                _log.warning("KB backup retention: could not delete %s: %s", oldest, exc)

        # Steps 6 & 7: Update kb_meta
        now_iso = now.isoformat(timespec="seconds")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO kb_meta (key, value) VALUES (?, ?)",
                (f"last_backup_{tier}", now_iso),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO kb_meta (key, value) VALUES ('last_backup_path', ?)",
                (str(dest_path),),
            )
            self._conn.execute("COMMIT")
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

        _log.info("KB backup: tier=%s path=%s", tier, dest_path)
        return dest_path

    def close(self) -> None:
        """Close the DB connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def _maybe_gc(self, ttl_days: int = 90) -> int:
        """DEBT-KG-002: Prune ghost entities (no edges, no recent activity).

        A 'ghost entity' is an entity with:
          - zero edges in the KB
          - last_seen / created_at older than *ttl_days*

        Runs at most once per calendar day per process to avoid latency impact.
        Returns the number of entities removed.

        This is a best-effort cleanup — if the DB is busy or locked, we skip
        silently and return 0.
        """
        import datetime as _dt_gc
        # Rate-limit: at most once per day per process via a module-level sentinel
        _today = _dt_gc.date.today().isoformat()
        _gc_key = f"_kg_gc_last_{id(self)}"
        if getattr(self, _gc_key, None) == _today:
            return 0
        setattr(self, _gc_key, _today)

        cutoff_ts = (
            _dt_gc.datetime.utcnow() - _dt_gc.timedelta(days=ttl_days)
        ).isoformat()

        try:
            cur = self._conn.cursor()
            # Find entities with no edges and older than cutoff
            cur.execute(
                """
                SELECT e.id FROM entities e
                WHERE NOT EXISTS (
                    SELECT 1 FROM edges ed
                    WHERE ed.from_id = e.id OR ed.to_id = e.id
                )
                AND e.last_seen < ? AND e.created_at < ?
                """,
                (cutoff_ts, cutoff_ts),
            )
            ghost_ids = [row[0] for row in cur.fetchall()]
            if not ghost_ids:
                return 0
            placeholders = ",".join("?" * len(ghost_ids))
            cur.execute(f"DELETE FROM entities WHERE id IN ({placeholders})", ghost_ids)
            self._conn.commit()
            removed = len(ghost_ids)
            _log.info("KG_GC: removed %d ghost entities (ttl=%dd) (DEBT-KG-002)", removed, ttl_days)
            return removed
        except Exception as exc:  # noqa: BLE001
            _log.debug("KG_GC: skipped — %s", exc)
            return 0


# ---------------------------------------------------------------------------
# Factory — returns NullKnowledgeGraph on corruption (spec §6.5)
# ---------------------------------------------------------------------------

def get_kb(
    artha_dir: Path | None = None,
    db_path: Path | None = None,
) -> KnowledgeGraph | NullKnowledgeGraph:
    """Safe factory for KnowledgeGraph.

    Returns NullKnowledgeGraph if the DB is corrupted or inaccessible.
    All pipeline callers should use this factory, not KnowledgeGraph() directly,
    so that KB failures never crash the work briefing.
    """
    try:
        return KnowledgeGraph(artha_dir=artha_dir, db_path=db_path)
    except sqlite3.DatabaseError as exc:
        _log.error("KB corrupted — returning NullKnowledgeGraph: %s", exc)
        _write_audit_log(artha_dir, f"[KB_CORRUPTED] {exc}")
        return NullKnowledgeGraph()
    except Exception as exc:
        _log.error("KB init failed — returning NullKnowledgeGraph: %s", exc)
        _write_audit_log(artha_dir, f"[KB_INIT_FAILED] {exc}")
        return NullKnowledgeGraph()


def _write_audit_log(artha_dir: Path | None, message: str) -> None:
    """Safe audit log write — never raises."""
    try:
        base = artha_dir or Path(__file__).resolve().parent.parent.parent
        audit = base / "state" / "audit.md"
        ts = _now_utc()
        with open(audit, "a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] KB | {message}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# KnowledgeEnricher — proactive context injection (spec §6.7)
# ---------------------------------------------------------------------------

class KnowledgeEnricher:
    """Pulls KB context into pipeline outputs proactively.

    Called by work_loop.py after entity detection and by work:prep.
    Always wraps calls in try/except — graph failures never crash the pipeline.
    """

    def __init__(self, kg: KnowledgeGraph | NullKnowledgeGraph) -> None:
        self.kg = kg

    def enrich_briefing(
        self,
        briefing: dict,
        token_budget: int = 800,
    ) -> dict:
        """Called by work_loop.py. Accepts a briefing dict, returns it enriched with 'kb_context'.

        Extracts entity mentions from briefing['summary'] / briefing['content'], looks up KB
        context, and injects a 'kb_context' markdown block into the returned dict.
        Returns the original dict unchanged if KB is empty or unavailable.
        """
        try:
            if not isinstance(briefing, dict):
                return {}
            summary = briefing.get("summary") or briefing.get("content") or ""
            # Derive candidate entity queries from words in the summary
            words = [w.strip(".,!?;:\"'") for w in summary.split() if len(w) > 3]
            briefing_entities = [" ".join(words[i:i + 2]) for i in range(0, min(len(words) - 1, 8), 2)]

            parts: list[str] = []
            used = 0
            per_entity_budget = token_budget // max(len(briefing_entities), 1)
            per_entity_budget = max(200, min(per_entity_budget, 400))

            for entity_name in briefing_entities[:3]:
                entity = self.kg.resolve_entity(entity_name)
                if entity is None:
                    continue
                ctx = self.kg.context_for(entity.id, token_budget=per_entity_budget, depth=1)
                if ctx is None or ctx.entity is None:
                    continue
                e = ctx.entity
                state_part = f" {e.current_state}." if e.current_state else ""
                staleness = (
                    f" ⚠ {e.effective_staleness}" if e.effective_staleness in ("stale", "expired") else ""
                )
                gaps_part = f" {len(ctx.gaps)} open gaps." if ctx.gaps else ""
                last_dec = ctx.decisions[0].get("title") if ctx.decisions else None
                dec_part = f" Last decision: {last_dec}." if last_dec else ""
                line = f"**{e.name}**:{state_part}{gaps_part}{dec_part}{staleness}"
                cost = _token_estimate(line)
                if used + cost > token_budget:
                    break
                parts.append(line)
                used += cost

            result = dict(briefing)
            if parts:
                result["kb_context"] = "## KB Context\n\n" + "\n\n".join(parts)
            return result
        except Exception as exc:
            _log.warning("KnowledgeEnricher.enrich_briefing failed: %s", exc)
            return dict(briefing) if isinstance(briefing, dict) else {}

    def enrich_meeting_prep(
        self,
        context: dict,
        token_budget: int = 2000,
    ) -> dict:
        """Called by work:prep. Accepts a context dict with 'meeting_title'/'attendees',
        returns it enriched with 'kb_context'."""
        try:
            if not isinstance(context, dict):
                return {}
            meeting_title = context.get("meeting_title") or context.get("title") or ""
            parts: list[str] = []
            if meeting_title:
                candidates = self.kg.resolve_entity_candidates(meeting_title, limit=3)
                for entity, _, _ in candidates:
                    ctx = self.kg.context_for(
                        entity.id, token_budget=token_budget // max(len(candidates), 1)
                    )
                    if ctx is None or ctx.entity is None:
                        continue
                    parts.append(self._format_context_block(ctx))
                if not parts:
                    # Fallback: FTS search
                    results = self.kg.search(meeting_title, limit=5)
                    for sr in results[:2]:
                        if sr.source_type == "entity":
                            ctx = self.kg.context_for(sr.entity_id, token_budget=600)
                            if ctx and ctx.entity:
                                parts.append(self._format_context_block(ctx))
            result = dict(context)
            if parts:
                result["kb_context"] = "\n\n".join(parts)
            return result
        except Exception as exc:
            _log.warning("KnowledgeEnricher.enrich_meeting_prep failed: %s", exc)
            return dict(context) if isinstance(context, dict) else {}

    def _format_context_block(self, ctx: EntityContext) -> str:
        """Compact graph-markdown notation (§4.11a).

        Each entity renders on one line:
          - **<name>** [<type>|<lifecycle_stage>|<confidence:.2f>] → <rel_type>: <target>; ...
        This achieves ~3× token density vs. the previous prose format.
        """
        if ctx.entity is None:
            return ""
        e = ctx.entity
        # Build relationship tail: "→ rel_type: target_name; ..."
        rel_parts: list[str] = []
        for edge in ctx.edges[:5]:
            other_id = edge.to_entity if edge.from_entity == e.id else edge.from_entity
            other_ent = self.get_entity(other_id)
            other_name = other_ent.name if other_ent else other_id
            rel_parts.append(f"{edge.rel_type}: {other_name}")
        rel_tail = " → " + "; ".join(rel_parts) if rel_parts else ""
        lc = e.lifecycle_stage or "unknown"
        conf = f"{e.confidence:.2f}" if e.confidence is not None else "?"
        line = f"- **{e.name}** [{e.type or 'entity'}|{lc}|{conf}]{rel_tail}"
        extras: list[str] = []
        if ctx.gaps:
            extras.append(f"gaps: {', '.join(g.name for g in ctx.gaps[:3])}")
        if ctx.decisions:
            d = ctx.decisions[0]
            extras.append(f"last-decision: {d.get('title', '')}")
        if e.current_state:
            extras.append(f"state: {e.current_state}")
        if extras:
            line += "  (" + "; ".join(extras) + ")"
        return line

