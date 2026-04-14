#!/usr/bin/env python3
# pii-guard: ignore-file — handler; PII guard applied by ActionExecutor before this runs
"""
scripts/actions/instruction_sheet.py — Generate a domain-specific instruction guide.

Creates a markdown instruction sheet tailored to a task + service and saves
it to tmp/instructions/{domain}_{task}_{date}.md.  No external API calls.

SAFETY:
  - Pure text generation — no network calls, no data mutations.
  - autonomy_floor: false — can be auto-executed at Trust Level 2.
  - dry_run: returns the content without writing to disk.
  - One file per (domain, task, date) — deterministic filename prevents duplication.

Ref: specs/act.md §8.6
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from actions.base import ActionProposal, ActionResult


# ---------------------------------------------------------------------------
# Required parameters
# ---------------------------------------------------------------------------

_REQUIRED_PARAMS = ("task", "service")

_MAX_CONTENT_LENGTH = 64 * 1024  # 64 KB soft limit


# ---------------------------------------------------------------------------
# Protocol functions
# ---------------------------------------------------------------------------

def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Check required params."""
    params = proposal.parameters

    for field in _REQUIRED_PARAMS:
        if not params.get(field, "").strip():
            return False, f"Missing required parameter: '{field}'"

    task = params.get("task", "")
    service = params.get("service", "")

    if len(task) > 200:
        return False, "Parameter 'task' too long (max 200 chars)"
    if len(service) > 100:
        return False, "Parameter 'service' too long (max 100 chars)"

    context = params.get("context", {})
    if context and not isinstance(context, dict):
        return False, "Parameter 'context' must be a dict if provided"

    return True, ""


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Generate the instruction sheet content and return it without saving."""
    params = proposal.parameters
    content = _generate_content(proposal, params)

    return ActionResult(
        status="success",
        message=f"Preview: instruction sheet for '{params.get('task')}' ({params.get('service')})",
        data={
            "preview_mode": True,
            "content": content,
            "char_count": len(content),
        },
        reversible=False,
        reverse_action=None,
    )


def execute(proposal: ActionProposal) -> ActionResult:
    """Generate and save the instruction sheet to tmp/instructions/."""
    params = proposal.parameters
    task: str = params.get("task", "")
    service: str = params.get("service", "")
    domain = proposal.domain or "general"

    # DEBT-036: Idempotency check — prevent duplicate instruction sheets within the window
    try:
        from lib.idempotency import check_or_reserve, mark_completed  # noqa: PLC0415
        idem_status, idem_key = check_or_reserve(
            recipient=f"{domain}:{service}",
            intent=task,
            action_type="instruction_sheet",
        )
        if idem_status == "duplicate":
            return ActionResult(
                status="skipped",
                message=f"Duplicate instruction_sheet skipped (idempotency window active): {task}/{service}",
                data={"idem_key": idem_key, "task": task, "service": service},
                reversible=False,
                reverse_action=None,
            )
        if idem_status == "pending":
            return ActionResult(
                status="skipped",
                message=f"instruction_sheet already in-flight (pending): {task}/{service}",
                data={"idem_key": idem_key, "task": task, "service": service},
                reversible=False,
                reverse_action=None,
            )
    except Exception as _idem_exc:
        idem_key = None  # non-fatal — proceed without idempotency guard
        print(f"[instruction_sheet] idempotency check failed (non-fatal): {_idem_exc}", file=sys.stderr)

    try:
        content = _generate_content(proposal, params)

        # Determine output path
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Sanitize filename components
        safe_domain = _safe_filename_part(domain)
        safe_task = _safe_filename_part(task)
        safe_service = _safe_filename_part(service)
        filename = f"{safe_domain}_{safe_task}_{safe_service}_{date_str}.md"

        # tmp/instructions/ relative to Artha root
        artha_root = Path(__file__).resolve().parent.parent.parent
        output_dir = artha_root / "tmp" / "instructions"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename

        # DEBT-036: Warn on overwrite — same filename already exists
        _overwrite_warning = None
        if output_path.exists():
            _overwrite_warning = f"WARNING: overwriting existing instruction sheet: {filename}"
            print(f"[instruction_sheet] {_overwrite_warning}", file=sys.stderr)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        # DEBT-036: Mark idempotency key completed after successful write
        if idem_key is not None:
            try:
                mark_completed(idem_key)
            except Exception:
                pass  # non-fatal

        return ActionResult(
            status="success",
            message=f"✅ Instruction sheet saved: tmp/instructions/{filename}"
                    + (f" [{_overwrite_warning}]" if _overwrite_warning else ""),
            data={
                "file_path": str(output_path),
                "filename": filename,
                "char_count": len(content),
                "task": task,
                "service": service,
                **({"overwrite_warning": _overwrite_warning} if _overwrite_warning else {}),
            },
            reversible=False,
            reverse_action=None,
        )

    except Exception as e:
        return ActionResult(
            status="failure",
            message=f"Failed to generate instruction sheet: {e}",
            data={"error": str(e), "task": task, "service": service},
            reversible=False,
            reverse_action=None,
        )


def build_reverse_proposal(
    original: ActionProposal,
    result_data: dict[str, Any],
) -> ActionProposal:
    """instruction_sheet does not support undo (file write, not a mutation)."""
    raise NotImplementedError("instruction_sheet does not support undo")


def health_check() -> bool:
    """instruction_sheet has no external dependencies — always healthy."""
    return True


# ---------------------------------------------------------------------------
# Content generation
# ---------------------------------------------------------------------------

def _generate_content(proposal: ActionProposal, params: dict[str, Any]) -> str:
    """Generate structured markdown instruction content.

    Uses provided context dict and domain-aware formatting.
    The output is deterministic given the same input params.
    """
    task: str = params.get("task", "")
    service: str = params.get("service", "")
    context: dict = params.get("context", {})
    domain: str = proposal.domain or "general"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []

    # Header
    lines.append(f"# Instruction Sheet: {task}")
    lines.append(f"**Service:** {service}  ")
    lines.append(f"**Domain:** {domain}  ")
    lines.append(f"**Generated:** {generated_at}  ")
    lines.append(f"**Source:** Artha action `instruction_sheet`  ")
    lines.append("")

    # Overview section
    lines.append("## Overview")
    lines.append(f"This guide covers **{task}** for **{service}**.")
    if context.get("description"):
        lines.append("")
        lines.append(str(context["description"]))
    lines.append("")

    # Context-provided sections
    if context.get("prerequisites"):
        lines.append("## Prerequisites")
        prereqs = context["prerequisites"]
        if isinstance(prereqs, list):
            for item in prereqs:
                lines.append(f"- {item}")
        else:
            lines.append(str(prereqs))
        lines.append("")

    if context.get("steps"):
        lines.append("## Steps")
        steps = context["steps"]
        if isinstance(steps, list):
            for i, step in enumerate(steps, 1):
                lines.append(f"{i}. {step}")
        else:
            lines.append(str(steps))
        lines.append("")

    if context.get("notes"):
        lines.append("## Notes")
        notes = context["notes"]
        if isinstance(notes, list):
            for note in notes:
                lines.append(f"- {note}")
        else:
            lines.append(str(notes))
        lines.append("")

    if context.get("contacts"):
        lines.append("## Key Contacts")
        contacts = context["contacts"]
        if isinstance(contacts, list):
            for c in contacts:
                lines.append(f"- {c}")
        elif isinstance(contacts, dict):
            for name, info in contacts.items():
                lines.append(f"- **{name}**: {info}")
        else:
            lines.append(str(contacts))
        lines.append("")

    if context.get("links"):
        lines.append("## Reference Links")
        links = context["links"]
        if isinstance(links, list):
            for link in links:
                lines.append(f"- {link}")
        elif isinstance(links, dict):
            for label, url in links.items():
                lines.append(f"- [{label}]({url})")
        else:
            lines.append(str(links))
        lines.append("")

    # Any extra context fields not handled above
    handled = {"description", "prerequisites", "steps", "notes", "contacts", "links"}
    extra = {k: v for k, v in context.items() if k not in handled}
    if extra:
        lines.append("## Additional Information")
        for key, val in extra.items():
            lines.append(f"### {key.replace('_', ' ').title()}")
            if isinstance(val, list):
                for item in val:
                    lines.append(f"- {item}")
            elif isinstance(val, dict):
                for k2, v2 in val.items():
                    lines.append(f"- **{k2}**: {v2}")
            else:
                lines.append(str(val))
            lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Generated by Artha instruction_sheet handler | {generated_at}*")

    content = "\n".join(lines)

    if len(content) > _MAX_CONTENT_LENGTH:
        content = content[:_MAX_CONTENT_LENGTH] + "\n\n... [truncated at 64 KB limit]\n"

    return content


def _safe_filename_part(value: str) -> str:
    """Convert a string to a safe lowercase filename component."""
    import re
    safe = re.sub(r"[^\w\-]", "_", value.lower().strip())
    safe = re.sub(r"_+", "_", safe)
    return safe[:40]  # Prevent excessively long filenames
