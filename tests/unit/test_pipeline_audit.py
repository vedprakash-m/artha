"""Direct unit tests for scripts/pipeline_audit.py."""
from __future__ import annotations

import sys

import pipeline_audit as pa

ARTHA_TEST_OWNER = {
    "requirements": ["RA-04", "FR-27"],
    "modules": ["scripts/pipeline_audit.py"],
}


def test_audit_counts_citations_missing_bad_and_uncited(tmp_path, monkeypatch):
    oi = tmp_path / "open_items.md"
    oi.write_text("- id: OI-010\n  status: open\n", encoding="utf-8")
    monkeypatch.setattr(pa, "_OPEN_ITEMS", oi)

    briefing = tmp_path / "2026-10-12.md"
    briefing.write_text(
        "# Morning Briefing\n"
        "- Pay rent [src: OI-010]\n"
        "Portfolio is up today [src: state:finance] and signal [src: signal:SIG-004].\n"
        "Tax deadline is Friday without any citation.\n"
        "Missing item {{MISSING: tooth_check}}\n"
        "Stale item [src: OI-999]\n"
        "Malformed citation [src: unknown_source]\n",
        encoding="utf-8",
    )

    r = pa.audit(briefing)
    assert r["file"] == "2026-10-12.md"
    assert r["oi"] == 2
    assert r["state"] == 1
    assert r["signal"] == 1
    assert r["missing"] == 1
    assert r["invalid_oi"] == ["OI-999"]
    assert r["bad"] == ["[src: unknown_source]"]
    assert r["uncited"] == 1
    assert r["total_src"] == 4


def test_append_audit_and_main_last_and_missing(tmp_path, monkeypatch, capsys):
    briefings = tmp_path / "briefings"
    briefings.mkdir()
    b1 = briefings / "2026-10-12.md"
    b1.write_text("Balance is confirmed [src: state:finance].\n", encoding="utf-8")
    oi = tmp_path / "open_items.md"
    oi.write_text("", encoding="utf-8")
    audit_log = tmp_path / "audit.md"

    monkeypatch.setattr(pa, "_ROOT", tmp_path)
    monkeypatch.setattr(pa, "_OPEN_ITEMS", oi)
    monkeypatch.setattr(pa, "_AUDIT_FILE", audit_log)

    monkeypatch.setattr(sys, "argv", ["pipeline_audit.py", "--last"])
    assert pa.main() == 0
    assert "2026-10-12.md" in audit_log.read_text(encoding="utf-8")
    assert "src=1" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["pipeline_audit.py", str(tmp_path / "missing.md")])
    assert pa.main() == 1
    assert "not found" in capsys.readouterr().err
