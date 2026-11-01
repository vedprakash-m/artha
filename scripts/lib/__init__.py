# pii-guard: ignore-file — this package contains code patterns, not PII
"""
scripts/lib — Shared infrastructure for Artha fetch scripts.

Modules:
    retry           — Configurable retry with exponential backoff
    html_processing — HTML stripping and email footer removal
    output          — JSONL formatting with consistent field ordering
    cli_base        — Common argparse argument groups
    common          — Canonical ARTHA_DIR and shared constants

Usage:
    from scripts.lib.retry import with_retry
    from scripts.lib.html_processing import strip_html, strip_footers
    from scripts.lib.output import emit_jsonl, truncate_body
    from scripts.lib.cli_base import add_common_args, add_email_args
    from scripts.lib.common import ARTHA_DIR, SCRIPTS_DIR

Ref: remediation.md §6, standardization.md §7.5
"""
