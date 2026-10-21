from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from catchup_core import documents
from catchup_core import ledger as lg
from test_coordinator import NOW, KEY, CIPHER


def pdf(text="Registration closes 2026-10-14"):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 20 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_real_text_pdf_is_grounded_to_page_without_plaintext_output(tmp_path):
    data = pdf()
    assert "2026-10-14" in documents.extract_pdf(data)["pages"][0]
    path = tmp_path / "source.pdf"
    path.write_bytes(data)
    with lg.Ledger(
        tmp_path / "runtime" / "ledger.db", id_key=KEY, cipher=CIPHER, known_subjects=frozenset({"owner"})
    ).open() as led:
        led.migrate()
        identity = documents.ingest(path, "owner", led, "owner", NOW)
        saved = led._path.parent / "documents" / f"{identity}.ae1"
        assert b"Registration" not in saved.read_bytes()
        fetched = documents.fetch_documents(led, "owner")
        assert fetched.records[0].upstream_id.endswith(":p1") and not fetched.records[0].incomplete
        assert "2026-10-14" in fetched.records[0].text
        led.forget("documents", "manual", fetched.records[0].upstream_id)
        assert documents.fetch_documents(led, "owner").records == ()


def test_blank_scan_stays_incomplete():
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    out = io.BytesIO()
    writer.write(out)
    assert not documents.extract_pdf(out.getvalue())["complete"]


def test_limits_and_worker_failure_do_not_claim_success():
    with pytest.raises(ValueError):
        documents.extract_pdf(b"not PDF")
    with pytest.raises(ValueError):
        documents.extract_pdf(b"%PDF-" + b"x" * documents.MAX_BYTES)
    with pytest.raises(ValueError):
        documents.extract_pdf(b"%PDF-x", runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout=b""))

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("pdf", 20)

    with pytest.raises(ValueError, match="limit"):
        documents.extract_pdf(b"%PDF-x", runner=timeout)


def test_worker_reads_real_pages_and_rejects_encrypted_or_oversized_inputs(monkeypatch, capsys):
    import json

    def run(data):
        monkeypatch.setattr(documents.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data)))
        documents._worker()

    run(pdf())
    assert json.loads(capsys.readouterr().out)["complete"]
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret")
    out = io.BytesIO()
    writer.write(out)
    with pytest.raises(ValueError):
        run(out.getvalue())
    monkeypatch.setattr(documents, "MAX_TEXT", 1)
    with pytest.raises(ValueError, match="text"):
        run(pdf())
    monkeypatch.setattr(documents, "MAX_BYTES", 2)
    with pytest.raises(ValueError, match="large"):
        run(b"abc")
