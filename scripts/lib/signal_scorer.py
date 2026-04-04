#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/lib/signal_scorer.py — Composite signal scoring for Artha.

Replaces flat signal enumeration in catch-up Step 8 with a composite score
based on urgency, impact, and freshness (a recency-decayed component).

Score formula::

    score = urgency_weight * (urgency / 5)
          + impact_weight  * (impact  / 5)
          + freshness_weight * max(0, 1.0 - decay_per_day * age_days)

Signals scoring below ``suppress_below`` are noise and filtered before
briefing assembly.  Signals scoring ``promote_above`` or higher are marked
for the briefing header with an alert emoji.

Default thresholds are intentionally conservative pending empirical
calibration against the ``briefings/`` archive.  Run calibration first and
record results in ``tmp/signal_scorer_calibration.json`` before tightening
the thresholds.

Spec: specs/agent-fw.md §AFW-9 — Composite Signal Scoring
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Defaults — all overridable via config/artha_config.yaml → signal_scorer
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS: dict[str, float] = {
    "urgency_weight": 0.4,
    "impact_weight": 0.4,
    "freshness_weight": 0.2,
    "freshness_decay_per_day": 0.1,
}

# Thresholds calibrated against briefings/ archive (27 files, 712 signals).
# See specs/agent-fw.md §3.9.2 and tmp/signal_scorer_calibration.json.
# suppress_below=0.2 CONFIRMED — no critical signals suppressed at this level.
# promote_above=0.66 CALIBRATED from p90 of score distribution (3.2% reach 0.8).
_DEFAULT_SUPPRESS_BELOW: float = 0.2
_DEFAULT_PROMOTE_ABOVE: float = 0.66


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_scorer_config(artha_dir: Path | None = None) -> dict[str, Any]:
    """Load ``signal_scorer`` section from ``config/artha_config.yaml``.

    Returns the ``signal_scorer`` sub-dict, or ``{}`` on any failure.
    """
    try:
        import sys  # noqa: PLC0415

        _root = artha_dir if artha_dir is not None else Path(__file__).resolve().parent.parent.parent
        _scripts = str(_root / "scripts")
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        from lib.config_loader import load_config  # noqa: PLC0415

        cfg = load_config("artha_config")
        return cfg.get("signal_scorer", {})
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_signal(
    signal: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> float:
    """Compute the composite score for a single signal dict.

    Args:
        signal: Dict with optional keys:
            ``urgency`` (int 1–5, default 1),
            ``impact``  (int 1–5, default 1),
            ``age_days`` (int ≥ 0, default 0).
        weights: Mapping that overrides one or more of the keys in
            :data:`_DEFAULT_WEIGHTS`.  ``None`` uses defaults only.

    Returns:
        Float in ``[0.0, 1.0]``.
    """
    w = {**_DEFAULT_WEIGHTS, **(weights or {})}
    urgency = float(signal.get("urgency", 1)) / 5.0
    impact = float(signal.get("impact", 1)) / 5.0
    age_days = float(signal.get("age_days", 0))
    freshness = max(0.0, 1.0 - w["freshness_decay_per_day"] * age_days)
    return (
        w["urgency_weight"] * urgency
        + w["impact_weight"] * impact
        + w["freshness_weight"] * freshness
    )


def rank_signals(
    signals: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
    artha_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Score and sort signals highest-first.

    Each output dict is a shallow copy of the input with a ``_score`` key
    added.  Original dicts are not mutated.

    Args:
        signals: List of signal dicts (see :func:`score_signal`).
        weights: Optional weight overrides (merged on top of config).
        artha_dir: Artha project root for config loading (``None`` → auto).

    Returns:
        New list sorted by ``_score`` descending.
    """
    cfg = _load_scorer_config(artha_dir)
    merged_weights = {**_DEFAULT_WEIGHTS, **cfg.get("weights", {}), **(weights or {})}

    scored: list[dict[str, Any]] = []
    for sig in signals:
        entry = dict(sig)
        entry["_score"] = score_signal(sig, merged_weights)
        scored.append(entry)
    scored.sort(key=lambda s: s["_score"], reverse=True)
    return scored


def partition_signals(
    signals: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
    suppress_below: float | None = None,
    promote_above: float | None = None,
    artha_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition signals into (promoted, normal, suppressed) buckets.

    * **promoted** — ``_score >= promote_above`` → briefing header alert.
    * **normal**   — ``suppress_below <= _score < promote_above`` → standard.
    * **suppressed** — ``_score < suppress_below`` → noise, excluded from briefing.

    Each returned list is sorted by ``_score`` descending.

    Args:
        signals: Input signal dicts.
        weights: Optional weight overrides.
        suppress_below: Threshold below which signals are suppressed.
            ``None`` reads from config, falls back to :data:`_DEFAULT_SUPPRESS_BELOW`.
        promote_above: Threshold at or above which signals are promoted.
            ``None`` reads from config, falls back to :data:`_DEFAULT_PROMOTE_ABOVE`.
        artha_dir: Artha project root.

    Returns:
        Three lists: ``(promoted, normal, suppressed)``.
    """
    cfg = _load_scorer_config(artha_dir)
    supp = (
        suppress_below
        if suppress_below is not None
        else cfg.get("suppress_below", _DEFAULT_SUPPRESS_BELOW)
    )
    prom = (
        promote_above
        if promote_above is not None
        else cfg.get("promote_above", _DEFAULT_PROMOTE_ABOVE)
    )

    ranked = rank_signals(signals, weights=weights, artha_dir=artha_dir)

    promoted: list[dict[str, Any]] = []
    normal: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []

    for sig in ranked:
        s = sig["_score"]
        if s >= prom:
            promoted.append(sig)
        elif s >= supp:
            normal.append(sig)
        else:
            suppressed.append(sig)

    return promoted, normal, suppressed
