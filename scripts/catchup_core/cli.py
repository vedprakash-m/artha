"""Command entry point of the new core (re-artha §12.4 contract: entry point and result).

``python -m catchup_core.cli catchup --json [--shadow] --id ID [--format F]``

Exit codes: 0 complete · 10 degraded · 20 blocked · 30 usage error.  Result JSON goes
to stdout, diagnostics to stderr.  Dependencies are injected through ``deps_factory``;
the default factory refuses to run, because the pilot has not been cut over (that
needs consented real data and owner sign-off), so the command can never silently
produce an authoritative Briefing.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable, Sequence
from typing import TextIO

from catchup_core.contracts import BriefingFormat, CatchupRequest, CatchupResult, ContractError, ExecutionMode, ExitCode
from catchup_core.coordinator import Deps, run_catchup

__all__ = ["main", "parse_args"]

DepsFactory = Callable[[CatchupRequest], Deps]


class NotConfigured(RuntimeError):
    pass


def _default_factory(_request: CatchupRequest) -> Deps:
    raise NotConfigured("the new Catch-up core is not cut over; run in shadow with an explicit deps factory")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="catchup_core", add_help=True)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("catchup", help="run one Catch-up")
    run.add_argument("--json", action="store_true", help="print the typed result as JSON")
    run.add_argument("--shadow", action="store_true", help="comparison run: never publishes")
    run.add_argument("--id", default=None, help="Catch-up ID / idempotency key (default: new)")
    run.add_argument("--format", choices=[f.value for f in BriefingFormat], default="standard")
    run.add_argument("--host", default="", help="designated authority host (required unless --shadow)")
    run.add_argument("--actor", default="user")
    run.add_argument("--channel", default="cli")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None, *, deps_factory: DepsFactory = _default_factory,
         out: TextIO | None = None, err: TextIO | None = None) -> int:
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return int(ExitCode.USAGE_ERROR) if exc.code not in (0, None) else 0
    try:
        request = CatchupRequest(
            catchup_id=args.id or f"cu_{uuid.uuid4().hex[:12]}", actor=args.actor, channel=args.channel,
            mode=ExecutionMode.SHADOW if args.shadow else ExecutionMode.AUTHORITATIVE,
            briefing_format=BriefingFormat(args.format), host=args.host)
    except ContractError as exc:
        print(f"usage error: {exc}", file=stderr)
        return int(ExitCode.USAGE_ERROR)
    try:
        result: CatchupResult = run_catchup(request, deps_factory(request))
    except NotConfigured as exc:
        print(f"not configured: {exc}", file=stderr)
        return int(ExitCode.USAGE_ERROR)
    if args.json:
        print(result.to_json(), file=stdout)
    else:
        print(f"{result.status.value}: {len(result.artifact_ids)} alert(s); "
              f"briefing {result.briefing_id or 'not published'}", file=stdout)
    return int(result.exit_code())


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())
