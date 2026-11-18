"""The owner classifies existing Signals and Facts, in their own terminal (specs/refix-artha.md §B.1).

The owner picks objects from a metadata list (R-1), sees each one's metadata, authorization inputs and, for a Signal,
the stripped 400-character excerpt (R-2), and answers one scope per object. Each Fact is offered after its source
Signal and gets its own answer (R-4). Everything shown goes to the terminal only; the returned record holds counts.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from catchup_core.corpus import _excerpt
from catchup_core.ledger import Ledger, PermissionDenied

_ANSWERS = {"p": "personal", "w": "work", "m": "mixed", "u": "unknown",
            "personal": "personal", "work": "work", "mixed": "mixed", "unknown": "unknown"}
_PROMPT = "Scope? [p]ersonal [w]ork [m]ixed [u]nknown; anything else skips: "


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


def pending(ledger: Ledger, connector: str) -> list[dict[str, Any]]:
    """Signals of one source that have no review at the current version and permission generation."""
    rows = ledger.conn.execute(
        "SELECT signal_id,account_scope,observed_at FROM signals WHERE connector=? "
        "AND coalesce(exclusion_reason,'')<>'forgotten' ORDER BY observed_at,signal_id", (connector,)).fetchall()
    return [{"signal_id": str(r["signal_id"]), "account": str(r["account_scope"]), "observed_at": r["observed_at"]}
            for r in rows if ledger.scope_review_item("signal", str(r["signal_id"]))["current_scope"] is None]


def _show(item: dict[str, Any], write: Callable[[str], object]) -> None:
    write(f"  version {item['object_version']}; source {item['source_id']} / account {item['account_id']}\n")
    for name, value in item["authz"].items():
        shown = ", ".join(map(str, value)) if isinstance(value, list) else value
        write(f"  {name.replace('_', ' ')}: {shown if shown not in (None, '') else '(none)'}\n")


def _decide(ledger: Ledger, viewer: str, item: dict[str, Any], readline: Callable[[str], str]) -> str | None:
    scope = _ANSWERS.get(readline(_PROMPT).strip().casefold())
    if scope is not None:
        ledger.review_scope(item["object_type"], item["object_id"], scope, reviewer=viewer,
                            expected_version=item["object_version"], expected_authz=item["authz_digest"])
    return scope


def run(ledger: Ledger, *, viewer: str, connector: str, text_of: Callable[[str], str],
        readline: Callable[[str], str], write: Callable[[str], object], interactive: bool) -> dict[str, Any]:
    if not interactive:
        raise PermissionDenied("scope review must be done interactively, in the owner's own terminal")
    if viewer != ledger._owner:
        raise PermissionDenied("only the owner can review personal scope")
    items = pending(ledger, connector)
    for number, item in enumerate(items, 1):
        write(f"{number:>4}. {item['observed_at']}  {item['account']}  {item['signal_id']}\n")
    chosen = parse_selection(readline("Numbers to review (e.g. 1,3-5); blank cancels: ") if items else "",
                             len(items))
    reviewed: Counter[str] = Counter()
    skipped = 0
    offered: set[str] = set()
    for position, index in enumerate(chosen, 1):
        signal_id = items[index]["signal_id"]
        item = ledger.scope_review_item("signal", signal_id)
        row = ledger.signal_row(signal_id)
        write(f"\nSignal {position}/{len(chosen)}: {signal_id}, observed {items[index]['observed_at']}\n")
        _show(item, write)
        try:
            excerpt = _excerpt(text_of(str(row["evidence_blob_ref"]))) if row is not None else ""
        except (OSError, ValueError):
            excerpt = "(excerpt unavailable)"
        write(f"  excerpt: {excerpt}\n")
        outcome = [_decide(ledger, viewer, item, readline)]
        facts = ledger.conn.execute(
            "SELECT DISTINCT fact_id FROM fact_evidence WHERE signal_id=? ORDER BY fact_id", (signal_id,)).fetchall()
        for (fact_id,) in facts:
            fact_item = ledger.scope_review_item("fact", fact_id)
            if fact_id in offered or fact_item["current_scope"] is not None:
                continue
            offered.add(fact_id)
            fact = ledger.conn.execute("SELECT predicate FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
            write(f"\n  Fact from this Signal: {fact_id}, {fact['predicate']}\n")
            _show(fact_item, write)
            outcome.append(_decide(ledger, viewer, fact_item, readline))
        for scope in outcome:
            if scope is None:
                skipped += 1
            else:
                reviewed[scope] += 1
    return {"connector": connector, "listed": len(items), "reviewed": dict(reviewed), "skipped": skipped}
