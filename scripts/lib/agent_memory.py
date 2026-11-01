# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/agent_memory.py — Per-agent memory system (EAR-1).

Implements compound learning across invocations:
  - Write: append memory entry to daily log after each invocation
  - Curate: distill daily log → curated memory.md when log > 2KB
  - Load: select relevant entries (TF-IDF cosine) for context injection
  - Evict: prune entries >14 days, contradiction eviction

Data model:
  tmp/ext-agent-memory/<agent-name>/
  ├── memory.md          # Curated long-term memory (max 4KB)
  └── daily/
      └── YYYY-MM-DD.md  # Raw invocation notes (auto-pruned >14 days)

Memory curation uses durability × quality ranking (Sonnet v2 R-7):
  - durability_score: inverse of temporal specificity (date/version-stamped
    facts decay; general diagnostic patterns persist)
  - quality_score: from scorer (0–1)
  - Rank = durability × quality; top entries go into curated memory.md

Thread safety: per-agent threading.Lock held during write + curate.

Ref: specs/ext-agent-reloaded.md §EAR-1, Sonnet v2 R-7
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MEMORY_DIR = (
    Path(__file__).resolve().parent.parent.parent / "tmp" / "ext-agent-memory"
)

_MAX_MEMORY_BYTES = 4_096      # Curated memory.md size cap
_MAX_DAILY_BYTES = 2_048       # Trigger curate when daily log exceeds this
_MAX_DAILY_DAYS = 14           # Prune daily files older than this
_MAX_CURATED_ENTRIES = 20      # Maximum entries in memory.md
_MAX_LOAD_ENTRIES = 5          # Max entries loaded per compose() call
_LOAD_MAX_CHARS = 1_200        # Budget for memory in prompt context

_SCHEMA_VERSION = "1.0"

# Temporal-specificity patterns → high specificity → lower durability
_TEMPORAL_PATTERNS = re.compile(
    r'\b(20\d\d[-/]\d{2}|v\d+\.\d+|build \d+|sprint \d+|IcM-?\d{7,}|'
    r'yesterday|last week|this morning|today)\b',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Per-agent lock registry
# ---------------------------------------------------------------------------

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _get_lock(agent_name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if agent_name not in _LOCKS:
            _LOCKS[agent_name] = threading.Lock()
        return _LOCKS[agent_name]


# ---------------------------------------------------------------------------
# TF-IDF helpers (stdlib-only)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())


def _tf(tokens: list[str]) -> dict[str, float]:
    total = max(len(tokens), 1)
    tf: dict[str, float] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    return {t: c / total for t, c in tf.items()}


def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    dot = sum(vec_a.get(t, 0.0) * f for t, f in vec_b.items())
    mag_a = math.sqrt(sum(f ** 2 for f in vec_a.values())) or 1.0
    mag_b = math.sqrt(sum(f ** 2 for f in vec_b.values())) or 1.0
    return dot / (mag_a * mag_b)


def _relevance_score(query: str, entry_text: str) -> float:
    """Simple TF cosine similarity for relevance ranking."""
    q_tf = _tf(_tokenize(query))
    e_tf = _tf(_tokenize(entry_text))
    return _cosine_similarity(q_tf, e_tf)


# ---------------------------------------------------------------------------
# Durability scoring (R-7)
# ---------------------------------------------------------------------------

def _durability_score(text: str) -> float:
    """Score 0–1: high = durable diagnostic pattern; low = temporal/specific event."""
    temporal_hits = len(_TEMPORAL_PATTERNS.findall(text))
    # Each temporal-specific token reduces durability by 0.1, floor 0.2
    return max(0.2, 1.0 - temporal_hits * 0.1)


# ---------------------------------------------------------------------------
# Entry parsing
# ---------------------------------------------------------------------------

_ENTRY_HEADER_RE = re.compile(r'^## \[(\d{2}:\d{2})\] Query: "(.+)"', re.MULTILINE)


def _parse_entries(text: str) -> list[dict]:
    """Parse a daily log or memory.md into a list of entry dicts."""
    entries = []
    for m in _ENTRY_HEADER_RE.finditer(text):
        start = m.start()
        # Find next entry header or end of string
        next_m = _ENTRY_HEADER_RE.search(text, m.end())
        end = next_m.start() if next_m else len(text)
        body = text[start:end].strip()
        entries.append({
            "time": m.group(1),
            "query": m.group(2),
            "text": body,
        })
    return entries


# ---------------------------------------------------------------------------
# AgentMemory
# ---------------------------------------------------------------------------

class AgentMemory:
    """Per-agent memory: write, curate, load, evict.

    Parameters:
        agent_name: Agent identifier (used for subdirectory naming).
        memory_dir: Base directory override (for testing).
    """

    def __init__(self, agent_name: str, memory_dir: Path | None = None) -> None:
        self._agent = agent_name
        self._base = (memory_dir or _MEMORY_DIR) / agent_name
        self._daily_dir = self._base / "daily"
        self._memory_file = self._base / "memory.md"
        self._lock = _get_lock(agent_name)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_entry(
        self,
        query: str,
        quality_score: float,
        key_finding: str = "",
        lesson: str = "",
        user_correction: str = "",
        kb_corroborations: int = 0,
    ) -> None:
        """Append one invocation memory entry to today's daily log.

        Fire-and-forget: OSError is logged but not re-raised.
        Thread-safe via per-agent lock.
        """
        try:
            with self._lock:
                self._daily_dir.mkdir(parents=True, exist_ok=True)
                today = date.today().isoformat()
                daily_file = self._daily_dir / f"{today}.md"

                confidence = (
                    "HIGH" if quality_score >= 0.75
                    else ("MED" if quality_score >= 0.5 else "LOW")
                )
                correction_line = user_correction.strip() or "none"
                ts = datetime.now(timezone.utc).strftime("%H:%M")

                entry = (
                    f"\n## [{ts}] Query: \"{query[:120]}\"\n"
                    f"- Quality: {quality_score:.2f} | KB corr: {kb_corroborations} | "
                    f"Confidence: {confidence}\n"
                    f"- Key finding: {key_finding or '(none)'}\n"
                    f"- User correction: {correction_line}\n"
                    f"- Lesson: {lesson or '(none)'}\n"
                )

                with daily_file.open("a", encoding="utf-8") as fh:
                    fh.write(entry)

                # Trigger curate if daily log is too large
                if daily_file.stat().st_size > _MAX_DAILY_BYTES:
                    self._curate()

        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Curate (R-7: durability × quality ranking)
    # ------------------------------------------------------------------

    def _curate(self) -> None:
        """Distill all daily logs → curated memory.md.

        Ranking: durability_score × quality_score (higher = keep).
        Cap: _MAX_CURATED_ENTRIES entries, _MAX_MEMORY_BYTES total.
        Must be called under self._lock.
        """
        try:
            all_entries: list[dict] = []

            # Read all daily logs within retention window
            cutoff = date.today() - timedelta(days=_MAX_DAILY_DAYS)
            for daily_file in sorted(self._daily_dir.glob("*.md")):
                try:
                    file_date = date.fromisoformat(daily_file.stem)
                except ValueError:
                    continue
                if file_date < cutoff:
                    # Past retention window — delete
                    daily_file.unlink(missing_ok=True)
                    continue
                text = daily_file.read_text(encoding="utf-8", errors="ignore")
                for e in _parse_entries(text):
                    e["file_date"] = file_date.isoformat()
                    # Extract quality from entry text
                    q_match = re.search(r'Quality:\s*([\d.]+)', e["text"])
                    e["quality_score"] = float(q_match.group(1)) if q_match else 0.5
                    e["durability"] = _durability_score(e["text"])
                    e["rank"] = e["durability"] * e["quality_score"]
                    all_entries.append(e)

            if not all_entries:
                return

            # Sort by rank DESC; dedup near-duplicate queries
            all_entries.sort(key=lambda x: -x["rank"])
            seen_queries: set[str] = set()
            curated: list[dict] = []
            for e in all_entries:
                q_norm = e["query"][:60].lower()
                if q_norm in seen_queries:
                    continue
                seen_queries.add(q_norm)
                curated.append(e)
                if len(curated) >= _MAX_CURATED_ENTRIES:
                    break

            # Write curated memory.md
            lines = [
                f"---\nschema_version: \"{_SCHEMA_VERSION}\"\n---\n",
                f"# Curated Memory — {self._agent}\n\n",
                f"_Last curated: {date.today().isoformat()} | "
                f"{len(curated)} entries_\n\n",
            ]
            total_chars = sum(len(l) for l in lines)

            for e in curated:
                entry_text = e["text"] + "\n"
                if total_chars + len(entry_text) > _MAX_MEMORY_BYTES:
                    break
                lines.append(entry_text)
                total_chars += len(entry_text)

            import os, tempfile  # noqa: E401
            content = "".join(lines)
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=self._base, prefix=".memory_tmp_", suffix=".md"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                    fh.write(content)
                os.replace(tmp_path, self._memory_file)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load_relevant(self, query: str, max_chars: int = _LOAD_MAX_CHARS) -> str:
        """Load most relevant memory entries for context injection.

        Returns formatted markdown block (≤ max_chars) or empty string.
        Relevance ranked by TF-IDF cosine similarity to query.
        """
        try:
            all_entries: list[dict] = []

            # Load from curated memory.md
            if self._memory_file.exists():
                text = self._memory_file.read_text(encoding="utf-8", errors="ignore")
                all_entries.extend(_parse_entries(text))

            # Load from today's daily log
            today_file = self._daily_dir / f"{date.today().isoformat()}.md"
            if today_file.exists():
                text = today_file.read_text(encoding="utf-8", errors="ignore")
                all_entries.extend(_parse_entries(text))

            if not all_entries:
                return ""

            # Rank by relevance
            scored = [
                (e, _relevance_score(query, e["text"]))
                for e in all_entries
            ]
            scored.sort(key=lambda x: -x[1])

            lines = ["## Agent Memory (prior invocations)\n"]
            total = len(lines[0])
            shown = 0
            for entry, score in scored[:_MAX_LOAD_ENTRIES]:
                if score < 0.05:
                    break
                block = entry["text"] + "\n"
                if total + len(block) > max_chars:
                    break
                lines.append(block)
                total += len(block)
                shown += 1

            if shown == 0:
                return ""

            return "\n".join(lines)

        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------
    # Evict
    # ------------------------------------------------------------------

    def evict_stale(self) -> int:
        """Delete daily files older than _MAX_DAILY_DAYS. Returns count deleted."""
        try:
            with self._lock:
                cutoff = date.today() - timedelta(days=_MAX_DAILY_DAYS)
                removed = 0
                for f in self._daily_dir.glob("*.md"):
                    try:
                        if date.fromisoformat(f.stem) < cutoff:
                            f.unlink(missing_ok=True)
                            removed += 1
                    except ValueError:
                        pass
                return removed
        except Exception:  # noqa: BLE001
            return 0

    def clear(self) -> None:
        """Remove all memory files for this agent (operator action)."""
        import shutil
        try:
            with self._lock:
                shutil.rmtree(self._base, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
