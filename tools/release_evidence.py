#!/usr/bin/env python3
"""Assemble full-profile release evidence from measured inputs (re-artha §12, FC-01..09).

Each subcommand joins evidence that already exists (held-out labels, metric records, checkpoint bytes and the
owner's recorded observations) into the exact artifact the runtime gate reads, then runs that same gate before
writing.  Nothing here invents a measurement, a label or an observation: missing input is a refusal, and a refusal
writes no file.

    python3 tools/release_evidence.py measure --config config/catchup_core.yaml --metrics-dir DIR --plan PLAN.json --out M.json
    python3 tools/release_evidence.py assemble-release --config config/catchup_core.yaml --heldout H.json \
        --bounds B.json --measurements M.json [M2.json ...] --owner-signed-at 2026-11-05T18:00:00-08:00 \
        --owner-decision-ref <64-hex-digest> --out S.json
    python3 tools/release_evidence.py recovery-receipt --config config/catchup_core.yaml --work-checkpoint W.ack2 \
        --observations O.json --owner-signed-at ... --owner-decision-ref <64-hex-digest> --out R.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from catchup_core import corpus, domain_pack as dp  # noqa: E402
from catchup_core.contracts import CatchupRequest, ExecutionMode  # noqa: E402
from catchup_core import metrics, policy  # noqa: E402
from catchup_core.ledger import LedgerError, PermissionDenied  # noqa: E402
from catchup_core.runtime import RuntimeConfigError, build_deps, load_runtime_config  # noqa: E402
from catchup_core.scorecard import score  # noqa: E402
from catchup_core.session_lock import SessionBusy, session_lock  # noqa: E402
from catchup_core.crypto import KeyUnavailableError  # noqa: E402

NORMAL = ("normal_cold", "normal_warm")
RELEASE_REPETITIONS = 30
# Owner-attested scenario facts the gate reads; copied verbatim, never defaulted.
ATTESTED = ("fair_progress", "incomplete_interpretation_safe", "verified_alerts_preserved",
            "coverage_degradation_reported", "query_verified", "restore_verified", "plaintext_fallback",
            "fetch_skipped", "vault_skipped")


class EvidenceError(Exception):
    """The inputs cannot support the requested artifact; nothing was written."""


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique)
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"{path}: not readable JSON ({exc.__class__.__name__})") from None


def _load_config(path: Path) -> tuple[dict[str, Any], Path]:
    try:
        config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvidenceError(f"{path}: not readable YAML ({exc.__class__.__name__})") from None
    if not isinstance(config, dict):
        raise EvidenceError(f"{path}: configuration must be a mapping")
    return config, Path(path).resolve().parents[1]


def _owner_decision_ref(config: Mapping[str, Any], value: Any) -> str:
    release = config.get("release")
    approved = release.get("owner_decision_sha256") if isinstance(release, Mapping) else None
    if not isinstance(approved, str) or not re.fullmatch(r"[0-9a-f]{64}", approved):
        raise EvidenceError(
            "release owner_decision_sha256 must be the approved authenticated owner decision digest")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise EvidenceError(
            "owner_decision_ref must be the SHA-256 of the authenticated owner decision")
    if value != approved:
        raise EvidenceError("owner_decision_ref differs from the approved authenticated owner decision")
    return value


def _domains(config: dict[str, Any], requested: list[str] | None) -> list[str]:
    domains = requested or (config.get("release") or {}).get("domains")
    if not isinstance(domains, list) or not domains:
        raise EvidenceError("no Domains: pass --domains or set release.domains")
    return list(domains)


def _write(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _summary(runs: list[dict[str, Any]], label: str) -> dict[str, Any]:
    summary = metrics.summarize(runs)
    logical = metrics._logical_runs(runs)
    completed = sum(run.get("status") == "complete" for run in logical)
    if (summary.get("runs", 0) < RELEASE_REPETITIONS
            or summary.get("p95_seconds") is None
            or completed * 100 < len(logical) * int(metrics.COMPLETE_GOAL * 100)):
        raise EvidenceError(f"{label}: no complete single-version latency history "
                            f"with {RELEASE_REPETITIONS} repetitions and the "
                            f"{metrics.COMPLETE_GOAL:.0%} completion floor "
                            f"({summary.get('verdict', 'no runs')})")
    return summary


def _cost_measurement(
        config: Mapping[str, Any], plan: Mapping[str, Any], runs: list[dict[str, Any]]
) -> dict[str, Any]:
    pricing = plan.get("pricing", config.get("pricing"))
    summary = metrics.estimate_cost(runs, pricing if isinstance(pricing, Mapping) else None)
    if not summary.get("history_complete"):
        raise EvidenceError("monthly_usd: metric receipt history is incomplete")
    verified = summary.get("actual_projected_monthly_usd")
    estimated = summary.get("projected_monthly_usd")
    receipt_verified = bool(runs) and all(
        run.get("actual_usage_verified") is True
        and isinstance(run.get("actual_cost_usd"), int | float)
        and not isinstance(run.get("actual_cost_usd"), bool)
        and math.isfinite(float(run["actual_cost_usd"]))
        and run["actual_cost_usd"] >= 0
        and all(type(run.get(name)) is int and run[name] >= 0
                for name in ("actual_prompt_tokens", "actual_completion_tokens"))
        for run in runs
    )
    if (receipt_verified and summary.get("actual_usage_verified") is True
            and isinstance(verified, int | float)):
        monthly = float(verified)
        basis = "verified_usage"
    elif isinstance(estimated, int | float):
        monthly = float(estimated)
        basis = "estimated"
    else:
        raise EvidenceError("monthly_usd: metric receipts have no verified or estimated cost")
    claimed = plan.get("monthly_usd")
    if (isinstance(claimed, bool) or not isinstance(claimed, int | float)
            or not math.isfinite(float(claimed)) or float(claimed) < 0
            or round(float(claimed), 2) != round(monthly, 2)):
        raise EvidenceError("plan monthly_usd differs from metric receipt cost evidence")
    return {
        "monthly_usd": round(monthly, 2),
        "cost_basis": basis,
        "verified_monthly_usd": float(verified) if receipt_verified and verified is not None else None,
        "estimated_monthly_usd": float(estimated) if estimated is not None else None,
        "invoiced_monthly_usd": None,
    }


def _load_retained_outcome(config_path: Path, catchup_id: str) -> dict[str, Any]:
    """Read one permission-rechecked output from the encrypted local ledger without fetching sources."""
    repo_root = Path(config_path).resolve().parents[1]
    runtime_config = load_runtime_config(repo_root / "config" / "catchup_core.yaml")
    request = CatchupRequest(f"release_{uuid.uuid4().hex[:12]}", runtime_config.viewer, "cli",
                             mode=ExecutionMode.SHADOW)
    deps = build_deps(request, repo_root=repo_root, inspection=True)
    try:
        with deps.maintenance(), session_lock(deps.ledger._path.with_suffix(".session.lock")):
            outcome = corpus.latest_outcome(deps.ledger, catchup_id)
        if outcome is None:
            raise EvidenceError(f"{catchup_id}: no retained Catch-up outcome")
        return outcome
    finally:
        deps.ledger.close()


def _evaluations_from_outcome(heldout: dict[str, Any], outcome: dict[str, Any]) -> list[dict[str, Any]]:
    cases = heldout.get("evaluations")
    catchup_id = outcome.get("catchup_id")
    signals = outcome.get("signals")
    generations = outcome.get("provider_signal_generations", {})
    generation = outcome.get("permission_generation")
    live_signal_ids = outcome.get("consented_live_signal_ids", [])
    if (not isinstance(cases, list) or not cases or not isinstance(catchup_id, str)
            or not isinstance(signals, list) or any(not isinstance(sid, str) for sid in signals)
            or len(set(signals)) != len(signals)
            or not isinstance(generations, Mapping)
            or any(not isinstance(sid, str) or type(value) is not int or value < 0
                   for sid, value in generations.items())
            or (generation is not None and (type(generation) is not int or generation < 0))
            or not isinstance(live_signal_ids, list)
            or any(not isinstance(sid, str) for sid in live_signal_ids)
            or len(set(live_signal_ids)) != len(live_signal_ids)):
        raise EvidenceError("retained Catch-up has invalid provider provenance")
    generation_bound = {sid for sid, value in generations.items() if value == generation}
    if (not set(generations) <= set(signals) or not set(live_signal_ids) <= generation_bound
            or not set(live_signal_ids) <= set(signals)):
        raise EvidenceError("retained Catch-up has inconsistent provider provenance")
    live = set(live_signal_ids)
    evaluations = []
    for case in cases:
        if (not isinstance(case, dict) or case.get("catchup_id") != catchup_id
                or not isinstance(case.get("signal_id"), str) or case["signal_id"] not in signals):
            raise EvidenceError("held-out cases do not match the retained Catch-up")
        evaluations.append({**case, "consented_live": case["signal_id"] in live})
    return evaluations


def measure(config: dict[str, Any], workspace: Path, metrics_dir: Path, plan: dict[str, Any],
            domains: list[str]) -> dict[str, Any]:
    """One Domain's measurements entry: p95 and completion from metric records; attestations from the plan."""
    domain = plan.get("domain")
    if domain not in domains:
        raise EvidenceError(f"plan Domain {domain!r} is not among {domains}")
    cohort = policy.evaluation_cohort(config, domains, workspace=workspace)
    runs = metrics.read_runs(metrics_dir, limit=1_000_000)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_id.setdefault(str(run.get("catchup_id")), []).append(run)
    scenarios = plan.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != set(policy._RELEASE_WORKLOADS):
        missing = sorted(set(policy._RELEASE_WORKLOADS) - set(scenarios or {}))
        raise EvidenceError(f"plan needs exactly the seven release scenarios; missing {missing}")
    rows: dict[str, Any] = {}
    every: list[dict[str, Any]] = []
    normal: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for name in policy._RELEASE_WORKLOADS:
        entry = scenarios[name]
        ids = entry.get("catchup_ids")
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
            raise EvidenceError(f"{name}: needs distinct catchup_ids of the measured runs")
        reused = used_ids.intersection(ids)
        if reused:
            raise EvidenceError(f"{name}: reuses measured Catch-ups from another workload: {sorted(reused)}")
        used_ids.update(ids)
        absent = [cid for cid in ids if cid not in by_id]
        if absent:
            raise EvidenceError(f"{name}: no metric record for {absent}")
        selected = [run for cid in ids for run in by_id[cid]]
        if any(run.get("cohort") != cohort for run in selected):
            raise EvidenceError(f"{name}: measured run cohort differs from the current release cohort")
        summary = _summary(selected, name)
        missing_flags = [flag for flag in ATTESTED if flag not in entry]
        if missing_flags:
            raise EvidenceError(f"{name}: missing owner attestation {missing_flags}")
        if not isinstance(entry.get("reference_machine"), str) or not isinstance(entry.get("workload"), dict):
            raise EvidenceError(f"{name}: needs reference_machine and workload")
        rows[name] = {"cohort": cohort, "runs": summary["runs"], "p95_seconds": summary["p95_seconds"],
                      "passed": summary["complete_ratio"] == 1.0 and summary["unknown_runs"] == 0,
                      "reference_machine": entry["reference_machine"], "workload": entry["workload"],
                      **{flag: entry[flag] for flag in ATTESTED}}
        every += selected
        if name in NORMAL:
            normal += selected
    logical = metrics._logical_runs(every)
    total = len(logical)
    completed = sum(run.get("status") == "complete" for run in logical)
    for key in ("review_minutes", "monthly_usd", "source_age_hours", "workload"):
        if key not in plan:
            raise EvidenceError(f"plan needs measured {key}")
    cost = _cost_measurement(config, plan, every)
    return {domain: {"cohort": cohort, "workload": plan["workload"], "review_minutes": plan["review_minutes"],
                     **cost, "source_age_hours": plan["source_age_hours"],
                     "p95_seconds": _summary(normal, "normal workload")["p95_seconds"],
                     "completed_runs": completed, "total_runs": total,
                     "workloads": rows}}


def _gate(result: dict[str, Any], domains: list[str], cohort: dict[str, str], minimum_precision: float) -> None:
    """The scorecard half of policy._validate_release_evidence, unchanged in meaning."""
    measured = score(policy._release_cases(result.get("evaluations")))
    live = sum(case.get("consented_live") is True for case in result["evaluations"])
    if (not measured.passes or measured.precision < minimum_precision or result["cases"] != measured.cases
            or result["live_cases"] != live or not live):
        raise PermissionDenied("release scorecard lacks passing consented live evidence")
    policy._validate_domains(result, domains)
    policy._validate_evaluation_cohort(result, cohort)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def assemble_release(config: dict[str, Any], workspace: Path, heldout: dict[str, Any], bounds: dict[str, Any],
                     measurements: dict[str, Any], owner_signed_at: str, domains: list[str], *,
                     outcome: dict[str, Any], owner_decision_ref: str | None = None) -> dict[str, Any]:
    if not isinstance(heldout, dict):
        raise EvidenceError("held-out assembly must be a mapping")
    for key in ("cohort", "evaluations", "corpus", "document_fields"):
        if key not in heldout:
            raise EvidenceError(f"held-out assembly lacks {key}; run the heldout assemble step first")
    if not isinstance(owner_signed_at, str) or not owner_signed_at.strip():
        raise EvidenceError("owner_signed_at is required")
    decision_ref = _owner_decision_ref(config, owner_decision_ref)
    filled: dict[str, Any] = {}
    for domain in domains:
        if domain not in bounds:
            raise EvidenceError(f"{domain}: no owner-approved bounds")
        if domain not in measurements:
            raise EvidenceError(f"{domain}: no measurements")
        pack = dp.load_pack_file(Path(dp.__file__).parent / "packs" / f"{domain}.yaml")
        precedence = {name: list(pred.authority) for name, pred in pack.predicates.items()}
        filled[domain] = {"source_precedence": precedence, **bounds[domain]}
    evaluations = _evaluations_from_outcome(heldout, outcome)
    result = {"schema": "artha.release.v4", "cohort": heldout["cohort"], "evaluations": evaluations,
              "cases": score(policy._release_cases(evaluations)).cases, "live_cases": sum(c.get("consented_live") is True for c in evaluations),
              "owner_signed_at": owner_signed_at, "corpus": heldout["corpus"],
              "document_fields": heldout["document_fields"], "bounds": filled,
              "measurements": {d: measurements[d] for d in domains},
              "owner_decision_ref": decision_ref}
    result["recorded_at"] = owner_signed_at
    cohort = policy.evaluation_cohort(config, domains, workspace=workspace)
    minimum = (config.get("release") or {}).get("minimum_precision", 0.8)
    _gate(result, domains, cohort, minimum)
    return result


def recovery_receipt(config: dict[str, Any], work_checkpoint: Path, observations: dict[str, Any],
                     owner_signed_at: str, *, owner_decision_ref: str | None = None) -> dict[str, Any]:
    release = config.get("release")
    if not isinstance(release, dict) or not release.get("rollback_snapshot"):
        raise EvidenceError("release.rollback_snapshot must be approved before the recovery receipt")
    decision_ref = _owner_decision_ref(config, owner_decision_ref)
    snapshot = release["rollback_snapshot"]
    head = policy.verify_preservation_checkpoint(Path(snapshot["path"]), expected_sha256=snapshot["sha256"])
    work_sha = _file_sha256(Path(work_checkpoint))
    work_head = policy.verify_preservation_checkpoint(Path(work_checkpoint), expected_sha256=work_sha)
    evidence = policy._evidence_field(release)
    if not release.get(evidence):
        raise EvidenceError(f"release.{evidence} must be approved first; the receipt binds its cohort")
    cohort = json.loads(policy._approved_file(release[evidence], "release evidence")).get("cohort")
    bound = {"mac_personal": ("personal", snapshot["sha256"], head["generation"]),
             "windows_work": ("work", work_sha, work_head["generation"]),
             "personal_cloud_copy": ("personal", snapshot["sha256"], head["generation"])}
    if set(observations) != set(bound):
        raise EvidenceError(f"observations must be exactly {sorted(bound)}")
    rows = {}
    for name, (profile, digest, generation) in bound.items():
        observed = observations[name]
        if not isinstance(observed, dict):
            raise EvidenceError(f"{name}: recovery observation must be a mapping")
        if (observed.get("profile") != profile or observed.get("checkpoint_sha256") != digest
                or observed.get("checkpoint_generation") != generation):
            raise EvidenceError(f"{name}: recovery observation checkpoint identity differs")
        rows[name] = dict(observed)
    receipt = {"schema": "artha.recovery-rehearsal.v2", "passed": True, "reconciled": True,
               "owner_signed_at": owner_signed_at, "checkpoint_sha256": snapshot["sha256"],
               "checkpoint_generation": head["generation"], "cohort": cohort,
               "work_checkpoint": {"path": str(work_checkpoint), "sha256": work_sha, "profile": "work",
                                   "generation": work_head["generation"]},
               "observations": rows, "owner_decision_ref": decision_ref}
    receipt["recorded_at"] = owner_signed_at
    policy._validate_recovery_acceptance(receipt, release, head, str(head["generation"]))
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("measure", "assemble-release", "recovery-receipt"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, default=ROOT / "config" / "catchup_core.yaml")
        command.add_argument("--out", type=Path, required=True)
        if name != "recovery-receipt":
            command.add_argument("--domains", nargs="+")
        if name != "measure":
            command.add_argument("--owner-signed-at", required=True,
                                 help="non-authoritative recorded_at compatibility value")
            command.add_argument("--owner-decision-ref", required=True,
                                 help="authenticated owner decision reference")
    sub.choices["measure"].add_argument("--metrics-dir", type=Path, required=True)
    sub.choices["measure"].add_argument("--plan", type=Path, required=True)
    sub.choices["assemble-release"].add_argument("--heldout", type=Path, required=True)
    sub.choices["assemble-release"].add_argument("--bounds", type=Path, required=True)
    sub.choices["assemble-release"].add_argument("--measurements", type=Path, nargs="+", required=True)
    sub.choices["recovery-receipt"].add_argument("--work-checkpoint", type=Path, required=True)
    sub.choices["recovery-receipt"].add_argument("--observations", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config, workspace = _load_config(args.config)
        if args.command == "measure":
            value = measure(config, workspace, args.metrics_dir, _load_json(args.plan),
                            _domains(config, args.domains))
        elif args.command == "assemble-release":
            merged: dict[str, Any] = {}
            for path in args.measurements:
                merged.update(_load_json(path))
            heldout = _load_json(args.heldout)
            if not isinstance(heldout, dict):
                raise EvidenceError("held-out assembly must be a mapping")
            cases = heldout.get("evaluations")
            if (not isinstance(cases, list) or not cases
                    or any(not isinstance(case, dict) or not isinstance(case.get("catchup_id"), str)
                           or not case["catchup_id"] for case in cases)):
                raise EvidenceError("held-out evaluations must identify exactly one Catch-up")
            catchup_ids = {case["catchup_id"] for case in cases}
            if len(catchup_ids) != 1:
                raise EvidenceError("held-out evaluations must identify exactly one Catch-up")
            outcome = _load_retained_outcome(args.config, next(iter(catchup_ids)))
            value = assemble_release(config, workspace, heldout, _load_json(args.bounds),
                                     merged, args.owner_signed_at, _domains(config, args.domains),
                                     outcome=outcome, owner_decision_ref=args.owner_decision_ref)
        else:
            value = recovery_receipt(config, args.work_checkpoint, _load_json(args.observations),
                                     args.owner_signed_at, owner_decision_ref=args.owner_decision_ref)
    except (EvidenceError, PermissionDenied, corpus.CorpusError, RuntimeConfigError, LedgerError,
            KeyUnavailableError, SessionBusy, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    _write(args.out, value)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
