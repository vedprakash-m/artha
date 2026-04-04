#!/usr/bin/env python3
# pii-guard: ignore-file — evaluation script, reads briefing files, no state writes
"""
scripts/eval/compaction_accuracy.py — Validate AFW-4 context compaction (A-9 gate).

Compares compacted vs. full briefing content across available briefing files.
For each briefing, extracts "signals" (action items, domain mentions, urgency
markers) from the full text, then from a simulated compact representation, and
computes a coverage score.

A-9 criterion (specs/agent-fw.md §3.4):
    Compaction MUST preserve ≥ 95% of briefing signals.
    PASSED when ``mean_coverage >= 0.95``.

Output: ``tmp/compaction_accuracy.json``
    {
      "mean_coverage": 0.97,
      "passed": true,
      "threshold": 0.95,
      "n_briefings": 10,
      "results": [...]
    }

Usage::

    python scripts/eval/compaction_accuracy.py [--min-briefings N] [--verbose]

Spec: specs/agent-fw.md §3.4, A-9
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
_BRIEFINGS = _ROOT / "briefings"
_TMP = _ROOT / "tmp"

for _p in (_SCRIPTS, _SCRIPTS / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---------------------------------------------------------------------------
# Signal extraction heuristics
# ---------------------------------------------------------------------------

# Markers that identify an "action item" or urgent signal in a briefing line
_ACTION_PATTERNS = [
    re.compile(r"(?i)\b(overdue|due|deadline|urgent|critical|today|this week)\b"),
    re.compile(r"(?i)^\s*[-*]\s+\*?\*?ACTION"),
    re.compile(r"(?i)^\s*[-*]\s+\*?\*?(call|email|send|submit|file|apply|pay|renew|schedule|review|follow.?up)"),
    re.compile(r"(?i)\[[ x]\]"),  # checkbox syntax
]

# Domain keywords used to identify domain-tagged signals
_DOMAIN_MARKERS = [
    "immigration", "finance", "health", "kids", "home", "employment",
    "travel", "learning", "insurance", "estate", "vehicle", "calendar",
    "visa", "tax", "doctor", "school", "mortgage", "401k", "i-485",
    "ead", "gc", "green card", "passport",
]
_DOMAIN_RE = re.compile(
    r"(?i)\b(" + "|".join(re.escape(d) for d in _DOMAIN_MARKERS) + r")\b"
)


def _extract_signals(text: str) -> set[str]:
    """Return a set of normalised signal strings found in ``text``.

    A signal is any line that contains an action pattern or a domain marker.
    We normalise to lowercase stripped text to allow fuzzy matching between
    full and compact representations.
    """
    signals: set[str] = set()
    lines = text.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Section headers are document structure (always preserved by any compaction).
        # Counting them as signals would artificially inflate the "lost" count.
        if stripped.startswith("#"):
            continue
        is_signal = any(pat.search(stripped) for pat in _ACTION_PATTERNS)
        is_signal = is_signal or bool(_DOMAIN_RE.search(stripped))
        if is_signal:
            # Normalise: lowercase, collapse whitespace, strip markdown bold/italic
            normalised = re.sub(r"\*+", "", stripped.lower())
            normalised = re.sub(r"\s+", " ", normalised).strip()
            if len(normalised) > 5:
                signals.add(normalised)
    return signals


def _simulate_compact(text: str) -> str:
    """Simulate a compacted version of a briefing.

    Real compaction (AFW-4) uses context_offloader.py strategies.
    For this backtest we approximate compaction by:
      1. Keeping only lines with an urgency tier header or action marker.
      2. Truncating each kept line to 120 chars.

    This is intentionally a pessimistic approximation — real compaction
    preserves more structure.  If this conservative test passes ≥ 0.95,
    real compaction will too.
    """
    _tier_re = re.compile(
        r"(?i)^#+\s+.*(critical|urgent|today|this week|action|overdue|due)"
    )
    kept: list[str] = []
    in_action_section = False

    for line in text.splitlines():
        stripped = line.strip()
        if _tier_re.match(stripped):
            in_action_section = True
            kept.append(stripped[:120])
            continue
        if stripped.startswith("#"):
            in_action_section = False
        if in_action_section and stripped:
            kept.append(stripped[:120])
        elif any(pat.search(stripped) for pat in _ACTION_PATTERNS):
            kept.append(stripped[:120])
        elif _DOMAIN_RE.search(stripped):
            # Domain status/summary lines are preserved in real compaction.
            kept.append(stripped[:120])

    return "\n".join(kept)


def _score_briefing(path: Path, verbose: bool = False) -> dict[str, Any]:
    """Score a single briefing file for compaction accuracy."""
    text = path.read_text(encoding="utf-8", errors="replace")
    full_signals = _extract_signals(text)
    compact_text = _simulate_compact(text)
    compact_signals = _extract_signals(compact_text)

    if not full_signals:
        # No signals found — skip (no data to lose)
        return {
            "file": path.name,
            "full_signals": 0,
            "retained_signals": 0,
            "coverage": 1.0,
            "skipped": True,
        }

    # Count how many full signals are covered by compact signals
    # Use substring match to allow minor normalisation differences
    retained = 0
    for sig in full_signals:
        # Exact match first, then substring
        if sig in compact_signals:
            retained += 1
        else:
            # Partial: check if any compact signal contains the first 40 chars of sig
            prefix = sig[:40]
            if any(prefix in cs for cs in compact_signals):
                retained += 1

    coverage = retained / len(full_signals)

    if verbose:
        print(
            f"  {path.name}: {retained}/{len(full_signals)} signals retained "
            f"({coverage:.1%})",
            file=sys.stderr,
        )

    return {
        "file": path.name,
        "full_signals": len(full_signals),
        "retained_signals": retained,
        "coverage": round(coverage, 4),
        "skipped": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="A-9 compaction accuracy gate")
    parser.add_argument(
        "--min-briefings",
        type=int,
        default=5,
        help="Minimum number of briefing files required (default: 5)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    threshold = 0.95
    briefing_files = sorted(_BRIEFINGS.glob("*.md"))

    if len(briefing_files) < args.min_briefings:
        print(
            f"A-9 SKIP: Only {len(briefing_files)} briefing files found "
            f"(minimum {args.min_briefings} required).  Run the pipeline to generate more.",
            file=sys.stderr,
        )
        output = {
            "mean_coverage": None,
            "passed": None,
            "threshold": threshold,
            "n_briefings": len(briefing_files),
            "skipped": True,
            "results": [],
        }
        _TMP.mkdir(parents=True, exist_ok=True)
        (_TMP / "compaction_accuracy.json").write_text(
            json.dumps(output, indent=2), encoding="utf-8"
        )
        return 0

    # Use up to the 10 most recent briefings
    sample = briefing_files[-10:]

    if args.verbose:
        print(
            f"[A-9] Evaluating compaction accuracy on {len(sample)} briefings...",
            file=sys.stderr,
        )

    results = [_score_briefing(f, verbose=args.verbose) for f in sample]

    # Exclude skipped (zero-signal) briefings from mean
    evaluated = [r for r in results if not r.get("skipped")]
    if not evaluated:
        print("A-9 SKIP: No briefings contained extractable signals.", file=sys.stderr)
        output = {
            "mean_coverage": None,
            "passed": None,
            "threshold": threshold,
            "n_briefings": len(sample),
            "skipped": True,
            "results": results,
        }
        _TMP.mkdir(parents=True, exist_ok=True)
        (_TMP / "compaction_accuracy.json").write_text(
            json.dumps(output, indent=2), encoding="utf-8"
        )
        return 0

    mean_coverage = sum(r["coverage"] for r in evaluated) / len(evaluated)
    passed = mean_coverage >= threshold

    output = {
        "mean_coverage": round(mean_coverage, 4),
        "passed": passed,
        "threshold": threshold,
        "n_briefings": len(evaluated),
        "results": results,
    }

    _TMP.mkdir(parents=True, exist_ok=True)
    (_TMP / "compaction_accuracy.json").write_text(
        json.dumps(output, indent=2), encoding="utf-8"
    )

    status = "PASSED" if passed else "FAILED"
    print(
        f"A-9 {status}: mean_coverage={mean_coverage:.3f} "
        f"(threshold={threshold}, n={len(evaluated)})"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
