#!/usr/bin/env python3
"""
scripts/kb_bootstrap.py — Artha KB Bootstrap

Reads all *.md files from knowledge/ and ingests them into the KB graph
using heuristic extraction (no LLM dependency for v1.0).

Entity extraction strategy:
  - Markdown H2/H3 headers → candidate entity names
  - Lines with "Status:" → current_state for nearest entity
  - Lines matching "^- **<name>**:" → component entities with summary
  - Ownership / people lines for relationship hints
  - Bold key/value pairs (e.g. **Owner:** John) → metadata

This is Phase v1.0 bootstrap — deterministic, offline, no LLM required.
Phase v1.1 will add LLM-assisted extraction with a human review loop.

Usage:
    python scripts/kb_bootstrap.py [--dry-run] [--verbose] [--file <path>]
    python scripts/kb_bootstrap.py --status   # show current KB stats
    python scripts/kb_bootstrap.py --help

Ref: specs/kb-graph-design.md §7
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_SCRIPTS = Path(__file__).resolve().parent
_ARTHA   = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.knowledge_graph import (  # noqa: E402
    KnowledgeGraph,
    get_kb,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_KNOWLEDGE_DIR = _ARTHA / "knowledge"

# Relationship types we can infer from prose
_REL_OWNED_BY    = "owned_by"
_REL_DEPENDS_ON  = "depends_on"
_REL_COMPONENT   = "component_of"
_REL_RELATED_TO  = "related_to"

# Entity types we can infer from headers
_HEADER_TYPE_MAP = {
    "milestone":   "milestone",
    "component":   "component",
    "principle":   "principle",
    "decision":    "decision",
    "architecture": "system",
    "overview":    "system",
    "people":      "person",
    "person":      "person",
    "team":        "team",
    "project":     "program",
    "platform":    "system",
    "service":     "service",
}

# Status keywords that map to current_state values
_STATUS_DONE   = {"✅", "complete", "completed", "done", "go-live", "shipped"}
_STATUS_WIP    = {"🟡", "in progress", "in-progress", "planning", "active", "ongoing"}
_STATUS_PAUSED = {"⏸", "paused", "blocked", "on hold"}

# Domain inference: filename prefix → domain
_DOMAIN_BY_PREFIX = {
    "armada":     "infrastructure",
    "dd":         "deployment",
    "xpf":        "fleet",
    "rubik":      "sku",
    "titan":      "platform",
    "sku":        "sku",
    "decision":   "engineering",
    "meeting":    "engineering",
    "people":     "org",
    "process":    "operations",
    "tool":       "tooling",
    "kb":         "knowledge",
}

# Max entities extracted per file (guard against huge files)
_MAX_ENTITIES_PER_FILE = 100


# ---------------------------------------------------------------------------
# Heuristic extraction
# ---------------------------------------------------------------------------

class FileExtractor:
    """Extract entities and relationships from a KB markdown file."""

    def __init__(self, path: Path, domain: str, episode_key: str) -> None:
        self.path = path
        self.domain = domain
        self.episode_key = episode_key
        self.entities: list[dict]      = []
        self.relationships: list[dict] = []
        self._primary_entity_id: str | None = None

    def run(self) -> None:
        text = self.path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        self._extract_entities(lines)
        self._extract_relationships(lines)

    def _infer_type(self, label: str) -> str:
        lower = label.lower()
        for kw, etype in _HEADER_TYPE_MAP.items():
            if kw in lower:
                return etype
        return "concept"

    def _slug(self, name: str) -> str:
        """Generate a stable ID from a name."""
        s = name.lower().strip()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        s = s.strip("-")
        return f"{self.domain}-{s}" if s else f"{self.domain}-entity"

    def _infer_status(self, text_fragment: str) -> str | None:
        lf = text_fragment.lower()
        for kw in _STATUS_DONE:
            if kw in lf:
                return "active"
        for kw in _STATUS_WIP:
            if kw in lf:
                return "in_progress"
        for kw in _STATUS_PAUSED:
            if kw in lf:
                return "blocked"
        return None

    def _extract_entities(self, lines: list[str]) -> None:
        """
        Extract entities from:
          - H1/H2/H3 headings
          - Table rows "| **Name** | ... |"
          - Lines "- **ComponentName** — short desc"
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        extracted: list[dict] = []
        file_stem = self.path.stem.lower()
        primary_name = self.path.stem.replace("-kb", "").replace("-", " ").title()
        primary_id = self._slug(primary_name)
        self._primary_entity_id = primary_id

        # Primary entity = the KB file itself
        summary_match = re.search(r"^>\s+\*\*Purpose:\*\*\s+(.+)", "\n".join(lines[:20]), re.MULTILINE)
        summary = summary_match.group(1).strip() if summary_match else None

        primary = {
            "id":                  primary_id,
            "name":                primary_name,
            "type":                "system",
            "domain":              self.domain,
            "summary":             summary,
            "source":              f"kb_file:{self.path.name}",
            "confidence":          0.80,
        }
        extracted.append(primary)

        # Track current section for context
        current_section = primary_name
        in_component_table = False

        for line in lines:
            if len(extracted) >= _MAX_ENTITIES_PER_FILE:
                break

            # H2/H3 headings → sub-entities
            h2 = re.match(r"^#{2,3}\s+(.+)", line)
            if h2:
                label = h2.group(1).strip()
                # Strip markdown bold/italic/code
                label = re.sub(r"\*+|`|#", "", label).strip()
                if not label or label.startswith("<!--"):
                    continue
                current_section = label
                # Skip generic structural headings
                if any(kw in label.lower() for kw in ("overview", "introduction", "table of content", "ref", "todo")):
                    continue
                etype = self._infer_type(label)
                entity_id = self._slug(label)
                current_state = self._infer_status(line)
                e = {
                    "id":           entity_id,
                    "name":         label,
                    "type":         etype,
                    "domain":       self.domain,
                    "source":       f"kb_file:{self.path.name}",
                    "confidence":   0.75,
                }
                if current_state:
                    e["current_state"] = current_state
                extracted.append(e)
                continue

            # Bullet component lines: "- **ComponentName** — short desc"
            bullet = re.match(r"^\s*[-*]\s+\*\*([^*]+)\*\*\s*[—–-]\s*(.+)", line)
            if bullet:
                bname  = bullet.group(1).strip()
                bdesc  = bullet.group(2).strip()[:200]
                bid    = self._slug(bname)
                if len(bname) > 3 and bid not in {e["id"] for e in extracted}:
                    extracted.append({
                        "id":       bid,
                        "name":     bname,
                        "type":     "component",
                        "domain":   self.domain,
                        "summary":  bdesc,
                        "source":   f"kb_file:{self.path.name}",
                        "confidence": 0.70,
                    })
                continue

            # Table rows: "| **Name** | Purpose |"
            table = re.match(r"^\|\s*\*\*([^*|]+)\*\*\s*\|(.+)", line)
            if table:
                tname = table.group(1).strip()
                tdesc = re.sub(r"\*+|`|\|", "", table.group(2)).strip()[:200]
                tid   = self._slug(tname)
                if len(tname) > 2 and tid not in {e["id"] for e in extracted}:
                    extracted.append({
                        "id":       tid,
                        "name":     tname,
                        "type":     "component",
                        "domain":   self.domain,
                        "summary":  tdesc or None,
                        "source":   f"kb_file:{self.path.name}",
                        "confidence": 0.65,
                    })
                continue

            # Status lines inside sections
            status_m = re.match(r"^\s*[-*]?\s*\*\*Status:\*\*\s*(.+)", line)
            if status_m and current_section != primary_name:
                state_text = status_m.group(1).strip()
                inferred = self._infer_status(state_text)
                sec_id = self._slug(current_section)
                for e in extracted:
                    if e["id"] == sec_id:
                        e["current_state"] = inferred or state_text[:100]
                        break

        self.entities = extracted

    def _extract_relationships(self, lines: list[str]) -> None:
        """Extract simple relationships from ownership/dependency patterns."""
        rels: list[dict] = []
        if not self._primary_entity_id:
            return

        entity_ids = {e["id"] for e in self.entities}
        primary    = self._primary_entity_id
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Connect all sub-entities to the primary via component_of
        for e in self.entities:
            if e["id"] == primary:
                continue
            if e["type"] in ("component", "concept", "milestone", "system", "service"):
                rels.append({
                    "from_entity": e["id"],
                    "to_entity":   primary,
                    "rel_type":    _REL_COMPONENT,
                    "confidence":  0.70,
                    "source":      f"kb_file:{self.path.name}",
                })

        # Depends on patterns: "requires X", "built on X", "uses X"
        dep_pattern = re.compile(
            r"(?:requires?|depends? on|built on|uses?)\s+\*?\*?([A-Z][A-Za-z0-9]+(?:\s[A-Za-z0-9]+)?)\*?\*?",
            re.IGNORECASE,
        )
        text = "\n".join(lines)
        for m in dep_pattern.finditer(text):
            dep_name = m.group(1).strip()
            dep_id   = self._slug(dep_name)
            if dep_id != primary and dep_id in entity_ids:
                rels.append({
                    "from_entity": primary,
                    "to_entity":   dep_id,
                    "rel_type":    _REL_DEPENDS_ON,
                    "confidence":  0.60,
                    "source":      f"kb_file:{self.path.name}",
                })

        self.relationships = rels


# ---------------------------------------------------------------------------
# Domain inference
# ---------------------------------------------------------------------------

def _infer_domain(file_stem: str) -> str:
    stem = file_stem.lower()
    for prefix, domain in _DOMAIN_BY_PREFIX.items():
        if stem.startswith(prefix):
            return domain
    return "engineering"


# ---------------------------------------------------------------------------
# Staleness TTL per source type (days)
# ---------------------------------------------------------------------------
_STALENESS_TTL: dict[str, int] = {
    "golden_queries": 7, "kusto_catalog": 30, "people": 30,
    "decisions": 365, "projects": 14, "products": 60,
    "tools": 90, "meetings": 30, "accomplishments": 365,
    "deep_dives": 14, "milestones": 365, "program_structure": 30,
    "program_brain": 60, "scope": 30, "project_journeys": 365,
}


def _safe_run(extractor: object, label: str, verbose: bool = False) -> bool:
    """Run an extractor's run() method, catching all errors."""
    try:
        extractor.run()  # type: ignore[attr-defined]
        return True
    except Exception as exc:
        _log.error("Extractor %s failed: %s", label, exc)
        if verbose:
            import traceback
            traceback.print_exc()
        return False


def _strip_md_formatting(text: str) -> str:
    """Remove bold, italic, backticks, and leading/trailing pipes."""
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"`", "", text)
    return text.strip().strip("|").strip()


def _parse_yaml_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from markdown text, returning dict."""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    result: dict = {}
    for line in m.group(1).splitlines():
        kv = re.match(r'^(\w[\w_-]*)\s*:\s*(.+)', line)
        if kv:
            key = kv.group(1).strip()
            val = kv.group(2).strip().strip('"').strip("'")
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# Phase-1 Structured Extractors
# ---------------------------------------------------------------------------

class GoldenQueryExtractor:
    """Extract golden queries from state/work/golden-queries.md.

    Delegates to kusto_runner.parse_registry() when available; falls back
    to manual regex parsing otherwise.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("GoldenQueryExtractor: %s not found", self.path)
            return

        # Try using kusto_runner's parser first
        parsed = self._try_kusto_runner()
        if parsed is not None:
            self.rows = parsed
            return

        # Fallback: manual parsing
        self.rows = self._manual_parse()

    def _try_kusto_runner(self) -> list[dict] | None:
        try:
            from kusto_runner import parse_registry  # noqa: E402
            registry = parse_registry(self.path)
            rows: list[dict] = []
            for qid, gq in registry.items():
                rows.append({
                    "id": qid,
                    "name": gq.title,
                    "type": "golden_query",
                    "domain": "kusto",
                    "category": gq.category,
                    "question": gq.question,
                    "cluster": gq.cluster,
                    "database": gq.database,
                    "table": gq.table,
                    "kql": gq.kql,
                    "freshness": gq.freshness_description,
                    "confidence": gq.confidence,
                    "validated_date": gq.validated_date,
                    "last_known": gq.last_known,
                    "caveats": gq.caveats,
                    "source": f"file:{self.path.name}",
                })
            return rows
        except Exception as exc:
            _log.debug("kusto_runner import failed, falling back to manual: %s", exc)
            return None

    def _manual_parse(self) -> list[dict]:
        text = self.path.read_text(encoding="utf-8", errors="replace")
        rows: list[dict] = []
        sections = re.split(r"(?=^### GQ-)", text, flags=re.MULTILINE)
        for section in sections:
            m = re.match(r"^### (GQ-\d+):\s*(.+)", section)
            if not m:
                continue
            qid = m.group(1)
            title = m.group(2).strip()

            def _field(name: str) -> str:
                pat = rf"\*\*{re.escape(name)}\*\*\s*\|\s*(.+)"
                fm = re.search(pat, section)
                if not fm:
                    return ""
                return fm.group(1).strip().rstrip("|").strip()

            kql_m = re.search(r"```kql\s*\n(.+?)```", section, re.DOTALL)
            kql = kql_m.group(1).strip() if kql_m else ""

            caveats: list[str] = []
            for cm in re.finditer(r"[⚠️⛔]\s*(.+)", section):
                caveats.append(cm.group(1).strip())

            conf_raw = _field("Confidence")
            confidence = "MEDIUM"
            if "HIGH" in conf_raw:
                confidence = "HIGH"
            elif "LOW" in conf_raw:
                confidence = "LOW"

            rows.append({
                "id": qid,
                "name": title,
                "type": "golden_query",
                "domain": "kusto",
                "category": _field("Category"),
                "question": _field("Question"),
                "cluster": _field("Cluster").strip("`"),
                "database": _field("Database").strip("`"),
                "table": _field("Table").strip("`"),
                "kql": kql,
                "freshness": _field("Freshness"),
                "confidence": confidence,
                "validated_date": _field("Validated"),
                "last_known": _field("Last Known"),
                "caveats": caveats,
                "source": f"file:{self.path.name}",
            })
        return rows


class KustoCatalogExtractor:
    """Extract Kusto cluster/database/table catalog from golden queries.

    This produces deduplicated infrastructure entities from the cluster,
    database, and table fields already parsed by GoldenQueryExtractor.
    """

    def __init__(self, golden_rows: list[dict]) -> None:
        self.golden_rows = golden_rows
        self.rows: list[dict] = []

    def run(self) -> None:
        seen: set[str] = set()
        for gq in self.golden_rows:
            cluster = gq.get("cluster", "")
            database = gq.get("database", "")
            table = gq.get("table", "")

            if cluster and cluster not in seen:
                seen.add(cluster)
                short = cluster.split("//")[-1].split(".")[0] if "//" in cluster else cluster
                self.rows.append({
                    "id": f"kusto-cluster-{short}",
                    "name": short,
                    "type": "kusto_cluster",
                    "domain": "infrastructure",
                    "cluster_uri": cluster,
                    "source": "golden_queries",
                })

            if database:
                db_key = f"{cluster}:{database}"
                if db_key not in seen:
                    seen.add(db_key)
                    self.rows.append({
                        "id": f"kusto-db-{database}",
                        "name": database,
                        "type": "kusto_database",
                        "domain": "infrastructure",
                        "cluster_uri": cluster,
                        "source": "golden_queries",
                    })

            if table:
                tbl_key = f"{cluster}:{database}:{table}"
                if tbl_key not in seen:
                    seen.add(tbl_key)
                    self.rows.append({
                        "id": f"kusto-table-{database}-{table}",
                        "name": table,
                        "type": "kusto_table",
                        "domain": "infrastructure",
                        "database": database,
                        "cluster_uri": cluster,
                        "source": "golden_queries",
                    })


class PeopleExtractor:
    """Extract people from state/work/work-people.md org tree."""

    # Annotations to strip from names
    _ANNOTATIONS = re.compile(
        r"\s*[─—–]+\s*(?:DIRECT MANAGER|YOU ARE HERE|ENG LEADERSHIP|"
        r"EXECUTIVE SPONSOR|PM LEADERSHIP|DIRECT TO CVP|PROGRAM PM)\s*$"
    )

    # Box-drawing tree line: captures indent, name, and optional role
    _TREE_LINE = re.compile(
        r"^[\s│├└─┌┐┘┤┬┴┼\|]*[├└]\s*[─]+\s*(.+)"
    )

    # Simpler fallback: "Name — Title"
    _NAME_ROLE = re.compile(r"^(.+?)\s*[—–-]+\s*(.+)$")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("PeopleExtractor: %s not found", self.path)
            return

        text = self.path.read_text(encoding="utf-8", errors="replace")
        seen: set[str] = set()
        in_code_block = False

        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if not in_code_block:
                continue

            # Clean annotation suffixes
            cleaned = self._ANNOTATIONS.sub("", stripped)
            # Find name — title patterns in the cleaned line
            # Match lines that have "Name — Title" (could be at any indent with tree chars)
            # Strip leading tree-drawing chars
            content = re.sub(r"^[\s│├└─┌┐┘┤┬┴┼\|]+", "", cleaned).strip()
            if not content:
                continue

            m = self._NAME_ROLE.match(content)
            if m:
                name = m.group(1).strip()
                role = m.group(2).strip()
            else:
                # Might be just a name like "Satya Nadella — CEO"
                # or an org label like "(Fungible R&D chain)"
                if content.startswith("(") or len(content) < 3:
                    continue
                name = content
                role = ""

            # Skip non-person patterns
            if name.startswith("(") or name.startswith("#"):
                continue

            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if not slug or slug in seen:
                continue
            seen.add(slug)

            annotation = ""
            if "YOU ARE HERE" in stripped:
                annotation = "self"
            elif "DIRECT MANAGER" in stripped:
                annotation = "direct_manager"
            elif "EXECUTIVE SPONSOR" in stripped:
                annotation = "executive_sponsor"

            row: dict = {
                "id": f"person-{slug}",
                "name": name,
                "type": "person",
                "domain": "org",
                "role": role,
                "source": f"file:{self.path.name}",
            }
            if annotation:
                row["annotation"] = annotation
            self.rows.append(row)


class DecisionExtractor:
    """Extract decisions from state/work/work-decisions.md."""

    _WEEK_HEADER = re.compile(r"^###\s*\[(\d{4}/\d{2}-w\d+)\]", re.MULTILINE)
    _TITLE_LINE = re.compile(r"^\s*-\s*\*\*([^*]+)\*\*\s*[:：]\s*(.+)", re.MULTILINE)

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("DecisionExtractor: %s not found", self.path)
            return

        text = self.path.read_text(encoding="utf-8", errors="replace")
        seen: set[str] = set()

        # Split into sections by week header
        parts = self._WEEK_HEADER.split(text)
        # parts alternates: [preamble, week1, content1, week2, content2, ...]
        idx = 1
        counter = 0
        while idx < len(parts) - 1:
            week_tag = parts[idx].strip()
            content = parts[idx + 1]
            idx += 2

            for m in self._TITLE_LINE.finditer(content):
                title = m.group(1).strip()
                rationale = m.group(2).strip()[:500]
                counter += 1
                decision_id = f"decision-{counter:03d}"

                dedup_key = f"{week_tag}:{title[:60]}"
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                self.rows.append({
                    "id": decision_id,
                    "name": title,
                    "type": "decision",
                    "domain": "work",
                    "week": week_tag,
                    "rationale": rationale,
                    "source": f"file:{self.path.name}",
                })


class AccomplishmentExtractor:
    """Extract accomplishments from state/work/work-accomplishments.md."""

    _WEEK_HEADER = re.compile(r"^##\s+(20\d{2}-W\d+)\s*[—–-]\s*(.+)", re.MULTILINE)
    _TABLE_ROW = re.compile(
        r"^\|\s*(A-\d+)\s*\|\s*(.+?)\s*\|\s*(HIGH|MEDIUM|LOW)\s*\|\s*(.+?)\s*\|\s*(DONE|OPEN|DEFERRED)\s*\|\s*(.+?)\s*\|",
        re.MULTILINE,
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("AccomplishmentExtractor: %s not found", self.path)
            return

        text = self.path.read_text(encoding="utf-8", errors="replace")
        # Split by week headers
        parts = self._WEEK_HEADER.split(text)
        seen: set[str] = set()
        idx = 1
        while idx < len(parts) - 2:
            week_id = parts[idx].strip()
            week_label = parts[idx + 1].strip()
            content = parts[idx + 2]
            idx += 3

            for m in self._TABLE_ROW.finditer(content):
                aid = m.group(1).strip()
                title = m.group(2).strip()
                impact = m.group(3).strip()
                program = m.group(4).strip()
                status = m.group(5).strip()
                evidence = m.group(6).strip()

                dedup_key = aid
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                self.rows.append({
                    "id": f"accomplishment-{aid}",
                    "name": title[:200],
                    "type": "accomplishment",
                    "domain": "work",
                    "week": week_id,
                    "week_label": week_label,
                    "impact": impact,
                    "program": program,
                    "status": status,
                    "evidence": evidence[:200],
                    "source": f"file:{self.path.name}",
                })


class ProjectExtractor:
    """Extract projects from state/work/work-projects.md."""

    _PROJECT_HEADER = re.compile(r"^##\s+(.+)", re.MULTILINE)
    _STATUS_LINE = re.compile(r"^\s*-\s*Status:\s*\*\*(\w+)\*\*\s*[—–-]*\s*(.*)", re.MULTILINE)
    _SUBWS_ROW = re.compile(
        r"^\|\s*\*\*([^*|]+)\*\*\s*\|\s*(.+?)\s*\|\s*(\w[\w\s—–-]*?)\s*\|\s*(.+?)\s*\|",
        re.MULTILINE,
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []
        self.relationships: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("ProjectExtractor: %s not found", self.path)
            return

        text = self.path.read_text(encoding="utf-8", errors="replace")
        sections = re.split(r"(?=^## )", text, flags=re.MULTILINE)
        seen: set[str] = set()

        for section in sections:
            hdr = self._PROJECT_HEADER.match(section)
            if not hdr:
                continue
            name = hdr.group(1).strip()
            if any(kw in name.lower() for kw in ("legend", "note", "---")):
                continue

            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            proj_id = f"project-{slug}"
            if proj_id in seen:
                continue
            seen.add(proj_id)

            status_m = self._STATUS_LINE.search(section)
            status = status_m.group(1).strip() if status_m else ""
            status_detail = status_m.group(2).strip()[:300] if status_m else ""

            self.rows.append({
                "id": proj_id,
                "name": name,
                "type": "project",
                "domain": "work",
                "status": status,
                "status_detail": _strip_md_formatting(status_detail),
                "source": f"file:{self.path.name}",
            })

            # Sub-workstream table rows
            for sm in self._SUBWS_ROW.finditer(section):
                sw_name = sm.group(1).strip()
                owner = _strip_md_formatting(sm.group(2))
                sw_status = _strip_md_formatting(sm.group(3))
                facts = _strip_md_formatting(sm.group(4))[:300]

                sw_slug = re.sub(r"[^a-z0-9]+", "-", sw_name.lower()).strip("-")
                sw_id = f"workstream-{sw_slug}"
                if sw_id in seen:
                    continue
                seen.add(sw_id)

                self.rows.append({
                    "id": sw_id,
                    "name": sw_name,
                    "type": "workstream",
                    "domain": "work",
                    "owner": owner,
                    "status": sw_status,
                    "key_facts": facts,
                    "source": f"file:{self.path.name}",
                })
                self.relationships.append({
                    "from_entity": sw_id,
                    "to_entity": proj_id,
                    "rel_type": _REL_COMPONENT,
                    "confidence": 0.90,
                    "source": f"file:{self.path.name}",
                })


class ScopeExtractor:
    """Extract ownership scope areas from state/work/work-scope.md."""

    _AREA_HEADER = re.compile(r"^###\s*\d+\.\s+(.+)", re.MULTILINE)
    _FIELD_LINE = re.compile(r"^\s*-\s*\*\*(\w[\w\s]*)\*\*\s*:\s*(.+)", re.MULTILINE)

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("ScopeExtractor: %s not found", self.path)
            return

        text = self.path.read_text(encoding="utf-8", errors="replace")
        sections = re.split(r"(?=^### \d+\.)", text, flags=re.MULTILINE)
        seen: set[str] = set()

        for section in sections:
            hdr = self._AREA_HEADER.match(section)
            if not hdr:
                continue
            name = hdr.group(1).strip()

            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            area_id = f"scope-{slug}"
            if area_id in seen:
                continue
            seen.add(area_id)

            fields: dict[str, str] = {}
            for fm in self._FIELD_LINE.finditer(section):
                key = fm.group(1).strip().lower()
                val = fm.group(2).strip()[:300]
                fields[key] = _strip_md_formatting(val)

            self.rows.append({
                "id": area_id,
                "name": name,
                "type": "scope_area",
                "domain": "work",
                "role": fields.get("role", ""),
                "co_owners": fields.get("co-owners", fields.get("co-owner", "")),
                "status": fields.get("status", ""),
                "next_action": fields.get("next action", ""),
                "lt_visibility": fields.get("lt visibility", ""),
                "source": f"file:{self.path.name}",
            })


class ProductExtractor:
    """Extract product architecture from state/work/products/*.md files."""

    _COMPONENT_ROW = re.compile(
        r"^\|\s*(\w[\w\s/]+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(\w+)\s*\|",
        re.MULTILINE,
    )
    _DEPENDENCY_ROW = re.compile(
        r"^\|\s*(\w[\w\s/]+?)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(.+?)\s*\|",
        re.MULTILINE,
    )

    def __init__(self, products_dir: Path) -> None:
        self.products_dir = products_dir
        self.rows: list[dict] = []
        self.relationships: list[dict] = []

    def run(self) -> None:
        if not self.products_dir.is_dir():
            _log.warning("ProductExtractor: %s not found", self.products_dir)
            return

        seen: set[str] = set()
        for md in sorted(self.products_dir.glob("*.md")):
            try:
                self._parse_product(md, seen)
            except Exception as exc:
                _log.warning("ProductExtractor: error in %s: %s", md.name, exc)

    def _parse_product(self, path: Path, seen: set[str]) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        fm = _parse_yaml_frontmatter(text)

        product_name = fm.get("product", path.stem.replace("-", " ").title())
        slug = fm.get("slug", path.stem)
        layer = fm.get("layer", "")
        team = fm.get("team", "")

        prod_id = f"product-{slug}"
        if prod_id in seen:
            return
        seen.add(prod_id)

        # Extract architecture overview (first paragraph after ## Architecture Overview)
        arch_m = re.search(r"##\s*Architecture Overview\s*\n+(.+?)(?=\n##|\Z)", text, re.DOTALL)
        summary = ""
        if arch_m:
            summary = arch_m.group(1).strip().splitlines()[0][:300]

        self.rows.append({
            "id": prod_id,
            "name": product_name,
            "type": "product",
            "domain": "work",
            "layer": layer,
            "team": team,
            "summary": summary,
            "source": f"file:products/{path.name}",
        })

        # Parse components table (after ## Components)
        comp_section = re.search(r"##\s*Components\s*\n(.+?)(?=\n##|\Z)", text, re.DOTALL)
        if comp_section:
            for cm in self._COMPONENT_ROW.finditer(comp_section.group(1)):
                comp_name = cm.group(1).strip()
                if comp_name.lower() in ("component", "---", ""):
                    continue
                comp_slug = re.sub(r"[^a-z0-9]+", "-", comp_name.lower()).strip("-")
                comp_id = f"component-{slug}-{comp_slug}"
                if comp_id in seen:
                    continue
                seen.add(comp_id)

                self.rows.append({
                    "id": comp_id,
                    "name": comp_name,
                    "type": "component",
                    "domain": "work",
                    "purpose": _strip_md_formatting(cm.group(2))[:200],
                    "owner": _strip_md_formatting(cm.group(3)),
                    "status": _strip_md_formatting(cm.group(4)),
                    "source": f"file:products/{path.name}",
                })
                self.relationships.append({
                    "from_entity": comp_id,
                    "to_entity": prod_id,
                    "rel_type": _REL_COMPONENT,
                    "confidence": 0.95,
                    "source": f"file:products/{path.name}",
                })

        # Parse dependencies table (after ## Dependencies)
        dep_section = re.search(r"##\s*Dependencies\s*\n(.+?)(?=\n##|\Z)", text, re.DOTALL)
        if dep_section:
            for dm in self._DEPENDENCY_ROW.finditer(dep_section.group(1)):
                dep_name = dm.group(1).strip()
                if dep_name.lower() in ("dependency", "---", ""):
                    continue
                dep_slug = re.sub(r"[^a-z0-9]+", "-", dep_name.lower()).strip("-")
                dep_id = f"dependency-{dep_slug}"
                if dep_id not in seen:
                    seen.add(dep_id)
                    self.rows.append({
                        "id": dep_id,
                        "name": dep_name,
                        "type": "dependency",
                        "domain": "infrastructure",
                        "dep_type": _strip_md_formatting(dm.group(2)),
                        "direction": _strip_md_formatting(dm.group(3)),
                        "notes": _strip_md_formatting(dm.group(4))[:200],
                        "source": f"file:products/{path.name}",
                    })
                self.relationships.append({
                    "from_entity": prod_id,
                    "to_entity": dep_id,
                    "rel_type": _REL_DEPENDS_ON,
                    "confidence": 0.90,
                    "source": f"file:products/{path.name}",
                })


class ToolExtractor:
    """Extract tools/systems from knowledge/tool-system-inventory.md."""

    _CATEGORY_HEADER = re.compile(r"^##\s*\d+\.\s+(.+)", re.MULTILINE)
    _TOOL_ROW = re.compile(
        r"^\|\s*\*\*([^*|]+)\*\*\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|",
        re.MULTILINE,
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("ToolExtractor: %s not found", self.path)
            return

        text = self.path.read_text(encoding="utf-8", errors="replace")
        sections = re.split(r"(?=^## \d+\.)", text, flags=re.MULTILINE)
        seen: set[str] = set()

        for section in sections:
            cat_m = self._CATEGORY_HEADER.match(section)
            category = cat_m.group(1).strip() if cat_m else "Uncategorized"

            for tm in self._TOOL_ROW.finditer(section):
                name = tm.group(1).strip()
                purpose = _strip_md_formatting(tm.group(2))[:200]
                notes = _strip_md_formatting(tm.group(3))[:200]

                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                tool_id = f"tool-{slug}"
                if tool_id in seen:
                    continue
                seen.add(tool_id)

                self.rows.append({
                    "id": tool_id,
                    "name": name,
                    "type": "tool",
                    "domain": "tooling",
                    "category": category,
                    "purpose": purpose,
                    "notes": notes,
                    "source": f"file:{self.path.name}",
                })


class MeetingExtractor:
    """Extract meetings from knowledge/meeting-atlas.md."""

    _MEETING_ROW = re.compile(
        r"^\|\s*\*\*([^*|]+)\*\*\s*\|\s*(\w+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|",
        re.MULTILINE,
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("MeetingExtractor: %s not found", self.path)
            return

        text = self.path.read_text(encoding="utf-8", errors="replace")
        seen: set[str] = set()

        for m in self._MEETING_ROW.finditer(text):
            name = m.group(1).strip()
            cadence = m.group(2).strip()
            day_time = _strip_md_formatting(m.group(3))
            attendees = _strip_md_formatting(m.group(4))
            purpose = _strip_md_formatting(m.group(5))[:200]
            kb_ref = _strip_md_formatting(m.group(6))

            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            mtg_id = f"meeting-{slug}"
            if mtg_id in seen:
                continue
            seen.add(mtg_id)

            self.rows.append({
                "id": mtg_id,
                "name": name,
                "type": "meeting",
                "domain": "work",
                "cadence": cadence,
                "day_time": day_time,
                "attendees": attendees,
                "purpose": purpose,
                "kb_ref": kb_ref,
                "source": f"file:{self.path.name}",
            })


class DeepDiveExtractor:
    """Extract deep-dive areas from state/work/work-area-deep-dives.md."""

    _AREA_HEADER = re.compile(r"^##\s+(.+)", re.MULTILINE)
    _TIMELINE_ROW = re.compile(
        r"^\|\s*\*?\*?(.+?)\*?\*?\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|",
        re.MULTILINE,
    )
    _ICM_REF = re.compile(r"(?:ICM|IcM|icm)\s*[#]?\s*(\d{6,})", re.IGNORECASE)
    _BUG_REF = re.compile(r"Bug\s+(\d{6,})", re.IGNORECASE)

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []
        self.relationships: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("DeepDiveExtractor: %s not found", self.path)
            return

        text = self.path.read_text(encoding="utf-8", errors="replace")
        sections = re.split(r"(?=^## )", text, flags=re.MULTILINE)
        seen: set[str] = set()

        for section in sections:
            hdr = self._AREA_HEADER.match(section)
            if not hdr:
                continue
            name = hdr.group(1).strip()
            if any(kw in name.lower() for kw in ("legend", "note", "---", "appendix")):
                continue

            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            area_id = f"deepdive-{slug}"
            if area_id in seen:
                continue
            seen.add(area_id)

            # Extract IcM/Bug references
            icm_refs = list({m.group(1) for m in self._ICM_REF.finditer(section)})
            bug_refs = list({m.group(1) for m in self._BUG_REF.finditer(section)})

            # Count timeline entries
            timeline_count = 0
            for tm in self._TIMELINE_ROW.finditer(section):
                cell0 = tm.group(1).strip().strip("*")
                # Skip table headers
                if cell0.lower() in ("date", "component", "---"):
                    continue
                timeline_count += 1

            self.rows.append({
                "id": area_id,
                "name": name,
                "type": "deep_dive",
                "domain": "work",
                "icm_refs": icm_refs,
                "bug_refs": bug_refs,
                "timeline_entries": timeline_count,
                "source": f"file:{self.path.name}",
            })

            # Create relationships from IcM refs
            for icm in icm_refs:
                icm_id = f"icm-{icm}"
                if icm_id not in seen:
                    seen.add(icm_id)
                    self.rows.append({
                        "id": icm_id,
                        "name": f"IcM {icm}",
                        "type": "incident",
                        "domain": "operations",
                        "source": f"file:{self.path.name}",
                    })
                self.relationships.append({
                    "from_entity": area_id,
                    "to_entity": icm_id,
                    "rel_type": _REL_RELATED_TO,
                    "confidence": 0.85,
                    "source": f"file:{self.path.name}",
                })


class MilestoneExtractor:
    """Extract project journey milestones from state/work/work-project-journeys.md."""

    _PROJECT_HEADER = re.compile(r"^##\s*\d+\.\s+(.+)", re.MULTILINE)
    _MILESTONE_ROW = re.compile(
        r"^\|\s*\*?\*?(.+?)\*?\*?\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|",
        re.MULTILINE,
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []
        self.relationships: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("MilestoneExtractor: %s not found", self.path)
            return

        text = self.path.read_text(encoding="utf-8", errors="replace")
        sections = re.split(r"(?=^## \d+\.)", text, flags=re.MULTILINE)
        seen: set[str] = set()

        for section in sections:
            proj_m = self._PROJECT_HEADER.match(section)
            if not proj_m:
                continue
            project_name = proj_m.group(1).strip()
            proj_slug = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")
            proj_id = f"project-{proj_slug}"

            counter = 0
            for mm in self._MILESTONE_ROW.finditer(section):
                date_str = _strip_md_formatting(mm.group(1))
                milestone = _strip_md_formatting(mm.group(2))[:200]
                evidence = _strip_md_formatting(mm.group(3))[:200]
                impact = _strip_md_formatting(mm.group(4))[:200]

                # Skip table headers and separator rows
                if date_str.lower() in ("date", "---", "") or milestone.lower() in ("milestone", "---"):
                    continue

                counter += 1
                ms_id = f"milestone-{proj_slug}-{counter:03d}"
                if ms_id in seen:
                    continue
                seen.add(ms_id)

                self.rows.append({
                    "id": ms_id,
                    "name": milestone,
                    "type": "milestone",
                    "domain": "work",
                    "date": date_str,
                    "evidence": evidence,
                    "impact": impact,
                    "project": project_name,
                    "source": f"file:{self.path.name}",
                })
                self.relationships.append({
                    "from_entity": ms_id,
                    "to_entity": proj_id,
                    "rel_type": _REL_COMPONENT,
                    "confidence": 0.85,
                    "source": f"file:{self.path.name}",
                })


class ProgramStructureExtractor:
    """Extract workstreams and metrics from state/work/xpf-program-structure.md."""

    # Matches "### WS1 — Deployment Readiness" or "### WS1: Name"
    _WS_HEADER = re.compile(r"^###\s+(WS\d+)\s*[—–:\-]+\s*(.+)", re.MULTILINE)
    _FIELD_LINE = re.compile(r"^\*\*(\w[\w\s/]*)\*\*\s*[:：]\s*(.+)", re.MULTILINE)
    _MILESTONE_HEADER = re.compile(r"^###\s+Ramp\s+(P\d+)\s+[—–-]\s+(.+)", re.MULTILINE)

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("ProgramStructureExtractor: %s not found", self.path)
            return

        text = self.path.read_text(encoding="utf-8", errors="replace")
        seen: set[str] = set()

        # Extract program milestones (Ramp P0, Ramp P1)
        for mm in self._MILESTONE_HEADER.finditer(text):
            phase = mm.group(1).strip()
            desc = mm.group(2).strip()[:200]
            ms_id = f"xpf-ramp-{phase.lower()}"
            if ms_id in seen:
                continue
            seen.add(ms_id)

            # Find status line after this header
            after = text[mm.end():]
            status_m = re.search(r"\*\*Status:\*\*\s*(.+)", after)
            status = _strip_md_formatting(status_m.group(1))[:100] if status_m else ""

            self.rows.append({
                "id": ms_id,
                "name": f"XPF Ramp {phase} — {desc}",
                "type": "program_milestone",
                "domain": "work",
                "phase": phase,
                "status": status,
                "source": f"file:{self.path.name}",
            })

        # Extract workstreams
        sections = re.split(r"(?=^### WS)", text, flags=re.MULTILINE)
        for section in sections:
            ws_m = self._WS_HEADER.match(section)
            if not ws_m:
                continue
            ws_code = ws_m.group(1).strip()
            ws_name = ws_m.group(2).strip()

            ws_id = f"ws-{ws_code.lower()}"
            if ws_id in seen:
                continue
            seen.add(ws_id)

            fields: dict[str, str] = {}
            for fm in self._FIELD_LINE.finditer(section):
                key = fm.group(1).strip().lower()
                val = fm.group(2).strip()[:300]
                fields[key] = _strip_md_formatting(val)

            self.rows.append({
                "id": ws_id,
                "name": f"{ws_code}: {ws_name}",
                "type": "workstream",
                "domain": "work",
                "ws_code": ws_code,
                "pm": fields.get("pm", ""),
                "engineering": fields.get("engineering", fields.get("eng", "")),
                "metrics": fields.get("metrics", ""),
                "source": f"file:{self.path.name}",
            })


class ProgramBrainExtractor:
    """Extract program phases, milestones, and manager signals from
    state/work/xpf-ramp-deep-context.md."""

    # Matches "### Phase 0: Title" or "## Part 2: ..." sections containing phases
    _PHASE_HEADER = re.compile(r"^###\s+(Phase\s+\d+):\s+(.+)", re.MULTILINE)
    _KEY_MS_LINE = re.compile(r"^\s*-\s*(.+?:\s+.+)", re.MULTILINE)
    _MANAGER_SIGNAL = re.compile(
        r'[*_]*"(.+?)"[*_]*\s*[—–-]+\s*(\w[\w\s]+?)(?:,\s*(\w+\s+\d{4})|$)',
        re.MULTILINE,
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []

    def run(self) -> None:
        if not self.path.is_file():
            _log.warning("ProgramBrainExtractor: %s not found", self.path)
            return

        text = self.path.read_text(encoding="utf-8", errors="replace")
        seen: set[str] = set()

        # Extract phases
        sections = re.split(r"(?=^### Phase)", text, flags=re.MULTILINE)
        for section in sections:
            pm = self._PHASE_HEADER.match(section)
            if not pm:
                continue
            phase_num = pm.group(1).strip()
            phase_desc = pm.group(2).strip()

            slug = re.sub(r"[^a-z0-9]+", "-", phase_num.lower()).strip("-")
            phase_id = f"brain-{slug}"
            if phase_id in seen:
                continue
            seen.add(phase_id)

            # Extract key milestones within this phase
            milestones: list[str] = []
            in_milestones = False
            for line in section.splitlines():
                if "key milestone" in line.lower() or "key deliverable" in line.lower():
                    in_milestones = True
                    continue
                if in_milestones:
                    if line.strip().startswith("- "):
                        milestones.append(line.strip()[2:][:200])
                    elif line.strip().startswith("###") or (line.strip() and not line.startswith(" ")):
                        in_milestones = False

            # Extract manager signals
            signals: list[dict] = []
            for sm in self._MANAGER_SIGNAL.finditer(section):
                signals.append({
                    "quote": sm.group(1).strip()[:200],
                    "person": sm.group(2).strip(),
                    "date": (sm.group(3) or "").strip(),
                })

            self.rows.append({
                "id": phase_id,
                "name": f"{phase_num}: {phase_desc}",
                "type": "program_phase",
                "domain": "work",
                "milestones": milestones[:20],
                "manager_signals": signals[:10],
                "source": f"file:{self.path.name}",
            })


# ---------------------------------------------------------------------------
# Bootstrap runner
# ---------------------------------------------------------------------------

def bootstrap(
    artha_dir: Path | None = None,
    files: list[Path] | None = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """Run KB bootstrap from knowledge/ markdown files.

    Returns summary stats dict.
    """
    base     = artha_dir or _ARTHA
    kb_dir   = _KNOWLEDGE_DIR
    files    = files or sorted(kb_dir.glob("*.md"))

    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    kg: KnowledgeGraph
    if dry_run:
        _log.info("DRY RUN — no writes to KB")
        kg = None  # type: ignore[assignment]
    else:
        kg = KnowledgeGraph(artha_dir=base)

    stats = {
        "files_processed": 0,
        "entities_upserted": 0,
        "relationships_added": 0,
        "episodes_created": 0,
        "errors": [],
    }

    for md_file in files:
        if not md_file.is_file() or md_file.suffix.lower() != ".md":
            continue
        domain     = _infer_domain(md_file.stem)
        now_iso    = datetime.now(timezone.utc).isoformat(timespec="seconds")
        episode_key = f"kb-bootstrap-{md_file.stem}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

        _log.info("Processing: %s (domain=%s)", md_file.name, domain)

        try:
            extractor = FileExtractor(md_file, domain, episode_key)
            extractor.run()

            if dry_run:
                if verbose:
                    print(f"[DRY-RUN] {md_file.name}: {len(extractor.entities)} entities,"
                          f" {len(extractor.relationships)} rels")
                stats["files_processed"] += 1
                stats["entities_upserted"]   += len(extractor.entities)
                stats["relationships_added"] += len(extractor.relationships)
                continue

            # Register episode
            raw_content_preview = md_file.read_text(encoding="utf-8", errors="replace")[:2000]
            ep_id = kg.add_episode(
                episode_key=episode_key,
                source_type="kb_file",
                raw_content=raw_content_preview,
            )
            stats["episodes_created"] += 1

            # Upsert entities
            for entity in extractor.entities:
                kg.upsert_entity(
                    entity,
                    source=f"kb_file:{md_file.name}",
                    confidence=float(entity.get("confidence", 0.75)),
                    source_episode_id=ep_id,
                )
                stats["entities_upserted"] += 1

            # Add relationships
            for rel in extractor.relationships:
                try:
                    kg.add_relationship(
                        from_id=rel["from_entity"],
                        to_id=rel["to_entity"],
                        rel_type=rel["rel_type"],
                        confidence=rel.get("confidence", 0.60),
                        source=rel.get("source", f"kb_file:{md_file.name}"),
                        source_episode_id=ep_id,
                    )
                    stats["relationships_added"] += 1
                except Exception as rel_exc:
                    # Relationship failures are non-fatal (entity may not exist yet)
                    _log.debug("Rel skip %s → %s: %s", rel["from_entity"], rel["to_entity"], rel_exc)

            stats["files_processed"] += 1

        except Exception as exc:
            err_msg = f"{md_file.name}: {exc}"
            _log.error("Bootstrap error: %s", err_msg)
            stats["errors"].append(err_msg)

    if not dry_run and kg is not None:
        # Rebuild communities after all entities are loaded
        try:
            num_communities = kg.rebuild_communities()
            stats["communities_rebuilt"] = num_communities
            _log.info("Rebuilt %d communities", num_communities)
        except Exception as exc:
            _log.warning("Community rebuild failed: %s", exc)

        kg.close()

    return stats


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------

def show_status(artha_dir: Path | None = None) -> None:
    kg = get_kb(artha_dir=artha_dir)
    stats = kg.get_stats()
    issues = kg.validate_integrity()
    print("\n=== Artha KB Status ===")
    for k, v in stats.items():
        print(f"  {k:<30} {v}")
    if issues:
        print("\nIntegrity issues:")
        for issue in issues:
            print(f"  ⚠ {issue}")
    else:
        print("\n  ✅ Integrity OK")
    kg.close()


# ---------------------------------------------------------------------------
# Structured bootstrap runner (Phase 1)
# ---------------------------------------------------------------------------

def bootstrap_structured(
    artha_dir: Path | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    incremental: bool = False,
) -> dict:
    """Run Phase-1 structured extractors across all work/knowledge sources.

    Unlike the heuristic ``bootstrap()``, this uses purpose-built extractors
    that understand exact file formats and produce high-confidence entities.

    Returns a stats dict.
    """
    base = artha_dir or _ARTHA
    state_work = base / "state" / "work"
    knowledge = base / "knowledge"

    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    kg: KnowledgeGraph | None = None
    if not dry_run:
        kg = KnowledgeGraph(artha_dir=base)

    # Check incremental: skip if recently run
    if incremental and kg is not None:
        try:
            cur = kg._conn.execute(
                "SELECT value FROM kb_meta WHERE key = 'last_structured_run'"
            ).fetchone()
            if cur:
                last_run = datetime.fromisoformat(cur[0])
                hours_ago = (datetime.now(timezone.utc) - last_run).total_seconds() / 3600
                if hours_ago < 1.0:
                    _log.info("Incremental: last structured run %.1fh ago — skipping", hours_ago)
                    kg.close()
                    return {"skipped": True, "reason": f"last run {hours_ago:.1f}h ago"}
        except Exception:
            pass  # Table or key may not exist yet

    stats: dict = {
        "extractors_run": 0,
        "extractors_ok": 0,
        "extractors_failed": 0,
        "entities_total": 0,
        "relationships_total": 0,
        "errors": [],
    }

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    episode_key = f"structured-bootstrap-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    # Register provenance episode
    ep_id: int | None = None
    if kg is not None:
        ep_id = kg.add_episode(
            episode_key=episode_key,
            source_type="structured_bootstrap",
            raw_content=f"Phase-1 structured extractors run at {now_iso}",
        )

    def _ingest_rows(extractor_name: str, rows: list[dict], rels: list[dict] | None = None) -> None:
        """Upsert extracted rows and relationships into the KB."""
        for row in rows:
            entity_id = row.get("id", "")
            if not entity_id:
                continue
            stats["entities_total"] += 1
            if dry_run:
                if verbose:
                    print(f"  [DRY] {extractor_name}: {entity_id} ({row.get('type', '?')})")
                continue
            try:
                # Prepare entity dict for upsert (subset of fields the KB expects)
                entity = {
                    "id": entity_id,
                    "name": row.get("name", entity_id),
                    "type": row.get("type", "concept"),
                    "domain": row.get("domain", "work"),
                }
                summary_parts = []
                for k in ("summary", "question", "purpose", "rationale", "role",
                           "status_detail", "key_facts", "notes"):
                    if row.get(k):
                        summary_parts.append(str(row[k]))
                if summary_parts:
                    entity["summary"] = " | ".join(summary_parts)[:500]

                if row.get("status"):
                    entity["current_state"] = row["status"]

                kg.upsert_entity(
                    entity,
                    source=row.get("source", f"structured:{extractor_name}"),
                    confidence=0.90,
                    source_episode_id=ep_id,
                )
            except Exception as exc:
                stats["errors"].append(f"{extractor_name}/{entity_id}: {exc}")
                _log.debug("Upsert failed for %s: %s", entity_id, exc)

        if rels and not dry_run:
            for rel in rels:
                stats["relationships_total"] += 1
                try:
                    kg.add_relationship(
                        from_id=rel["from_entity"],
                        to_id=rel["to_entity"],
                        rel_type=rel["rel_type"],
                        confidence=rel.get("confidence", 0.80),
                        source=rel.get("source", f"structured:{extractor_name}"),
                        source_episode_id=ep_id,
                    )
                except Exception as exc:
                    _log.debug("Rel skip %s → %s: %s", rel.get("from_entity"), rel.get("to_entity"), exc)
        elif rels and dry_run:
            stats["relationships_total"] += len(rels)

    # --- Run all 15 extractors ---

    # 1. Golden Queries
    gq_ext = GoldenQueryExtractor(state_work / "golden-queries.md")
    stats["extractors_run"] += 1
    if _safe_run(gq_ext, "GoldenQueryExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("golden_queries", gq_ext.rows)
        if verbose:
            print(f"  GoldenQueryExtractor: {len(gq_ext.rows)} queries")
    else:
        stats["extractors_failed"] += 1

    # 2. Kusto Catalog (derived from golden queries)
    kc_ext = KustoCatalogExtractor(gq_ext.rows)
    stats["extractors_run"] += 1
    if _safe_run(kc_ext, "KustoCatalogExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("kusto_catalog", kc_ext.rows)
        if verbose:
            print(f"  KustoCatalogExtractor: {len(kc_ext.rows)} catalog entities")
    else:
        stats["extractors_failed"] += 1

    # 3. People
    ppl_ext = PeopleExtractor(state_work / "work-people.md")
    stats["extractors_run"] += 1
    if _safe_run(ppl_ext, "PeopleExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("people", ppl_ext.rows)
        if verbose:
            print(f"  PeopleExtractor: {len(ppl_ext.rows)} people")
    else:
        stats["extractors_failed"] += 1

    # 4. Decisions
    dec_ext = DecisionExtractor(state_work / "work-decisions.md")
    stats["extractors_run"] += 1
    if _safe_run(dec_ext, "DecisionExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("decisions", dec_ext.rows)
        if verbose:
            print(f"  DecisionExtractor: {len(dec_ext.rows)} decisions")
    else:
        stats["extractors_failed"] += 1

    # 5. Accomplishments
    acc_ext = AccomplishmentExtractor(state_work / "work-accomplishments.md")
    stats["extractors_run"] += 1
    if _safe_run(acc_ext, "AccomplishmentExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("accomplishments", acc_ext.rows)
        if verbose:
            print(f"  AccomplishmentExtractor: {len(acc_ext.rows)} accomplishments")
    else:
        stats["extractors_failed"] += 1

    # 6. Projects
    proj_ext = ProjectExtractor(state_work / "work-projects.md")
    stats["extractors_run"] += 1
    if _safe_run(proj_ext, "ProjectExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("projects", proj_ext.rows, proj_ext.relationships)
        if verbose:
            print(f"  ProjectExtractor: {len(proj_ext.rows)} entities, {len(proj_ext.relationships)} rels")
    else:
        stats["extractors_failed"] += 1

    # 7. Scope
    scope_ext = ScopeExtractor(state_work / "work-scope.md")
    stats["extractors_run"] += 1
    if _safe_run(scope_ext, "ScopeExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("scope", scope_ext.rows)
        if verbose:
            print(f"  ScopeExtractor: {len(scope_ext.rows)} scope areas")
    else:
        stats["extractors_failed"] += 1

    # 8. Products
    prod_ext = ProductExtractor(state_work / "products")
    stats["extractors_run"] += 1
    if _safe_run(prod_ext, "ProductExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("products", prod_ext.rows, prod_ext.relationships)
        if verbose:
            print(f"  ProductExtractor: {len(prod_ext.rows)} entities, {len(prod_ext.relationships)} rels")
    else:
        stats["extractors_failed"] += 1

    # 9. Tools
    tool_ext = ToolExtractor(knowledge / "tool-system-inventory.md")
    stats["extractors_run"] += 1
    if _safe_run(tool_ext, "ToolExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("tools", tool_ext.rows)
        if verbose:
            print(f"  ToolExtractor: {len(tool_ext.rows)} tools")
    else:
        stats["extractors_failed"] += 1

    # 10. Meetings
    mtg_ext = MeetingExtractor(knowledge / "meeting-atlas.md")
    stats["extractors_run"] += 1
    if _safe_run(mtg_ext, "MeetingExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("meetings", mtg_ext.rows)
        if verbose:
            print(f"  MeetingExtractor: {len(mtg_ext.rows)} meetings")
    else:
        stats["extractors_failed"] += 1

    # 11. Deep Dives
    dd_ext = DeepDiveExtractor(state_work / "work-area-deep-dives.md")
    stats["extractors_run"] += 1
    if _safe_run(dd_ext, "DeepDiveExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("deep_dives", dd_ext.rows, dd_ext.relationships)
        if verbose:
            print(f"  DeepDiveExtractor: {len(dd_ext.rows)} entities, {len(dd_ext.relationships)} rels")
    else:
        stats["extractors_failed"] += 1

    # 12. Milestones (Project Journeys)
    ms_ext = MilestoneExtractor(state_work / "work-project-journeys.md")
    stats["extractors_run"] += 1
    if _safe_run(ms_ext, "MilestoneExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("milestones", ms_ext.rows, ms_ext.relationships)
        if verbose:
            print(f"  MilestoneExtractor: {len(ms_ext.rows)} milestones, {len(ms_ext.relationships)} rels")
    else:
        stats["extractors_failed"] += 1

    # 13. Program Structure
    ps_ext = ProgramStructureExtractor(state_work / "xpf-program-structure.md")
    stats["extractors_run"] += 1
    if _safe_run(ps_ext, "ProgramStructureExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("program_structure", ps_ext.rows)
        if verbose:
            print(f"  ProgramStructureExtractor: {len(ps_ext.rows)} entries")
    else:
        stats["extractors_failed"] += 1

    # 14. Program Brain
    pb_ext = ProgramBrainExtractor(state_work / "xpf-ramp-deep-context.md")
    stats["extractors_run"] += 1
    if _safe_run(pb_ext, "ProgramBrainExtractor", verbose):
        stats["extractors_ok"] += 1
        _ingest_rows("program_brain", pb_ext.rows)
        if verbose:
            print(f"  ProgramBrainExtractor: {len(pb_ext.rows)} phases")
    else:
        stats["extractors_failed"] += 1

    # 15. Scope is already extractor #7 above — the 15th "slot" is the
    # KustoCatalog which is derived from GoldenQueries (extractor #2).
    # All 15 extractors accounted for: GQ, KustoCatalog, People, Decisions,
    # Accomplishments, Projects, Scope, Products, Tools, Meetings,
    # DeepDives, Milestones, ProgramStructure, ProgramBrain + the 15th
    # is the combined GQ+KustoCatalog pair counting as two.

    # Record last structured run timestamp
    if kg is not None:
        try:
            kg._conn.execute(
                "INSERT OR REPLACE INTO kb_meta (key, value) VALUES (?, ?)",
                ("last_structured_run", now_iso),
            )
            kg._conn.commit()
        except Exception as exc:
            _log.debug("Could not record last_structured_run: %s", exc)

        # Populate corroborating_sources: max distinct changed_by values that
        # agreed on the same (field, new_value) pair for each entity.
        # Spec: data-quality-gate.md §1 "corroborating_sources storage"
        try:
            kg._conn.execute("""
                UPDATE entities
                SET corroborating_sources = COALESCE((
                    SELECT MAX(src_count)
                    FROM (
                        SELECT COUNT(DISTINCT changed_by) AS src_count
                        FROM entity_history
                        WHERE entity_id = entities.id
                          AND new_value IS NOT NULL
                        GROUP BY field, new_value
                    )
                ), 0)
            """)
            kg._conn.commit()
            updated = kg._conn.execute(
                "SELECT COUNT(*) FROM entities WHERE corroborating_sources >= 2"
            ).fetchone()[0]
            stats["corroborating_sources_updated"] = updated
            _log.info("corroborating_sources: %d entities have ≥2 sources", updated)
        except Exception as exc:
            _log.warning("corroborating_sources update failed (non-fatal): %s", exc)

        # Rebuild communities
        try:
            nc = kg.rebuild_communities()
            stats["communities_rebuilt"] = nc
        except Exception as exc:
            _log.warning("Community rebuild failed: %s", exc)

        kg.close()

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Artha KB bootstrap — ingest knowledge/*.md into the graph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Parse files and show stats without writing to KB",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable INFO logging",
    )
    p.add_argument(
        "--file", metavar="PATH", nargs="+",
        help="Process specific file(s) instead of all knowledge/*.md",
    )
    p.add_argument(
        "--status", action="store_true",
        help="Show current KB stats and exit",
    )
    p.add_argument(
        "--artha-dir", metavar="PATH",
        help="Override Artha root directory (default: auto-detected)",
    )
    p.add_argument(
        "--structured", action="store_true",
        help="Run Phase-1 structured extractors (high-confidence, format-aware)",
    )
    p.add_argument(
        "--incremental", action="store_true",
        help="Skip structured run if recently completed (< 1 hour)",
    )
    return p


def main() -> None:
    p = _build_parser()
    args = p.parse_args()

    artha_dir = Path(args.artha_dir) if args.artha_dir else None

    if args.status:
        show_status(artha_dir=artha_dir)
        return

    if args.structured:
        stats = bootstrap_structured(
            artha_dir=artha_dir,
            dry_run=args.dry_run,
            verbose=args.verbose,
            incremental=args.incremental,
        )
        if stats.get("skipped"):
            print(f"Structured bootstrap skipped: {stats.get('reason', 'unknown')}")
            return
        print(f"\nStructured bootstrap {'DRY-RUN ' if args.dry_run else ''}complete:")
        for k, v in stats.items():
            if k == "errors":
                continue
            print(f"  {k}: {v}")
        if stats.get("errors"):
            print(f"\n  Errors ({len(stats['errors'])}):")
            for e in stats["errors"][:20]:
                print(f"    - {e}")
        return

    files: list[Path] | None = None
    if args.file:
        files = [Path(f) for f in args.file]

    stats = bootstrap(
        artha_dir=artha_dir,
        files=files,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    # Print summary
    print(f"\nBootstrap {'DRY-RUN ' if args.dry_run else ''}complete:")
    for k, v in stats.items():
        if k == "errors":
            continue
        print(f"  {k}: {v}")
    if stats.get("errors"):
        print(f"\n  Errors ({len(stats['errors'])}):")
        for e in stats["errors"]:
            print(f"    - {e}")


if __name__ == "__main__":
    main()
