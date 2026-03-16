# pii-guard: ignore-file — utility code only; no personal data
"""
scripts/lib/cli_base.py — Common CLI argument definitions for Artha fetch scripts.

Each of the 6+ fetch scripts (gmail_fetch, gcal_fetch, msgraph_fetch,
icloud_mail_fetch, icloud_calendar_fetch, canvas_fetch) was defining the
same --health, --reauth, --since, --max-results flags independently.  This
module provides canonical add_*_args() helpers so scripts share identical
argument names, help text, and defaults.

Usage:
    import argparse
    from scripts.lib.cli_base import make_base_parser, add_email_args, add_calendar_args

    parser = make_base_parser("gmail_fetch", description="Fetch Gmail messages as JSONL.")
    add_email_args(parser)
    args = parser.parse_args()

Or compose manually:
    parser = argparse.ArgumentParser(...)
    add_common_args(parser)       # --health, --reauth, --dry-run
    add_email_args(parser)        # --since, --max-results, --folder
    add_calendar_args(parser)     # --from, --to, --today-plus-days

Ref: remediation.md §6.6, standardization.md §7.5.4
"""
from __future__ import annotations

import argparse
from typing import Optional


# ---------------------------------------------------------------------------
# Common arguments (all fetch scripts)
# ---------------------------------------------------------------------------

def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add universal flags present in every Artha fetch script.

    Adds:
        --health      Run connectivity / auth health-check and exit.
        --reauth      Force re-authentication (delete cached tokens) and exit.
        --dry-run     Validate config and auth without emitting any output.
        --debug       Enable verbose debug logging to stderr.
    """
    parser.add_argument(
        "--health",
        action="store_true",
        default=False,
        help="Run a connectivity / auth health-check and exit with 0 (ok) or 1 (fail).",
    )
    parser.add_argument(
        "--reauth",
        action="store_true",
        default=False,
        help="Force re-authentication by deleting cached tokens, then exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Validate config and connectivity without emitting any JSONL output.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable verbose debug logging to stderr.",
    )


# ---------------------------------------------------------------------------
# Email fetch arguments
# ---------------------------------------------------------------------------

def add_email_args(
    parser: argparse.ArgumentParser,
    *,
    default_max_results: int = 200,
    default_since: str = "7d",
) -> None:
    """Add email-fetch specific flags.

    Adds:
        --since N[d|h]     Lookback window (e.g. "7d", "24h"). Default: "7d".
        --max-results N    Maximum messages to fetch. Default: 200.
        --folder NAME      Mailbox folder/label to read (default varies by backend).
        --no-body          Emit only headers + snippet; omit full body text.
    """
    parser.add_argument(
        "--since",
        default=default_since,
        metavar="WINDOW",
        help=(
            "Lookback window: number followed by 'd' (days) or 'h' (hours). "
            f"Default: {default_since!r}. Example: --since 24h"
        ),
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=default_max_results,
        dest="max_results",
        metavar="N",
        help=f"Maximum number of messages to fetch. Default: {default_max_results}.",
    )
    parser.add_argument(
        "--folder",
        default=None,
        metavar="NAME",
        help="Mailbox folder or label to read (e.g. 'INBOX', 'Sent'). "
             "Default: backend-specific.",
    )
    parser.add_argument(
        "--no-body",
        action="store_true",
        default=False,
        dest="no_body",
        help="Emit only headers and snippet; skip full body text.",
    )


# ---------------------------------------------------------------------------
# Calendar fetch arguments
# ---------------------------------------------------------------------------

def add_calendar_args(
    parser: argparse.ArgumentParser,
    *,
    default_today_plus_days: int = 14,
) -> None:
    """Add calendar-fetch specific flags.

    Adds:
        --from DATE          Start date (ISO 8601, e.g. "2025-01-01").
        --to DATE            End date (ISO 8601).
        --today-plus-days N  Shorthand: from today to today + N days. Default: 14.
        --include-declined   Include events the user declined.
    """
    parser.add_argument(
        "--from",
        default=None,
        dest="date_from",
        metavar="DATE",
        help="Start date in ISO 8601 format (YYYY-MM-DD). "
             "Overrides --today-plus-days if set.",
    )
    parser.add_argument(
        "--to",
        default=None,
        dest="date_to",
        metavar="DATE",
        help="End date in ISO 8601 format (YYYY-MM-DD). "
             "Required if --from is set.",
    )
    parser.add_argument(
        "--today-plus-days",
        type=int,
        default=default_today_plus_days,
        dest="today_plus_days",
        metavar="N",
        help=f"Fetch events from today through today + N days. "
             f"Default: {default_today_plus_days}. Ignored when --from/--to are set.",
    )
    parser.add_argument(
        "--include-declined",
        action="store_true",
        default=False,
        dest="include_declined",
        help="Include calendar events that the user declined.",
    )


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------

def make_base_parser(
    script_name: str,
    *,
    description: str = "",
    epilog: Optional[str] = None,
) -> argparse.ArgumentParser:
    """Create an ArgumentParser pre-loaded with common Artha fetch arguments.

    Args:
        script_name: Used in the prog field (e.g. "gmail_fetch").
        description: Short one-line description of this fetch script.
        epilog:      Optional epilog text for --help output.

    Returns:
        ArgumentParser with --health, --reauth, --dry-run, --debug already added.
    """
    parser = argparse.ArgumentParser(
        prog=f"python scripts/{script_name}.py",
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_common_args(parser)
    return parser
