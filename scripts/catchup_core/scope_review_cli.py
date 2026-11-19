"""The owner classifies existing Signals and Facts, in their own terminal (specs/refix-artha.md §B.1).

The owner picks objects from a metadata list (R-1), sees each one's metadata, authorization inputs and, for a Signal,
the stripped 400-character excerpt (R-2), and answers one scope per object. Each Fact is offered after its source
Signal and gets its own answer (R-4). Everything shown goes to the terminal only; the returned record holds counts.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from typing import Any

from catchup_core.corpus import _excerpt
from catchup_core.ledger import Ledger, LedgerError, PermissionDenied, require_approved_source_enrollment

_ANSWERS = {"p": "personal", "w": "work", "m": "mixed", "u": "unknown",
            "personal": "personal", "work": "work", "mixed": "mixed", "unknown": "unknown"}
_PROMPT = "Scope? [p]ersonal [w]ork [m]ixed [u]nknown; anything else skips: "
_EXCERPT_BYTES = 1_000_000


def parse_selection(text: str, count: int) -> list[int]:
    """'1,3-5' -> zero-based indexes in the order given; blank selects nothing; anything malformed is refused."""
    if not text.strip():
        return []
    chosen: list[int] = []
    for token in text.split(","):
        first, dash, last = token.strip().partition("-")
        if not first.isdigit() or (dash and not last.isdigit()):
            raise ValueError("selection must be numbers or ranges such as 1,3-5")
        low, high = int(first), int(last) if dash else int(first)
        if not 1 <= low <= high <= count:
            raise ValueError(f"selection must be between 1 and {count}")
        chosen.extend(i - 1 for i in range(low, high + 1) if i - 1 not in chosen)
    return chosen


def _safe_terminal(value: Any) -> str:
    rendered = str(value)
    return "".join(
        char if ord(char) >= 32 and ord(char) != 127 else f"\\x{ord(char):02x}"
        for char in rendered
    )


def pending(ledger: Ledger, connector: str, viewer: str, object_id: str | None = None) -> list[dict[str, Any]]:
    """Signals of one source that have no review at the current version and permission generation."""
    if object_id is not None:
        row = ledger.conn.execute(
            "SELECT signal_id,account_scope,observed_at FROM signals WHERE connector=? AND signal_id=?",
            (connector, object_id)).fetchone()
        if row is None:
            raise LedgerError("scope-review object does not belong to this source")
        rows = [row]
    else:
        rows = ledger.conn.execute(
            "SELECT signal_id,account_scope,observed_at FROM signals WHERE connector=? "
            "AND coalesce(exclusion_reason,'')<>'forgotten' ORDER BY observed_at,signal_id",
            (connector,)).fetchall()
    result = []
    for row in rows:
        try:
            ledger.require_scope_review_discovery("signal", str(row["signal_id"]), viewer)
        except PermissionDenied:
            continue
        if object_id is None and ledger.scope_review_item("signal", str(row["signal_id"]))[
                "current_scope"] is not None:
            continue
        result.append({
            "signal_id": str(row["signal_id"]), "account": str(row["account_scope"]),
            "observed_at": row["observed_at"],
        })
    return result


def _show(item: dict[str, Any], write: Callable[[str], object]) -> None:
    write(
        f"  version {_safe_terminal(item['object_version'])}; "
        f"source {_safe_terminal(item['source_id'])} / account {_safe_terminal(item['account_id'])}\n")
    for name, value in item["authz"].items():
        shown = ", ".join(map(str, value)) if isinstance(value, list) else value
        write(
            f"  {_safe_terminal(name.replace('_', ' '))}: "
            f"{_safe_terminal(shown) if shown not in (None, '') else '(none)'}\n")


def _decide(ledger: Ledger, viewer: str, item: dict[str, Any], readline: Callable[[str], str]) -> str | None:
    scope = _ANSWERS.get(readline(_PROMPT).strip().casefold())
    if scope is not None:
        ledger.review_scope(item["object_type"], item["object_id"], scope, reviewer=viewer,
                            expected_descriptor=item["review_descriptor"])
    return scope


def run(ledger: Ledger, *, viewer: str, connector: str, text_of: Callable[[str], str],
        readline: Callable[[str], str], write: Callable[[str], object], interactive: bool,
        object_id: str | None = None) -> dict[str, Any]:
    if not interactive:
        raise PermissionDenied("scope review must be done interactively, in the owner's own terminal")
    if viewer != ledger._owner:
        raise PermissionDenied("only the owner can review personal scope")
    items = pending(ledger, connector, viewer, object_id)
    for number, item in enumerate(items, 1):
        write(
            f"{number:>4}. {_safe_terminal(item['observed_at'])}  {_safe_terminal(item['account'])}  "
            f"{_safe_terminal(item['signal_id'])}\n")
    chosen = ([0] if len(items) == 1 and object_id is not None else
              parse_selection(readline("Numbers to review (e.g. 1,3-5); blank cancels: ") if items else "",
                              len(items)))
    reviewed: Counter[str] = Counter()
    skipped = 0
    offered: set[str] = set()
    for position, index in enumerate(chosen, 1):
        signal_id = items[index]["signal_id"]
        item = ledger.scope_review_item("signal", signal_id)
        row = ledger.signal_row(signal_id)
        write(
            f"\nSignal {position}/{len(chosen)}: {_safe_terminal(signal_id)}, "
            f"observed {_safe_terminal(items[index]['observed_at'])}\n")
        _show(item, write)
        try:
            excerpt = _excerpt(text_of(str(row["evidence_blob_ref"]))) if row is not None else ""
        except (OSError, ValueError):
            excerpt = "(excerpt unavailable)"
        write(f"  excerpt: {_safe_terminal(excerpt)}\n")
        outcome = [_decide(ledger, viewer, item, readline)]
        facts = ledger.conn.execute(
            "SELECT DISTINCT fact_id FROM fact_evidence WHERE signal_id=? ORDER BY fact_id", (signal_id,)).fetchall()
        for (fact_id,) in facts:
            fact_item = ledger.scope_review_item("fact", fact_id)
            try:
                ledger.require_scope_review_discovery("fact", fact_id, viewer)
            except PermissionDenied:
                continue
            if fact_id in offered or fact_item["current_scope"] is not None:
                continue
            offered.add(fact_id)
            fact = ledger.conn.execute("SELECT predicate FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
            write(
                f"\n  Fact from this Signal: {_safe_terminal(fact_id)}, "
                f"{_safe_terminal(fact['predicate'])}\n")
            _show(fact_item, write)
            outcome.append(_decide(ledger, viewer, fact_item, readline))
        for scope in outcome:
            if scope is None:
                skipped += 1
            else:
                reviewed[scope] += 1
    return {"connector": connector, "listed": len(items), "reviewed": dict(reviewed), "skipped": skipped}


def enroll(
        ledger: Ledger, *, viewer: str, definition: dict[str, Any],
        readline: Callable[[str], str], write: Callable[[str], object],
        interactive: bool) -> dict[str, Any]:
    if not interactive:
        raise PermissionDenied("source enrollment must be done interactively, in the owner's own terminal")
    if viewer != ledger._owner:
        raise PermissionDenied("only the owner can enroll a personal source")
    source_name = definition.get("source_name")
    if not isinstance(source_name, str):
        raise LedgerError("source enrollment requires a source_name")
    require_approved_source_enrollment(source_name)
    rendered = {
        "source_name": _safe_terminal(definition["source_name"]),
        "connector": _safe_terminal(definition["connector"]),
        "provider_account": _safe_terminal(definition["provider_account"]),
        "account_scope": _safe_terminal(definition["account_scope"]),
        "query": _safe_terminal(definition["query"]),
        "subjects": [_safe_terminal(subject) for subject in definition["subjects"]],
        "policy": json.dumps(definition["policy"], sort_keys=True),
    }
    for name, value in rendered.items():
        write(f"  {name}: {value}\n")
    answer = readline("Enroll this source for new Personal ingest? [yes/N]: ").strip().casefold()
    if answer not in ("yes", "y"):
        return {"enrolled": False}
    result = ledger.enroll_source(reviewer=viewer, **definition)
    return {"enrolled": True, "enrollment_id": result["enrollment_id"], "source": result["source_name"]}
