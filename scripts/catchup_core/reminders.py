"""Reminder proposals for future-dated obligations (.archive/specs/reminders-2026-11-05.md; owner decisions 2 and 4).

Pure planning plus an injected proposer: the core never executes an effect, it queues a proposal the owner approves.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from catchup_core.triage import AlertDraft

__all__ = ["MAX_PER_RUN", "Result", "propose", "title_for"]

MAX_PER_RUN = 50
Proposer = Callable[..., object]


@dataclass(frozen=True)
class Result:
    proposed: int
    skipped: int


def title_for(alert: AlertDraft, labels: Mapping[str, str]) -> str:
    who = labels.get(alert.subject_id, alert.subject_id)
    due = alert.deadline.isoformat() if alert.deadline else ""
    return f"{who}: {alert.predicate.replace('_', ' ')} due {due} ({alert.domain})"


def _wanted(alert: AlertDraft) -> bool:
    return (alert.deadline is not None and alert.days_until is not None and alert.days_until >= 0
            and alert.state != "disputed")


def propose(alerts: Sequence[AlertDraft], labels: Mapping[str, str], proposer: Proposer) -> Result:
    """Propose one reminder per obligation. A refusal (duplicate, PII scan) is counted, never fatal."""
    unique = {a.obligation_key: a for a in alerts if _wanted(a)}.values()
    ordered = sorted(unique, key=lambda a: (a.deadline, a.obligation_key))[:MAX_PER_RUN]
    proposed = skipped = 0
    for alert in ordered:
        title = title_for(alert, labels)
        try:
            proposer("reminder_create", alert.domain, title,
                     {"title": title, "due_date": alert.deadline.isoformat() if alert.deadline else ""})
        except ValueError:
            skipped += 1
        else:
            proposed += 1
    return Result(proposed, skipped)
