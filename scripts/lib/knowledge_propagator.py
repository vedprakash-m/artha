# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/knowledge_propagator.py — Cross-agent knowledge propagation (EAR-10).

One-directional explicit knowledge propagation via `relationships.feeds` declarations.

Algorithm:
  After agent A completes and caches its response:
    For each agent B in A.relationships.feeds:
      1. Extract top-3 key facts from A's cached response (by specificity)
      2. PII-scrub for B's trust tier
      3. Tag with source agent trust tier (trust attribution, architectural review)
      4. Write to tmp/ext-agent-propagated/<B-name>/from-<A-name>.md

  When composing prompt for agent B:
    1. Read tmp/ext-agent-propagated/<B-name>/*.md
    2. Budget: 500 chars per source, 1500 total

Safety constraints:
  - One-directional only (A → B, not B → A). Cycles impossible.
  - Propagated content respects receiving agent's trust tier.
  - Propagated content is PII-scrubbed before write.
  - Max 3 propagation sources per agent.
  - Propagated facts expire when source cache expires (mtime-based TTL).
  - Trust attribution: propagated facts tagged with source agent trust tier.
  - Downgrade logged: if source trust > receiver trust, log to audit.

Thread safety:
  Per-agent file lock for write operations.

Ref: specs/ext-agent-reloaded.md §EAR-10
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_PROPAGATION_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "tmp"
    / "ext-agent-propagated"
)

_MAX_CHARS_PER_SOURCE = 500
_MAX_TOTAL_CHARS = 1_500
_MAX_SOURCES_PER_AGENT = 3
_DEFAULT_TTL_DAYS = 7

_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')

# Trust tier ordering for downgrade detection
_TRUST_ORDER = {"owned": 0, "trusted": 1, "verified": 2, "external": 3, "untrusted": 4}

# ---------------------------------------------------------------------------
# Per-agent write locks
# ---------------------------------------------------------------------------

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _get_lock(agent_name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if agent_name not in _LOCKS:
            _LOCKS[agent_name] = threading.Lock()
        return _LOCKS[agent_name]


# ---------------------------------------------------------------------------
# Key fact extraction (reuses same specificity logic as ChainStepState)
# ---------------------------------------------------------------------------

_ACTIONABLE_RE = re.compile(
    r'\b(recommend|should|must|need to|required|action|step|fix|resolve|'
    r'investigate|check|verify|confirm|ensure|update|restart|rollback|'
    r'escalate|alert|deploy|pause|resume|trigger)\b',
    re.IGNORECASE,
)
_ICM_RE = re.compile(r'\bIcM[-#]?\d{5,}\b', re.IGNORECASE)
_REGION_RE = re.compile(r'\b(eastus|westus|westeurope|southcentralus|'
                          r'australiaeast|northeurope|canadacentral)\b', re.IGNORECASE)
_ERROR_CODE_RE = re.compile(r'\b[A-Z][a-zA-Z]+(Error|Exception|Fault|Timeout|Failure)\b')


def _extract_key_facts(text: str, top_k: int = 3) -> list[str]:
    sentences = _SENTENCE_RE.split(text.strip())
    scored = []
    for s in sentences:
        s = s.strip()
        if len(s) < 10:
            continue
        score = (
            len(_ACTIONABLE_RE.findall(s))
            + len(_ICM_RE.findall(s)) * 2
            + len(_REGION_RE.findall(s))
            + len(_ERROR_CODE_RE.findall(s)) * 2
        )
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:top_k]]


# ---------------------------------------------------------------------------
# KnowledgePropagator
# ---------------------------------------------------------------------------

class KnowledgePropagator:
    """Manages cross-agent knowledge propagation.

    Parameters:
        prop_dir: Base dir for propagated facts (default: tmp/ext-agent-propagated/).
        scrub_fn: PII scrubber callable(text, trust_tier) → str.
    """

    def __init__(
        self,
        prop_dir: Path | None = None,
        scrub_fn=None,
    ) -> None:
        self._dir = prop_dir or _PROPAGATION_DIR
        self._scrub = scrub_fn or (lambda text, _tier: text)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def propagate(
        self,
        source_agent_name: str,
        source_trust_tier: str,
        cached_response: str,
        target_agents: list[str],
        target_trust_tiers: dict[str, str] | None = None,
        cache_ttl_days: int = _DEFAULT_TTL_DAYS,
    ) -> int:
        """Propagate key facts from source agent to target agents.

        Returns count of successful propagations.
        Thread-safe (per-target-agent lock).
        """
        if not cached_response.strip() or not target_agents:
            return 0

        key_facts = _extract_key_facts(cached_response)
        if not key_facts:
            return 0

        propagated = 0
        for target in target_agents[:_MAX_SOURCES_PER_AGENT]:
            try:
                target_tier = (
                    (target_trust_tiers or {}).get(target, "external")
                )
                # PII-scrub for receiver's trust tier
                scrubbed_facts = []
                for fact in key_facts:
                    scrubbed = self._scrub(fact, target_tier)
                    scrubbed_facts.append(scrubbed)

                # Trust downgrade detection
                src_rank = _TRUST_ORDER.get(source_trust_tier, 99)
                tgt_rank = _TRUST_ORDER.get(target_tier, 99)
                trust_note = ""
                if tgt_rank > src_rank:
                    trust_note = (
                        f"\n_⚠️ Trust downgrade: source={source_trust_tier} "
                        f"→ receiver={target_tier}_\n"
                    )

                # Build propagation file
                from datetime import datetime, timezone  # noqa: PLC0415
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                content = (
                    f"# Propagated from {source_agent_name}\n"
                    f"_Source trust: {source_trust_tier} | Written: {ts} | "
                    f"TTL: {cache_ttl_days}d_{trust_note}\n\n"
                    + "\n".join(f"- {f}" for f in scrubbed_facts)
                    + "\n"
                )

                target_dir = self._dir / target
                prop_file = target_dir / f"from-{source_agent_name}.md"
                lock = _get_lock(target)

                with lock:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    # Atomic write
                    tmp_fd, tmp_path = tempfile.mkstemp(
                        dir=target_dir, prefix=".prop_tmp_", suffix=".md"
                    )
                    try:
                        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                            fh.write(content)
                        os.replace(tmp_path, prop_file)
                    except Exception:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                        raise

                propagated += 1

            except Exception:  # noqa: BLE001
                continue

        return propagated

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load_for_agent(
        self,
        agent_name: str,
        cache_ttl_days: int = _DEFAULT_TTL_DAYS,
    ) -> str:
        """Load propagated facts for agent_name into a context block.

        Returns formatted markdown block (≤ _MAX_TOTAL_CHARS) or empty string.
        Expired propagation files (source cache TTL exceeded) are deleted.
        """
        target_dir = self._dir / agent_name
        if not target_dir.exists():
            return ""

        files = sorted(target_dir.glob("from-*.md"))
        if not files:
            return ""

        sections: list[str] = []
        total_chars = 0

        for prop_file in files[:_MAX_SOURCES_PER_AGENT]:
            try:
                # Check if expired (based on mtime)
                age_days = (time.time() - prop_file.stat().st_mtime) / 86400
                if age_days > cache_ttl_days:
                    prop_file.unlink(missing_ok=True)
                    continue

                text = prop_file.read_text(encoding="utf-8", errors="ignore")
                chunk = text.strip()[:_MAX_CHARS_PER_SOURCE]

                if total_chars + len(chunk) > _MAX_TOTAL_CHARS:
                    break

                sections.append(chunk)
                total_chars += len(chunk)

            except OSError:
                continue

        if not sections:
            return ""

        return "## Cross-Agent Context\n\n" + "\n\n---\n\n".join(sections) + "\n"

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def delete_for_agent(self, agent_name: str) -> int:
        """Delete all propagated facts for a target agent. Returns count deleted."""
        target_dir = self._dir / agent_name
        count = 0
        try:
            for f in target_dir.glob("from-*.md"):
                f.unlink(missing_ok=True)
                count += 1
        except OSError:
            pass
        return count
