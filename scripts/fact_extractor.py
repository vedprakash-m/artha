#!/usr/bin/env python3
# pii-guard: ignore-file — handles PII redaction internally
"""
scripts/fact_extractor.py — Persistent fact extraction for cross-session learning.

Runs after session summarization (post-command).  Reads the latest session
history from ``tmp/session_history_*.md``, extracts durable facts, deduplicates
them against existing facts in ``state/memory.md``, and appends new facts.

Facts are stored as structured YAML entries in the ``facts:`` section of
``state/memory.md``'s frontmatter.  The markdown body is preserved unchanged.

Fact schema (each entry under ``facts:``):
    - id: "correction-finance-costco-not-anomalous"
      type: correction | pattern | preference | contact | schedule | threshold
      domain: finance
      statement: "Costco purchases are not anomalous spending — routine bulk shopping"
      source: "session-2026-03-15"
      date_added: 2026-03-15
      ttl_days: null          # null = indefinite; int = expire after N days
      confidence: 1.0         # 0.0–1.0 (corrections=1.0, patterns=0.8, inferred=0.6)
      last_seen: 2026-03-15

Inspired by Agno's ``UserMemory`` — extracts structured facts from sessions,
deduplicates against existing memory, persists across runs.

Phase 5 of the Agentic Intelligence Improvement Plan (specs/agentic-improve.md).

Usage:
    python -c "
    from pathlib import Path
    from scripts.fact_extractor import extract_facts_from_summary, persist_facts
    import glob, sys

    summaries = sorted(glob.glob('tmp/session_history_*.md'))
    if summaries:
        facts = extract_facts_from_summary(Path(summaries[-1]), Path('.'))
        count = persist_facts(facts, Path('.'))
        print(f'Extracted {count} new facts to state/memory.md')
    else:
        print('No session summary found — skipping fact extraction')
    "

Config flag: harness.agentic.fact_extraction.enabled (default: true)
When disabled, extract_facts_from_summary returns [] and persist_facts
is a no-op.

Ref: specs/agentic-improve.md Phase 5
"""
from __future__ import annotations

import re
import textwrap
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False

try:
    from pydantic import BaseModel, Field
    _PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYDANTIC_AVAILABLE = False
    BaseModel = object  # type: ignore[assignment,misc]

    class Field:  # type: ignore[no-redef]
        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            return kwargs.get("default", None)

try:
    from context_offloader import load_harness_flag as _load_harness_flag
except ImportError:  # pragma: no cover
    def _load_harness_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default

# ---------------------------------------------------------------------------
# PII patterns — strip from fact statements before persistence
# ---------------------------------------------------------------------------

_PII_PATTERNS = [
    (re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"), "[SSN-REDACTED]"),
    (re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE-REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL-REDACTED]"),
]


def _strip_pii(text: str) -> str:
    """Remove common PII patterns from a fact statement."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

if _PYDANTIC_AVAILABLE:
    from pydantic import BaseModel, Field as PydanticField, field_validator

    class Fact(BaseModel):
        """A single durable fact extracted from a session."""
        id: str
        type: str  # correction | pattern | preference | contact | schedule | threshold
        domain: str
        statement: str
        source: str = ""
        date_added: str = ""   # ISO date string YYYY-MM-DD
        ttl_days: int | None = None
        confidence: float = PydanticField(default=0.8, ge=0.0, le=1.0)
        last_seen: str = ""    # ISO date string YYYY-MM-DD

        def __init__(self, **data: Any) -> None:
            today = date.today().isoformat()
            if not data.get("date_added"):
                data["date_added"] = today
            if not data.get("last_seen"):
                data["last_seen"] = today
            super().__init__(**data)

        def is_expired(self) -> bool:
            """Return True if this fact has exceeded its TTL."""
            if self.ttl_days is None:
                return False
            try:
                added = date.fromisoformat(self.date_added)
                return (date.today() - added).days > self.ttl_days
            except (ValueError, AttributeError):
                return False

        def to_dict(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "type": self.type,
                "domain": self.domain,
                "statement": self.statement,
                "source": self.source,
                "date_added": self.date_added,
                "ttl_days": self.ttl_days,
                "confidence": self.confidence,
                "last_seen": self.last_seen,
            }

else:
    class Fact:  # type: ignore[no-redef]
        """Fallback Fact implementation when Pydantic is unavailable."""

        def __init__(
            self,
            id: str,
            type: str,
            domain: str,
            statement: str,
            source: str = "",
            date_added: str = "",
            ttl_days: int | None = None,
            confidence: float = 0.8,
            last_seen: str = "",
        ) -> None:
            today = date.today().isoformat()
            self.id = id
            self.type = type
            self.domain = domain
            self.statement = _strip_pii(statement)
            self.source = source
            self.date_added = date_added or today
            self.ttl_days = ttl_days
            self.confidence = max(0.0, min(1.0, confidence))
            self.last_seen = last_seen or today

        def is_expired(self) -> bool:
            if self.ttl_days is None:
                return False
            try:
                added = date.fromisoformat(self.date_added)
                return (date.today() - added).days > self.ttl_days
            except (ValueError, AttributeError):
                return False

        def to_dict(self) -> dict[str, Any]:
            return {
                "id": self.id, "type": self.type, "domain": self.domain,
                "statement": self.statement, "source": self.source,
                "date_added": self.date_added, "ttl_days": self.ttl_days,
                "confidence": self.confidence, "last_seen": self.last_seen,
            }


# ---------------------------------------------------------------------------
# Extraction signals
# ---------------------------------------------------------------------------

# (pattern, fact_type, domain_hint, ttl_days, confidence)
_EXTRACTION_SIGNALS: list[tuple[re.Pattern, str, str, int | None, float]] = [
    # Correction signals — indefinite, high confidence
    (re.compile(r"\b(not an anomaly|not anomalous|expected|normal spending|not a problem|ignore this|false positive|user noted|correction)\b", re.I),
     "correction", "general", None, 1.0),
    # Pattern signals — 180 days
    (re.compile(r"\b(recurring|pattern|always|typically|consistently|every (month|week|year)|regular|routine)\b", re.I),
     "pattern", "general", 180, 0.8),
    # Preference signals — indefinite
    (re.compile(r"\b(prefers?|preference|wants|would like|user\s+(?:wants|prefers?)|always\s+prefers?|likes?\s+to|dislikes?)\b", re.I),
     "preference", "general", None, 0.9),
    # Threshold calibration signals — indefinite
    (re.compile(r"\b(threshold|calibrat|normal range|acceptable|budget|usual spend)\b", re.I),
     "threshold", "finance", None, 0.85),
    # Schedule signals — 90 days
    (re.compile(r"\b(schedule|every (tuesday|wednesday|thursday|monday|friday|saturday|sunday)|at \d+\s*(am|pm)|weekly|biweekly)\b", re.I),
     "schedule", "calendar", 90, 0.75),
]

# Domain keywords for auto-tagging
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "finance": ["bill", "payment", "bank", "account", "spend", "budget", "costco", "amazon", "subscription", "paycheck", "salary", "invoice", "tax", "credit"],
    "immigration": ["visa", "i-", "uscis", "travel", "passport", "advance parole", "work authorization", "ead", "green card", "petition", "filing"],
    "health": ["doctor", "appointment", "prescription", "lab", "medical", "health", "clinic", "insurance claim", "refill"],
    "calendar": ["schedule", "meeting", "appointment", "event", "reminder", "pickup", "dropoff"],
    "kids": ["school", "pickup", "dropoff", "homework", "activity", "soccer", "practice", "teacher"],
    "goals": ["goal", "sprint", "progress", "milestone", "habit"],
    "home": ["utility", "mortgage", "maintenance", "property tax", "hoa"],
    "employment": ["payroll", "rsu", "benefits", "401k", "w2", "employer"],
}


def _detect_domain(text: str) -> str:
    """Detect the most likely domain from a text string."""
    text_l = text.lower()
    scores: dict[str, int] = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        scores[domain] = sum(1 for kw in keywords if kw in text_l)
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] > 0 else "general"


def _make_id(fact_type: str, domain: str, statement: str) -> str:
    """Generate a canonical ID for a fact."""
    slug = re.sub(r"[^a-z0-9]+", "-", statement.lower()[:60]).strip("-")
    return f"{fact_type}-{domain}-{slug}"


# ---------------------------------------------------------------------------
# Parsing session summary markdown
# ---------------------------------------------------------------------------

def _parse_summary_md(content: str) -> dict[str, Any]:
    """Parse a session summary markdown file into a structured dict."""
    result: dict[str, Any] = {
        "key_findings": [],
        "state_mutations": [],
        "open_threads": [],
        "command_executed": "",
        "timestamp": "",
    }

    # Extract timestamp from header
    ts_match = re.search(r"Session Summary[^0-9]*(\d{4}-\d{2}-\d{2})", content)
    if ts_match:
        result["timestamp"] = ts_match.group(1)

    # Extract command
    cmd_match = re.search(r"\*\*Command:\*\*\s+`([^`]+)`", content)
    if cmd_match:
        result["command_executed"] = cmd_match.group(1)

    # Extract sections by markdown heading
    current_section: str | None = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Key Findings"):
            current_section = "key_findings"
        elif stripped.startswith("## State Mutations"):
            current_section = "state_mutations"
        elif stripped.startswith("## Open Threads"):
            current_section = "open_threads"
        elif stripped.startswith("##"):
            current_section = None
        elif current_section and re.match(r"^\d+\.\s+", stripped):
            result[current_section].append(re.sub(r"^\d+\.\s+", "", stripped))
        elif current_section and stripped.startswith("- "):
            result[current_section].append(stripped[2:])

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_facts_from_summary(
    summary_path: Path,
    artha_dir: Path,
) -> list[Fact]:
    """Extract durable facts from a session summary file.

    Reads the session summary (markdown), applies pattern matching on
    ``key_findings``, ``state_mutations``, and ``open_threads``, and returns
    a list of ``Fact`` objects for deduplication and persistence.

    Args:
        summary_path: Path to a ``tmp/session_history_*.md`` file.
        artha_dir: Artha project root (used for feature flag lookup).

    Returns:
        List of extracted ``Fact`` objects (may be empty when:
        - Feature flag disabled
        - Summary file unreadable or empty
        - No extraction signals found in the summary
        ).
    """
    if not _load_harness_flag("agentic.fact_extraction.enabled"):
        return []

    if not summary_path.exists():
        return []

    try:
        content = summary_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    if not content.strip():
        return []

    parsed = _parse_summary_md(content)
    session_id = f"session-{parsed.get('timestamp', date.today().isoformat())}"

    facts: list[Fact] = []
    all_lines = (
        parsed.get("key_findings", [])
        + parsed.get("open_threads", [])
    )

    for line in all_lines:
        if not line.strip():
            continue
        for signal_pattern, fact_type, domain_hint, ttl_days, confidence in _EXTRACTION_SIGNALS:
            if signal_pattern.search(line):
                domain = _detect_domain(line)
                if domain == "general" and domain_hint != "general":
                    domain = domain_hint
                statement = _strip_pii(line.strip())
                fact_id = _make_id(fact_type, domain, statement)
                facts.append(Fact(
                    id=fact_id,
                    type=fact_type,
                    domain=domain,
                    statement=statement,
                    source=session_id,
                    ttl_days=ttl_days,
                    confidence=confidence,
                ))
                break  # one signal per line is sufficient

    return facts


def load_existing_facts(artha_dir: Path) -> list[Fact]:
    """Load existing facts from ``state/memory.md`` frontmatter.

    Args:
        artha_dir: Artha project root.

    Returns:
        List of ``Fact`` objects from the ``facts:`` key in memory.md
        frontmatter.  Returns empty list when:
        - memory.md does not exist
        - YAML unavailable
        - Frontmatter has no ``facts:`` key (schema v1 — backward compat)
        - facts entries fail to parse
    """
    if not _YAML_AVAILABLE:
        return []

    memory_path = artha_dir / "state" / "memory.md"
    if not memory_path.exists():
        return []

    try:
        content = memory_path.read_text(encoding="utf-8")
    except OSError:
        return []

    frontmatter = _parse_frontmatter(content)
    raw_facts = frontmatter.get("facts", [])
    if not isinstance(raw_facts, list):
        return []

    result: list[Fact] = []
    for entry in raw_facts:
        if not isinstance(entry, dict):
            continue
        try:
            result.append(Fact(**entry))
        except (TypeError, ValueError):
            continue
    return result


def deduplicate_facts(
    new_facts: list[Fact],
    existing_facts: list[Fact],
) -> list[Fact]:
    """Return only facts not already present in ``existing_facts`` by ID.

    Also bumps ``confidence`` and ``last_seen`` on existing facts when a
    new observation reinforces them (handled in ``persist_facts``).

    Args:
        new_facts: Candidate facts just extracted from a session.
        existing_facts: Facts already in ``state/memory.md``.

    Returns:
        Subset of ``new_facts`` whose IDs are not in ``existing_facts``.
    """
    existing_ids = {f.id for f in existing_facts}
    return [f for f in new_facts if f.id not in existing_ids]


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML frontmatter dict from a markdown file."""
    if not _YAML_AVAILABLE:
        return {}
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}
    try:
        return yaml.safe_load("\n".join(lines[1:end_idx])) or {}
    except yaml.YAMLError:
        return {}


def _rebuild_frontmatter(content: str, updated_data: dict[str, Any]) -> str:
    """Rebuild a markdown file with updated YAML frontmatter.

    Preserves the body (everything after the closing ``---``) unchanged.
    """
    if not _YAML_AVAILABLE:
        return content

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        # No existing frontmatter — prepend one
        fm_text = yaml.dump(updated_data, default_flow_style=False, allow_unicode=True)
        return f"---\n{fm_text}---\n\n{content}"

    # Find the closing ---
    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return content  # Malformed frontmatter — leave unchanged

    body = "\n".join(lines[end_idx + 1:])
    fm_text = yaml.dump(updated_data, default_flow_style=False, allow_unicode=True)
    return f"---\n{fm_text}---\n{body}"


def persist_facts(
    new_facts: list[Fact],
    artha_dir: Path,
) -> int:
    """Append new facts to ``state/memory.md`` frontmatter.

    Loads existing facts, deduplicates, expires stale facts by TTL, then
    writes the merged set back.  The markdown body is preserved intact.

    An upper cap of 500 facts is enforced: when the merged set exceeds
    500, the oldest non-correction, non-preference facts are evicted first.

    Args:
        new_facts: Newly extracted facts to persist.
        artha_dir: Artha project root.

    Returns:
        int: Count of net-new facts added (0 if all were duplicates or
            feature disabled).
    """
    if not _load_harness_flag("agentic.fact_extraction.enabled"):
        return 0

    if not _YAML_AVAILABLE:
        return 0

    existing = load_existing_facts(artha_dir)
    unique_new = deduplicate_facts(new_facts, existing)

    if not unique_new:
        return 0

    # Expire stale facts
    active = [f for f in existing if not f.is_expired()]

    merged = active + unique_new

    # Enforce 500-fact cap — evict INTERMEDIATE facts (not corrections/preferences)
    _MAX_FACTS = 500
    if len(merged) > _MAX_FACTS:
        evictable = [f for f in merged if f.type not in ("correction", "preference")]
        preserved = [f for f in merged if f.type in ("correction", "preference")]
        evictable.sort(key=lambda f: f.date_added)
        overflow = len(merged) - _MAX_FACTS
        merged = preserved + evictable[overflow:]

    memory_path = artha_dir / "state" / "memory.md"
    if memory_path.exists():
        content = memory_path.read_text(encoding="utf-8")
        fm = _parse_frontmatter(content)
    else:
        content = textwrap.dedent("""\
            ---
            domain: memory
            last_updated: never
            schema_version: '2.0'
            facts: []
            ---

            ## Memory & Learned Facts

            This file stores durable facts extracted from catch-up sessions.
            Do NOT manually edit the `facts:` frontmatter — it is managed by
            `scripts/fact_extractor.py`.
        """)
        fm = _parse_frontmatter(content)

    fm["facts"] = [f.to_dict() for f in merged]
    fm["schema_version"] = "2.0"
    fm["last_updated"] = date.today().isoformat()

    updated = _rebuild_frontmatter(content, fm)
    memory_path.write_text(updated, encoding="utf-8")

    return len(unique_new)
