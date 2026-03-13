#!/usr/bin/env python3
"""
gmail_send.py — Artha Gmail send script
========================================
Sends catch-up briefings and other emails via the Gmail API using
the authenticated Artha Google account. Zero plaintext credential storage.

Usage:
  # Send with body as argument
  python scripts/gmail_send.py \
    --to "ved@gmail.com" \
    --subject "Artha · Mon Mar 9 — 🟠 2 items" \
    --body "Body text here"

  # Send with body from stdin (typical use: pipe from catch-up)
  cat briefing.md | python scripts/gmail_send.py \
    --to "ved@gmail.com" \
    --subject "Artha · Mon Mar 9 — 🟠 2 items"

  # Send with HTML body
  python scripts/gmail_send.py \
    --to "ved@gmail.com" \
    --subject "Artha · ..." \
    --html "<h1>Briefing</h1><p>...</p>"

  # Dry-run (validates auth, prints what would be sent, does NOT send)
  python scripts/gmail_send.py \
    --to "ved@gmail.com" \
    --subject "Artha · Mon Mar 9" \
    --body "Briefing text" \
    --dry-run

  # Send and archive to briefings/YYYY-MM-DD.md
  python scripts/gmail_send.py \
    --to "ved@gmail.com" \
    --subject "Artha · Mon Mar 9" \
    --body "Briefing text" \
    --archive

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
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

ARTHA_DIR     = os.path.expanduser("~/OneDrive/Artha")
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

    for line in lines:
        escaped = html_lib.escape(line)

        # Dividers
        if re.match(r"^━{3,}", line):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append('<hr style="border: none; border-top: 2px solid #333; margin: 12px 0;">')
            continue

        # Section headers (## or ###)
        m = re.match(r"^(#{2,3})\s+(.*)", line)
        if m:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            level = "2" if m.group(1) == "##" else "3"
            text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", html_lib.escape(m.group(2)))
            color = "#111" if level == "2" else "#444"
            html_lines.append(
                f'<h{level} style="margin: 16px 0 4px; color: {color};">{text}</h{level}>'
            )
            continue

        # Bullet lines
        bullet_match = re.match(r"^•\s+(.*)", line)
        if bullet_match:
            if not in_list:
                html_lines.append('<ul style="margin: 4px 0; padding-left: 20px;">')
                in_list = True
            text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", html_lib.escape(bullet_match.group(1)))
            html_lines.append(f'<li style="margin: 2px 0;">{text}</li>')
            continue

        # Close list if needed
        if in_list and line.strip():
            html_lines.append("</ul>")
            in_list = False

        # Empty line
        if not line.strip():
            html_lines.append("<br>")
            continue

        # Code blocks (``` ... ```) — render in monospace
        if line.startswith("```"):
            html_lines.append('<pre style="background:#f5f5f5; padding:8px; '
                             'font-family:monospace; font-size:12px; border-radius:4px;">')
            continue

        # Regular paragraph
        text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", escaped)
        html_lines.append(f"<p style='margin: 4px 0;'>{text}</p>")

    if in_list:
        html_lines.append("</ul>")
    html_lines.extend(["</div>", "</body></html>"])
    return "\n".join(html_lines)


def archive_briefing(body_text: str, subject: str) -> dict:
    """
    Save briefing body to briefings/YYYY-MM-DD.md.
    Returns {"archived": True, "path": "..."} or {"archived": False, "error": "..."}
    """
    try:
        os.makedirs(BRIEFINGS_DIR, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        archive_path = os.path.join(BRIEFINGS_DIR, f"{today}.md")

        # Build a clean archive file with metadata header
        header = (
            f"---\n"
            f"date: {today}\n"
            f"subject: {subject}\n"
            f"archived: {datetime.now().isoformat()}\n"
            f"sensitivity: standard\n"
            f"---\n\n"
        )

        # If file already exists today (e.g. second catch-up), append with separator
        if os.path.exists(archive_path):
            with open(archive_path, "a") as f:
                f.write(f"\n\n---\n# Second Run\n\n{body_text}\n")
        else:
            with open(archive_path, "w") as f:
                f.write(header + body_text + "\n")

        return {"archived": True, "path": archive_path}
    except OSError as exc:
        return {"archived": False, "error": str(exc)}


def send_email(
    to: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    dry_run: bool = False,
    archive: bool = False,
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
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send an email via Gmail API. Body can come from --body or stdin."
    )
    # --to and --subject are not marked required=True so --health can work standalone
    parser.add_argument("--to",       default=None, help="Recipient email address")
    parser.add_argument("--subject",  default=None, help="Email subject line")
    parser.add_argument("--body",     default=None, help="Plain text body")
    parser.add_argument("--html",     default=None, help="HTML body (overrides auto-HTML)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Validate auth and log, but do NOT actually send the email")
    parser.add_argument("--archive",  action="store_true",
                        help="Archive briefing body to briefings/YYYY-MM-DD.md after sending")
    parser.add_argument("--health",   action="store_true",
                        help="Test send connection (checks auth, does NOT send)")

    args = parser.parse_args()

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

    # ── Get body: --body arg takes precedence, then stdin ─────────────────
    body_text = args.body
    if body_text is None:
        if sys.stdin.isatty():
            print("ERROR: Provide --body or pipe content to stdin.", file=sys.stderr)
            sys.exit(1)
        body_text = sys.stdin.read()

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
