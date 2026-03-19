#!/usr/bin/env python3
# pii-guard: ignore-file — handler; PII guard applied by ActionExecutor before this runs
"""
scripts/actions/email_send.py — Email compose-and-send handler.

Wraps and extends scripts/gmail_send.py to conform to the ActionHandler protocol.

SAFETY:
  - draft_first=true (default): creates a Gmail draft first; user sees exact email
    before it is sent. The draft is visible in Gmail Drafts.
  - draft_first=false: direct send (only for pre-approved templates at Trust Level 2).
  - autonomy_floor: true in actions.yaml — ALWAYS requires human approval.
  - Undo: Gmail messages.trash() within undo_window_sec (default 30s).

API: Gmail API messages.send / drafts.create / drafts.send
Scope: gmail.send + gmail.readonly (already authorized in google_auth.py GMAIL_SCOPES)

Ref: specs/act.md §8.1
"""
from __future__ import annotations

import base64
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from actions.base import ActionProposal, ActionResult


# ---------------------------------------------------------------------------
# Required parameters
# ---------------------------------------------------------------------------

_REQUIRED_PARAMS = ("to", "subject", "body")


# ---------------------------------------------------------------------------
# Protocol functions
# ---------------------------------------------------------------------------

def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Check that all required parameters are present and structurally valid."""
    params = proposal.parameters

    # Required field presence
    for field in _REQUIRED_PARAMS:
        if not params.get(field, "").strip():
            return False, f"Missing required parameter: '{field}'"

    # Recipient format: basic sanity check (not empty, contains @)
    to_field = params.get("to", "")
    recipients = [r.strip() for r in to_field.split(",") if r.strip()]
    if not recipients:
        return False, "Parameter 'to' must contain at least one recipient"
    for r in recipients:
        if "@" not in r:
            return False, f"Invalid recipient address: '{r}' (missing @)"

    # Subject length
    subject = params.get("subject", "")
    if len(subject) > 998:  # RFC 5321 limit
        return False, f"Subject line too long ({len(subject)} chars; max 998)"

    # Body not absurdly large (10 MB soft limit)
    body = params.get("body", "")
    if len(body.encode("utf-8")) > 10 * 1024 * 1024:
        return False, "Email body exceeds 10 MB; split into smaller messages"

    return True, ""


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Create a Gmail draft (visible in Drafts folder) and return the draft URL.

    Creates an ACTUAL draft via Gmail API so the user can preview the exact
    email that will be sent. The draft must be manually deleted if not approved.
    """
    params = proposal.parameters
    to = params.get("to", "")
    subject = params.get("subject", "")
    body = params.get("body", "")
    draft_first = params.get("draft_first", True)

    if not draft_first:
        # Direct-send mode: return preview without API call
        return ActionResult(
            status="success",
            message=f"Preview: email to {to} | Subject: {subject!r} | Body: {len(body)} chars",
            data={"preview_mode": True, "to": to, "subject": subject, "body_length": len(body)},
            reversible=False,
            reverse_action=None,
        )

    try:
        from google_auth import build_service  # noqa: PLC0415
        service = build_service("gmail", "v1")

        # Build the MIME message
        mime_msg = _build_mime(to, subject, body, params)
        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")

        # Create draft
        draft_body: dict[str, Any] = {"message": {"raw": raw}}
        if params.get("thread_id"):
            draft_body["message"]["threadId"] = params["thread_id"]

        draft = service.users().drafts().create(userId="me", body=draft_body).execute()
        draft_id = draft.get("id", "")

        # Note: Gmail web URL for drafts
        draft_url = f"https://mail.google.com/mail/u/0/#drafts/{draft_id}"

        return ActionResult(
            status="success",
            message=f"Draft created: {draft_url}",
            data={
                "draft_id": draft_id,
                "draft_url": draft_url,
                "to": to,
                "subject": subject,
                "body_length": len(body),
            },
            reversible=False,
            reverse_action=None,
        )

    except Exception as e:
        return ActionResult(
            status="failure",
            message=f"Failed to create draft: {e}",
            data={"error": str(e)},
            reversible=False,
            reverse_action=None,
        )


def execute(proposal: ActionProposal) -> ActionResult:
    """Send the email via Gmail API.

    If draft_id exists in parameters (from a previous dry_run), sends that
    draft via drafts.send (converts draft to sent message).
    Otherwise uses messages.send directly.

    Idempotency: if proposal.id has already been sent (succeeded status),
    the executor skips re-execution. handlers do NOT need to check this —
    ActionExecutor handles idempotency at the queue layer.
    """
    params = proposal.parameters
    to = params.get("to", "")
    subject = params.get("subject", "")
    body = params.get("body", "")
    draft_id = params.get("draft_id")

    try:
        from google_auth import build_service  # noqa: PLC0415
        service = build_service("gmail", "v1")

        if draft_id:
            # Send existing draft
            result = service.users().drafts().send(
                userId="me",
                body={"id": draft_id},
            ).execute()
        else:
            # Direct send
            mime_msg = _build_mime(to, subject, body, params)
            raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
            result = service.users().messages().send(
                userId="me",
                body={"raw": raw},
            ).execute()

        message_id = result.get("id", "")

        # The undo_deadline is computed by ActionExecutor and injected into result.data
        return ActionResult(
            status="success",
            message=f"✅ Email sent to {to}",
            data={
                "message_id": message_id,
                "to": to,
                "subject": subject,
                "thread_id": result.get("threadId", ""),
                "draft_id": draft_id,
                # undo_deadline is added by ActionExecutor after execute() returns
            },
            reversible=True,
            reverse_action=None,  # built by build_reverse_proposal()
        )

    except Exception as e:
        return ActionResult(
            status="failure",
            message=f"Failed to send email: {e}",
            data={"error": str(e), "to": to},
            reversible=False,
            reverse_action=None,
        )


def build_reverse_proposal(
    original: ActionProposal,
    result_data: dict[str, Any],
) -> ActionProposal:
    """Build an undo proposal that moves the sent message to Trash.

    Called by ActionExecutor.undo() within the undo window.
    """
    import uuid
    from datetime import timedelta

    message_id = result_data.get("message_id", "")
    if not message_id:
        raise ValueError("Cannot undo: message_id not found in result data")

    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type="email_send_undo",
        domain=original.domain,
        title=f"Undo: Move sent email to Trash",
        description=f"Move message {message_id} to Gmail Trash (undo email send)",
        parameters={
            "message_id": message_id,
            "action": "trash",
        },
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=None,
        source_step=original.source_step,
        source_skill=original.source_skill,
        linked_oi=original.linked_oi,
    )


def health_check() -> bool:
    """Verify Gmail send credentials are available."""
    try:
        from google_auth import check_stored_credentials  # noqa: PLC0415
        status = check_stored_credentials()
        return bool(status.get("gmail_token_stored", False))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_mime(
    to: str,
    subject: str,
    body: str,
    params: dict[str, Any],
) -> MIMEMultipart:
    """Build a MIME multipart message from action parameters."""
    from google_auth import build_service  # noqa: PLC0415
    service = build_service("gmail", "v1")
    profile = service.users().getProfile(userId="me").execute()
    from_addr = profile.get("emailAddress", "me")

    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["From"] = from_addr
    msg["Subject"] = subject

    cc = params.get("cc")
    if cc:
        msg["Cc"] = cc
    bcc = params.get("bcc")
    if bcc:
        msg["Bcc"] = bcc
    if params.get("in_reply_to"):
        msg["In-Reply-To"] = params["in_reply_to"]
        msg["References"] = params.get("references", params["in_reply_to"])

    # Always include plain text
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Auto-generate HTML
    try:
        _scripts = Path(__file__).resolve().parent.parent
        if str(_scripts) not in sys.path:
            sys.path.insert(0, str(_scripts))
        import gmail_send  # noqa: PLC0415
        html_body = gmail_send._markdown_to_html(body)
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    except Exception:
        pass  # HTML optional; plain text always included

    return msg
