"""Command entry point of the new core (re-artha §12.4 contract: entry point and result).

``python -m catchup_core.cli catchup --json [--shadow] --id ID [--format F]``

Exit codes: 0 complete · 10 degraded · 20 blocked · 30 usage error.  Result JSON goes
to stdout, diagnostics to stderr.  Dependencies are injected through ``deps_factory``;
the default factory wires real dependencies for ``--shadow`` only and refuses an
authoritative run, because the pilot has not been cut over (that needs consented real
data and owner sign-off) — the command can never silently publish a Briefing.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
import uuid
from collections.abc import Callable, Sequence
from typing import TextIO

from catchup_core.contracts import BriefingFormat, CatchupRequest, CatchupResult, ContractError, ExecutionMode, ExitCode
from catchup_core.coordinator import Deps, run_catchup
from catchup_core.exchange import AwaitingExtraction, ExchangeExtractor, parse_responses, render_bundle

__all__ = ["main", "parse_args"]

DepsFactory = Callable[[CatchupRequest], Deps]


class NotConfigured(RuntimeError):
    pass


def _default_factory(request: CatchupRequest) -> Deps:
    """Real wiring, **shadow only**: an authoritative run needs the cut-over decision (§12.6)."""
    if request.mode is not ExecutionMode.SHADOW:
        raise NotConfigured("authoritative runs are disabled until the pilot is cut over; use --shadow")
    from catchup_core.runtime import RuntimeConfigError, build_deps  # noqa: PLC0415
    try:
        return build_deps(request)
    except (RuntimeConfigError, OSError) as exc:
        raise NotConfigured(str(exc)) from exc


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="catchup_core", add_help=True)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("catchup", help="run one Catch-up")
    run.add_argument("--json", action="store_true", help="print the typed result as JSON")
    run.add_argument("--shadow", action="store_true", help="comparison run: never publishes")
    run.add_argument("--id", default=None, help="Catch-up ID / idempotency key (default: new)")
    run.add_argument("--format", choices=[f.value for f in BriefingFormat], default="standard")
    run.add_argument("--responses", default=None, metavar="PATH|-",
                     help="answers to a paused run's extraction requests (JSON; '-' reads stdin)")
    run.add_argument("--host", default="", help="designated authority host (required unless --shadow)")
    run.add_argument("--actor", default="user")
    run.add_argument("--channel", default="cli")
    return parser.parse_args(list(argv))


def _read_responses(source: str | None, stdin: TextIO) -> dict[str, str] | None:
    if source is None:
        return None
    if source == "-":
        return parse_responses(stdin.read())
    with open(source, encoding="utf-8") as fh:
        return parse_responses(fh.read())


def main(argv: Sequence[str] | None = None, *, deps_factory: DepsFactory = _default_factory,
         out: TextIO | None = None, err: TextIO | None = None, stdin: TextIO | None = None) -> int:
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr
    stdin = stdin if stdin is not None else sys.stdin
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
        responses = _read_responses(args.responses, stdin)
    except (OSError, ValueError) as exc:
        print(f"usage error: {exc}", file=stderr)
        return int(ExitCode.USAGE_ERROR)
    try:
        deps = deps_factory(request)
        if responses is not None:
            deps = dataclasses.replace(deps, extractor=ExchangeExtractor(responses))
        result: CatchupResult = run_catchup(request, deps)
    except NotConfigured as exc:
        print(f"not configured: {exc}", file=stderr)
        return int(ExitCode.USAGE_ERROR)
    except AwaitingExtraction as paused:
        print(render_bundle(paused.catchup_id, paused.requests), file=stdout)
        print(f"awaiting extraction: {len(paused.requests)} request(s); resume with --id "
              f"{paused.catchup_id} --responses", file=stderr)
        return int(ExitCode.AWAITING_EXTRACTION)
    if args.json:
        print(result.to_json(), file=stdout)
    else:
        print(f"{result.status.value}: {len(result.artifact_ids)} alert(s); "
              f"briefing {result.briefing_id or 'not published'}", file=stdout)
    return int(result.exit_code())


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())
