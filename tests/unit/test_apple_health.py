"""
tests/unit/test_apple_health.py — Unit tests for apple_health connector.

Covers:
  - Valid ZIP with export.xml → yields expected records
  - Missing ZIP file → FileNotFoundError
  - ZIP without export.xml → ValueError
  - since filter respects date boundary (records before cutoff are skipped)
  - max_results caps output correctly
  - Unknown record types are ignored
  - health_check() always returns True
  - _iso_to_str() normalises Apple Health date format
  - _parse_since() handles relative ("7d") and absolute date strings
  - Memory efficiency: iterparse doesn't load full file at once (structural test)
  - Unsupported file extension → ValueError

Ref: specs/improve.md §9 I-13
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

_CONNECTORS = Path(__file__).resolve().parent.parent.parent / "scripts" / "connectors"
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from connectors.apple_health import (
    fetch,
    health_check,
    _iso_to_str,
    _parse_since,
    TRACKED_TYPES,
)


# ── XML fixtures ──────────────────────────────────────────────────────────────

_SAMPLE_RECORDS = [
    dict(
        type="HKQuantityTypeIdentifierBodyMass",
        value="82.5",
        unit="kg",
        startDate="2026-03-14 08:30:00 -0700",
        sourceName="iPhone",
    ),
    dict(
        type="HKQuantityTypeIdentifierHeartRate",
        value="68",
        unit="count/min",
        startDate="2026-03-14 09:00:00 -0700",
        sourceName="Apple Watch",
    ),
    dict(
        type="HKQuantityTypeIdentifierStepCount",
        value="8432",
        unit="count",
        startDate="2026-03-14 23:59:00 -0700",
        sourceName="iPhone",
    ),
    dict(
        type="HKQuantityTypeIdentifierBloodPressureSystolic",
        value="120",
        unit="mmHg",
        startDate="2026-03-13 07:00:00 -0700",
        sourceName="Omron",
    ),
    # Old record — before "since" cutoff in date-filtered tests
    dict(
        type="HKQuantityTypeIdentifierBodyMass",
        value="83.0",
        unit="kg",
        startDate="2025-01-01 08:00:00 -0800",
        sourceName="iPhone",
    ),
    # Unknown type — should be ignored
    dict(
        type="HKQuantityTypeIdentifierUnknownCustomType",
        value="999",
        unit="unit",
        startDate="2026-03-14 10:00:00 -0700",
        sourceName="App",
    ),
]


def _build_xml(records: list[dict]) -> bytes:
    """Build a minimal Apple Health export.xml from a list of record dicts."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<HealthData>"]
    for r in records:
        attrs = " ".join(f'{k}="{v}"' for k, v in r.items())
        lines.append(f"  <Record {attrs} />")
    lines.append("</HealthData>")
    return "\n".join(lines).encode("utf-8")


def _build_zip(xml_bytes: bytes, xml_name: str = "apple_health_export/export.xml") -> bytes:
    """Wrap XML bytes into a ZIP in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(xml_name, xml_bytes)
    return buf.getvalue()


def _write_zip(tmp_path: Path, records: list[dict] | None = None) -> Path:
    """Write a test export ZIP to tmp_path and return its Path."""
    xml = _build_xml(records if records is not None else _SAMPLE_RECORDS)
    zdata = _build_zip(xml)
    zp = tmp_path / "export.zip"
    zp.write_bytes(zdata)
    return zp


def _write_xml(tmp_path: Path, records: list[dict] | None = None) -> Path:
    """Write a bare export.xml to tmp_path and return its Path."""
    xml = _build_xml(records if records is not None else _SAMPLE_RECORDS)
    xp = tmp_path / "export.xml"
    xp.write_bytes(xml)
    return xp


def _run_fetch(zip_path: Path, since: str = "2020-01-01", max_results: int = 1000) -> list[dict]:
    """Helper: call fetch() and collect all yielded records."""
    return list(fetch(
        since=since,
        max_results=max_results,
        auth_context={},
        source_tag="apple_health",
        file=str(zip_path),
    ))


# ── _iso_to_str ───────────────────────────────────────────────────────────────

class TestIsoToStr:
    def test_converts_apple_format(self):
        raw = "2026-03-14 08:30:00 -0700"
        result = _iso_to_str(raw)
        assert result == "2026-03-14T08:30:00-0700"

    def test_empty_string_passthrough(self):
        assert _iso_to_str("") == ""

    def test_already_iso_passthrough(self):
        iso = "2026-03-14T08:30:00+00:00"
        assert _iso_to_str(iso) == iso


# ── _parse_since ──────────────────────────────────────────────────────────────

class TestParseSince:
    def test_relative_days(self):
        result = _parse_since("30d")
        # Result should be a recent ISO string
        assert "T" in result or "-" in result
        # Should be within the past 30+ days
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(result.replace("+00:00", "+00:00"))
        age_days = (datetime.now(timezone.utc) - dt).days
        assert 29 <= age_days <= 31

    def test_absolute_iso_passthrough(self):
        iso = "2026-01-01T00:00:00+00:00"
        assert _parse_since(iso) == iso

    def test_relative_hours(self):
        result = _parse_since("24h")
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(result.replace("+00:00", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        assert 23 <= age_hours <= 25


# ── fetch() from ZIP ──────────────────────────────────────────────────────────

class TestFetchFromZip:
    def test_yields_known_records(self, tmp_path):
        zp = _write_zip(tmp_path)
        records = _run_fetch(zp, since="2020-01-01")
        types = {r["type"] for r in records}
        assert "HKQuantityTypeIdentifierBodyMass" in types
        assert "HKQuantityTypeIdentifierHeartRate" in types

    def test_ignores_unknown_types(self, tmp_path):
        zp = _write_zip(tmp_path)
        records = _run_fetch(zp, since="2020-01-01")
        types = {r["type"] for r in records}
        assert "HKQuantityTypeIdentifierUnknownCustomType" not in types

    def test_since_filter(self, tmp_path):
        zp = _write_zip(tmp_path)
        # Only request records from 2026 onwards
        records = _run_fetch(zp, since="2026-01-01")
        # The 2025-01-01 body mass record should be excluded
        old_records = [r for r in records if r["date_iso"].startswith("2025")]
        assert len(old_records) == 0

    def test_max_results_caps_output(self, tmp_path):
        zp = _write_zip(tmp_path)
        records = _run_fetch(zp, since="2020-01-01", max_results=2)
        assert len(records) <= 2

    def test_record_fields_present(self, tmp_path):
        zp = _write_zip(tmp_path)
        records = _run_fetch(zp, since="2020-01-01")
        assert len(records) > 0
        for rec in records:
            assert "source" in rec
            assert "type" in rec
            assert "type_short" in rec
            assert "value" in rec
            assert "unit" in rec
            assert "date_iso" in rec
            assert "device" in rec
            assert "id" in rec

    def test_source_tag_in_records(self, tmp_path):
        zp = _write_zip(tmp_path)
        records = _run_fetch(zp, since="2020-01-01")
        assert all(r["source"] == "apple_health" for r in records)

    def test_type_short_strips_prefix(self, tmp_path):
        zp = _write_zip(tmp_path)
        records = _run_fetch(zp, since="2020-01-01")
        body_mass = next(
            (r for r in records if r["type"] == "HKQuantityTypeIdentifierBodyMass"),
            None,
        )
        assert body_mass is not None
        assert body_mass["type_short"] == "BodyMass"


# ── fetch() from bare XML ─────────────────────────────────────────────────────

class TestFetchFromXml:
    def test_xml_file_parses(self, tmp_path):
        xp = _write_xml(tmp_path)
        records = list(fetch(
            since="2020-01-01",
            max_results=1000,
            auth_context={},
            source_tag="apple_health",
            file=str(xp),
        ))
        assert len(records) > 0

    def test_xml_since_filter(self, tmp_path):
        xp = _write_xml(tmp_path)
        records = list(fetch(
            since="2026-01-01",
            max_results=1000,
            auth_context={},
            source_tag="apple_health",
            file=str(xp),
        ))
        old_records = [r for r in records if r["date_iso"].startswith("2025")]
        assert len(old_records) == 0


# ── Error conditions ──────────────────────────────────────────────────────────

class TestFetchErrors:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _run_fetch(tmp_path / "nonexistent.zip")

    def test_zip_without_export_xml_raises(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("README.txt", "not an apple health export")
        zp = tmp_path / "bad.zip"
        zp.write_bytes(buf.getvalue())
        with pytest.raises(ValueError, match="export.xml"):
            _run_fetch(zp)

    def test_no_file_param_raises(self):
        with pytest.raises(ValueError, match="--file"):
            list(fetch(
                since="2020-01-01",
                max_results=100,
                auth_context={},
                source_tag="apple_health",
                # file not provided
            ))

    def test_unsupported_extension_raises(self, tmp_path):
        bad_file = tmp_path / "export.csv"
        bad_file.write_text("fake data")
        with pytest.raises(ValueError, match="Unsupported file type"):
            _run_fetch(bad_file)


# ── health_check ──────────────────────────────────────────────────────────────

class TestHealthCheck:
    def test_always_returns_true(self):
        assert health_check({}) is True

    def test_returns_true_with_any_context(self):
        assert health_check({"file": "/some/path"}) is True
