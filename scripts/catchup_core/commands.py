"""Local user-started commands over the same evidence and permission boundary."""
from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from contextlib import closing
from pathlib import Path
from typing import Any

from catchup_core import ids, views
from catchup_core.backup import atomic_bytes, encrypted_snapshot
from catchup_core.coordinator import Deps
from catchup_core.ledger import LedgerError

_CODE_ROOT = Path(__file__).resolve().parents[2]      # where the capability code lives, not the user workspace


def execute(args: Namespace, deps: Deps, workspace: Path) -> dict[str, Any]:  # noqa: PLR0911, PLR0912, PLR0915
    # A flat command router: each branch is independent and returns one typed projection.
    led, viewer = deps.ledger, deps.viewer
    if args.command == "beliefs":
        return {"beliefs": views.beliefs(led, viewer, domain=args.domain, subject=args.subject)}
    if args.command == "why":
        return views.safe_projection(views.explain(led, deps.blobs, viewer, args.fact_id, as_of=args.as_of))
    if args.command == "forget":
        # The authenticated local owner can remove retained data after consent is revoked.
        if viewer != led._owner:
            raise LedgerError("source deletion requires the local owner")
        receipt = led.forget(args.connector, args.scope, args.upstream)
        return {"deleted_signals": receipt.deleted_signals, "deleted_facts": receipt.deleted_facts,
                "deleted_alerts": receipt.deleted_alerts, "minimal_audit": receipt.minimal_audit,
                "encrypted_backups_pending_expiry": receipt.backup_copies_pending_expiry}
    if args.command == "feedback":
        now = deps.now()
        fid = views.feedback(led, viewer, args.obligation, args.response, args.reason, now, days=args.days)
        case = views.replay_case(led, deps.blobs, viewer, args.obligation, args.response, args.reason, fid, now,
                                 led._path.parent / "replay")
        return {"feedback_id": fid, "replay_case": case.name, "expected_outcome": "pending_review"}
    if args.command == "correct":
        fact = led.get_fact(args.fact_id, viewer)
        predicate = deps.pack.predicates[fact.predicate]
        from catchup_core.extract import _typed_value  # noqa: PLC0415
        typed = _typed_value(predicate, args.value)
        value = typed.isoformat() if hasattr(typed, "isoformat") else typed
        if args.apply is None:
            return {"before": fact.value, "after": value, "apply_version": fact.recorded_at,
                    "instruction": "Review this diff, then repeat with --apply <apply_version>."}
        if args.apply != fact.recorded_at:
            raise ValueError("Fact changed since the correction preview")
        # Capture the old evidence before the correction replaces its active spans.
        correction_key = f"correction:{fact.fact_id}:{fact.recorded_at}"
        led.upsert_alert(correction_key, domain=fact.domain, primary_subject_id=fact.primary_subject_id,
                         severity="P4", fact_ids=(fact.fact_id,))
        fid = views.feedback(led, viewer, correction_key, "useful", "Explicit correction", deps.now())
        views.replay_case(led, deps.blobs, viewer, correction_key, "correct", "Explicit correction", fid,
                          deps.now(), led._path.parent / "replay")
        with led.transaction() as db:
            db.execute("UPDATE feedback SET response='correct' WHERE feedback_id=?", (fid,))
        views.correct(led, viewer, fact.fact_id, value, args.apply)
        return {"corrected": fact.fact_id, "provenance": "user_confirmed"}
    if args.command == "health":
        return {"integrity": led.integrity_check(), "schema": led.schema_version(),
            "capabilities": _capability_states(led, workspace, deps),
            "coverage": [{"connector": r["connector"], "status": r["status"],
                          "last_verified": r["completed_at"]} for r in led.coverage_rows()],
            "uninterpreted_signals": len(led.unresolved_signals()), "feedback": views.precision(led, viewer),
            "reviewed_alert_classes": views.class_precision(led, viewer)}
    if args.command == "review":
        return {"review": views.review_feedback(led, viewer, led._path.parent / "replay",
                                                args.feedback_id, args.label, deps.now())}
    if args.command == "history":
        return {"briefings": [dict(r) for r in led.conn.execute(
            "SELECT b.catchup_id,b.version,b.published_at,b.status,(SELECT count(*) FROM correction_notices n "
            "WHERE n.catchup_id=b.catchup_id) AS correction_notices "
            "FROM briefings b ORDER BY published_at DESC LIMIT 30")]}
    if args.command == "export":
        from catchup_core.policy import authorize_source  # noqa: PLC0415
        for row in led.conn.execute("SELECT fact_id,primary_subject_id,domain FROM facts"):
            led.get_fact(row["fact_id"], viewer)
            authorize_source(led, (row["primary_subject_id"],), led._consent_subjects,
                             row["domain"], viewer, operations=("export",))
            for co_subject in led.conn.execute("SELECT subject_id FROM fact_subjects WHERE fact_id=?",
                                               (row["fact_id"],)):
                authorize_source(led, (co_subject[0],), led._consent_subjects,
                                 row["domain"], viewer, operations=("export",))
        for row in led.coverage_rows():
            authorize_source(led, tuple(led._consent_subjects), led._consent_subjects,
                             f"{row['connector']}:{row['account_scope']}", viewer, operations=("export",))
        cipher = led._cipher
        if cipher is None:
            raise LedgerError("export requires the encryption key")
        path = Path(args.path).expanduser()
        encrypted_snapshot(path, led._path, cipher, blobs=deps.blobs._root)
        return {"export": str(path), "format": "artha.snapshot.v1", "dispatchable": False}
    if args.command == "capture":
        cipher = led._cipher
        if cipher is None:
            raise LedgerError("capture requires the encryption key")
        capture_id = ids.new_id("cap")
        payload = json.dumps({"id": capture_id, "text": args.text, "actor": viewer,
                              "observed_at": deps.now().isoformat()}).encode()
        atomic_bytes(led._path.parent / "inbox" / f"{capture_id}.ae1",
                     cipher.encrypt(payload, aad=capture_id.encode())[0])
        return {"captured": capture_id, "next": "Reviewed during the next Catch-up."}
    if args.command == "ingest":
        from catchup_core.documents import ingest  # noqa: PLC0415
        names = [sid for sid, name in deps.subject_labels.items() if name.casefold() == args.subject.casefold()]
        subject = names[0] if len(names) == 1 else args.subject
        return {"document": ingest(Path(args.path).expanduser(), subject, led, viewer, deps.now()),
                "next": "Page evidence will be reviewed at the next Catch-up; unread pages remain unverified."}
    if args.command in ("items", "goals"):
        from catchup_core.curated import read_state, records  # noqa: PLC0415
        from catchup_core.policy import authorize_source  # noqa: PLC0415
        name = "open_items" if args.command == "items" else "goals"
        subjects = (viewer,) if name == "goals" else tuple(led._consent_subjects)
        authorize_source(led, subjects, led._consent_subjects, f"curated:{name}", viewer)
        authored = records(read_state(workspace / "state", name), name)
        if name == "goals":
            from catchup_core.runtime import _goal_lines  # noqa: PLC0415
            lines = _goal_lines(workspace / "state", ledger=led, viewer=viewer, now=deps.now(),
                                today=deps.now().date(),
                                required_sources=tuple(s.connector for s in deps.pack.sources if s.required))
            return {"goals": authored, "observations": lines}
        return {args.command: authored}
    if args.command == "activity":
        paused = (workspace / "config" / "actions.paused").exists()
        path = led._path.parent.parent / "actions.db"
        if not path.is_file():
            return {"activity": [], "actions_paused": paused}
        from catchup_core.action_mapping import describe  # noqa: PLC0415
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM actions ORDER BY created_at DESC LIMIT 50").fetchall()
        return {"activity": [describe(dict(r)) for r in rows], "actions_paused": paused}
    if args.command in ("reconcile", "resolve"):
        return _action_resolution(args, viewer, workspace)
    if args.command in ("pause", "resume"):
        path = workspace / "config" / "actions.paused"
        if args.command == "pause":
            atomic_bytes(path, b"Actions paused by the local owner.\n")
        else:
            path.unlink(missing_ok=True)
        return {"actions_paused": path.exists()}
    raise ValueError("unsupported command")


def _capability_states(led: Any, workspace: Path, deps: Deps) -> list[dict[str, str]]:
    """RA-10: disabled / not configured / unknown / last succeeded / degraded, plus whether measured."""
    from catchup_core.capabilities import runtime_states  # noqa: PLC0415
    from catchup_core.contracts import CatchupRequest, ExecutionMode  # noqa: PLC0415
    from catchup_core.policy import validate_release  # noqa: PLC0415
    from catchup_core.runtime import RuntimeConfigError, load_runtime_config  # noqa: PLC0415
    try:
        config = load_runtime_config(workspace / "config" / "catchup_core.yaml")
    except RuntimeConfigError:
        return runtime_states(_CODE_ROOT, {}, led.conn, False, extractor_configured=False)
    try:
        validate_release(CatchupRequest("health_probe", config.viewer, "cli", mode=ExecutionMode.AUTHORITATIVE,
                                        host=str(config.policy.get("release", {}).get("authority_host", ""))),
                         config.policy, viewer=config.viewer, domains=config.domains)
        measured = True
        from catchup_core.policy import _approved_file, verify_rollback  # noqa: PLC0415
        verify_rollback(config.policy, led._cipher)
        scorecard = json.loads(_approved_file(config.policy["release"]["scorecard"], "scorecard"))
        reviewed = [cap for cap, review in scorecard.get("capability_reviews", {}).items()
                    if review.get("owner_signed_at") and review.get("outcome") == "passed"
                    and review.get("case_ids") and set(review["case_ids"]) <= {
                        c["case_id"] for c in scorecard["evaluations"] if c.get("consented_live") is True}]
    except Exception:
        measured = False
        reviewed = []
    return runtime_states(_CODE_ROOT, {"domains": list(config.domains), "reviewed_capabilities": reviewed},
                          led.conn, measured,
                          extractor_configured=True)


def _action_resolution(args: Namespace, viewer: str, workspace: Path) -> dict[str, Any]:
    """Close an ``unknown`` action: ask the provider (`reconcile`) or record a person's decision (`resolve`)."""
    import importlib  # noqa: PLC0415
    import sys  # noqa: PLC0415
    scripts = str(workspace / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    # Loaded dynamically: the incumbent action layer is outside the strictly typed surface.
    executor = importlib.import_module("action_executor").ActionExecutor(workspace)
    result = (executor.reconcile(args.action_id) if args.command == "reconcile"
              else executor.resolve_unknown(args.action_id, args.outcome, viewer))
    return {"action": args.action_id, "status": result.status, "message": result.message}
