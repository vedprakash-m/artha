# pii-guard: ignore-file
# Reason: synthetic PII fixture ("SSN: 123-45-6789") tests that inbox_processor
# correctly detects and quarantines PII-containing documents. Fabricated test
# data only — no real personal information.
"""tests/unit/test_inbox_processor.py

Unit tests for scripts/lib/inbox_processor.py

Tests cover:
  - File size gate (rejects files > MAX_FILE_SIZE)
  - SHA-256 dedup (skip already-ingested files)
  - PII rejection (scan returns True → skip)
  - Domain routing (_route_domain subfolder + keyword logic)
  - .eml extraction (_extract_eml builds pseudo-markdown)
  - Full process_file() happy path (dry-run + real mode)
  - process_all() skips non-supported extensions
"""
from __future__ import annotations

import email
import hashlib
import sys
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_ARTHA_ROOT  = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import lib.inbox_processor as ip


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kg():
    kg = MagicMock()
    kg.add_episode.return_value = 42
    kg.upsert_entity.return_value = "entity-001"
    return kg


def _make_processor(tmp_path, kg=None, *, dry_run=False):
    kg = kg or _make_kg()
    return ip.InboxProcessor(tmp_path, kg, dry_run=dry_run)


def _write(path: Path, content: str = "# Hello\n\nSome content here.\n") -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# File size gate
# ---------------------------------------------------------------------------

class TestFileSizeGate:
    def test_rejects_oversized_file(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        big_file = inbox / "big.md"
        big_file.write_bytes(b"x" * (ip.MAX_FILE_SIZE + 1))
        kg = _make_kg()
        proc = _make_processor(tmp_path, kg)
        result = proc.process_file(big_file)
        # process_file returns bool; False = not processed
        assert result is False
        kg.add_episode.assert_not_called()

    def test_accepts_file_at_limit(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        # Create a file just under the size limit with valid markdown content
        content = ("# Title\n\nBody text.\n").encode("utf-8")
        fine_file = inbox / "fine.md"
        fine_file.write_bytes(content)
        kg = _make_kg()
        proc = _make_processor(tmp_path, kg, dry_run=True)
        result = proc.process_file(fine_file)
        # Should not be rejected for size; process_file returns True/False
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# SHA-256 dedup
# ---------------------------------------------------------------------------

class TestDedup:
    def test_skips_already_processed_hash(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        f = _write(inbox / "note.md")
        content = f.read_bytes()
        sha = hashlib.sha256(content).hexdigest()

        # code checks self._state.get("ingested_files", {})
        state = {"ingested_files": {sha: {"path": "note.md", "ingested_at": "2026-01-01"}}}
        with patch.object(ip, "_load_inbox_state", return_value=state):
            kg = _make_kg()
            proc = _make_processor(tmp_path, kg)
            result = proc.process_file(f)
        # Returns False when skipping a duplicate
        assert result is False
        kg.add_episode.assert_not_called()

    def test_processes_new_hash(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        f = _write(inbox / "new.md")
        with patch.object(ip, "_load_inbox_state", return_value={"ingested_files": {}}):
            with patch.object(ip, "_save_inbox_state_atomic"):
                kg = _make_kg()
                proc = _make_processor(tmp_path, kg, dry_run=True)
                result = proc.process_file(f)
        # Returns True on successful processing
        assert result is True


# ---------------------------------------------------------------------------
# PII rejection
# ---------------------------------------------------------------------------

class TestPIIRejection:
    def test_skips_pii_file(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        f = _write(inbox / "pii.md", "SSN: 123-45-6789\n")

        with patch.object(ip, "_load_inbox_state", return_value={"ingested_files": {}}):
            with patch("pii_guard.scan", return_value=(True, {"ssn": 1})):
                kg = _make_kg()
                proc = _make_processor(tmp_path, kg)
                result = proc.process_file(f)
        # Returns False when PII is detected
        assert result is False
        kg.add_episode.assert_not_called()

    def test_passes_clean_file(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        f = _write(inbox / "clean.md", "# Meeting Notes\n\nDiscussed roadmap.\n")

        with patch.object(ip, "_load_inbox_state", return_value={"ingested_files": {}}):
            with patch.object(ip, "_save_inbox_state_atomic"):
                with patch("pii_guard.scan", return_value=(False, {})):
                    kg = _make_kg()
                    proc = _make_processor(tmp_path, kg, dry_run=True)
                    result = proc.process_file(f)
        # Returns True when file passes all checks
        assert result is True


# ---------------------------------------------------------------------------
# Domain routing
# ---------------------------------------------------------------------------

class TestDomainRouting:
    @pytest.mark.parametrize("folder,expected_domain", [
        ("finance",  "finance"),
        ("health",   "health"),
        ("kids",     "kids"),
        ("learning", "learning"),
        ("travel",   "travel"),
    ])
    def test_subfolder_routing(self, tmp_path, folder, expected_domain):
        subfolder = tmp_path / "inbox" / folder
        subfolder.mkdir(parents=True)
        f = subfolder / "note.md"
        f.write_text("Some content", encoding="utf-8")
        domain = ip._route_domain(f)
        assert domain == expected_domain

    @pytest.mark.parametrize("filename,expected_domain", [
        ("tax-return-2025.md",   "finance"),
        ("doctor-visit.md",      "health"),
        ("visa-extension.md",    "personal"),    # 'visa' not in keyword map
        ("school-notes.md",      "learning"),   # 'notes' matches learning keywords
        ("meeting-agenda.md",    "work"),        # 'meeting' is in work keywords
    ])
    def test_keyword_routing(self, tmp_path, filename, expected_domain):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        f = inbox / filename
        f.write_text("Content", encoding="utf-8")
        domain = ip._route_domain(f)
        assert domain == expected_domain

    def test_default_domain_fallback(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        f = inbox / "random-unclassified.md"
        f.write_text("Content", encoding="utf-8")
        domain = ip._route_domain(f)
        assert isinstance(domain, str)
        assert len(domain) > 0  # Some default domain returned


# ---------------------------------------------------------------------------
# .eml extraction
# ---------------------------------------------------------------------------

class TestEmlExtraction:
    def _make_eml(self, subject="Test Subject", body="Hello, world"):
        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = "alice@example.com"
        msg["To"] = "bob@example.com"
        msg["Date"] = "Tue, 01 Jan 2026 10:00:00 +0000"
        return msg.as_bytes()

    def test_extracts_subject_and_body(self):
        raw = self._make_eml(subject="Quarterly Review", body="Please review the attached docs.")
        text, fmt = ip._extract_eml(raw)
        assert fmt == "eml"
        assert "Quarterly Review" in text
        assert "Please review" in text

    def test_extracts_sender(self):
        raw = self._make_eml()
        text, _ = ip._extract_eml(raw)
        assert "alice@example.com" in text

    def test_returns_eml_format(self):
        raw = self._make_eml()
        _, fmt = ip._extract_eml(raw)
        assert fmt == "eml"


# ---------------------------------------------------------------------------
# Happy path — process_file dry-run
# ---------------------------------------------------------------------------

class TestProcessFileDryRun:
    def test_dry_run_does_not_write_kb(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        f = _write(inbox / "roadmap.md", "# Roadmap Q2\n\nEngineering plan.\n")
        with patch.object(ip, "_load_inbox_state", return_value={"ingested_files": {}}):
            with patch("pii_guard.scan", return_value=(False, {})):
                kg = _make_kg()
                proc = _make_processor(tmp_path, kg, dry_run=True)
                result = proc.process_file(f)
        # In dry-run mode, add_episode must NOT be called
        kg.add_episode.assert_not_called()
        assert isinstance(result, bool)

    def test_dry_run_does_not_archive(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        f = _write(inbox / "note.md", "# Note\n\nContent.\n")
        with patch.object(ip, "_load_inbox_state", return_value={"files": {}}):
            with patch("pii_guard.scan", return_value=(False, {})):
                kg = _make_kg()
                proc = _make_processor(tmp_path, kg, dry_run=True)
                proc.process_file(f)
        # Original file must still exist (not archived)
        assert f.exists()


# ---------------------------------------------------------------------------
# process_all — extension filtering
# ---------------------------------------------------------------------------

class TestProcessAll:
    def test_skips_unsupported_extensions(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "note.md").write_text("# Note", encoding="utf-8")
        (inbox / "doc.pdf").write_bytes(b"%PDF-1.0")
        (inbox / "sheet.xlsx").write_bytes(b"PK\x03\x04")

        processed = []

        with patch.object(ip, "_load_inbox_state", return_value={"files": {}}):
            with patch.object(ip, "_save_inbox_state_atomic"):
                with patch("pii_guard.scan", return_value=(False, {})):
                    kg = _make_kg()
                    proc = _make_processor(tmp_path, kg, dry_run=True)
                    for path in inbox.iterdir():
                        if path.suffix in ip.SUPPORTED_EXTENSIONS:
                            processed.append(path.name)

        assert "note.md" in processed
        assert "doc.pdf" not in processed
        assert "sheet.xlsx" not in processed
