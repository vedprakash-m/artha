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
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from catchup_core import coordinator as co  # noqa: E402
from catchup_core import crypto, ids  # noqa: E402
from catchup_core import domain_pack as dp  # noqa: E402
from catchup_core import ledger as lg  # noqa: E402
from catchup_core.blobs import EncryptedBlobStore  # noqa: E402
from catchup_core.contracts import CatchupRequest, CoverageStatus, ExecutionMode  # noqa: E402

KEY = ids.HmacKey(1, b"b" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x09" * 32}))
NOW = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)
PACK = dp.load_pack_file(ROOT / "scripts" / "catchup_core" / "packs" / "kids_school.yaml")
NEWSLETTER = "Weekly newsletter: the library has new books, the PTA bake sale is on Friday, lunch menu attached. " * 6


def make_records(count: int) -> tuple[co.ConnectorRecord, ...]:
    """About one message in four carries a dated obligation; the rest are noise the core must stay quiet about."""
    records = []
    for i in range(count):
        if i % 4 == 0:
            day = 14 + (i // 4) % 14
            text = f"Families: SAT registration closes October {day} for kid{1 + i % 2}. Please register online."
        else:
            text = f"{NEWSLETTER} (issue {i})"
        records.append(co.ConnectorRecord(f"m{i}", text))
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
    blobs = EncryptedBlobStore(directory / "blobs", CIPHER, KEY)
    seen: list[dict[str, object]] = []
    deps = co.Deps(
        ledger=ledger, pack=PACK, blobs=blobs, id_key=KEY, extractor=model, subjects=["kid1", "kid2"], viewer="owner",
        now=lambda: NOW, source_class={"school_email": "school_email", "calendar": "calendar"},
        fetchers={"school_email": lambda: co.ConnectorFetch("school_email", "acct", records, CoverageStatus.COMPLETE),
                  "calendar": lambda: co.ConnectorFetch("calendar", "acct", (), CoverageStatus.COMPLETE)},
        publisher=lambda *_a: None, coverage_line=lambda: "Coverage: benchmark.", metrics=seen.append)
    started = time.perf_counter()
    try:
        result = co.run_catchup(CatchupRequest(catchup_id, "owner", "cli", mode=ExecutionMode.AUTHORITATIVE, host="bench"), deps)
        elapsed = time.perf_counter() - started
    finally:
        ledger.close()
    entry = seen[0] if seen else {}
    return {"seconds": round(elapsed, 3), "stages": entry.get("stages", {}), "status": result.status.value,
            "alerts": len(result.artifact_ids), "model_requests": entry.get("model_requests", 0)}


def benchmark(sizes: list[int]) -> list[dict[str, Any]]:
    rows = []
    for size in sizes:
        with tempfile.TemporaryDirectory(prefix="artha-bench-") as tmp:
            records = make_records(size)
            cold = run_once(Path(tmp), records, f"cu_cold_{size}")
            warm = run_once(Path(tmp), records, f"cu_warm_{size}")          # same mail again: nothing new to read
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
    rows = benchmark(sorted(args.sizes))
    summary = {"rows": rows, "scaling": scaling(rows), "gate_p95_seconds": 180}
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
