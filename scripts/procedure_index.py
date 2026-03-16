#!/usr/bin/env python3
"""procedure_index.py — Index and search learned procedures in state/learned_procedures/.

Provides efficient lookup of agent-learned procedures for the pre-OODA
procedure check (AR-5, specs/agentic-reloaded.md).

Procedures are Markdown files with YAML frontmatter containing:
    domain, trigger, confidence, created, source

Usage:
    from procedure_index import find_matching_procedures, list_procedures

    matches = find_matching_procedures(
        query="USCIS status check IOE format",
        artha_dir=Path("."),
    )
    for m in matches:
        print(f"[{m.domain}] confidence={m.confidence:.1f}  {m.trigger}")
        print(f"  {m.file}")

Config flag: harness.agentic.procedural_memory.enabled (default: true)

Ref: specs/agentic-reloaded.md Phase AR-5
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────

PROCEDURES_DIR = "state/learned_procedures"
_CONFIDENCE_DECAY_DAYS = 90   # Days without re-validation before confidence decays
_CONFIDENCE_DECAY_RATE = 0.2  # Each interval reduces confidence by this amount
_MIN_DECAY_CONFIDENCE = 0.5   # Never decay below this floor


@dataclass
class ProcedureMatch:
    """A procedure that matched the caller's query."""

    file: Path
    domain: str
    trigger: str
    confidence: float    # 0.0–1.0 (decayed by age if not re-validated)
    created: str         # ISO date string
    relevance: float     # 0.0–1.0 match score against query


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML frontmatter dict from Markdown content (minimal parser)."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm_lines = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        fm_lines.append(line)
    try:
        import yaml
        return yaml.safe_load("\n".join(fm_lines)) or {}
    except Exception:
        # Fallback: simple key: value parsing
        result = {}
        for line in fm_lines:
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip()] = v.strip().strip("'\"")
        return result


def _decay_confidence(confidence: float, created: str) -> float:
    """Decay confidence for procedures not re-validated recently."""
    try:
        created_date = date.fromisoformat(created)
        days_old = (date.today() - created_date).days
        intervals = days_old // _CONFIDENCE_DECAY_DAYS
        if intervals <= 0:
            return confidence
        decayed = confidence - (intervals * _CONFIDENCE_DECAY_RATE)
        return max(_MIN_DECAY_CONFIDENCE, decayed)
    except (ValueError, TypeError):
        return confidence


def _compute_relevance(query: str, domain: str, trigger: str) -> float:
    """Score how relevant a procedure is to the query (0.0–1.0)."""
    if not query:
        return 0.0
    terms = [t.lower() for t in query.split() if len(t) >= 2]
    if not terms:
        return 0.0
    searchable = (domain + " " + trigger).lower()
    matched = sum(1 for t in terms if t in searchable)
    return matched / len(terms)


def _load_harness_flag(path: str, default: bool = True) -> bool:
    """Check a harness.* config flag (silent on missing config)."""
    try:
        import yaml
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "artha_config.yaml"
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        node: Any = raw.get("harness", {})
        for key in path.split("."):
            if not isinstance(node, dict):
                return default
            node = node.get(key, {})
        if isinstance(node, bool):
            return node
        if isinstance(node, dict):
            return node.get("enabled", default)
        return default
    except Exception:
        return default


def list_procedures(artha_dir: Path) -> list[ProcedureMatch]:
    """Return all known procedures with metadata.

    Returns empty list if directory missing or config flag disabled.
    """
    if not _load_harness_flag("agentic.procedural_memory.enabled"):
        return []

    proc_dir = artha_dir / PROCEDURES_DIR
    if not proc_dir.is_dir():
        return []

    results: list[ProcedureMatch] = []
    for md_file in sorted(proc_dir.glob("*.md")):
        if md_file.name.lower() == "readme.md":
            continue
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        fm = _parse_frontmatter(content)
        domain = str(fm.get("domain", "general"))
        trigger = str(fm.get("trigger", md_file.stem))
        created = str(fm.get("created", ""))
        raw_confidence = float(fm.get("confidence", 0.8))
        confidence = _decay_confidence(raw_confidence, created)

        results.append(ProcedureMatch(
            file=md_file,
            domain=domain,
            trigger=trigger,
            confidence=confidence,
            created=created,
            relevance=0.0,  # not query-specific for list_procedures
        ))

    return results


def find_matching_procedures(
    query: str,
    artha_dir: Path,
    min_confidence: float = 0.7,
    min_relevance: float = 0.3,
    max_results: int = 5,
) -> list[ProcedureMatch]:
    """Find procedures matching the query above confidence and relevance thresholds.

    Args:
        query: Free-text description of the current task/domain.
        artha_dir: Artha project root directory.
        min_confidence: Minimum (possibly decayed) confidence (default 0.7).
        min_relevance: Minimum relevance score for a match (default 0.3).
        max_results: Maximum procedures to return.

    Returns:
        Matching ProcedureMatch objects sorted by relevance × confidence (desc).
        Empty list if no matches or flag disabled.
    """
    procedures = list_procedures(artha_dir)
    if not procedures or not query.strip():
        return []

    scored: list[ProcedureMatch] = []
    for proc in procedures:
        if proc.confidence < min_confidence:
            continue
        relevance = _compute_relevance(query, proc.domain, proc.trigger)
        if relevance < min_relevance:
            continue
        scored.append(ProcedureMatch(
            file=proc.file,
            domain=proc.domain,
            trigger=proc.trigger,
            confidence=proc.confidence,
            created=proc.created,
            relevance=relevance,
        ))

    # Sort by relevance × confidence (compound score), then by created desc (newest first)
    scored.sort(key=lambda m: (m.relevance * m.confidence, m.created), reverse=True)
    return scored[:max_results]


def format_procedures_for_context(matches: list[ProcedureMatch]) -> str:
    """Format matched procedures as a compact string for AI context injection.

    Returns empty string if matches is empty.
    """
    if not matches:
        return ""
    lines = ["**Matching Learned Procedures (follow if confidence ≥ 0.7):**"]
    for m in matches:
        conf_pct = int(m.confidence * 100)
        lines.append(f"- [{m.domain}] {m.trigger} (confidence: {conf_pct}%)")
        lines.append(f"  See: {m.file.name}")
    return "\n".join(lines)


# ── CLI entry point ───────────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Search Artha learned procedures.",
    )
    parser.add_argument("query", nargs="?", default="", help="Task query (empty = list all)")
    parser.add_argument("--artha-dir", default=".", help="Artha root directory")
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    artha_dir = Path(args.artha_dir).resolve()
    if args.query:
        results = find_matching_procedures(args.query, artha_dir, min_confidence=args.min_confidence)
    else:
        results = list_procedures(artha_dir)

    if not results:
        print("No matching procedures found.")
        return

    if args.json:
        print(json.dumps(
            [{"domain": m.domain, "trigger": m.trigger, "confidence": m.confidence,
              "relevance": m.relevance, "file": str(m.file)}
             for m in results],
            indent=2,
        ))
    else:
        for m in results:
            print(f"[{m.domain}] confidence={m.confidence:.2f}  {m.trigger}")
            print(f"  {m.file}")
            print()


if __name__ == "__main__":
    _main()
