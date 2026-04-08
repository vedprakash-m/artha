# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/tfidf_router.py — Enhanced Lexical Routing via TF-IDF character trigrams (EAR-4).

Implements the TF-IDF fallback tier of the two-tier routing system:
  Tier 1: Existing keyword router (agent_router.py) — <10ms, exact match
  Tier 2: This module — TF-IDF character trigrams — <50ms, morphological similarity

Naming: "Enhanced Lexical Routing" — TF-IDF character trigrams are a
deterministic bag-of-characters similarity measure, NOT neural embeddings.
Expectation: handles morphological variants ("deploy" → "deployed",
"deploying", "deployment") but misses semantic paraphrases.

Algorithm:
  1. Precompute TF-IDF vectors for each agent's description + keywords corpus.
  2. On query: compute query TF-IDF vector.
  3. Cosine similarity against each agent vector.
  4. Filter by semantic_min_confidence.
  5. Return top-N matches sorted by similarity.

Vectors are cached in tmp/ext-agent-route-vectors.json.
Vectors auto-recomputed when agents are registered/registered.

Upgrade path (V2.1): when median confidence margin < 0.10 over 7 days,
surface heartbeat alert recommending local embedding upgrade.

Thread safety: vector rebuild uses a module-level lock.

Ref: specs/ext-agent-reloaded.md §EAR-4, DD-EAR-4
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_VECTOR_CACHE_FILE = (
    Path(__file__).resolve().parent.parent.parent
    / "tmp"
    / "ext-agent-route-vectors.json"
)

_REBUILD_LOCK = threading.Lock()

# Character trigram window
_NGRAM_N = 3


# ---------------------------------------------------------------------------
# Trigram TF-IDF
# ---------------------------------------------------------------------------

def _char_ngrams(text: str, n: int = _NGRAM_N) -> list[str]:
    """Extract character n-grams from lowercased, cleaned text."""
    clean = re.sub(r'[^a-z0-9]', ' ', text.lower())
    # Generate n-grams over the full token string (no padding)
    return [clean[i:i+n] for i in range(max(0, len(clean) - n + 1))]


def _tf_vector(ngrams: list[str]) -> dict[str, float]:
    total = max(len(ngrams), 1)
    counts: dict[str, float] = {}
    for ng in ngrams:
        counts[ng] = counts.get(ng, 0) + 1
    return {ng: c / total for ng, c in counts.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    dot = sum(a.get(k, 0.0) * v for k, v in b.items())
    mag_a = math.sqrt(sum(v ** 2 for v in a.values())) or 1.0
    mag_b = math.sqrt(sum(v ** 2 for v in b.values())) or 1.0
    return dot / (mag_a * mag_b)


def _text_to_vec(text: str) -> dict[str, float]:
    return _tf_vector(_char_ngrams(text))


# ---------------------------------------------------------------------------
# Vector cache I/O
# ---------------------------------------------------------------------------

def _load_vector_cache(cache_file: Path | None = None) -> dict[str, dict]:
    """Load precomputed vectors from JSON cache. Returns {} if missing/corrupt."""
    target = cache_file or _VECTOR_CACHE_FILE
    try:
        if target.exists():
            return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_vector_cache(vectors: dict[str, dict], cache_file: Path | None = None) -> None:
    """Atomically write vector cache. Never raises."""
    target = cache_file or _VECTOR_CACHE_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=target.parent, prefix=".vectors_tmp_", suffix=".json"
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(vectors, fh)
        os.replace(tmp_path, target)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class LexicalMatch(NamedTuple):
    agent_name: str
    similarity: float
    source: str = "tfidf"


# ---------------------------------------------------------------------------
# TFIDFRouter
# ---------------------------------------------------------------------------

class TFIDFRouter:
    """Lexical fallback router using TF-IDF character trigrams.

    Usage::
        router = TFIDFRouter()
        router.rebuild(registry)   # precompute vectors
        matches = router.query("nodes not progressing", top_n=3, min_sim=0.1)
    """

    def __init__(self, cache_file: Path | None = None) -> None:
        self._cache_file = cache_file or _VECTOR_CACHE_FILE
        self._vectors: dict[str, dict[str, float]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            raw = _load_vector_cache(self._cache_file)
            self._vectors = {
                name: {k: float(v) for k, v in vec.items()}
                for name, vec in raw.items()
            }
            self._loaded = True

    def rebuild(self, registry) -> int:
        """Recompute TF-IDF vectors for all active agents.

        Called on agent registration / rediscovery.
        Returns count of agents vectorised.
        Thread-safe via module-level lock.
        """
        with _REBUILD_LOCK:
            vectors: dict[str, dict[str, float]] = {}
            try:
                agents = list(registry.active_agents())
            except Exception:
                return 0

            for agent in agents:
                corpus_parts = []
                corpus_parts.append(getattr(agent, "description", "") or "")
                corpus_parts.append(getattr(agent, "label", "") or "")
                routing = getattr(agent, "routing", None)
                if routing:
                    keywords = getattr(routing, "keywords", []) or []
                    corpus_parts.extend(keywords)
                    domains = getattr(routing, "domains", []) or []
                    corpus_parts.extend(domains)

                corpus = " ".join(corpus_parts)
                if corpus.strip():
                    vectors[agent.name] = _text_to_vec(corpus)

            _save_vector_cache(vectors, self._cache_file)
            self._vectors = vectors
            self._loaded = True
            return len(vectors)

    def query(
        self,
        query_text: str,
        top_n: int = 3,
        min_sim: float = 0.1,
    ) -> list[LexicalMatch]:
        """Query TF-IDF vectors for top-N similar agents.

        Returns list of LexicalMatch sorted by similarity DESC.
        """
        self._ensure_loaded()

        if not self._vectors:
            return []

        query_vec = _text_to_vec(query_text)
        scores: list[LexicalMatch] = []

        for agent_name, agent_vec in self._vectors.items():
            sim = _cosine(query_vec, agent_vec)
            if sim >= min_sim:
                scores.append(LexicalMatch(
                    agent_name=agent_name,
                    similarity=sim,
                ))

        scores.sort(key=lambda m: -m.similarity)
        return scores[:top_n]

    def is_ready(self) -> bool:
        """True if vector cache exists and has at least one entry."""
        self._ensure_loaded()
        return len(self._vectors) > 0


# ---------------------------------------------------------------------------
# Blueprint 4 — UNCLASSIFIED queue + per-signal confidence telemetry
# ---------------------------------------------------------------------------

class RoutingDecision(NamedTuple):
    signal_id: str
    matched_domain: str
    confidence: float
    tier: str  # "tfidf" | "unclassified"


def route_with_unclassified(
    signals: list[dict],
    *,
    router: "TFIDFRouter | None" = None,
    min_sim: float = 0.1,
    top_n: int = 1,
) -> tuple[list[RoutingDecision], list[dict]]:
    """Route a batch of signals, segregating low-confidence ones.

    Each signal dict must have:
        signal_id (str)   — stable identifier for telemetry correlation
        text      (str)   — query text fed to TF-IDF similarity

    Returns:
        (classified: list[RoutingDecision], unclassified: list[dict])

    ``classified`` entries have confidence >= ``min_sim``.
    ``unclassified`` entries are the raw signal dicts that scored below threshold.

    Per-signal telemetry is emitted for *every* signal regardless of result:
        classified  → event = ``routing.classified``
        unclassified → event = ``routing.unclassified``

    Migration note (spec §Blueprint-4):
        Keep min_sim=0.1 until ≥14 days of telemetry confirm no regression,
        then raise to 0.4.  The UNCLASSIFIED queue surfaces the signals that
        would be dropped by a higher threshold so operators can review them.
    """
    if router is None:
        router = TFIDFRouter()

    try:
        from scripts.lib.telemetry import emit_routing  # lazy, non-fatal
        _emit = emit_routing
    except Exception:
        def _emit(*_a, **_kw) -> None:  # type: ignore[misc]
            pass

    classified: list[RoutingDecision] = []
    unclassified: list[dict] = []

    for signal in signals:
        signal_id: str = str(signal.get("signal_id", ""))
        text: str = str(signal.get("text", ""))

        matches = router.query(text, top_n=top_n, min_sim=min_sim)

        if matches:
            best = matches[0]
            decision = RoutingDecision(
                signal_id=signal_id,
                matched_domain=best.agent_name,
                confidence=round(best.similarity, 4),
                tier="tfidf",
            )
            classified.append(decision)
            _emit(
                signal_id=signal_id,
                matched_domain=best.agent_name,
                confidence=round(best.similarity, 4),
                tier="tfidf",
            )
        else:
            unclassified.append(signal)
            _emit(
                signal_id=signal_id,
                matched_domain="UNCLASSIFIED",
                confidence=0.0,
                tier="unclassified",
            )

    return classified, unclassified

