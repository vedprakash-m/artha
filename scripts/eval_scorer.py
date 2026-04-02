#!/usr/bin/env python3
"""scripts/eval_scorer.py — Five-dimension heuristic briefing quality scorer.

Scores a catch-up briefing on five equally weighted dimensions (20 pts each):

    1. Actionability   — active verbs + proper noun specificity
    2. Specificity     — numbers, dates, percentages, named entities
    3. Completeness    — section coverage + active-goal alignment
    4. Signal Purity   — low stale-echo; low domain noise
    5. Calibration     — confidence qualifiers; balanced claims vs hedges

Total: 0–100 float.

Compliance score is pulled from audit_compliance.audit_latest_briefing()
(0–100 int) and stored alongside the five dimensions.

Output: appends one entry to state/briefing_scores.json (keeps last 50).

Usage:
    python scripts/eval_scorer.py briefings/2026-03-21.md
    python scripts/eval_scorer.py briefings/2026-03-21.md --json
    python scripts/eval_scorer.py briefings/2026-03-21.md --verbose

Ref: specs/eval.md EV-5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent

try:
    from _bootstrap import reexec_in_venv  # type: ignore[import]
    reexec_in_venv()
except ImportError:
    pass

STATE_DIR = _REPO_ROOT / "state"
CONFIG_DIR = _REPO_ROOT / "config"
_BRIEFING_SCORES = STATE_DIR / "briefing_scores.json"
_DOMAIN_REGISTRY = CONFIG_DIR / "domain_registry.yaml"
_GOALS_FILE = STATE_DIR / "goals.md"

_MAX_STORED_SCORES = 50
_SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Scoring dimension constants
# ---------------------------------------------------------------------------

_ACTIONABLE_VERBS = re.compile(
    r"\b(review|schedule|call|email|pay|file|submit|renew|book|check|follow up|"
    r"update|complete|prepare|confirm|transfer|cancel|contact|sign|apply|send|"
    r"monitor|resolve|fix|investigate|draft|meet|discuss|close|open|log|set)\b",
    re.IGNORECASE,
)
_NUMBERS_DATES = re.compile(
    r"\b(\d{1,3}(?:,\d{3})*(?:\.\d+)?%?|\$\S+|\d{4}-\d{2}-\d{2}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}(?:,? \d{4})?)\b",
    re.IGNORECASE,
)
_CONFIDENCE_QUALIFIERS = re.compile(
    r"\b(likely|probably|may|might|could|should|appears|seems|suggests|"
    r"roughly|approximately|around|about|estimated|possibly)\b",
    re.IGNORECASE,
)
_HEDGE_WORDS = re.compile(
    r"\b(unclear|unknown|uncertain|TBD|pending|unconfirmed|not yet|"
    r"no data|no information|unavailable)\b",
    re.IGNORECASE,
)
_STALE_ECHO_THRESHOLD = 0.8  # >80% domain overlap = stale


# ---------------------------------------------------------------------------
# Domain registry helper
# ---------------------------------------------------------------------------

def _load_domain_names() -> list[str]:
    """Return list of enabled domain names from config/domain_registry.yaml."""
    try:
        import yaml  # type: ignore[import]
        raw = yaml.safe_load(_DOMAIN_REGISTRY.read_text(encoding="utf-8"))
        domains = raw.get("domains", {})
        # Handle list format: [{id: finance, active: true}, ...]
        if isinstance(domains, list):
            return [
                item["id"] for item in domains
                if isinstance(item, dict)
                and item.get("id")
                and item.get("active", item.get("enabled", item.get("enabled_by_default", True)))
            ]
        # Handle dict format: {finance: {enabled_by_default: true}, ...}
        return [
            k for k, v in domains.items()
            if isinstance(v, dict)
            and v.get("active", v.get("enabled", v.get("enabled_by_default", True)))
        ]
    except Exception:
        # Fallback: minimal set that's always expected
        return ["finance", "immigration", "health", "kids", "calendar", "goals", "comms"]


# ---------------------------------------------------------------------------
# Dimension scorers
# ---------------------------------------------------------------------------

def _score_actionability_impl(text: str, lines: list[str]) -> tuple[float, dict]:
    """20 pts: active verbs in bullet items + proper noun density. Returns (score, meta)."""
    bullets = [l for l in lines if re.match(r"^\s*[-*•]", l)]
    if not bullets:
        return 0.0, {"bullets": 0, "verb_hits": 0, "proper_nouns": 0}
    bullet_text = " ".join(bullets)
    verb_hits = len(_ACTIONABLE_VERBS.findall(bullet_text))
    # 1 verb per bullet expected; cap at 20
    score = min(20.0, (verb_hits / max(len(bullets), 1)) * 20.0)
    # Bonus: proper nouns in bullets (capitalized words not at line start)
    proper_nouns = len(re.findall(r"(?<!\. )(?<!^)\b[A-Z][a-z]{2,}\b", bullet_text))
    score = min(20.0, score + min(3.0, proper_nouns * 0.5))
    return round(score, 2), {"bullets": len(bullets), "verb_hits": verb_hits, "proper_nouns": proper_nouns}


def _score_actionability(text: str, lines: list[str]) -> float:
    """20 pts: active verbs in bullet items + proper noun density."""
    return _score_actionability_impl(text, lines)[0]


def _score_specificity_impl(text: str, lines: list[str]) -> tuple[float, dict]:
    """20 pts: numbers/dates/percentages + named entities. Returns (score, meta)."""
    num_hits = len(_NUMBERS_DATES.findall(text))
    # ~1 number per 4 lines expected; cap at 20
    expected = max(len(lines) / 4, 1)
    score = min(20.0, (num_hits / expected) * 20.0)
    return round(score, 2), {"num_hits": num_hits, "expected": round(expected, 1)}


def _score_specificity(text: str, lines: list[str]) -> float:
    """20 pts: numbers/dates/percentages + named entities."""
    return _score_specificity_impl(text, lines)[0]


def _score_completeness_impl(
    text: str,
    lines: list[str],
    domain_names: list[str],
) -> tuple[float, dict]:
    """20 pts: section headings coverage + active goal alignment. Returns (score, meta)."""
    # Section coverage: count H2/H3 headers present
    headers = [l for l in lines if re.match(r"^#{1,3} ", l)]
    section_score = min(10.0, len(headers) * 1.5)

    # Goals alignment: +6 if any active goal keyword appears in the briefing
    goal_bonus = 0.0
    goals_refs = 0
    if _GOALS_FILE.exists():
        try:
            goals_text = _GOALS_FILE.read_text(encoding="utf-8")
            # Find active goal names (lines starting with - or * that aren't completed)
            active_goals = re.findall(
                r"^[-*] (?!~~)(.+?)(?:\s*\[|$)", goals_text, re.MULTILINE
            )
            for goal in active_goals[:5]:  # Check first 5 active goals
                goal_keyword = goal.strip()[:20]
                if goal_keyword and goal_keyword.lower() in text.lower():
                    goals_refs += 1
            if goals_refs > 0:
                goal_bonus = min(6.0, goals_refs * 2.0)
        except Exception:
            pass

    # Domain breadth bonus: more domains surfaced = higher completeness
    surfaced_domains = sum(1 for d in domain_names if d.lower() in text.lower())
    domain_score = min(4.0, surfaced_domains * 0.5)

    score = min(20.0, section_score + goal_bonus + domain_score)
    return round(score, 2), {
        "headers": len(headers),
        "goals_refs": goals_refs,
        "surfaced_domains": surfaced_domains,
    }


def _score_completeness(
    text: str,
    lines: list[str],
    domain_names: list[str],
) -> float:
    """20 pts: section headings coverage + active goal alignment."""
    return _score_completeness_impl(text, lines, domain_names)[0]


def _score_signal_purity_impl(
    text: str,
    lines: list[str],
    domain_names: list[str],
    prev_domains: list[str] | None = None,
) -> tuple[float, dict]:
    """20 pts: low stale-echo; low noise ratio. Returns (score, meta)."""
    # Stale-echo: domain overlap with previous session
    current_domains = {d for d in domain_names if d.lower() in text.lower()}
    stale_penalty = 0.0
    overlap_ratio = 0.0
    if prev_domains and current_domains:
        prev_set = set(prev_domains)
        overlap = current_domains & prev_set
        overlap_ratio = len(overlap) / max(len(current_domains), 1)
        if overlap_ratio >= _STALE_ECHO_THRESHOLD:
            stale_penalty = 6.0

    # Noise ratio: ratio of non-actionable long lines to total lines
    long_lines = [l for l in lines if len(l.strip()) > 80 and not re.match(r"^#{1,3} ", l)]
    actionable_long = [l for l in long_lines if _ACTIONABLE_VERBS.search(l)]
    noise_ratio = (len(long_lines) - len(actionable_long)) / max(len(lines), 1)
    noise_penalty = min(4.0, noise_ratio * 20.0)

    score = max(0.0, 20.0 - stale_penalty - noise_penalty)
    return round(score, 2), {
        "overlap_ratio": round(overlap_ratio, 3),
        "stale_penalty": stale_penalty,
        "noise_ratio": round(noise_ratio, 3),
    }


def _score_signal_purity(
    text: str,
    lines: list[str],
    domain_names: list[str],
    prev_domains: list[str] | None = None,
) -> float:
    """20 pts: low stale-echo; low noise ratio."""
    return _score_signal_purity_impl(text, lines, domain_names, prev_domains)[0]


def _score_calibration_impl(text: str, lines: list[str]) -> tuple[float, dict]:
    """20 pts: confidence qualifier presence + balanced claims vs hedges. Returns (score, meta)."""
    qualifier_count = len(_CONFIDENCE_QUALIFIERS.findall(text))
    hedge_count = len(_HEDGE_WORDS.findall(text))
    # Use max of sentence splits and line count: bullet-point briefings have
    # few sentence-ending chars but many meaningful lines.
    total_sentences = max(len(re.split(r"[.!?]+", text)), len(lines), 1)

    # Qualifiers should be present but not excessive (~5% of sentences)
    qualifier_rate = qualifier_count / total_sentences
    qual_score = 10.0 if 0.02 <= qualifier_rate <= 0.15 else max(0.0, 5.0 - abs(qualifier_rate - 0.08) * 30)

    # Hedge words signal honesty, but too many = low confidence (cap at 5)
    hedge_score = min(10.0, (hedge_count * 2.0))
    hedge_score = max(0.0, hedge_score - max(0, hedge_count - 5) * 2.0)

    score = min(20.0, qual_score + hedge_score)
    return round(score, 2), {
        "qualifiers": qualifier_count,
        "hedges": hedge_count,
        "qualifier_rate": round(qualifier_rate, 3),
    }


def _score_calibration(text: str, lines: list[str]) -> float:
    """20 pts: confidence qualifier presence + balanced claims vs hedges."""
    return _score_calibration_impl(text, lines)[0]


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------

def score_briefing(
    briefing_path: "Path | str",
    prev_domains: list[str] | None = None,
    artha_dir: Any = None,
) -> dict[str, Any]:
    """Score a briefing file. Returns a fully populated score dict."""
    briefing_path = Path(briefing_path)
    text = briefing_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    domain_names = _load_domain_names()

    act_score, act_meta = _score_actionability_impl(text, lines)
    spe_score, spe_meta = _score_specificity_impl(text, lines)
    com_score, com_meta = _score_completeness_impl(text, lines, domain_names)
    sig_score, sig_meta = _score_signal_purity_impl(text, lines, domain_names, prev_domains)
    cal_score, cal_meta = _score_calibration_impl(text, lines)

    total = round(act_score + spe_score + com_score + sig_score + cal_score, 2)

    # Compliance score via audit_compliance (optional — fails gracefully)
    compliance_score: int | None = None
    try:
        sys.path.insert(0, str(_SCRIPTS_DIR))
        from audit_compliance import audit_latest_briefing  # type: ignore[import]
        report = audit_latest_briefing(str(briefing_path))
        compliance_score = report.compliance_score
    except Exception:
        pass

    return {
        "schema_version": _SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "briefing_file": briefing_path.name,
        "quality_score": total,
        "compliance_score": compliance_score,
        "dimensions": {
            "actionability": act_score,
            "specificity": spe_score,
            "completeness": com_score,
            "signal_purity": sig_score,
            "calibration": cal_score,
        },
        "meta": {
            "actionability": act_meta,
            "specificity": spe_meta,
            "completeness": com_meta,
            "signal_purity": sig_meta,
            "calibration": cal_meta,
        },
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def append_score(score: dict[str, Any]) -> None:
    """Append *score* to state/briefing_scores.json; keep last 50 entries."""
    existing: list[dict] = []
    if _BRIEFING_SCORES.exists():
        try:
            existing = json.loads(_BRIEFING_SCORES.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    existing.append(score)
    if len(existing) > _MAX_STORED_SCORES:
        existing = existing[-_MAX_STORED_SCORES:]

    STATE_DIR.mkdir(exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, prefix=".briefing_scores-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, ensure_ascii=False, indent=2, default=str)
            fh.write("\n")
        os.replace(tmp_path, _BRIEFING_SCORES)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="eval_scorer.py",
        description="Score a catch-up briefing on five quality dimensions.",
    )
    p.add_argument("briefing_file", nargs="?", help="Path to briefing .md file")
    p.add_argument(
        "--latest",
        action="store_true",
        help="Auto-detect the latest briefing in briefings/",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON output")
    p.add_argument("--verbose", "-v", action="store_true", help="Show dimension breakdown")
    p.add_argument(
        "--no-save",
        action="store_true",
        help="Print score but do not write to briefing_scores.json",
    )
    return p.parse_args(argv)


def _find_latest_briefing() -> Path | None:
    """Return the most recently modified .md file in briefings/."""
    briefings_dir = _REPO_ROOT / "briefings"
    if not briefings_dir.exists():
        return None
    files = sorted(briefings_dir.glob("*.md"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.latest:
        path = _find_latest_briefing()
        if path is None:
            print("No briefings found in briefings/", file=sys.stderr)
            return 1
    elif args.briefing_file:
        path = Path(args.briefing_file)
    else:
        print("Provide a briefing file or use --latest", file=sys.stderr)
        return 1

    if not path.exists():
        print(f"Briefing file not found: {path}", file=sys.stderr)
        sys.exit(1)

    score = score_briefing(path)

    if args.json:
        print(json.dumps(score, indent=2, default=str))
    else:
        dims = score["dimensions"]
        print(f"Briefing: {score['briefing_file']}")
        print(f"Quality score: {score['quality_score']:.1f}/100")
        if score.get("compliance_score") is not None:
            print(f"Compliance:    {score['compliance_score']}/100")
        if args.verbose:
            print("\nDimension breakdown:")
            for dim, val in dims.items():
                print(f"  {dim:<18} {val:.1f}/20")

    if not args.no_save:
        try:
            append_score(score)
            if args.verbose:
                print(f"\n[eval_scorer] Score saved to {_BRIEFING_SCORES}", file=sys.stderr)
        except Exception as exc:
            print(f"[eval_scorer] Warning: could not save score: {exc}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
