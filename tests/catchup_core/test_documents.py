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


def test_method_is_worker_context_not_an_ocr_marker():
    pages, meta = documents.merge_ocr(["[OCR, confidence 99%] literal document text", ""],
        {1: SimpleNamespace(text="Synthetic amount 12.34", confidence=99.0)})
    assert meta["page_methods"] == ["text", "ocr"]
    assert meta["complete"]
    assert "literal document text" in pages[0]


@pytest.mark.parametrize("metadata,expected", [({}, "unknown"), ({"page_methods": ["ocr"]}, "ocr"),
    ({"page_methods": ["text"]}, "text"), ({"page_methods": ["forged"]}, "unknown"),
    ({"page_methods": "text"}, "unknown")])
def test_fetch_transports_explicit_method_and_legacy_is_unknown(tmp_path, metadata, expected):
    import json
    with lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER) as led:
        led.migrate()
        directory = tmp_path / "documents"
        directory.mkdir()
        payload = {"subject": "owner", "observed_at": NOW.isoformat(), "complete": True,
                   "pages": ["[OCR, confidence 99%] not trusted provenance"], **metadata}
        (directory / "doc_synthetic.ae1").write_bytes(CIPHER.encrypt(
            json.dumps(payload).encode(), aad=b"doc_synthetic")[0])
        record = documents.fetch_documents(led, "owner").records[0]
        assert record.document_method == expected


@pytest.mark.parametrize("outcome", ["empty", "mixed", "unreadable", "timeout", "oserror"])
def test_worker_caps_ocr_page_attempts_independent_of_results(monkeypatch, capsys, outcome):
    import json

    import pypdf

    from catchup_core import ocr

    extracted_pages = []
    image_attempts = []
    calls = []
    real_ocr_image = ocr.ocr_image

    class BlankImagePage:
        def extract_text(self):
            extracted_pages.append(self)
            return ""

        @property
        def images(self):
            image_attempts.append(self)
            if outcome == "unreadable":
                raise ValueError("synthetic unreadable image")
            return [SimpleNamespace(data=b"synthetic image")]

    def failing_runner(*args, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("synthetic OCR", ocr.TIMEOUT_SECONDS)
        raise OSError("synthetic OCR failure")

    def fake_ocr_image(data):
        calls.append(data)
        if outcome in ("timeout", "oserror"):
            return real_ocr_image(data, tesseract="synthetic", confine=False, runner=failing_runner)
        text = "Synthetic recognized text" if outcome == "mixed" and len(calls) % 2 == 0 else ""
        return ocr.OcrResult(text, 95.0 if text else 0.0, True)

    reader = SimpleNamespace(is_encrypted=False, pages=[BlankImagePage() for _ in range(40)])
    monkeypatch.setattr(pypdf, "PdfReader", lambda *args, **kwargs: reader)
    monkeypatch.setattr(ocr, "ocr_image", fake_ocr_image)
    monkeypatch.setattr(documents.sys, "argv", ["documents.py", "pdf"])
    monkeypatch.setattr(documents.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"%PDF-synthetic")))

    documents._worker()

    result = json.loads(capsys.readouterr().out)
    assert len(calls) <= documents.OCR_MAX_PAGES
    assert len(image_attempts) == documents.OCR_MAX_PAGES
    assert len(calls) == (0 if outcome == "unreadable" else documents.OCR_MAX_PAGES)
    assert len(extracted_pages) == len(result["pages"]) == 40
    assert not result["complete"]
    assert result["pages"][documents.OCR_MAX_PAGES:] == [""] * (40 - documents.OCR_MAX_PAGES)
    expected_ocr_pages = list(range(1, documents.OCR_MAX_PAGES, 2)) if outcome == "mixed" else []
    assert result["ocr_pages"] == expected_ocr_pages


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


def _rows_pdf(rows, widgets=()):
    from pypdf.generic import ArrayObject, NumberObject, TextStringObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    commands = ["BT /F1 10 Tf"]
    for number, cells in enumerate(rows):
        y = 250 - 20 * number
        for column, cell in enumerate(cells):
            commands.append(f"1 0 0 1 {20 + 150 * column} {y} Tm ({cell}) Tj")
    commands.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    if widgets:
        annotations = ArrayObject()
        for name, value in widgets:
            annotations.append(writer._add_object(DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject(name),
                NameObject("/V"): TextStringObject(value),
                NameObject("/Rect"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)])})))
        page[NameObject("/Annots")] = annotations
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_table_page_is_read_in_layout_order_and_marked_as_a_table():
    data = _rows_pdf([("Item", "Amount", "Due"), ("Tuition", "1200.00", "2026-10-14"),
                      ("Bus", "85.00", "2026-10-20")])
    result = documents.extract_pdf(data)
    assert result["page_structure"] == ["table"]
    row = next(line for line in result["pages"][0].splitlines() if "Tuition" in line)
    assert row.index("Tuition") < row.index("1200.00") < row.index("2026-10-14")
    assert result["page_methods"] == ["text"]


def test_plain_paragraph_is_not_marked_as_a_table():
    assert documents.extract_pdf(pdf())["page_structure"] == ["text"]


def test_filled_form_fields_become_labeled_page_text_and_stay_grounded():
    data = _rows_pdf([("Registration form",)], widgets=[("child_name", "Asha"), ("deadline", "2026-10-14")])
    result = documents.extract_pdf(data)
    assert "Form field child_name: Asha" in result["pages"][0]
    assert "Form field deadline: 2026-10-14" in result["pages"][0]
    assert result["page_structure"] == ["form"]
    assert result["page_methods"] == ["text"]


def test_form_field_text_is_bounded_and_control_characters_removed():
    value = "A" * 500 + "\x00\x07line\nbreak"
    lines = documents.form_lines([{"/Subtype": "/Widget", "/T": "name", "/V": value}] * 80)
    assert len(lines) == documents.MAX_FORM_FIELDS
    assert all(len(line) <= documents.MAX_FORM_VALUE + 80 and "\x00" not in line and "\n" not in line
               for line in lines)


def test_right_to_left_text_keeps_reading_order_unverified():
    pages, meta = documents.merge_ocr(["Date שלום 2026-10-14", "Plain English page"], {})
    assert meta["page_scripts"] == [["hebrew", "latin"], ["latin"]]
    assert meta["page_methods"] == ["unknown", "text"]
    assert meta["complete"]


def test_non_latin_left_to_right_text_is_still_text_and_ocr_language_is_recorded():
    pages, meta = documents.merge_ocr(["तारीख 2026-10-14", ""],
        {1: SimpleNamespace(text="Synthetic amount", confidence=90.0)})
    assert meta["page_scripts"][0] == ["devanagari"]
    assert meta["page_methods"] == ["text", "ocr"]
    assert meta["ocr_languages"] == "eng"


def test_image_with_non_english_script_is_not_asserted_complete_by_english_ocr(monkeypatch):
    pages, meta = documents.merge_ocr([""], {0: SimpleNamespace(text="Дата 2026", confidence=95.0)})
    assert meta["page_methods"] == ["ocr"] and meta["page_scripts"] == [["cyrillic", "latin"]]
    assert not meta["complete"]


def test_form_lines_skip_other_annotations_and_empty_fields():
    widgets = [{"/Subtype": "/Link", "/T": "a", "/V": "x"}, {"/Subtype": "/Widget", "/T": "b", "/V": ""},
               {"/Subtype": "/Widget", "/T": "", "/V": "no name"}, {"/Subtype": "/Widget", "/T": "c"},
               {"/Subtype": "/Widget", "/T": "d", "/V": "kept"}]
    assert documents.form_lines(widgets) == ["Form field d: kept"]


def test_unreadable_annotations_leave_extracted_text(monkeypatch, capsys):
    import json

    import pypdf

    class Page:
        def extract_text(self, **kwargs):
            return "Registration closes 2026-10-14"

        def get(self, *args):
            raise ValueError("synthetic damaged annotation list")

    reader = SimpleNamespace(is_encrypted=False, pages=[Page()])
    monkeypatch.setattr(pypdf, "PdfReader", lambda *args, **kwargs: reader)
    monkeypatch.setattr(documents.sys, "argv", ["documents.py", "pdf"])
    monkeypatch.setattr(documents.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"%PDF-synthetic")))
    documents._worker()
    result = json.loads(capsys.readouterr().out)
    assert result["pages"] == ["Registration closes 2026-10-14"] and result["page_structure"] == ["text"]
