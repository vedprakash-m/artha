#!/usr/bin/env python3
# pii-guard: ignore-file — handler; PII guard applied by ActionExecutor before this runs
"""
scripts/actions/email_reply.py — Reply-to-email handler.

Fetches the original thread to extract proper headers (From, Subject,
References, Message-ID) and creates a draft reply.  Conforms to
ActionHandler protocol.

SAFETY:
  - draft_first=true (default): creates a Gmail draft so user reviews
    before send.
  - autonomy_floor: true — always requires human approval.
  - reply_all: default False (only reply to the top-level sender).
  - Undo: messages.trash() within undo_window_sec.

Ref: specs/act.md §8.1
"""
from __future__ import annotations

import base64
import sys
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

_REQUIRED_PARAMS = ("thread_id", "body")


# ---------------------------------------------------------------------------
# Protocol functions
# ---------------------------------------------------------------------------

def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Check required params and structural validity."""
    params = proposal.parameters

    for field in _REQUIRED_PARAMS:
        if not params.get(field, "").strip():
            return False, f"Missing required parameter: '{field}'"

    body = params.get("body", "")
    if len(body.encode("utf-8")) > 10 * 1024 * 1024:
        return False, "Reply body exceeds 10 MB"

    return True, ""


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Fetch thread headers and create a Gmail draft reply.  Returns draft URL."""
    params = proposal.parameters
    thread_id: str = params.get("thread_id", "")
    body: str = params.get("body", "")
    reply_all: bool = params.get("reply_all", False)
    draft_first: bool = params.get("draft_first", True)

    try:
        from google_auth import build_service  # noqa: PLC0415
        service = build_service("gmail", "v1")

        # Fetch thread to get reply headers
        reply_to, subject, in_reply_to, references, cc_list = _extract_reply_headers(
            service, thread_id, include_all=reply_all,
        )

        if not draft_first:
            return ActionResult(
                status="success",
                message=(
                    f"Preview: reply to {reply_to} | Subject: {subject!r} | "
                    f"Body: {len(body)} chars"
                ),
                data={
                    "preview_mode": True,
                    "to": reply_to,
                    "subject": subject,
                    "thread_id": thread_id,
                },
                reversible=False,
                reverse_action=None,
            )

        # Build MIME reply
        mime_msg = _build_reply_mime(
            service, reply_to, subject, body, in_reply_to, references,
            thread_id, cc_list if reply_all else "",
        )
        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")

        draft_body: dict[str, Any] = {
            "message": {"raw": raw, "threadId": thread_id},
        }
        draft = service.users().drafts().create(userId="me", body=draft_body).execute()
        draft_id = draft.get("id", "")
        draft_url = f"https://mail.google.com/mail/u/0/#drafts/{draft_id}"

        return ActionResult(
            status="success",
            message=f"Draft reply created: {draft_url}",
            data={
                "draft_id": draft_id,
                "draft_url": draft_url,
                "to": reply_to,
                "subject": subject,
                "thread_id": thread_id,
            },
            reversible=False,
            reverse_action=None,
        )

    except Exception as e:
        return ActionResult(
            status="failure",
            message=f"Failed to create reply draft: {e}",
            data={"error": str(e), "thread_id": thread_id},
            reversible=False,
            reverse_action=None,
        )


def execute(proposal: ActionProposal) -> ActionResult:
    """Send reply email.  Sends existing draft if draft_id provided, else direct send."""
    params = proposal.parameters
    thread_id: str = params.get("thread_id", "")
    body: str = params.get("body", "")
    reply_all: bool = params.get("reply_all", False)
    draft_id: str = params.get("draft_id", "")

    try:
        from google_auth import build_service  # noqa: PLC0415
        service = build_service("gmail", "v1")

        if draft_id:
            result = service.users().drafts().send(
                userId="me",
                body={"id": draft_id},
            ).execute()
        else:
            reply_to, subject, in_reply_to, references, cc_list = _extract_reply_headers(
                service, thread_id, include_all=reply_all,
            )
            mime_msg = _build_reply_mime(
                service, reply_to, subject, body, in_reply_to, references,
                thread_id, cc_list if reply_all else "",
            )
            raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
            result = service.users().messages().send(
                userId="me",
                body={"raw": raw, "threadId": thread_id},
            ).execute()

        message_id = result.get("id", "")

        return ActionResult(
            status="success",
            message=f"✅ Reply sent (thread {thread_id})",
            data={
                "message_id": message_id,
                "thread_id": result.get("threadId", thread_id),
                "draft_id": draft_id,
            },
            reversible=True,
            reverse_action=None,
        )

    except Exception as e:
        return ActionResult(
            status="failure",
            message=f"Failed to send reply: {e}",
            data={"error": str(e), "thread_id": thread_id},
            reversible=False,
            reverse_action=None,
        )


def build_reverse_proposal(
    original: ActionProposal,
    result_data: dict[str, Any],
) -> ActionProposal:
    """Build undo proposal: move sent reply to Trash."""
    import uuid

    message_id = result_data.get("message_id", "")
    if not message_id:
        raise ValueError("Cannot undo: message_id not found in result data")

    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type="email_reply_undo",
        domain=original.domain,
        title="Undo: Move sent reply to Trash",
        description=f"Move reply {message_id} to Gmail Trash (undo email reply)",
        parameters={"message_id": message_id, "action": "trash"},
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
    """Verify Gmail credentials are available."""
    try:
        from google_auth import check_stored_credentials  # noqa: PLC0415
        status = check_stored_credentials()
        return bool(status.get("gmail_token_stored", False))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_reply_headers(
    service: Any,
    thread_id: str,
    include_all: bool,
) -> tuple[str, str, str, str, str]:
    """Fetch thread and extract headers needed for a proper reply.

    Returns: (reply_to, subject, in_reply_to, references, cc_list)
    """
    thread = service.users().threads().get(
        userId="me",
        id=thread_id,
        format="metadata",
        metadataHeaders=["From", "To", "Subject", "Message-ID", "References", "Cc"],
    ).execute()

    messages = thread.get("messages", [])
    if not messages:
        raise ValueError(f"Thread {thread_id} has no messages")

    # Get headers from the last message in the thread
    last_msg = messages[-1]
    headers = {
        h["name"]: h["value"]
        for h in last_msg.get("payload", {}).get("headers", [])
    }

    # Subject: keep "Re:" prefix logic
    raw_subject = headers.get("Subject", "(no subject)")
    subject = raw_subject if raw_subject.lower().startswith("re:") else f"Re: {raw_subject}"

    # Reply-To or From
    reply_to = headers.get("Reply-To", headers.get("From", ""))

    # Threading headers
    in_reply_to = headers.get("Message-ID", "")
    existing_refs = headers.get("References", "")
    references = f"{existing_refs} {in_reply_to}".strip()

    # CC for reply-all
    cc_list = ""
    if include_all:
        cc_list = headers.get("Cc", "")

    return reply_to, subject, in_reply_to, references, cc_list


def _build_reply_mime(
    service: Any,
    reply_to: str,
    subject: str,
    body: str,
    in_reply_to: str,
    references: str,
    thread_id: str,
    cc_list: str,
) -> MIMEMultipart:
    """Build MIME reply message."""
    profile = service.users().getProfile(userId="me").execute()
    from_addr = profile.get("emailAddress", "me")

    msg = MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = reply_to
    msg["Subject"] = subject
    if cc_list:
        msg["Cc"] = cc_list
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        import gmail_send  # noqa: PLC0415
        msg.attach(MIMEText(gmail_send._markdown_to_html(body), "html", "utf-8"))
    except Exception:
        pass

    return msg
