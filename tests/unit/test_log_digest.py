"""tests/unit/test_log_digest.py — Unit tests for scripts/log_digest.py.

All log data is synthetic — no real connector output (DD-5).
Ref: specs/eval.md EV-6, T-EV-6-01 through T-EV-6-08
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def digest_mod():
    return _load_module("log_digest", _SCRIPTS_DIR / "log_digest.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_log_file(log_dir: Path, date_str: str, records: list) -> Path:
    """Write a JSONL log file for the given date."""
    p = log_dir / f"artha.{date_str}.log.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return p


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago_str(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


# ===========================================================================
# T-EV-6-01: _parse_records skips malformed JSONL lines
# ===========================================================================

def test_parse_records_skips_malformed_lines(digest_mod, tmp_path):
    """T-EV-6-01: Malformed lines must be silently skipped; valid lines parsed."""
    log_file = tmp_path / "artha.2026-01-01.log.jsonl"
    log_file.write_text(
        '{"connector": "graph", "ok": true, "ms": 120}\n'
        'NOT VALID JSON {{{\n'
        '{"connector": "graph", "ok": false, "ms": 500}\n',
        encoding="utf-8",
    )
    records = digest_mod._parse_records(log_file)
    assert len(records) == 2, f"Expected 2 valid records, got {len(records)}"
    assert all("connector" in r for r in records)


# ===========================================================================
# T-EV-6-02: _aggregate counts total / errors correctly
# ===========================================================================

def test_aggregate_counts_correctly(digest_mod):
    """T-EV-6-02: _aggregate must count total and error records per connector."""
    records = [
        {"connector": "graph", "ok": True, "ms": 100},
        {"connector": "graph", "ok": False, "ms": 800},
        {"connector": "outlook", "ok": True, "ms": 50},
        {"connector": "graph", "ok": True, "ms": 120},
    ]
    agg = digest_mod._aggregate(records)

    assert agg["graph"]["total"] == 3
    assert agg["graph"]["errors"] == 1
    assert agg["outlook"]["total"] == 1
    assert agg["outlook"]["errors"] == 0


# ===========================================================================
# T-EV-6-03: _percentile returns correct p95
# ===========================================================================

def test_percentile_p95(digest_mod):
    """T-EV-6-03: _percentile([1..100], 95) must return the 95th value."""
    values = list(range(1, 101))  # 1–100
    p95 = digest_mod._percentile(values, 95)
    assert p95 == 95, f"Expected p95=95, got {p95}"


def test_percentile_single_element(digest_mod):
    """T-EV-6-03b: _percentile with a single-element list must not raise."""
    assert digest_mod._percentile([42], 95) == 42


# ===========================================================================
# T-EV-6-04: build_digest() error_budget_pct computed correctly
# ===========================================================================

def test_build_digest_error_budget_pct(digest_mod, tmp_path):
    """T-EV-6-04: error_budget_pct must equal total_errors / total_records * 100."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    records = [
        {"connector": "graph", "ok": True, "ms": 100},
        {"connector": "graph", "ok": False, "ms": 800},
        {"connector": "graph", "ok": False, "ms": 900},
        {"connector": "graph", "ok": True, "ms": 110},
    ]  # 2/4 = 50% error
    _write_log_file(log_dir, _today_str(), records)

    digest = digest_mod.build_digest(log_dir=log_dir, lookback_days=7)
    assert digest["total_records"] == 4
    assert digest["total_errors"] == 2
    assert abs(digest["error_budget_pct"] - 50.0) < 0.01


# ===========================================================================
# T-EV-6-05: HIGH_ERROR_RATE anomaly > 20%
# ===========================================================================

def test_detect_anomalies_high_error_rate(digest_mod, tmp_path):
    """T-EV-6-05: Connector with >20% error rate must trigger HIGH_ERROR_RATE."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # 3 errors in 5 records = 60%
    records = [
        {"connector": "graph", "ok": False, "ms": 900},
        {"connector": "graph", "ok": False, "ms": 800},
        {"connector": "graph", "ok": False, "ms": 750},
        {"connector": "graph", "ok": True, "ms": 100},
        {"connector": "graph", "ok": True, "ms": 110},
    ]
    _write_log_file(log_dir, _today_str(), records)

    digest = digest_mod.build_digest(log_dir=log_dir, lookback_days=7)
    codes = [a["code"] for a in digest["anomalies"]]
    assert "HIGH_ERROR_RATE" in codes, (
        f"Expected HIGH_ERROR_RATE anomaly. Got: {digest['anomalies']}"
    )


# ===========================================================================
# T-EV-6-06: No anomaly when error rate ≤ 20%
# ===========================================================================

def test_detect_anomalies_no_high_error_rate_under_threshold(digest_mod, tmp_path):
    """T-EV-6-06: Error rate ≤ 20% must NOT trigger HIGH_ERROR_RATE."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # 1 error in 10 = 10%
    records = (
        [{"connector": "graph", "ok": True, "ms": 100}] * 9
        + [{"connector": "graph", "ok": False, "ms": 800}]
    )
    _write_log_file(log_dir, _today_str(), records)

    digest = digest_mod.build_digest(log_dir=log_dir, lookback_days=7)
    codes = [a["code"] for a in digest["anomalies"]]
    assert "HIGH_ERROR_RATE" not in codes, (
        f"Unexpected HIGH_ERROR_RATE for 10% error rate. Got: {codes}"
    )


# ===========================================================================
# T-EV-6-07: Empty log directory → zero stats
# ===========================================================================

def test_build_digest_empty_dir(digest_mod, tmp_path):
    """T-EV-6-07: Empty log directory must produce zero-records digest."""
    log_dir = tmp_path / "empty_logs"
    log_dir.mkdir()

    digest = digest_mod.build_digest(log_dir=log_dir, lookback_days=7)
    assert digest["total_records"] == 0
    assert digest["total_errors"] == 0
    assert digest["error_budget_pct"] == 0.0
    assert digest["anomalies"] == []
    assert digest["schema_version"] == "1.0.0"


# ===========================================================================
# T-EV-6-08: write_digest atomic write creates tmp/log_digest.json
# ===========================================================================

def test_write_digest_atomic_write(digest_mod, tmp_path, monkeypatch):
    """T-EV-6-08: write_digest() must atomically create tmp/log_digest.json."""
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    monkeypatch.setattr(digest_mod, "TMP_DIR", tmp_dir)

    digest = {
        "schema_version": "1.0.0",
        "generated_at": "2026-01-15T12:00:00Z",
        "total_records": 10,
        "total_errors": 1,
        "error_budget_pct": 10.0,
        "connectors": {},
        "anomalies": [],
    }
    digest_mod.write_digest(digest)

    out_file = tmp_dir / "log_digest.json"
    assert out_file.exists(), "log_digest.json must be created"
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["total_records"] == 10
    assert data["schema_version"] == "1.0.0"
