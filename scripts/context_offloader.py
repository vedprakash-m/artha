#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/context_offloader.py — Context offloading for Artha catch-up workflow.

Writes large intermediate artifacts to tmp/ files and returns a compact
summary card suitable for inclusion in the AI's context window.

Phase 1 of the Deep Agents Architecture adoption (specs/deep-agents.md §5 Phase 1).

When a data artifact exceeds the configured token threshold, it is written to
tmp/{name}.json (or .jsonl for lists of dicts) and replaced in the AI context
with a compact summary card containing key stats and a preview.

Usage:
    from context_offloader import offload_artifact

    card = offload_artifact(
        name="pipeline_output",
        data=jsonl_records,
        summary_fn=lambda recs: f"{len(recs)} records across {len({r['source'] for r in recs})} sources",
    )
    # card is a short string (< 500 tokens) with path + key stats

Config flag: harness.context_offloading.enabled (default: true)
When disabled, returns serialized data directly without writing any file.

Ref: specs/deep-agents.md Phase 1
"""
from __future__ import annotations

import json
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable

from lib.common import ARTHA_DIR

_CHARS_PER_TOKEN = 4  # heuristic: 1 token ≈ 4 chars
_MAX_CARD_TOKENS = 500  # summary card must never exceed this

# ---------------------------------------------------------------------------
# Tiered eviction — Phase 2 (specs/agentic-improve.md)
# ---------------------------------------------------------------------------


class EvictionTier(IntEnum):
    """Artifact eviction priority.  Lower value = higher preservation priority (evicted LAST)."""

    PINNED = 0       # Never offload (e.g., session summary card)
    CRITICAL = 1     # Keep in-context at normal pressure; offload only at CRITICAL pressure
    INTERMEDIATE = 2  # Default; offload at base threshold
    EPHEMERAL = 3    # Evict first — aggressive threshold (pipeline JSONL, raw emails)


# Threshold multipliers per tier (applied to the caller-supplied threshold_tokens).
# float("inf") means never offload.
_TIER_THRESHOLDS: dict[EvictionTier, float] = {
    EvictionTier.PINNED: float("inf"),   # Never offload
    EvictionTier.CRITICAL: 1.0,          # Use base threshold
    EvictionTier.INTERMEDIATE: 1.0,      # Use base threshold
    EvictionTier.EPHEMERAL: 0.4,         # Aggressive: 40% of base (≈2K at default 5K)
}

# Default tier assignments for known artifact names.
# Any unregistered name receives INTERMEDIATE.
_ARTIFACT_TIERS: dict[str, EvictionTier] = {
    "pipeline_output": EvictionTier.EPHEMERAL,
    "processed_emails": EvictionTier.EPHEMERAL,
    "domain_extractions": EvictionTier.INTERMEDIATE,
    "cross_domain_analysis": EvictionTier.INTERMEDIATE,
    "alert_list": EvictionTier.CRITICAL,
    "one_thing": EvictionTier.CRITICAL,
    "compound_signals": EvictionTier.CRITICAL,
    "session_summary": EvictionTier.PINNED,
}

# Registry of all filenames/patterns that context_offloader may write to tmp/.
# Step 18 ephemeral cleanup uses this manifest to ensure complete removal.
OFFLOADED_FILES: list[str] = [
    "pipeline_output.jsonl",
    "processed_emails.json",
    "domain_extractions",  # directory — remove recursively
    "cross_domain_analysis.json",
    ".checkpoint.json",    # Phase 4: step checkpoint marker
]
# Session history files match pattern: session_history_N.md (written by session_summarizer)
OFFLOADED_GLOB_PATTERNS: list[str] = [
    "session_history_*.md",
]


def _estimate_tokens(text: str) -> int:
    """Estimate token count using the 1 token ≈ 4 chars heuristic."""
    return len(text) // _CHARS_PER_TOKEN


def load_harness_flag(feature_path: str, default: bool = True) -> bool:
    """Read a nested boolean feature flag from config/artha_config.yaml.

    Args:
        feature_path: dot-separated path under the ``harness:`` key,
            e.g. ``"context_offloading.enabled"``
        default: value returned when the key is missing or the file cannot
            be read (defaults to ``True`` so new flags are opt-out)

    Returns:
        bool: the configured value, or ``default`` on any error
    """
    try:
        from lib.config_loader import load_config  # noqa: PLC0415

        cfg = load_config("artha_config")
        harness = cfg.get("harness", {})
        parts = feature_path.split(".")
        cur: Any = harness
        for part in parts:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(part, None)
            if cur is None:
                return default
        if isinstance(cur, bool):
            return cur
        return default
    except Exception:  # noqa: BLE001
        return default


def _serialize(data: Any) -> tuple[str, str]:
    """Serialize data to string and determine file extension.

    Returns:
        (serialized_text, file_extension) — extension includes the leading dot.
    """
    is_jsonl = isinstance(data, list) and all(isinstance(item, dict) for item in data)
    if is_jsonl:
        serialized = "\n".join(
            json.dumps(rec, ensure_ascii=False, default=str) for rec in data
        )
        return serialized, ".jsonl"
    if isinstance(data, (dict, list)):
        return json.dumps(data, ensure_ascii=False, indent=2, default=str), ".json"
    return str(data), ".txt"


def _build_card(
    name: str,
    out_path: Path,
    serialized: str,
    stats: str,
    estimated_tokens: int,
    preview_lines: int,
) -> str:
    """Build the summary card that replaces the artifact in the AI context."""
    lines = serialized.splitlines()
    n_preview = min(preview_lines, len(lines))
    preview_block = "\n".join(f"   {ln}" for ln in lines[:n_preview])

    card_lines = [
        f"📦 OFFLOADED: {name}",
        f"   Path:    {out_path}",
        f"   Size:    ~{estimated_tokens:,} tokens ({len(serialized):,} chars)",
        f"   Stats:   {stats}",
        "",
        f"   Preview (lines 1–{n_preview} of {len(lines)}):",
        "   " + "─" * 60,
        preview_block,
        "   " + "─" * 60,
        f"   ➜ Read {out_path} for full details.",
    ]
    card = "\n".join(card_lines)

    # Safety truncation: if card itself is too large, strip preview
    if _estimate_tokens(card) > _MAX_CARD_TOKENS:
        minimal = [
            f"📦 OFFLOADED: {name}",
            f"   Path:  {out_path}",
            f"   Size:  ~{estimated_tokens:,} tokens",
            f"   Stats: {stats}",
            f"   ➜ Read {out_path} for full details.",
        ]
        card = "\n".join(minimal)

    return card


def offload_artifact(
    name: str,
    data: Any,
    summary_fn: Callable[[Any], str],
    *,
    threshold_tokens: int = 5_000,
    preview_lines: int = 10,
    artha_dir: Path | None = None,
    tier: EvictionTier | None = None,
) -> str:
    """Write data to tmp/{name}.json; return summary card if data > tier-adjusted threshold.

    If the serialized data is at or below the effective threshold, returns the
    serialized text directly — no file is written.

    If the data exceeds the threshold (and the feature flag is enabled in
    ``config/artha_config.yaml``), writes it to ``tmp/{name}{ext}`` and
    returns a compact summary card containing the file path, key stats from
    ``summary_fn``, and a configurable-length preview of the raw content.

    Args:
        name: Artifact name used as the stem of the output filename.
        data: The data to potentially offload (dict, list, or any
            JSON-serialisable value).
        summary_fn: Callable that receives ``data`` and returns a one-line
            stats string (e.g. record counts, date ranges).
        threshold_tokens: Minimum estimated token count before offloading.
            Defaults to 5,000 (~20 KB of text).
        preview_lines: Number of serialized lines included in the card preview.
        artha_dir: Override the Artha project root (used in tests to point
            at a temporary directory instead of the real ``ARTHA_DIR``).
        tier: Explicit eviction tier override.  When ``None``, the tier is
            looked up in ``_ARTIFACT_TIERS`` by ``name``; unknown names
            default to ``INTERMEDIATE``.  ``PINNED`` artifacts are never
            offloaded regardless of size.  ``EPHEMERAL`` artifacts use 40%
            of ``threshold_tokens``.  Ignored when tiered eviction is
            disabled via feature flag.

    Returns:
        str: Serialized data (if below threshold) or a compact summary card
            string (if offloaded).
    """
    if not load_harness_flag("context_offloading.enabled"):
        # Feature disabled — return serialized data, no file written
        serialized, _ = _serialize(data)
        return serialized

    serialized, ext = _serialize(data)

    # Determine effective eviction threshold
    if load_harness_flag("agentic.tiered_eviction.enabled"):
        effective_tier = (
            tier
            if tier is not None
            else _ARTIFACT_TIERS.get(name, EvictionTier.INTERMEDIATE)
        )
        multiplier = _TIER_THRESHOLDS[effective_tier]
        if multiplier == float("inf"):
            # PINNED — never offload regardless of size
            return serialized
        effective_threshold = max(1, int(threshold_tokens * multiplier))
    else:
        effective_threshold = threshold_tokens

    estimated_tokens = _estimate_tokens(serialized)

    if estimated_tokens <= effective_threshold:
        # Below threshold — return inline, no file written
        return serialized

    # Above threshold — write to tmp/
    base_dir = artha_dir if artha_dir is not None else ARTHA_DIR
    tmp_dir = base_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_path = tmp_dir / f"{name}{ext}"
    out_path.write_text(serialized, encoding="utf-8")

    stats = summary_fn(data)
    return _build_card(name, out_path, serialized, stats, estimated_tokens, preview_lines)


# ---------------------------------------------------------------------------
# Built-in summary functions for standard Artha artifacts
# ---------------------------------------------------------------------------

def pipeline_summary(records: list[dict]) -> str:
    """Compact stats card for pipeline JSONL output."""
    if not records:
        return "0 records"
    sources = {r.get("source", "unknown") for r in records}
    dates = sorted(
        {r.get("date_iso", r.get("start", ""))[:10] for r in records if r.get("date_iso") or r.get("start")}
    )
    date_range = f"{dates[0]} → {dates[-1]}" if len(dates) >= 2 else (dates[0] if dates else "unknown")
    return f"{len(records)} records | sources: {', '.join(sorted(sources))} | dates: {date_range}"


def emails_summary(records: list[dict]) -> str:
    """Compact stats for processed email batch."""
    if not records:
        return "0 emails"
    domains = {}
    for r in records:
        d = r.get("domain", r.get("routed_domain", "unclassified"))
        domains[d] = domains.get(d, 0) + 1
    top = sorted(domains.items(), key=lambda x: -x[1])[:5]
    top_str = ", ".join(f"{d}:{n}" for d, n in top)
    return f"{len(records)} emails routed → {top_str}"


def domain_extraction_summary(data: dict) -> str:
    """Compact stats for a single-domain extraction result."""
    alerts = data.get("alerts", [])
    bullets = data.get("briefing_bullets", data.get("bullets", []))
    return f"{len(alerts)} alerts | {len(bullets)} briefing bullets"


def cross_domain_summary(data: dict) -> str:
    """Compact stats for cross-domain analysis."""
    signals = data.get("compound_signals", [])
    one_thing = data.get("one_thing", "")
    top_alerts = data.get("top_alerts", [])
    preview = (one_thing[:60] + "…") if len(one_thing) > 60 else one_thing
    return (
        f"{len(top_alerts)} top alerts | {len(signals)} compound signals"
        + (f" | ONE THING: {preview}" if preview else "")
    )
