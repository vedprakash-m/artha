"""AR-9 External Agent Composition — Knowledge Extraction & Caching (§4.7).

Extracts novel insights from high-quality agent responses and caches them
to reduce future invocation frequency.

Cache location: tmp/ext-agent-cache/<agent-name>.md
Cache is platform-local; excluded from cloud sync (tmp/ is gitignored).
"""

from __future__ import annotations

import re
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Cache entry separator
_SEPARATOR = "---"

# Front-matter keys recognised in cache entries
_TTL_RE = re.compile(r"^TTL:\s*.+?\(expires:\s*(\S+)\)", re.MULTILINE)
_NEEDS_REVAL_RE = re.compile(r"^needs_revalidation:\s*true", re.MULTILINE | re.IGNORECASE)
_QUERY_RE = re.compile(r'^Query:\s*"(.+)"', re.MULTILINE)
_CONFIDENCE_RE = re.compile(r"^Confidence:\s*([0-9.]+)", re.MULTILINE)
_SOURCE_RE = re.compile(r"^Source:\s*(.+)", re.MULTILINE)

_DEFAULT_MIN_QUALITY = 0.7
_DEFAULT_TTL_DAYS = 7
_DEFAULT_MAX_SIZE_CHARS = 50_000


class KnowledgeExtractor:
    """Extract and cache insights from external agent responses.

    Usage::

        extractor = KnowledgeExtractor(cache_dir=Path("tmp/ext-agent-cache"),
                                       agent_name="storage-deployment-expert")
        extractor.extract_and_cache(response, query, quality_score=0.82)
        cached = extractor.read_cached("SDP block error")
    """

    def __init__(
        self,
        cache_dir: Path,
        agent_name: str,
        min_quality: float = _DEFAULT_MIN_QUALITY,
        ttl_days: int = _DEFAULT_TTL_DAYS,
        max_cache_size_chars: int = _DEFAULT_MAX_SIZE_CHARS,
    ) -> None:
        self._cache_dir = cache_dir
        self._agent_name = agent_name
        self._min_quality = min_quality
        self._ttl_days = ttl_days
        self._max_cache_size_chars = max_cache_size_chars

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def extract_and_cache(
        self,
        response: str,
        query: str,
        quality_score: float,
        kb_text: str = "",
    ) -> bool:
        """Attempt to extract novel knowledge and append to cache.

        Returns True if knowledge was cached, False if skipped.

        Extraction criteria (spec §4.7):
          - quality_score ≥ min_quality (default 0.7)
          - response contains ≥ 1 fact not present in local KB (novel check)
          - cache_dir is writable
        """
        if quality_score < self._min_quality:
            return False
        if not response or not response.strip():
            return False
        if not self._is_novel(response, kb_text):
            return False

        cache_file = self._cache_file()
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False

        entry = self._build_entry(response, query, quality_score)

        # Enforce max size with oldest-first eviction
        current = ""
        if cache_file.exists():
            try:
                current = cache_file.read_text(encoding="utf-8")
            except OSError:
                current = ""

        combined = current.rstrip() + ("\n\n" if current.strip() else "") + entry
        if len(combined) > self._max_cache_size_chars:
            combined = self._evict_oldest(combined)

        try:
            cache_file.write_text(combined, encoding="utf-8")
        except OSError:
            return False

        return True

    # ------------------------------------------------------------------
    # Read path (used by AgentRouter before invocation)
    # ------------------------------------------------------------------

    def read_cached(self, query: str) -> str | None:
        """Return cached knowledge relevant to query, or None if no hit.

        Implements stale-while-revalidate:
        - Fresh entry: return immediately.
        - Expired entry: return stale content + mark needs_revalidation=true
          so the next actual invocation can replace it.
        - No entry: return None.
        """
        cache_file = self._cache_file()
        if not cache_file.exists():
            return None

        try:
            text = cache_file.read_text(encoding="utf-8")
        except OSError:
            return None

        entries = _split_entries(text)
        query_words = _significant_words(query)
        if not query_words:
            return None

        best_entry: str | None = None
        best_overlap = 0.0
        best_is_stale = False

        for entry in entries:
            overlap = _query_overlap(entry, query_words)
            if overlap < 0.3:
                continue
            if overlap > best_overlap:
                best_overlap = overlap
                best_entry = entry
                best_is_stale = _is_expired(entry)

        if best_entry is None:
            return None

        if best_is_stale:
            # Mark for revalidation on disk
            self._mark_needs_revalidation(cache_file, best_entry)
            # Still return the stale content (stale-while-revalidate)
            return best_entry.strip() + "\n\n*(Note: This cached knowledge may be outdated.)*"

        return best_entry.strip()

    def needs_revalidation(self, query: str) -> bool:
        """True if the best matching cache entry is stale and needs refresh."""
        cache_file = self._cache_file()
        if not cache_file.exists():
            return False
        try:
            text = cache_file.read_text(encoding="utf-8")
        except OSError:
            return False
        for entry in _split_entries(text):
            if _NEEDS_REVAL_RE.search(entry):
                if _query_overlap(entry, _significant_words(query)) >= 0.3:
                    return True
        return False

    def invalidate(self) -> None:
        """Remove the entire cache file (called on registry agent update)."""
        cache_file = self._cache_file()
        if cache_file.exists():
            try:
                cache_file.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cache_file(self) -> Path:
        return self._cache_dir / f"{self._agent_name}.md"

    def _build_entry(self, response: str, query: str, quality: float) -> str:
        """Build a single cache entry in spec format."""
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=self._ttl_days)
        topic = _extract_topic(query)
        entry = textwrap.dedent(f"""\
            ## {topic} — cached {now.strftime('%Y-%m-%dT%H:%M:%SZ')}
            Source: external-agent/{self._agent_name}
            Query: "{_truncate(query, 120)}"
            Confidence: {quality:.2f}
            TTL: {self._ttl_days} days (expires: {expires.strftime('%Y-%m-%d')})

            {response.strip()}

            {_SEPARATOR}
        """)
        return entry

    def _is_novel(self, response: str, kb_text: str) -> bool:
        """Check if response contains at least one sentence not in KB."""
        if not kb_text:
            return True  # No KB to compare — assume novel
        kb_lower = kb_text.lower()
        sentences = re.split(r"(?<=[.!?])\s+", response)
        for s in sentences:
            s = s.strip()
            if len(s) < 30:
                continue
            # Heuristic: check if the first 40 chars are absent from KB
            fingerprint = s[:40].lower().strip()
            if len(fingerprint) > 10 and fingerprint not in kb_lower:
                return True
        return False

    def _evict_oldest(self, combined: str) -> str:
        """Evict the oldest entries until combined fits max size."""
        entries = _split_entries(combined)
        while entries and len(_join_entries(entries)) > self._max_cache_size_chars:
            entries.pop(0)  # remove oldest (first)
        return _join_entries(entries)

    def _mark_needs_revalidation(self, cache_file: Path, entry: str) -> None:
        """Append needs_revalidation: true to the matching entry on disk."""
        try:
            text = cache_file.read_text(encoding="utf-8")
        except OSError:
            return
        # Only mark once
        if entry in text and "needs_revalidation: true" not in entry:
            updated = text.replace(
                entry,
                entry.rstrip() + "\nneeds_revalidation: true\n",
                1,
            )
            try:
                cache_file.write_text(updated, encoding="utf-8")
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _split_entries(text: str) -> list[str]:
    """Split a cache file into individual entries by separator lines."""
    raw_entries = re.split(r"(?m)^---\s*$", text)
    return [e.strip() for e in raw_entries if e.strip()]


def _join_entries(entries: list[str]) -> str:
    return "\n\n---\n\n".join(entries) + "\n\n---\n"


_STOPWORDS = frozenset(
    "a an and are as at be by for from have how i in is it its "
    "of on or that the this to was what will with you your".split()
)


def _significant_words(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_-]{3,}", text.lower())
    return [w for w in words if w not in _STOPWORDS]


def _query_overlap(entry: str, query_words: list[str]) -> float:
    if not query_words:
        return 0.0
    entry_lower = entry.lower()
    hits = sum(1 for w in query_words if w in entry_lower)
    return hits / len(query_words)


def _is_expired(entry: str) -> bool:
    """True if the entry's TTL expiry date is in the past."""
    m = _TTL_RE.search(entry)
    if not m:
        return False
    try:
        expiry = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expiry
    except ValueError:
        return False


def _extract_topic(query: str) -> str:
    """Generate a short topic headline from a query string."""
    words = query.split()[:6]
    topic = " ".join(words)
    if len(topic) > 60:
        topic = topic[:57] + "..."
    return topic or "General knowledge"


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
