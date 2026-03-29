"""scripts/backfill — Phase 1.5 Backfill Engine.

Parses 82 work-scrape files → cross-references with existing state →
produces historical weekly reflections for the Reflection Loop.

Ref: specs/reflection-loop.md §6
"""
from backfill.scrape_parser import parse_scrape_file, detect_format_family, ParsedScrapeWeek
from backfill.cross_reference import enrich_week, EnrichedWeek
from backfill.backfill_runner import run_backfill, run_backfill_review, BackfillResult

__all__ = [
    "parse_scrape_file",
    "detect_format_family",
    "ParsedScrapeWeek",
    "enrich_week",
    "EnrichedWeek",
    "run_backfill",
    "run_backfill_review",
    "BackfillResult",
]
