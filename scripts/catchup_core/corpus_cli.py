"""``python -m catchup_core.corpus_cli``: pre-fill, label and audit the evaluation corpus of a shadow run (hand-run)."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from catchup_core import corpus, heldout
from catchup_core.contracts import CatchupRequest, ExecutionMode


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="corpus_cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("prefill", help="list the cases a shadow run produced, with what the core surfaced")
    pre.add_argument("--source", default="school_email")
    pre.add_argument("--domain", default="kids_school")
    pre.add_argument("--unlabeled", action="store_true")
    label = sub.add_parser("label", help="record the owner's decision for one case")
    label.add_argument("case_id")
    label.add_argument("--needs-alert", required=True, choices=("yes", "no", "unsure"))
    label.add_argument("--severity", choices=corpus.SEVERITIES)
    label.add_argument("--expect", action="append", default=[], help="obligation key the core should have surfaced")
    label.add_argument("--type", dest="case_type", choices=corpus.CASE_TYPES)
    label.add_argument("--note", default="")
    label.add_argument("--before", type=json.loads)
    label.add_argument("--after", type=json.loads)
    label.add_argument("--field", action="append", default=[], type=json.loads,
                       help='JSON {"kind": date|amount|name, "predicate": ..., "value": ..., "span": verbatim text}')
    label.add_argument("--thread", action="append", default=[], help="thread id (defaults to the case id)")
    label.add_argument("--source", default="school_email")
    label.add_argument("--domain", default="kids_school")
    nxt = sub.add_parser("next", help="show the next unlabeled message, source text only (blind labeling)")
    nxt.add_argument("--source", default="school_email")
    nxt.add_argument("--domain", default="kids_school")
    declare = sub.add_parser("declare", help="fix the held-out split once: id, development cutoff and ids")
    declare.add_argument("--id", required=True, dest="corpus_id")
    declare.add_argument("--development-end", required=True)
    declare.add_argument("--development-obligation", action="append", default=[])
    declare.add_argument("--development-thread", action="append", default=[])
    sub.add_parser("status", help="what the release scorecard still lacks")
    assemble = sub.add_parser("assemble", help="write release corpus and field evidence; refuses on any shortfall")
    assemble.add_argument("--catchup-id", required=True)
    assemble.add_argument("--as-of", required=True, help="evaluation instant, ISO with timezone")
    assemble.add_argument("--out", required=True, type=Path, help="new file; never overwritten")
    assemble.add_argument("--min-fields", type=int, default=heldout.MIN_FIELDS)
    evaluate = sub.add_parser("evaluate", help="join reviewed labels to one returned Catch-up")
    evaluate.add_argument("--catchup-id", required=True)
    return parser.parse_args(list(argv))


def _run(args: argparse.Namespace) -> dict[str, Any]:  # noqa: PLR0911 - one result per subcommand
    from catchup_core.runtime import build_deps, load_runtime_config  # noqa: PLC0415
    from catchup_core.session_lock import session_lock  # noqa: PLC0415

    root = Path(__file__).resolve().parents[2]
    config = load_runtime_config(root / "config" / "catchup_core.yaml")
    request = CatchupRequest(f"corpus_{uuid.uuid4().hex[:12]}", config.viewer, "cli", mode=ExecutionMode.SHADOW)
    deps = build_deps(request, inspection=True, mutation=args.command == "label")
    try:
        with deps.maintenance(), session_lock(deps.ledger._path.with_suffix(".session.lock")):
            ledger = deps.ledger
            if args.command == "evaluate":
                outcome = corpus.latest_outcome(ledger, args.catchup_id)
                if outcome is None:
                    raise corpus.CorpusError("Catch-up has no retained output evidence; rerun before evaluation")
                return {"evaluations": corpus.evaluations(corpus.load_labels(ledger), outcome)}
            if args.command == "status":
                return heldout.status(ledger, corpus.load_labels(ledger))
            if args.command == "declare":
                return heldout.declare(ledger, corpus_id=args.corpus_id, development_end_at=args.development_end,
                                       development_obligation_ids=args.development_obligation,
                                       development_thread_ids=args.development_thread, now=datetime.now(UTC))
            if args.command == "assemble":
                outcome = corpus.latest_outcome(ledger, args.catchup_id)
                if outcome is None:
                    raise corpus.CorpusError("Catch-up has no retained output evidence; rerun before assembly")
                evidence = heldout.assemble(ledger, corpus.load_labels(ledger), outcome, as_of=args.as_of,
                                            min_fields=args.min_fields)
                with args.out.open("x", encoding="utf-8") as handle:
                    json.dump(evidence, handle, indent=2, default=str)
                return {"written": str(args.out), "cases": len(evidence["evaluations"]),
                        "document_fields": evidence["document_fields"]}
            cases = corpus.prefill(ledger, deps.blobs.get, args.source, args.domain)
            if args.command == "prefill":
                return {"cases": [{k: v for k, v in c.items() if k != "blob_ref"} for c in cases
                                  if not (args.unlabeled and c["labeled"])]}
            if args.command == "next":
                pending = next((c for c in cases if not c["labeled"]), None)
                return ({"remaining": 0} if pending is None else
                        {"remaining": sum(not c["labeled"] for c in cases),
                         **corpus.blind_case(pending, deps.blobs.get(pending["blob_ref"]))})
            chosen = next((c for c in cases if c["case_id"] == args.case_id), None)
            if chosen is None:
                raise corpus.CorpusError(f"no such case: {args.case_id}")
            stored = corpus.record_label(ledger, chosen, {
                "needs_alert": args.needs_alert, "severity": args.severity, "expected": args.expect,
                "case_type": args.case_type, "note": args.note, "before": args.before, "after": args.after,
                "fields": args.field, "thread_ids": args.thread or [chosen["case_id"]]},
                datetime.now(UTC), source_text=deps.blobs.get(chosen["blob_ref"]))
            return {"recorded": stored["case_id"], "needs_alert": stored["needs_alert"], "reviewed": stored["reviewed"]}
    finally:
        deps.ledger.close()


def main(argv: Sequence[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out, err = stdout or sys.stdout, stderr or sys.stderr
    try:
        print(json.dumps(_run(_parse(sys.argv[1:] if argv is None else argv)), indent=2, default=str), file=out)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=err)
        return 1


if __name__ == "__main__":
    sys.exit(main())
