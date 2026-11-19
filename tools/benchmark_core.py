#!/usr/bin/env python3
"""Synthetic workload benchmark for the Catch-up core (re-artha 10.4, 10.7, 11.2).

Runs full Catch-ups over generated mail (no personal data, no network, no model) in a temporary directory and reports
wall-clock seconds per run and per stage, cold versus warm, and how cost scales with volume.  It answers "is the
core fast enough, and does it scale roughly linearly?" so the owner's real-data measurements only have to show the
extraction and connector cost this benchmark deliberately excludes.

    python3 tools/benchmark_core.py                  # 30, 120 and 480 signals
    python3 tools/benchmark_core.py --sizes 30 1000 --json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from types import MethodType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from catchup_core import coordinator as co  # noqa: E402
from catchup_core import crypto, ids  # noqa: E402
from catchup_core import domain_pack as dp  # noqa: E402
from catchup_core import ledger as lg  # noqa: E402
from catchup_core import resources as rs  # noqa: E402
from catchup_core.blobs import EncryptedBlobStore  # noqa: E402
from catchup_core.contracts import CatchupRequest, CoverageStatus, ExecutionMode  # noqa: E402

KEY = ids.HmacKey(1, b"b" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x09" * 32}))
NOW = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)
PACK = dp.load_pack_file(ROOT / "scripts" / "catchup_core" / "packs" / "kids_school.yaml")
NEWSLETTER = "Weekly newsletter: the library has new books, the PTA bake sale is on Friday, lunch menu attached. " * 6
_EXPECTED_STAGES = {"FETCH", "EXTRACT", "RECONCILE", "COMPOSE", "COMMIT", "FINALIZED"}


class BenchmarkError(RuntimeError):
    """The benchmark did not exercise the requested synthetic work."""


def make_records(count: int) -> tuple[co.ConnectorRecord, ...]:
    """About one message in four carries a dated obligation; the rest are noise the core must stay quiet about."""
    records = []
    for i in range(count):
        if i % 4 == 0:
            day = 14 + (i // 4) % 14
            text = f"Families: SAT registration closes October {day} for kid{1 + i % 2}. Please register online."
        else:
            text = f"{NEWSLETTER} (issue {i})"
        records.append(co.ConnectorRecord(f"m{i}", text, occurred_at="2026-10-12T16:00:00+00:00"))
    return tuple(records)


def model(prompt: str) -> str:
    """Deterministic stand-in for the assistant: answers instantly so the benchmark measures the core, not a model."""
    message = prompt.rsplit("<untrusted>\n", 1)[1].rsplit("\n</untrusted>", 1)[0]
    if "registration closes October" not in message:
        return json.dumps({"complete": True, "candidates": []})
    start = message.index("registration closes October")
    day = int(message[start:].split("October ")[1].split(" ")[0])
    quote = message[start:start + len(f"registration closes October {day}")]
    subject = "kid2" if "kid2" in message else "kid1"
    return json.dumps({"complete": True, "candidates": [{
        "predicate": "exam_registration_deadline", "value": f"2026-10-{day:02d}", "subject_id": subject,
        "quote": quote, "span": [start, start + len(quote)]}]})


def run_once(directory: Path, records: tuple[co.ConnectorRecord, ...], catchup_id: str) -> dict[str, Any]:
    ledger = lg.Ledger(directory / "ledger.db", id_key=KEY, cipher=CIPHER, owner_id="owner").open()
    ledger.migrate()
    for method in ("require_personal_connector_admission", "require_personal_signal_admission",
                   "require_personal_fact_admission", "require_personal_calendar_admission"):
        setattr(ledger, method, lambda: None)
    upsert_signal = ledger.upsert_signal

    def reviewed(self, signal):
        signal_id, created = upsert_signal(signal)
        row = self.signal_row(signal_id)
        if not self._has_personal_binding(
                "signal", signal_id, row["upstream_version"], row["connector"],
                row["account_scope"], row["content_hmac"]):
            with self.transaction() as db:
                self._record_personal_binding(
                    db, "signal", signal_id, row["upstream_version"], row["connector"],
                    row["account_scope"], row["content_hmac"], provenance="source_review")
        return signal_id, created

    def bind_fact(self, db, fact_id, version, content_hmac, _provenance):
        self._record_personal_binding(
            db, "fact", fact_id, version, "ledger", self._owner, content_hmac)
        self._record_personal_binding(
            db, "fact_version", fact_id, version, "ledger", self._owner, content_hmac)

    ledger.upsert_signal = MethodType(reviewed, ledger)
    ledger._bind_fact_after_write = MethodType(bind_fact, ledger)
    blobs = EncryptedBlobStore(directory / "blobs", CIPHER, KEY)
    seen: list[dict[str, object]] = []
    allocation = rs.ResourcePolicy.from_mapping({
        "max_requests_per_bundle": min(65_536, max(64, len(records))),
        "max_prompt_bytes_per_bundle": min(32 * 1024 * 1024, max(512 * 1024, len(records) * 8 * 1024)),
        "max_requests_per_catchup": max(64, len(records)),
        "max_requests_per_month": max(64, len(records)),
        "max_attempts_per_signal": 3,
        "max_pending_signals": max(16, len(records)),
        "max_priority_streak": 3,
        "max_tokens_per_month": 16_000_000,
    })
    admissions = rs.AdmissionStore(ledger, allocation)
    deps = co.Deps(
        ledger=ledger, pack=PACK, blobs=blobs, id_key=KEY, extractor=model, subjects=["kid1", "kid2"], viewer="owner",
        now=lambda: NOW, source_class={"school_email": "school_email", "calendar": "calendar"},
        fetchers={"school_email": lambda: co.ConnectorFetch("school_email", "acct", records, CoverageStatus.COMPLETE),
                  "calendar": lambda: co.ConnectorFetch("calendar", "acct", (), CoverageStatus.COMPLETE)},
        publisher=lambda *_a: None, coverage_line=lambda: "Coverage: benchmark.", metrics=seen.append,
        admissions=admissions, require_resource_policy=True)
    started = time.perf_counter()
    try:
        result = co.run_catchup(CatchupRequest(catchup_id, "owner", "cli", mode=ExecutionMode.AUTHORITATIVE, host="bench"), deps)
        elapsed = time.perf_counter() - started
        entry = seen[0] if seen else {}
        usage = admissions.month_usage(NOW.strftime("%Y-%m"))
    finally:
        ledger.close()
    if result.status.value != "complete":
        raise BenchmarkError(
            f"benchmark did not complete: {result.status.value}; "
            f"reason={result.reason_unpublished or '-'}; failures={result.failures}")
    stages = dict(entry.get("stages", {}))
    if not _EXPECTED_STAGES <= set(stages) or len(result.artifact_ids) == 0:
        raise BenchmarkError(
            f"benchmark completed without the expected stages and artifacts "
            f"(stages={sorted(stages)}, artifacts={len(result.artifact_ids)})")
    return {"seconds": round(elapsed, 3), "stages": stages, "status": result.status.value,
            "alerts": len(result.artifact_ids), "model_requests": entry.get("model_requests", 0),
            "unknown_model_requests": usage.unknown_requests,
            "counted_model_tokens": usage.counted_tokens}


def benchmark(sizes: list[int]) -> list[dict[str, Any]]:
    rows = []
    for size in sizes:
        if size <= 0:
            raise BenchmarkError("benchmark sizes must be positive")
        if size > 65_536:
            raise BenchmarkError("benchmark size exceeds the bounded admission fixture")
        with tempfile.TemporaryDirectory(prefix=f"artha-benchmark-core-{size}-") as directory:
            scratch = Path(directory)
            records = make_records(size)
            cold = run_once(scratch, records, f"cu_cold_{size}")
            warm = run_once(scratch, records, f"cu_warm_{size}")          # same mail again: nothing new to read
            if cold["model_requests"] <= 0 or cold["unknown_model_requests"] <= 0:
                raise BenchmarkError("cold benchmark did not exercise reserved model requests")
        rows.append({"signals": size, "cold": cold, "warm": warm,
                     "cold_ms_per_signal": round(cold["seconds"] * 1000 / size, 2)})
    return rows


def scaling(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cost per signal at the largest size over the smallest: ~1 means linear, well above 1 means a hotspot."""
    if len(rows) < 2:
        return {"ratio": None, "verdict": "need two sizes to judge scaling"}
    ratio = rows[-1]["cold_ms_per_signal"] / max(rows[0]["cold_ms_per_signal"], 0.001)
    return {"ratio": round(ratio, 2), "verdict": "roughly linear" if ratio <= 2.0 else "superlinear: investigate"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[30, 120, 480])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        rows = benchmark(sorted(args.sizes))
    except BenchmarkError as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 1
    summary = {"rows": rows, "scaling": scaling(rows), "gate_p95_seconds": 180,
               "timing_samples": "single-run engineering measurement; not a production p95"}
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    print(f"{'signals':>8} {'cold s':>8} {'warm s':>8} {'ms/signal':>10}  slowest stage (cold)")
    for r in rows:
        stages = r["cold"]["stages"]
        slow = max(stages, key=lambda k: stages[k]) if stages else "-"
        print(f"{r['signals']:>8} {r['cold']['seconds']:>8} {r['warm']['seconds']:>8} {r['cold_ms_per_signal']:>10}  "
              f"{slow} ({stages.get(slow, 0)}s)")
    print(f"scaling: {summary['scaling']['verdict']} (ratio {summary['scaling']['ratio']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
