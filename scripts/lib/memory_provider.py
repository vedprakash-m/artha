#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/lib/memory_provider.py — Pluggable memory provider interface for Artha.

Defines the ``MemoryProvider`` ABC and provides two concrete implementations:

* ``FlatFileProvider`` — Permanent flat-file backend (ADR-001).  Parses and
  writes ``state/memory.md`` using YAML frontmatter, matching the authoritative
  format written by ``fact_extractor.py``.  Active whenever
  ``memory_provider: flat_file`` (default) in ``config/artha_config.yaml``.

* ``SqliteFtsProvider`` — Stub only.  NOT ACTIVE — upgrade trigger criteria in
  specs/agent-fw.md §3.7.7 are not met (ADR-001 is permanent until they are).

* ``LanceDbProvider`` — Stub only.  NOT IMPLEMENTED — same ADR-001 gate.

Design constraints (specs/agent-fw.md §3.7 review decisions):
- ALL memory read/write callers MUST go through ``MemoryProvider``.
- ``memory_provider`` feature flag in config is the single source of truth.
- Embedding path is always OPTIONAL — base functionality works without it
  (A-14 constraint: Artha runs in VS Code Copilot without Python deps).

Spec: specs/agent-fw.md §3.7 (AFW-7), ADR-001 (§10.8)
Phase: Wave 3 — FlatFileProvider permanent (ADR-001); SQLite/LanceDB stubs retained but inactive
"""
from __future__ import annotations

import re
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Schema version guard
# ---------------------------------------------------------------------------

class SchemaVersionMissing(RuntimeError):
    """Raised when config/memory.yaml is loaded without a schema_version field.

    This error is intentionally NOT caught silently — it signals that the
    memory store may be in an unknown migration state and is unsafe to use.
    """


def _require_schema_version(memory_cfg: dict) -> int:
    """Validate that memory_cfg contains schema_version. Raises if absent."""
    if "schema_version" not in memory_cfg:
        raise SchemaVersionMissing(
            "config/memory.yaml is missing 'schema_version'. "
            "This field is mandatory. Add 'schema_version: 1' before using "
            "the memory system. See specs/agent-fw.md §3.7."
        )
    return int(memory_cfg["schema_version"])


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class MemoryProvider(ABC):
    """Backend-agnostic memory storage interface.

    All concrete providers MUST implement the three core operations.
    Callers must never import a provider directly — use
    ``get_provider()`` to obtain the configured backend.
    """

    @abstractmethod
    def remember(self, scope: str, text: str, metadata: dict[str, Any]) -> str:
        """Store a memory entry.

        Args:
            scope: Hierarchical scope path, e.g. ``"/personal/finance"``.
            text: The memory text to store.
            metadata: Arbitrary JSON-serialisable metadata (importance,
                source, tags, etc.).

        Returns:
            A unique memory ID string.
        """
        ...

    @abstractmethod
    def recall(self, scope: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve memories matching query within scope.

        Args:
            scope: Scope prefix to search within.  Subscopes are included
                (e.g. ``"/personal"`` returns items from ``"/personal/finance"``).
            query: Free-text query string.
            limit: Maximum number of results to return.

        Returns:
            List of dicts with keys: ``id``, ``scope``, ``text``,
            ``metadata``, ``score`` (relevance, 0.0–1.0).
        """
        ...

    @abstractmethod
    def forget(self, memory_id: str) -> bool:
        """Delete a memory by ID.

        Args:
            memory_id: The ID returned by ``remember()``.

        Returns:
            ``True`` if the memory was found and deleted, ``False`` if not found.
        """
        ...


# ---------------------------------------------------------------------------
# FlatFileProvider — zero-dependency fallback
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Domain synonym map — zero external deps (Task 3, ADR-001)
# Used by FlatFileProvider._expand_query() for improved keyword recall.
# ---------------------------------------------------------------------------
_SYNONYMS: dict[str, set[str]] = {
    "immigration": {"visa", "h1b", "h4", "ead", "i-485", "green card", "i-140", "petition"},
    "finance": {"tax", "investment", "401k", "ira", "espp", "stock", "dividend", "irs"},
    "vehicle": {"car", "auto", "insurance", "dmv", "registration", "license"},
    "health": {"doctor", "appointment", "prescription", "insurance", "dental"},
    "work": {"job", "employment", "sprint", "career", "microsoft", "team", "manager"},
    "kids": {"school", "preschool", "daycare", "child", "children", "pediatric"},
    "home": {"mortgage", "hoa", "utility", "utilities", "rent", "lease"},
}


class FlatFileProvider(MemoryProvider):
    """Parses and writes ``state/memory.md`` using YAML frontmatter format.

    This is the permanent backend per ADR-001 (specs/agent-fw.md §10.8).
    The format matches the authoritative format written by ``fact_extractor.py``:

    .. code-block:: yaml

        ---
        domain: memory
        facts:
        - id: <id>
          statement: <text>
          domain: <domain>
          date_added: <iso-date>
          confidence: 1.0
          type: <type>
          source: <source>
          ttl_days: null
          last_seen: <iso-date>
        ---

    Human-readable, git-diffable, and zero-dependency (stdlib yaml fallback).
    Recall uses keyword + synonym expansion for domain-aware matching.
    Scoring is recency-only (newest = 1.0, decay by 0.1/day, floor 0).
    """

    def __init__(self, artha_dir: Path | None = None) -> None:
        self._root = artha_dir or Path(__file__).resolve().parents[2]
        self._memory_file = self._root / "state" / "memory.md"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_entries(self) -> list[dict[str, Any]]:
        """Parse state/memory.md YAML frontmatter into an entry list.

        Reads the authoritative YAML frontmatter format written by
        ``fact_extractor.py``.  Returns empty list if the file does not exist
        or has no frontmatter.
        """
        if not self._memory_file.exists():
            return []
        try:
            import yaml  # noqa: PLC0415
        except ImportError:
            yaml = None  # type: ignore[assignment]

        raw = self._memory_file.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            return []

        # Extract YAML frontmatter between the opening and closing ---
        parts = raw.split("---", 2)
        if len(parts) < 3:
            return []

        if yaml is not None:
            try:
                data = yaml.safe_load(parts[1]) or {}
            except Exception:  # noqa: BLE001
                return []
        else:
            # yaml not available — crude fallback: no facts accessible
            return []

        facts = data.get("facts", [])
        return [
            {
                "id": f.get("id", ""),
                "scope": "/" + f.get("domain", "general"),
                "text": f.get("statement", ""),
                "timestamp": f.get("date_added", ""),
                "metadata": {k: v for k, v in f.items()
                             if k not in ("id", "statement", "date_added")},
            }
            for f in facts
            if isinstance(f, dict)
        ]

    def _save_entries(self, entries: list[dict[str, Any]]) -> None:
        """Serialise entries back to state/memory.md as YAML frontmatter.

        Writes the authoritative format compatible with ``fact_extractor.py``.
        Requires PyYAML.  If yaml is unavailable, raises RuntimeError rather
        than silently corrupting the file.
        """
        try:
            import yaml  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "FlatFileProvider._save_entries() requires PyYAML. "
                "Install it with: pip install pyyaml"
            ) from None

        facts = []
        for entry in entries:
            scope = entry.get("scope", "/general")
            domain = scope.lstrip("/") or "general"
            metadata = entry.get("metadata", {})
            fact: dict[str, Any] = {
                "id": entry.get("id", ""),
                "statement": entry.get("text", ""),
                "date_added": entry.get("timestamp", ""),
                "domain": metadata.get("domain", domain),
                "confidence": metadata.get("confidence", 1.0),
                "type": metadata.get("type", "fact"),
                "source": metadata.get("source", ""),
                "ttl_days": metadata.get("ttl_days"),
                "last_seen": metadata.get("last_seen", entry.get("timestamp", "")),
            }
            facts.append(fact)

        data: dict[str, Any] = {"domain": "memory", "facts": facts}
        fm_text = yaml.dump(data, default_flow_style=False, allow_unicode=True,
                            sort_keys=True)
        self._memory_file.parent.mkdir(parents=True, exist_ok=True)
        self._memory_file.write_text(f"---\n{fm_text}---\n", encoding="utf-8")

    def _recency_score(self, ts_str: str) -> float:
        """Convert ISO timestamp to a recency score [0.0, 1.0]."""
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - ts).days
            return max(0.0, 1.0 - 0.1 * age_days)
        except Exception:  # noqa: BLE001
            return 0.0

    def _expand_query(self, query: str) -> set[str]:
        """Expand query tokens with domain-aware synonyms.

        If any query token matches a synonym in the ``_SYNONYMS`` map, the
        full synonym set for that domain is added to the search tokens.
        This improves recall for domain-specific queries without external deps.

        Example: query "visa" expands to include "h1b", "ead", "i-140", etc.
        """
        tokens = set(query.lower().split())
        expanded = set(tokens)
        for domain, synonyms in _SYNONYMS.items():
            if tokens & synonyms or domain in tokens:
                expanded |= synonyms
        return expanded

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def remember(self, scope: str, text: str, metadata: dict[str, Any]) -> str:
        import hashlib
        import time
        mem_id = hashlib.sha1(f"{scope}:{text}:{time.time()}".encode()).hexdigest()[:12]
        ts = datetime.now(timezone.utc).isoformat()
        entries = self._load_entries()
        entries.append({"id": mem_id, "scope": scope, "timestamp": ts, "metadata": metadata, "text": text})
        self._save_entries(entries)
        return mem_id

    def recall(self, scope: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve memories matching query within scope.

        Uses keyword + synonym expansion (``_expand_query``) for domain-aware
        recall.  Synonyms are resolved without LLM calls (zero external deps).
        Scoring is recency-based.
        """
        entries = self._load_entries()
        tokens = self._expand_query(query)
        results: list[dict[str, Any]] = []
        for entry in entries:
            if not entry["scope"].startswith(scope.rstrip("/")):
                continue
            entry_text = entry["text"].lower()
            if not any(tok in entry_text for tok in tokens):
                continue
            score = self._recency_score(entry["timestamp"])
            results.append({**entry, "score": score})
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def forget(self, memory_id: str) -> bool:
        entries = self._load_entries()
        original_len = len(entries)
        entries = [e for e in entries if e["id"] != memory_id]
        if len(entries) == original_len:
            return False
        self._save_entries(entries)
        return True


# ---------------------------------------------------------------------------
# SqliteFtsProvider — Phase 0 (stub — not yet active)
# ---------------------------------------------------------------------------

class SqliteFtsProvider(MemoryProvider):
    """Phase 0: SQLite + FTS5 keyword search.

    Zero external dependencies (sqlite3 is Python stdlib).

    STATUS: Stub implementation, inactive per ADR-001 (specs/agent-fw.md \u00a710.8).
    FlatFileProvider is the permanent backend until upgrade trigger criteria in
    \u00a73.7.7 are met. SQLite/LanceDB migration is cancelled.

    If upgrade triggers are met in future, implement:
    - FTS5 virtual table for full-text keyword search
    - Recency decay column for composite scoring
    - Scope hierarchy index for subtree queries
    """

    def __init__(self, db_path: Path | None = None, artha_dir: Path | None = None) -> None:
        root = artha_dir or Path(__file__).resolve().parents[2]
        self._db_path = db_path or root / "tmp" / "memory.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    importance REAL NOT NULL DEFAULT 0.5
                )
            """)
            # FTS5 virtual table for keyword recall
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(id UNINDEXED, scope, text, content=memories, content_rowid=rowid)
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, id, scope, text)
                    VALUES (new.rowid, new.id, new.scope, new.text);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, id, scope, text)
                    VALUES ('delete', old.rowid, old.id, old.scope, old.text);
                END
            """)
            conn.commit()

    def remember(self, scope: str, text: str, metadata: dict[str, Any]) -> str:
        import hashlib
        import json
        import time
        mem_id = hashlib.sha1(f"{scope}:{text}:{time.time()}".encode()).hexdigest()[:12]
        ts = datetime.now(timezone.utc).isoformat()
        importance = float(metadata.get("importance", 0.5))
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO memories (id, scope, text, metadata, created_at, importance) VALUES (?,?,?,?,?,?)",
                (mem_id, scope, text, json.dumps(metadata), ts, importance),
            )
            conn.commit()
        return mem_id

    def recall(self, scope: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        import json
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            # FTS5 match within scope prefix
            rows = conn.execute(
                """
                SELECT m.id, m.scope, m.text, m.metadata, m.created_at, m.importance,
                       bm25(memories_fts) AS bm25_score
                FROM memories_fts
                JOIN memories m ON m.id = memories_fts.id
                WHERE memories_fts MATCH ? AND m.scope LIKE ?
                ORDER BY bm25_score
                LIMIT ?
                """,
                (query, f"{scope.rstrip('/')}%", limit),
            ).fetchall()

        results = []
        for row in rows:
            try:
                meta = json.loads(row["metadata"])
            except Exception:  # noqa: BLE001
                meta = {}
            # Recency decay (same formula as FlatFileProvider)
            try:
                ts = datetime.fromisoformat(row["created_at"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - ts).days
                recency = max(0.0, 1.0 - 0.1 * age_days)
            except Exception:  # noqa: BLE001
                recency = 0.0
            # Simple composite: importance + recency (BM25 is secondary signal)
            score = 0.5 * float(row["importance"]) + 0.5 * recency
            results.append({
                "id": row["id"],
                "scope": row["scope"],
                "text": row["text"],
                "metadata": meta,
                "timestamp": row["created_at"],
                "score": score,
            })
        return results

    def forget(self, memory_id: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# LanceDbProvider — Phase 1 stub (NOT IMPLEMENTED — gated on A-5/A-6)
# ---------------------------------------------------------------------------

class LanceDbProvider(MemoryProvider):
    """Phase 1 stub: LanceDB + sentence-transformers embeddings.

    NOT IMPLEMENTED per ADR-001 (specs/agent-fw.md \u00a710.8).
    The flat-file backend is the permanent production choice until ALL of the
    upgrade trigger criteria in \u00a73.7.7 are simultaneously met.

    Instantiating this class always raises ``NotImplementedError``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(
            "LanceDbProvider is not active. ADR-001 (specs/agent-fw.md \u00a710.8) "
            "designates FlatFileProvider as the permanent backend. "
            "See \u00a73.7.7 for upgrade trigger criteria."
        )

    def remember(self, scope: str, text: str, metadata: dict[str, Any]) -> str:  # pragma: no cover
        raise NotImplementedError

    def recall(self, scope: str, query: str, limit: int = 5) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def forget(self, memory_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider(artha_dir: Path | None = None) -> MemoryProvider:
    """Return the configured memory provider.

    Reads ``memory_provider`` from ``config/artha_config.yaml``.
    Validates ``config/memory.yaml`` schema_version before constructing.

    Returns:
        A concrete ``MemoryProvider`` instance.

    Raises:
        SchemaVersionMissing: If ``config/memory.yaml`` is missing
            ``schema_version``.
        ValueError: If ``memory_provider`` value is unknown.
    """
    root = artha_dir or Path(__file__).resolve().parents[2]

    # Validate memory.yaml schema_version
    try:
        import yaml  # noqa: PLC0415 — optional; fall back below
        with open(root / "config" / "memory.yaml", encoding="utf-8") as fh:
            memory_cfg = yaml.safe_load(fh) or {}
    except ImportError:
        # yaml not available — read raw and check for key
        raw = (root / "config" / "memory.yaml").read_text(encoding="utf-8")
        memory_cfg = {"schema_version": 1} if "schema_version:" in raw else {}
    except FileNotFoundError:
        memory_cfg = {}

    _require_schema_version(memory_cfg)

    # Read provider from artha_config.yaml
    provider_name = "flat_file"
    try:
        import sys  # noqa: PLC0415
        scripts = str(root / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from lib.config_loader import load_config  # noqa: PLC0415
        cfg = load_config("artha_config", str(root / "config"))
        provider_name = cfg.get("memory_provider", "flat_file")
    except Exception:  # noqa: BLE001
        pass

    if provider_name == "flat_file":
        return FlatFileProvider(artha_dir=root)
    if provider_name == "sqlite_fts":
        return SqliteFtsProvider(artha_dir=root)
    if provider_name == "lancedb":
        return LanceDbProvider()  # Will raise NotImplementedError (gated)
    raise ValueError(
        f"Unknown memory_provider '{provider_name}'. "
        "Allowed values: flat_file | sqlite_fts | lancedb"
    )
