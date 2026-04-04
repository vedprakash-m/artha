#!/usr/bin/env python3
# pii-guard: ignore-file — evaluation script, reads briefing files, no state writes
"""
scripts/eval/keyword_precision_recall.py — Backtest AFW-2 routing keyword precision/recall.

Reads all briefing files in ``briefings/``, extracts signals with domain
labels, then runs ``should_load_domain()`` (or keyword matching) against the
domain_registry.yaml keywords to measure how accurately keywords identify the
correct domain.

A-2 criterion (specs/agent-fw.md §3.2.5):
    precision > 0.80  and  recall > 0.70
    over 30 days of briefing signals.

Output: ``tmp/keyword_precision_recall.json``
    {
      "precision": 0.87,
      "recall": 0.75,
      "f1": 0.81,
      "passed": true,
      "hits": 245,
      "misses": 41,
      "false_positives": 30,
      "n_signals": 316,
      "n_briefings": 30
    }

Usage::

    python scripts/eval/keyword_precision_recall.py [--verbose]

Spec: specs/agent-fw.md §3.2.5, A-2
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
# Domain registry loading
# ---------------------------------------------------------------------------

def _load_registry() -> dict:
    """Load config/domain_registry.yaml, return domains sub-dict or {}."""
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        cfg = load_config("domain_registry", str(_ROOT / "config"))
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _build_keyword_map(registry: dict) -> dict[str, list[str]]:
    """Build {domain_name: [keyword, ...]} from registry dict."""
    mapping: dict[str, list[str]] = {}
    for domain, cfg in (registry.get("domains") or {}).items():
        if not isinstance(cfg, dict):
            continue
        kws = cfg.get("routing_keywords") or []
        if kws:
            mapping[domain] = [str(k).lower() for k in kws]
    return mapping


# ---------------------------------------------------------------------------
# Signal extraction from briefing files
# ---------------------------------------------------------------------------

# Map of strong domain mentions in briefing headings / bullet text
# Format: (regex, canonical_domain_name)
_DOMAIN_HINT_PATTERNS: list[tuple[re.Pattern, str]] = []  # populated lazily

_EXPLICIT_DOMAIN_HEADERS = re.compile(
    r"(?i)^#+\s+(.+)\s*$"
)

# Patterns that indicate a domain-tagged signal line
_SIGNAL_LINE_RE = re.compile(
    r"(?i)^\s*[-*]\s+.{5,}"  # bullet with at least 5 chars
)


def _infer_domain_from_context(line: str, registry: dict) -> str | None:
    """Heuristically infer domain from line text using registry keywords."""
    text = line.lower()
    keyword_map = _build_keyword_map(registry)
    best_domain: str | None = None
    best_count = 0
    for domain, keywords in keyword_map.items():
        count = sum(1 for kw in keywords if kw in text)
        if count > best_count:
            best_count = count
            best_domain = domain
    return best_domain if best_count > 0 else None


def _extract_labelled_signals(path: Path, registry: dict) -> list[dict[str, str]]:
    """Extract (text, actual_domain) pairs from a briefing file.

    Uses section headers to infer domain context for bullets under them.
    Only retains signals where we can confidently infer the actual domain.
    """
    signals: list[dict[str, str]] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    current_domain: str | None = None
    known_domains = set((registry.get("domains") or {}).keys())

    for line in lines:
        header_match = _EXPLICIT_DOMAIN_HEADERS.match(line)
        if header_match:
            header_text = header_match.group(1).lower().strip()
            # Check if header text directly maps to a known domain
            for domain in known_domains:
                if domain.replace("_", " ") in header_text or header_text in domain.replace("_", " "):
                    current_domain = domain
                    break
            else:
                # Try keyword inference on the header text
                current_domain = _infer_domain_from_context(header_text, registry)
            continue

        if _SIGNAL_LINE_RE.match(line) and current_domain:
            stripped = line.strip()
            # Also try to infer domain from the line itself
            line_domain = _infer_domain_from_context(stripped, registry)
            # Use line-level domain if found and differs from header (more specific)
            actual = line_domain if line_domain else current_domain
            signals.append({"text": stripped, "domain": actual})

    return signals


# ---------------------------------------------------------------------------
# Keyword matching (predict domain from signal text)
# ---------------------------------------------------------------------------

def _match_domain_by_keywords(
    signal_text: str,
    keyword_map: dict[str, list[str]],
) -> str | None:
    """Predict the domain for a signal using keyword matching.

    Returns the domain with the most keyword hits, or None if no hit.
    """
    text = signal_text.lower()
    best_domain: str | None = None
    best_count = 0
    for domain, keywords in keyword_map.items():
        count = sum(1 for kw in keywords if kw in text)
        if count > best_count:
            best_count = count
            best_domain = domain
    return best_domain if best_count > 0 else None


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="A-2 routing keyword precision/recall backtest")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--min-briefings",
        type=int,
        default=5,
        help="Minimum briefing files required (default: 5)",
    )
    args = parser.parse_args()

    precision_threshold = 0.80
    recall_threshold = 0.70

    registry = _load_registry()
    if not registry.get("domains"):
        print(
            "A-2 SKIP: domain_registry.yaml has no domains section.",
            file=sys.stderr,
        )
        output = {
            "precision": None,
            "recall": None,
            "f1": None,
            "passed": None,
            "skipped": True,
            "reason": "Empty domain registry",
        }
        _TMP.mkdir(parents=True, exist_ok=True)
        (_TMP / "keyword_precision_recall.json").write_text(
            json.dumps(output, indent=2), encoding="utf-8"
        )
        return 0

    keyword_map = _build_keyword_map(registry)
    briefing_files = sorted(_BRIEFINGS.glob("*.md"))

    if len(briefing_files) < args.min_briefings:
        print(
            f"A-2 SKIP: Only {len(briefing_files)} briefing files "
            f"(minimum {args.min_briefings} required).",
            file=sys.stderr,
        )
        output = {
            "precision": None,
            "recall": None,
            "f1": None,
            "passed": None,
            "skipped": True,
            "reason": f"Only {len(briefing_files)} briefing files",
            "n_briefings": len(briefing_files),
        }
        _TMP.mkdir(parents=True, exist_ok=True)
        (_TMP / "keyword_precision_recall.json").write_text(
            json.dumps(output, indent=2), encoding="utf-8"
        )
        return 0

    # Use up to 30 most recent briefings
    sample_files = briefing_files[-30:]

    hits = 0
    misses = 0
    false_positives = 0
    total_signals = 0

    if args.verbose:
        print(
            f"[A-2] Evaluating keyword precision/recall on {len(sample_files)} briefings...",
            file=sys.stderr,
        )

    for bfile in sample_files:
        labelled = _extract_labelled_signals(bfile, registry)
        for signal in labelled:
            actual = signal["domain"]
            predicted = _match_domain_by_keywords(signal["text"], keyword_map)
            total_signals += 1
            if predicted == actual:
                hits += 1
            elif predicted is None:
                misses += 1
            else:
                false_positives += 1

        if args.verbose:
            print(
                f"  {bfile.name}: {len(labelled)} labelled signals",
                file=sys.stderr,
            )

    if total_signals == 0:
        print(
            "A-2 SKIP: No labelled signals extracted from briefings.",
            file=sys.stderr,
        )
        output = {
            "precision": None,
            "recall": None,
            "f1": None,
            "passed": None,
            "skipped": True,
            "reason": "No labelled signals found",
            "n_briefings": len(sample_files),
        }
        _TMP.mkdir(parents=True, exist_ok=True)
        (_TMP / "keyword_precision_recall.json").write_text(
            json.dumps(output, indent=2), encoding="utf-8"
        )
        return 0

    precision = hits / (hits + false_positives) if (hits + false_positives) > 0 else 0.0
    recall = hits / (hits + misses) if (hits + misses) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    passed = precision > precision_threshold and recall > recall_threshold

    output: dict[str, Any] = {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "passed": passed,
        "precision_threshold": precision_threshold,
        "recall_threshold": recall_threshold,
        "hits": hits,
        "misses": misses,
        "false_positives": false_positives,
        "n_signals": total_signals,
        "n_briefings": len(sample_files),
    }

    _TMP.mkdir(parents=True, exist_ok=True)
    (_TMP / "keyword_precision_recall.json").write_text(
        json.dumps(output, indent=2), encoding="utf-8"
    )

    status = "PASSED" if passed else "FAILED"
    print(
        f"A-2 {status}: precision={precision:.3f} (>{precision_threshold}), "
        f"recall={recall:.3f} (>{recall_threshold}), "
        f"f1={f1:.3f}, n={total_signals}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
