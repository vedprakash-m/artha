#!/usr/bin/env python3
"""session_search.py — Cross-session recall over historical briefings.

Provides full-text search over ``briefings/`` and ``summaries/`` directories.
Returns ranked excerpts from matching files for use in OODA OBSERVE and
cross-session recall (AR-4, specs/agentic-reloaded.md).

Design decision: grep-over-files (not SQLite FTS5) because:
- ~365 files/year is comfortable for in-process grep
- Aligns with "state in Markdown" philosophy (Tech Spec §1.1)
- Zero new infrastructure (no DB file to backup/encrypt)
- Upgrade path to FTS5 preserved via SearchResult abstraction

Usage:
    from session_search import search_sessions

    results = search_sessions(
        query="immigration deadline extension",
        artha_dir=Path("."),
        max_results=5,
    )
    for r in results:
        print(f"[{r.date}] {r.excerpt}  ({r.match_count} matches)")

    # CLI usage
    python3 scripts/session_search.py "USCIS status" --max-results 3

Config flag: harness.agentic.session_search.enabled (default: true)

Ref: specs/agentic-reloaded.md Phase AR-4
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── PII guard import (optional — degrades gracefully if unavailable) ──────────
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pii_guard import scrub as _pii_scrub
    _PII_AVAILABLE = True
except ImportError:
    _PII_AVAILABLE = False

    def _pii_scrub(text: str) -> str:  # type: ignore[misc]
        return text


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    """A single search result from a historical briefing or summary file."""

    file: Path
    date: str            # Extracted from filename YYYY-MM-DD, or "unknown"
    excerpt: str         # ~200 chars of surrounding context
    match_count: int     # Total number of distinct query terms matched
    relevance: float = field(default=0.0)  # match_count / file_token_count


def _extract_date_from_path(path: Path) -> str:
    """Extract YYYY-MM-DD from filename if present, otherwise 'unknown'."""
    match = re.search(r"\d{4}-\d{2}-\d{2}", path.stem)
    return match.group(0) if match else "unknown"


def _build_excerpt(content: str, match_positions: list[int], context_chars: int = 200) -> str:
    """Build a ~context_chars excerpt around the first match position."""
    if not match_positions:
        return content[:context_chars].replace("\n", " ").strip()
    pos = match_positions[0]
    start = max(0, pos - context_chars // 2)
    end = min(len(content), pos + context_chars // 2)
    excerpt = content[start:end].replace("\n", " ").strip()
    # Add ellipsis if we truncated
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(content):
        excerpt = excerpt + "…"
    return _pii_scrub(excerpt)


def _search_file(
    path: Path,
    terms: list[str],
    context_chars: int = 200,
) -> tuple[int, list[int], str]:
    """Search a single file for all query terms.

    Returns (match_count, match_positions, excerpt).
    match_count is the number of distinct terms found (not total occurrences).
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, [], ""

    content_lower = content.lower()
    match_positions: list[int] = []
    matched_terms = 0

    for term in terms:
        term_lower = term.lower()
        pos = content_lower.find(term_lower)
        if pos != -1:
            matched_terms += 1
            match_positions.append(pos)

    if not match_positions:
        return 0, [], ""

    excerpt = _build_excerpt(content, match_positions, context_chars)
    return matched_terms, match_positions, excerpt


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


def search_sessions(
    query: str,
    artha_dir: Path,
    max_results: int = 5,
    *,
    search_dirs: tuple[str, ...] = ("briefings", "summaries"),
    min_terms_matched: int = 1,
) -> list[SearchResult]:
    """Search historical briefings and summaries for cross-session recall.

    Splits query into whitespace-delimited terms, searches each .md file
    in search_dirs, ranks by relevance (matched_terms / sqrt(file_lines)),
    returns top max_results results with surrounding context excerpts.

    Args:
        query: Free-text search query (whitespace-delimited terms).
        artha_dir: Artha project root directory.
        max_results: Maximum number of results to return.
        search_dirs: Subdirectory names to search (relative to artha_dir).
        min_terms_matched: Minimum distinct terms that must match for a result.

    Returns:
        List of SearchResult sorted by relevance (highest first).
        Empty list if query is empty, config flag disabled, or no matches.
    """
    if not query or not query.strip():
        return []

    if not _load_harness_flag("agentic.session_search.enabled"):
        return []

    terms = [t.strip() for t in query.split() if len(t.strip()) >= 2]
    if not terms:
        return []

    results: list[SearchResult] = []

    for dir_name in search_dirs:
        search_dir = artha_dir / dir_name
        if not search_dir.is_dir():
            continue
        for md_file in sorted(search_dir.glob("*.md"), reverse=True):
            # Skip README-style files
            if md_file.name.lower() in ("readme.md", "index.md"):
                continue
            match_count, positions, excerpt = _search_file(md_file, terms)
            if match_count < min_terms_matched or not excerpt:
                continue

            # Relevance: terms matched / sqrt(file size in lines) — larger files penalised
            try:
                line_count = max(1, md_file.read_text(encoding="utf-8", errors="replace").count("\n"))
            except OSError:
                line_count = 1
            relevance = match_count / (line_count ** 0.5)

            results.append(SearchResult(
                file=md_file,
                date=_extract_date_from_path(md_file),
                excerpt=excerpt,
                match_count=match_count,
                relevance=relevance,
            ))

    # Sort by relevance desc, then date desc for ties
    results.sort(key=lambda r: (-r.relevance, r.date), reverse=False)
    results.sort(key=lambda r: r.relevance, reverse=True)
    return results[:max_results]


def format_results_for_context(results: list[SearchResult]) -> str:
    """Format search results as a compact string for AI context injection.

    Returns an empty string if results is empty.
    """
    if not results:
        return ""
    lines = ["**Cross-Session Recall:**"]
    for r in results:
        lines.append(f"- [{r.date}] {r.excerpt}")
    return "\n".join(lines)


# ── CLI entry point ───────────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Search historical Artha briefings and summaries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query", help="Search query (whitespace-delimited terms)")
    parser.add_argument("--artha-dir", default=".", help="Artha root directory (default: .)")
    parser.add_argument("--max-results", type=int, default=5, help="Max results (default: 5)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--dirs", nargs="+", default=["briefings", "summaries"],
                        help="Directories to search")
    args = parser.parse_args()

    artha_dir = Path(args.artha_dir).resolve()
    results = search_sessions(
        query=args.query,
        artha_dir=artha_dir,
        max_results=args.max_results,
        search_dirs=tuple(args.dirs),
    )

    if not results:
        print("No results found.")
        return

    if args.json:
        print(json.dumps(
            [{"date": r.date, "file": str(r.file), "excerpt": r.excerpt,
              "match_count": r.match_count, "relevance": round(r.relevance, 4)}
             for r in results],
            indent=2,
        ))
    else:
        for r in results:
            print(f"[{r.date}] ({r.match_count} match{'es' if r.match_count != 1 else ''})")
            print(f"  {r.excerpt}")
            print()


if __name__ == "__main__":
    _main()
