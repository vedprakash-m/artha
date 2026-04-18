#!/usr/bin/env python3
"""
gmail_send.py — Artha Gmail send script
========================================
Sends catch-up briefings and other emails via the Gmail API using
the authenticated Artha Google account. Zero plaintext credential storage.

Usage:
  # Send with body as argument
  python scripts/gmail_send.py \
    --to "you@gmail.com" \
    --subject "Artha · Mon Mar 9 — 🟠 2 items" \
    --body "Body text here"

  # Send with body from stdin (typical use: pipe from catch-up)
  cat briefing.md | python scripts/gmail_send.py \
    --to "you@gmail.com" \
    --subject "Artha · Mon Mar 9 — 🟠 2 items"

  # Send with HTML body
  python scripts/gmail_send.py \
    --to "you@gmail.com" \
    --subject "Artha · ..." \
    --html "<h1>Briefing</h1><p>...</p>"

  # Dry-run (validates auth, prints what would be sent, does NOT send)
  python scripts/gmail_send.py \
    --to "you@gmail.com" \
    --subject "Artha · Mon Mar 9" \
    --body "Briefing text" \
    --dry-run

  # Send and archive to briefings/YYYY-MM-DD.md
  python scripts/gmail_send.py \
    --to "you@gmail.com" \
    --subject "Artha · Mon Mar 9" \
    --body "Briefing text" \
    --archive

  # Archive only (no email send) — use this from CLI catch-up when email is not configured
  python scripts/gmail_send.py \
    --subject "Artha · Mon Mar 9" \
    --body "Briefing text" \
    --archive-only

Output (JSON to stdout):
  {"status": "sent", "message_id": "...", "to": "...", "subject": "...", "archived": true}
  {"status": "dry_run", "to": "...", "subject": "...", "body_length": 1234}
  {"status": "error", "error": "..."}

Exit code: 0 = success, 1 = error.

Ref: TS §3.4, T-1A.7.4, T-1A.11.2 (archive), T-1A.11.7 (dry-run)
"""

from __future__ import annotations

import sys
# Ensure we run inside the Artha venv. Ref: standardization.md §7.3
from _bootstrap import reexec_in_venv; reexec_in_venv()

import argparse
import base64
import json
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from lib.common import ARTHA_DIR as _ARTHA_DIR_PATH

ARTHA_DIR     = str(_ARTHA_DIR_PATH)
BRIEFINGS_DIR = os.path.join(ARTHA_DIR, "briefings")


def _markdown_to_html(markdown_body: str) -> str:
    """
    Convert Artha briefing markdown to clean HTML for email rendering.
    Handles the specific patterns used in Artha briefings:
    - ━━━ dividers → <hr>
    - ## Section headers → <h2>
    - • bullet lines → <li>
    - Bold **text** → <b>text</b>
    - Emoji severity markers preserved as-is (render in all modern clients)
    """
    import html as html_lib
    import re

    lines = markdown_body.split("\n")
    html_lines: list[str] = [
        "<html><body>",
        '<div style="font-family: -apple-system, Helvetica, Arial, sans-serif; '
        'font-size: 14px; line-height: 1.6; max-width: 650px; margin: 0 auto; '
        'padding: 20px; color: #222;">',
    ]
    in_list = False
    in_table = False

    def _fmt(text: str) -> str:
        """Apply inline formatting (bold, italic) to escaped HTML text."""
        text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
        return text

    def _table_cell(cell: str, tag: str = "td") -> str:
        """Render a single table cell with inline formatting."""
        content = _fmt(html_lib.escape(cell.strip()))
        if tag == "th":
            return (f'<th style="padding:6px 10px; border:1px solid #ddd; '
                    f'background:#f0f0f0; font-weight:600; text-align:left;">{content}</th>')
        return (f'<td style="padding:6px 10px; border:1px solid #ddd; '
                f'text-align:left;">{content}</td>')

    def _is_table_row(line: str) -> bool:
        return bool(line.strip().startswith("|") and line.strip().endswith("|"))

    def _is_separator_row(line: str) -> bool:
        return bool(re.match(r"^\|[\s\-:|]+\|$", line.strip()))

    def _parse_cells(line: str) -> list[str]:
        """Split a pipe-delimited line into cell contents."""
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [c.strip() for c in stripped.split("|")]

    i = 0
    while i < len(lines):
        line = lines[i]
        escaped = html_lib.escape(line)

        # ── Table detection ────────────────────────────────────────────
        if _is_table_row(line):
            if not in_table:
                # Close any open list
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
                # Check if next line is a separator (header row)
                is_header = (i + 1 < len(lines) and _is_separator_row(lines[i + 1]))
                html_lines.append(
                    '<table style="border-collapse:collapse; margin:8px 0; '
                    'font-size:13px; width:100%;">')
                if is_header:
                    cells = _parse_cells(line)
                    html_lines.append("<thead><tr>")
                    html_lines.extend(_table_cell(c, "th") for c in cells)
                    html_lines.append("</tr></thead><tbody>")
                    i += 2  # skip header + separator
                    in_table = True
                    continue
                else:
                    html_lines.append("<tbody>")
                    in_table = True

            if _is_separator_row(line):
                # Separator row inside table — skip
                i += 1
                continue

            cells = _parse_cells(line)
            html_lines.append("<tr>")
            html_lines.extend(_table_cell(c) for c in cells)
            html_lines.append("</tr>")
            i += 1
            continue

        # Close table if we were in one
        if in_table:
            html_lines.append("</tbody></table>")
            in_table = False

        # ── Dividers ───────────────────────────────────────────────────
        if re.match(r"^━{3,}", line) or re.match(r"^-{3,}$", line.strip()):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append('<hr style="border: none; border-top: 2px solid #333; margin: 12px 0;">')
            i += 1
            continue

        # ── Section headers (## or ###) ────────────────────────────────
        m = re.match(r"^(#{2,3})\s+(.*)", line)
        if m:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            level = "2" if m.group(1) == "##" else "3"
            text = _fmt(html_lib.escape(m.group(2)))
            color = "#111" if level == "2" else "#444"
            html_lines.append(
                f'<h{level} style="margin: 16px 0 4px; color: {color};">{text}</h{level}>'
            )
            i += 1
            continue

        # ── Bullet lines (• or - ) ────────────────────────────────────
        bullet_match = re.match(r"^[•\-]\s+(.*)", line)
        if bullet_match:
            if not in_list:
                html_lines.append('<ul style="margin: 4px 0; padding-left: 20px;">')
                in_list = True
            text = _fmt(html_lib.escape(bullet_match.group(1)))
            html_lines.append(f'<li style="margin: 2px 0;">{text}</li>')
            i += 1
            continue

        # Close list if needed
        if in_list and line.strip():
            html_lines.append("</ul>")
            in_list = False

        # Empty line
        if not line.strip():
            html_lines.append("<br>")
            i += 1
            continue

        # Code blocks (``` ... ```) — render in monospace
        if line.startswith("```"):
            html_lines.append('<pre style="background:#f5f5f5; padding:8px; '
                             'font-family:monospace; font-size:12px; border-radius:4px;">')
            i += 1
            continue

        # Regular paragraph
        text = _fmt(escaped)
        html_lines.append(f"<p style='margin: 4px 0;'>{text}</p>")
        i += 1

    if in_table:
        html_lines.append("</tbody></table>")
    if in_list:
        html_lines.append("</ul>")
    html_lines.extend(["</div>", "</body></html>"])
    return "\n".join(html_lines)


def archive_briefing(body_text: str, subject: str) -> dict:
    """
    Save briefing body to briefings/YYYY-MM-DD.md via canonical archive helper.
    Returns {"archived": True, "path": "..."} or {"archived": False, "error": "..."}
    """
    try:
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from lib.briefing_archive import save as _archive_save  # noqa: PLC0415
        result = _archive_save(body_text, source="email", subject=subject)
        if result["status"] in ("ok", "skipped"):
            return {"archived": True, "path": result.get("path", "")}
        return {"archived": False, "error": result.get("error", "unknown")}
    except Exception as exc:  # noqa: BLE001
        return {"archived": False, "error": str(exc)}


def send_email(
    to: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    dry_run: bool = False,
    archive: bool = True,
) -> dict:
    """
    Send an email via Gmail API.

    Args:
        to:         recipient email address(es), comma-separated
        subject:    email subject line
        body_text:  plain text body (always included for clients without HTML support)
        body_html:  optional HTML body (if None, generated from body_text)
        dry_run:    if True, validate auth and log but do NOT send the email
        archive:    if True, save body_text to briefings/YYYY-MM-DD.md

    Returns:
        dict with status, message_id, to, subject, archived
    """
    import os
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # ── Archive first (independent of send success) ───────────────────────
    archive_result = {"archived": False}
    if archive:
        archive_result = archive_briefing(body_text, subject)
        if not archive_result.get("archived"):
            print(
                f"[gmail_send] WARNING: Archive failed: {archive_result.get('error')}",
                file=sys.stderr,
            )

    # ── Dry-run: validate auth but don't send ─────────────────────────────
    if dry_run:
        try:
            from google_auth import build_service
            service = build_service("gmail", "v1")
            profile = service.users().getProfile(userId="me").execute()
            from_addr = profile.get("emailAddress", "?")
        except ImportError:
            return {"status": "error", "error": "google_auth.py not found (dry-run auth check)"}
        except Exception as exc:
            return {"status": "error", "error": f"Auth check failed: {exc}"}
        return {
            "status":      "dry_run",
            "to":          to,
            "from":        from_addr,
            "subject":     subject,
            "body_length": len(body_text),
            "archived":    archive_result.get("archived", False),
            "archive_path": archive_result.get("path", ""),
        }

    try:
        from google_auth import build_service
    except ImportError:
        return {"status": "error", "error": "google_auth.py not found"}

    try:
        service = build_service("gmail", "v1")
        profile = service.users().getProfile(userId="me").execute()
        from_addr = profile.get("emailAddress", "me")
    except Exception as exc:
        return {"status": "error", "error": f"Gmail connection failed: {exc}"}

    # Build MIME message
    if body_html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))
    else:
        # Auto-generate HTML from Artha markdown formatting
        generated_html = _markdown_to_html(body_text)
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(generated_html, "html", "utf-8"))

    msg["To"]      = to
    msg["From"]    = from_addr
    msg["Subject"] = subject

    # Encode and send
    raw_bytes = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    try:
        result = service.users().messages().send(
            userId="me",
            body={"raw": raw_bytes},
        ).execute()
        return {
            "status":      "sent",
            "message_id":  result.get("id", ""),
            "to":          to,
            "from":        from_addr,
            "subject":     subject,
            "archived":    archive_result.get("archived", False),
            "archive_path": archive_result.get("path", ""),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc), "to": to, "subject": subject}


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _resolve_body(args: argparse.Namespace) -> Optional[str]:
    """Return body text from --body, --body-file, or stdin (in that order)."""
    if args.body is not None:
        return args.body
    if getattr(args, "body_file", None) is not None:
        try:
            return open(args.body_file, encoding="utf-8").read()
        except OSError as exc:
            print(f"ERROR: Cannot read --body-file {args.body_file}: {exc}", file=sys.stderr)
            sys.exit(1)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return None


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send an email via Gmail API. Body can come from --body or stdin."
    )
    # --to and --subject are not marked required=True so --health can work standalone
    parser.add_argument("--to",       default=None, help="Recipient email address")
    parser.add_argument("--subject",  default=None, help="Email subject line")
    parser.add_argument("--body",      default=None, help="Plain text body")
    parser.add_argument("--body-file", default=None, metavar="PATH",
                        help="Read plain text body from file (e.g. tmp/current_briefing.md)")
    parser.add_argument("--html",      default=None, help="HTML body (overrides auto-HTML)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Validate auth and log, but do NOT actually send the email")
    parser.add_argument("--archive",  action="store_true", default=True,
                        help="Archive briefing body to briefings/YYYY-MM-DD.md after sending (default: on)")
    parser.add_argument("--no-archive", action="store_false", dest="archive",
                        help="Suppress archival to briefings/ (overrides --archive default)")
    parser.add_argument("--archive-only", action="store_true",
                        help="Save body to briefings/YYYY-MM-DD.md without sending email (no --to required)")
    parser.add_argument("--health",   action="store_true",
                        help="Test send connection (checks auth, does NOT send)")

    args = parser.parse_args()

    # ── Archive-only — no Gmail auth needed ───────────────────────────────
    if args.archive_only:
        if not args.subject:
            print("ERROR: --subject is required for --archive-only.", file=sys.stderr)
            sys.exit(1)
        body_text = _resolve_body(args)
        if body_text is None:
            print("ERROR: Provide --body, --body-file, or pipe content to stdin.", file=sys.stderr)
            sys.exit(1)
        if not body_text.strip():
            print("ERROR: Body is empty.", file=sys.stderr)
            sys.exit(1)
        result = archive_briefing(body_text, args.subject)
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result.get("archived") else 1)

    # ── Health check — standalone, no --to / --subject needed ─────────────
    if args.health:
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        try:
            from google_auth import check_stored_credentials
        except ImportError:
            print(json.dumps({"status": "error", "error": "google_auth.py not found"}))
            sys.exit(1)
        status = check_stored_credentials()
        has_token = status.get("gmail_token_stored", False)
        print(json.dumps({"status": "ok" if has_token else "not_configured",
                          "gmail_token_stored": has_token}))
        sys.exit(0 if has_token else 1)
        return

    # ── Validate required args for send / dry-run ─────────────────────────
    if not args.to:
        print("ERROR: --to is required.", file=sys.stderr)
        sys.exit(1)
    if not args.subject:
        print("ERROR: --subject is required.", file=sys.stderr)
        sys.exit(1)

    # ── Get body: --body / --body-file / stdin ────────────────────────────
    body_text = _resolve_body(args)
    if body_text is None:
        print("ERROR: Provide --body, --body-file, or pipe content to stdin.", file=sys.stderr)
        sys.exit(1)

    if not body_text.strip():
        print("ERROR: Email body is empty.", file=sys.stderr)
        sys.exit(1)

    result = send_email(
        to=args.to,
        subject=args.subject,
        body_text=body_text,
        body_html=args.html,
        dry_run=args.dry_run,
        archive=args.archive,
    )

    print(json.dumps(result, ensure_ascii=False))

    ok_statuses = {"sent", "dry_run"}
    if result.get("status") not in ok_statuses:
        sys.exit(1)


if __name__ == "__main__":
    main()
