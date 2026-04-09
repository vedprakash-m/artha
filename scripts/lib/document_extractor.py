"""Document extraction utilities — shared by all Artha ingestion paths.

Provides:
  DocumentExtractor
    .from_file(path, domain, episode_key) → DocumentExtractor
    .from_text(text, source_ref, domain, episode_key) → DocumentExtractor
    .entities  : list[dict]
    .relationships : list[dict]
    .run()     : trigger extraction (no-op if already run)

  write_markdown_stub(stub_path, *, title, source_ref, source_type,
                      content_hash, domain, extracted_text,
                      entities, script_name) → None

This module is a thin facade over the existing FileExtractor in kb_bootstrap.
Both constructors fill the same .entities / .relationships contract so all
consumer code (kb_bootstrap bootstrap(), connector ingestion paths) can use
a single call pattern.

Phase 0.5 contract: zero behavior change for existing bootstrap() path.
The full code migration (moving FileExtractor itself here) is deferred to
Phase 1 once stub rebuild parity is validated.
"""

from __future__ import annotations

import hashlib
import importlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Lazy import of FileExtractor from kb_bootstrap (avoids circular deps)
# ---------------------------------------------------------------------------

def _import_file_extractor():
    """Import FileExtractor from kb_bootstrap with proper sys.path setup."""
    scripts_dir = Path(__file__).resolve().parent.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    kb = importlib.import_module("kb_bootstrap")
    return kb.FileExtractor


# ---------------------------------------------------------------------------
# Shared extraction constants (mirrored from kb_bootstrap)
# ---------------------------------------------------------------------------

_HEADER_TYPE_MAP: dict[str, str] = {
    "milestone":    "milestone",
    "component":    "component",
    "principle":    "principle",
    "decision":     "decision",
    "architecture": "system",
    "overview":     "system",
    "people":       "person",
    "person":       "person",
    "team":         "team",
    "project":      "program",
    "platform":     "system",
    "service":      "service",
}

_STATUS_DONE   = frozenset({"✅", "complete", "completed", "done", "go-live", "shipped"})
_STATUS_WIP    = frozenset({"🟡", "in progress", "in-progress", "planning", "active", "ongoing"})
_STATUS_PAUSED = frozenset({"⏸", "paused", "blocked", "on hold"})

_MAX_ENTITIES   = 100
_EXCERPT_WORDS  = 40


# ---------------------------------------------------------------------------
# Internal text extractor (used by from_text())
# ---------------------------------------------------------------------------

class _TextExtractor:
    """Stateless extraction from raw markdown text."""

    def __init__(self, text: str, source_ref: str, domain: str, episode_key: str) -> None:
        self._text = text
        self.source_ref = source_ref
        self.domain = domain
        self.episode_key = episode_key
        self.entities: list[dict] = []
        self.relationships: list[dict] = []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _slug(self, name: str) -> str:
        s = name.lower().strip()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        s = s.strip("-")
        return f"{self.domain}-{s}" if s else f"{self.domain}-entity"

    def _infer_type(self, label: str) -> str:
        lower = label.lower()
        for kw, etype in _HEADER_TYPE_MAP.items():
            if kw in lower:
                return etype
        return "concept"

    @staticmethod
    def _infer_status(fragment: str) -> str | None:
        lf = fragment.lower()
        if any(k in lf for k in _STATUS_DONE):
            return "active"
        if any(k in lf for k in _STATUS_WIP):
            return "in_progress"
        if any(k in lf for k in _STATUS_PAUSED):
            return "on_hold"
        return None

    @staticmethod
    def _excerpt(text: str, max_words: int = _EXCERPT_WORDS) -> str:
        words = text.split()
        return " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")

    # ------------------------------------------------------------------
    # Entity extraction from markdown headings + bullets
    # ------------------------------------------------------------------

    def run(self) -> None:
        lines = self._text.splitlines()
        self._extract_entities(lines)
        self._extract_relationships(lines)

    def _extract_entities(self, lines: list[str]) -> None:
        seen: set[str] = set()
        primary_id: str | None = None
        h1_summary = ""
        h1_seen = False
        h2_summary: dict[str, str] = {}
        current_h2 = ""

        for ln in lines:
            stripped = ln.strip()

            # H1 → primary entity
            if stripped.startswith("# ") and not h1_seen:
                h1_seen = True
                label = stripped[2:].strip()
                if not label:
                    continue
                slug = self._slug(label)
                primary_id = slug
                seen.add(slug)
                status = self._infer_status(stripped)
                entity: dict = {
                    "id":        slug,
                    "name":      label,
                    "type":      self._infer_type(label),
                    "domain":    self.domain,
                    "source":    self.source_ref,
                    "confidence": 0.80,
                }
                if status:
                    entity["current_state"] = status
                self.entities.append(entity)
                continue

            # H2 → section entities
            h2_match = re.match(r"^## (.+)$", stripped)
            if h2_match:
                current_h2 = h2_match.group(1).strip()
                label = current_h2
                if not label or label.lower() in {"overview", "summary", "notes",
                                                    "references", "see also",
                                                    "extracted content",
                                                    "entities extracted"}:
                    continue
                if len(self.entities) >= _MAX_ENTITIES:
                    break
                slug = self._slug(label)
                if slug in seen:
                    continue
                seen.add(slug)
                entity = {
                    "id":        slug,
                    "name":      label,
                    "type":      self._infer_type(label),
                    "domain":    self.domain,
                    "source":    self.source_ref,
                    "confidence": 0.70,
                }
                self.entities.append(entity)
                if primary_id and slug != primary_id:
                    self.relationships.append({
                        "from_entity": primary_id,
                        "to_entity":   slug,
                        "rel_type":    "component_of",
                        "confidence":  0.60,
                        "source":      self.source_ref,
                    })
                continue

            # Bold key: value → attribute entity
            kv_match = re.match(r"^\*\*([^*]+)\*\*[:\s]+(.{10,})", stripped)
            if kv_match and primary_id:
                key   = kv_match.group(1).strip()
                value = kv_match.group(2).strip()
                if len(self.entities) >= _MAX_ENTITIES:
                    break
                slug = self._slug(key)
                if slug in seen:
                    continue
                seen.add(slug)
                entity = {
                    "id":          slug,
                    "name":        key,
                    "type":        "concept",
                    "domain":      self.domain,
                    "current_state": self._excerpt(value, 20),
                    "source":      self.source_ref,
                    "confidence":  0.65,
                }
                self.entities.append(entity)

    def _extract_relationships(self, lines: list[str]) -> None:
        """Extract explicit relationship markers from markdown bullets."""
        _DEPENDS_PAT  = re.compile(r"depends[_ ]on\s+(.+)", re.I)
        _OWNS_PAT     = re.compile(r"owned?[_ ]by\s+(.+)", re.I)
        _RELATED_PAT  = re.compile(r"related[_ ]to\s+(.+)", re.I)

        for ln in lines:
            stripped = ln.strip()
            for pattern, rel_type in [
                (_DEPENDS_PAT, "depends_on"),
                (_OWNS_PAT,    "owned_by"),
                (_RELATED_PAT, "related_to"),
            ]:
                m = pattern.search(stripped)
                if m and self.entities:
                    target_name = m.group(1).strip().strip("*").strip()
                    target_slug = self._slug(target_name)
                    from_id = self.entities[0]["id"]
                    self.relationships.append({
                        "from_entity": from_id,
                        "to_entity":   target_slug,
                        "rel_type":    rel_type,
                        "confidence":  0.55,
                        "source":      self.source_ref,
                    })
                    break


# ---------------------------------------------------------------------------
# Public DocumentExtractor facade
# ---------------------------------------------------------------------------

class DocumentExtractor:
    """Unified extraction facade for both file-based and text-based ingestion.

    Usage::

        # Existing kb_bootstrap path (zero behavior change):
        ex = DocumentExtractor.from_file(path, domain="work", episode_key="...")
        ex.run()
        for entity in ex.entities: ...

        # New SP/inbox path:
        ex = DocumentExtractor.from_text(html_text, source_ref=url, domain="work", episode_key="...")
        ex.run()
        for entity in ex.entities: ...
    """

    def __init__(self) -> None:
        self.entities: list[dict] = []
        self.relationships: list[dict] = []
        self._delegate: object | None = None
        self._ran = False

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: Path, domain: str, episode_key: str) -> "DocumentExtractor":
        """Create extractor backed by FileExtractor (existing bootstrap behavior)."""
        self = cls()
        FileExtractor = _import_file_extractor()
        fe = FileExtractor(path, domain, episode_key)
        self._delegate = fe
        return self

    @classmethod
    def from_text(
        cls,
        text: str,
        source_ref: str,
        domain: str,
        episode_key: str,
    ) -> "DocumentExtractor":
        """Create extractor from raw text (for SharePoint / inbox content)."""
        self = cls()
        self._delegate = _TextExtractor(text, source_ref, domain, episode_key)
        return self

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        if self._ran:
            return
        if self._delegate is None:
            return
        self._delegate.run()
        self.entities      = list(self._delegate.entities)
        self.relationships = list(self._delegate.relationships)
        self._ran = True


# ---------------------------------------------------------------------------
# Markdown stub writer  (§4.13 Markdown Stub Contract)
# ---------------------------------------------------------------------------

def write_markdown_stub(
    stub_path: Path,
    *,
    title: str,
    source_ref: str,
    source_type: str,
    content_hash: str,
    domain: str,
    extracted_text: str,
    entities: list[dict],
    script_name: str = "document_extractor",
    overwrite: bool = False,
) -> Path:
    """Write a §4.13 canonical markdown stub atomically.

    Returns the written path. Skips if file exists and ``overwrite=False``.
    """
    if stub_path.exists() and not overwrite:
        return stub_path

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    entity_lines: list[str] = []
    for e in entities:
        name     = e.get("name", e.get("id", "unknown"))
        etype    = e.get("type", "concept")
        stage    = e.get("lifecycle_stage", e.get("current_state", "unknown"))
        summary  = e.get("current_state", e.get("summary", ""))
        entity_lines.append(f"- **{name}** ({etype}, {stage}) — {summary}")

    entities_section = "\n".join(entity_lines) if entity_lines else "_None extracted._"

    stub = (
        f"<!-- Auto-generated by {script_name} | {now} | DO NOT EDIT -->\n"
        f"<!-- Source: {source_ref} -->\n"
        f"<!-- Source Type: {source_type} -->\n"
        f"<!-- Content Hash: {content_hash} -->\n"
        f"\n"
        f"# {title}\n"
        f"\n"
        f"**Source:** {source_ref}\n"
        f"**Ingested:** {now}\n"
        f"**Domain:** {domain}\n"
        f"\n"
        f"## Extracted Content\n"
        f"\n"
        f"{extracted_text.strip()}\n"
        f"\n"
        f"## Entities Extracted\n"
        f"\n"
        f"{entities_section}\n"
    )

    stub_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = stub_path.with_suffix(".tmp")
    try:
        tmp.write_text(stub, encoding="utf-8")
        tmp.replace(stub_path)
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise

    return stub_path


# ---------------------------------------------------------------------------
# Convenience: compute stub filename for each connector type
# ---------------------------------------------------------------------------

def stub_filename(
    *,
    source_type: str,
    doc_name: str = "",
    content_hash: str = "",
    item_id: str = "",
    date: str = "",
) -> str:
    """Return a deterministic stub filename for the given source_type."""
    safe = re.sub(r"[^a-z0-9\-]+", "-", doc_name.lower()).strip("-")[:60]
    trunc = content_hash[:12] if content_hash else "nohash"

    if source_type == "sharepoint":
        return f"{safe}-{trunc}.md"
    if source_type == "inbox":
        return f"{safe}-{trunc}.md"
    if source_type == "ado_sync":
        return f"{item_id or safe}.md"
    if source_type == "meeting":
        return f"{date or 'unknown'}-{safe}.md"
    return f"{safe}-{trunc}.md"
