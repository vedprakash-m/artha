"""An adult household member records their own consent, in person.

The Ledger already refuses a grant for an adult by anyone else. This is the surface that lets the adult make it:
interactive only, and valid only when they type their own name after seeing exactly what they are allowing.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from catchup_core.ledger import Ledger, PermissionDenied

OPERATIONS = ("disclose", "ingest", "view")


def grant_own_consent(ledger: Ledger, *, subject_id: str, subject_name: str, scopes: Sequence[str], days: int,
                      now: datetime, readline: Callable[[str], str], interactive: bool) -> list[str]:
    if subject_id not in ledger._consent_subjects:
        raise ValueError(f"{subject_id} does not need their own consent")
    if not interactive:
        raise PermissionDenied("consent must be given interactively by the person it concerns")
    curated = [s.removeprefix("curated:").replace("_", " ") for s in scopes if s.startswith("curated:")]
    sources = [s for s in scopes if not s.startswith("curated:")]
    parts = ([f"mail and calendar items that concern you from {', '.join(sources)}"] if sources else []) + (
        [f"entries in its {', '.join(curated)} records that may concern you, including entries that name no one"]
        if curated else [])
    prompt = (f"{subject_name}: Artha will read {' and '.join(parts)} "
              f"for {days} days. Type your full name to give this consent, anything else cancels: ")
    if " ".join(readline(prompt).split()).casefold() != " ".join(subject_name.split()).casefold():
        raise PermissionDenied("the name typed does not match; nothing was recorded")
    expires = (now + timedelta(days=days)).isoformat()
    return [ledger.grant_consent(subject_id, subject_id, scope, OPERATIONS, ledger._owner, expires)
            for scope in scopes]


def list_consents(ledger: Ledger) -> list[dict[str, Any]]:
    return [{"consent_id": r["consent_id"], "subject_id": r["subject_id"], "scope": r["scope"],
             "operations": r["operations"], "expires_at": r["expires_at"], "revoked": r["revoked_at"] is not None}
            for r in ledger.conn.execute("SELECT * FROM consent ORDER BY granted_at")]
