#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/eval/signal_scorer_calibration.py — Calibrate AFW-9 suppression/promotion thresholds.

Reads all briefings in ``briefings/`` (≥30 files required), heuristically
extracts signals with urgency/impact/age metadata, scores each with
``signal_scorer.score_signal()``, then writes percentile analysis to
``tmp/signal_scorer_calibration.json``.

The output informs whether the default 0.2 suppression threshold and 0.8
promotion threshold are correct — or whether they need adjustment to avoid
silencing time-sensitive finance/immigration alerts.

Spec mandate: specs/agent-fw.md §3.9.2
    "Run calibration and record results in ``tmp/signal_scorer_calibration.json``
    before finalizing thresholds."

Usage::

    python scripts/eval/signal_scorer_calibration.py

Output file: ``tmp/signal_scorer_calibration.json``
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Ensure scripts/lib is importable
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
_LIB = _SCRIPTS / "lib"
for _p in (_SCRIPTS, _LIB):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from signal_scorer import score_signal  # noqa: E402


# ---------------------------------------------------------------------------
# Tier → (urgency, impact) heuristic mapping
# ---------------------------------------------------------------------------

_TIER_MAP = {
    "CRITICAL": (5, 5),
    "URGENT": (4, 4),
    "TODAY / THIS WEEK": (3, 3),
    "THIS WEEK": (3, 3),
    "BY DOMAIN": (2, 2),
    "ACTION ITEMS": (3, 3),
    "OVERDUE": (5, 4),
    "REMINDER": (2, 2),
    "UPCOMING": (2, 3),
    "FYI": (1, 1),
}

# Pattern to match tier headers in briefing files
_TIER_RE = re.compile(
    r"━+\s*(?:🔴|🟠|📅|📬|⚠️|✅|🟡|🔵)?\s*([A-Z /]+?)\s*(?:🔴|🟠|📅|📬|⚠️|✅|🟡|🔵)?\s*━+",
    re.IGNORECASE,
)

# Bullet signal lines
_BULLET_RE = re.compile(r"^\s*[•\-\*]\s+(.+)$")


def _parse_briefing_date(path: Path) -> date | None:
    """Extract a date from the briefing file YAML frontmatter or filename."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^date:\s*(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
        if m:
            return date.fromisoformat(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    # Fall back to filename date
    try:
        name = path.stem.split("-full")[0].split("b")[0]
        return date.fromisoformat(name[:10])
    except Exception:  # noqa: BLE001
        return None


def _extract_signals(path: Path, today: date) -> list[dict]:
    """Extract signals from a single briefing file with heuristic metadata."""
    text = path.read_text(encoding="utf-8", errors="replace")
    briefing_date = _parse_briefing_date(path) or today
    age_days = (today - briefing_date).days

    signals: list[dict] = []
    current_urgency, current_impact = 2, 2  # default tier = moderate

    for line in text.splitlines():
        tier_match = _TIER_RE.search(line)
        if tier_match:
            tier_name = tier_match.group(1).strip().upper()
            for key, (u, i) in _TIER_MAP.items():
                if key in tier_name:
                    current_urgency, current_impact = u, i
                    break

        bullet_match = _BULLET_RE.match(line)
        if bullet_match:
            text_fragment = bullet_match.group(1)
            signals.append({
                "text": text_fragment[:200],
                "urgency": current_urgency,
                "impact": current_impact,
                "age_days": age_days,
                "source_file": path.name,
                "tier": tier_name if tier_match else "UNKNOWN",
            })
            # Reset tier_match tracking after assignment
            tier_match = None

    return signals


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * pct / 100)
    return sorted_values[min(idx, len(sorted_values) - 1)]


def run_calibration(artha_dir: Path | None = None) -> dict:
    """Run calibration and return the analysis dict."""
    root = artha_dir or _ROOT
    briefings_dir = root / "briefings"
    output_path = root / "tmp" / "signal_scorer_calibration.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    today = date.today()
    briefing_files = sorted(briefings_dir.glob("*.md"))

    if len(briefing_files) < 30:
        print(
            f"WARNING: Only {len(briefing_files)} briefing files found. "
            "Spec requires ≥30 for reliable calibration. Proceeding anyway.",
            file=sys.stderr,
        )

    all_signals: list[dict] = []
    for fpath in briefing_files:
        all_signals.extend(_extract_signals(fpath, today))

    if not all_signals:
        print("ERROR: No signals extracted — check briefings/ content.", file=sys.stderr)
        return {}

    # Score all signals
    scored = []
    for sig in all_signals:
        s = score_signal(sig)
        scored.append({**sig, "composite_score": round(s, 4)})

    scores = sorted(x["composite_score"] for x in scored)

    # Distribution analysis
    distribution = {
        "p5": round(_percentile(scores, 5), 4),
        "p10": round(_percentile(scores, 10), 4),
        "p25": round(_percentile(scores, 25), 4),
        "p50": round(_percentile(scores, 50), 4),
        "p75": round(_percentile(scores, 75), 4),
        "p90": round(_percentile(scores, 90), 4),
        "p95": round(_percentile(scores, 95), 4),
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
    }

    # Threshold analysis
    current_suppress = 0.2
    current_promote = 0.8

    suppressed = [s for s in scored if s["composite_score"] < current_suppress]
    promoted = [s for s in scored if s["composite_score"] >= current_promote]
    middle = [s for s in scored if current_suppress <= s["composite_score"] < current_promote]

    # Critical signals suppressed (urgency=5, impact>=4) — these should NEVER be suppressed
    critical_suppressed = [
        s for s in suppressed if s["urgency"] >= 5 and s["impact"] >= 4
    ]

    # Recommendation
    suppress_recommendation = current_suppress
    promote_recommendation = current_promote
    notes = []

    if critical_suppressed:
        # Lower suppression threshold to avoid silencing critical signals
        suppress_recommendation = round(
            max(0.05, min(s["composite_score"] for s in critical_suppressed) - 0.02),
            2,
        )
        notes.append(
            f"⚠️  {len(critical_suppressed)} CRITICAL signal(s) would be suppressed at threshold {current_suppress}. "
            f"Recommend lowering suppress_below to {suppress_recommendation}."
        )
    else:
        notes.append(f"✅ No critical signals suppressed at threshold {current_suppress}.")

    if distribution["p90"] < current_promote:
        promote_recommendation = round(distribution["p90"] + 0.02, 2)
        notes.append(
            f"ℹ️  Only {sum(1 for s in scores if s >= current_promote)} signals ({100*sum(1 for s in scores if s >= current_promote)/len(scores):.1f}%) "
            f"reach promote threshold {current_promote}. "
            f"Consider lowering to {promote_recommendation} (p90 = {distribution['p90']})."
        )

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "briefing_files_analyzed": len(briefing_files),
        "total_signals_extracted": len(all_signals),
        "score_distribution": distribution,
        "current_thresholds": {
            "suppress_below": current_suppress,
            "promote_above": current_promote,
        },
        "recommended_thresholds": {
            "suppress_below": suppress_recommendation,
            "promote_above": promote_recommendation,
        },
        "tier_breakdown": {
            "suppressed_count": len(suppressed),
            "promoted_count": len(promoted),
            "middle_count": len(middle),
            "critical_suppressed_count": len(critical_suppressed),
            "suppressed_pct": round(100 * len(suppressed) / len(scored), 1),
            "promoted_pct": round(100 * len(promoted) / len(scored), 1),
        },
        "critical_suppressed_examples": [
            {"text": s["text"][:120], "score": s["composite_score"], "urgency": s["urgency"], "impact": s["impact"]}
            for s in critical_suppressed[:5]
        ],
        "notes": notes,
        "spec_reference": "specs/agent-fw.md §3.9.2",
    }

    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    result = run_calibration()
    if result:
        print(f"\nCalibration complete — {result['total_signals_extracted']} signals analyzed")
        print(f"Score distribution: {result['score_distribution']}")
        print(f"\nCurrent thresholds: suppress < {result['current_thresholds']['suppress_below']}, promote > {result['current_thresholds']['promote_above']}")
        print(f"Recommended:        suppress < {result['recommended_thresholds']['suppress_below']}, promote > {result['recommended_thresholds']['promote_above']}")
        print("\nNotes:")
        for note in result["notes"]:
            print(f"  {note}")
        print(f"\nFull results written to: tmp/signal_scorer_calibration.json")
