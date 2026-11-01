"""Local-only OCR (RA-21): Tesseract on stdin inside a network-denied sandbox, no image on disk, honest confidence."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core import documents, ocr

TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def tsv(*words):
    rows = [TSV_HEADER]
    for line, (text, conf) in enumerate(words, 1):
        rows.append(f"5\t1\t1\t1\t{line}\t1\t0\t0\t1\t1\t{conf}\t{text}\n")
    rows.append("1\t1\t0\t0\t0\t0\t0\t0\t1\t1\t-1\t\n")
    return "".join(rows)


def runner_returning(stdout, code=0, seen=None):
    def run(argv, **kwargs):
        if seen is not None:
            seen.update(argv=argv, kwargs=kwargs)
        return SimpleNamespace(returncode=code, stdout=stdout.encode(), stderr=b"")
    return run


def test_text_and_mean_confidence_come_from_tesseract_tsv():
    result = ocr.ocr_image(b"\x89PNG....", tesseract="/x/tesseract", confine=False,
                           runner=runner_returning(tsv(("Exam", 96), ("Friday", 90))))
    assert result.text == "Exam\nFriday" and result.confidence == 93.0


def test_the_image_goes_on_stdin_with_a_minimal_environment_and_never_to_a_file(tmp_path):
    seen = {}
    ocr.ocr_image(b"\x89PNG....", tesseract="/x/tesseract", confine=False, runner=runner_returning(tsv(("a", 90)), seen=seen))
    assert seen["argv"][:3] == ["/x/tesseract", "stdin", "stdout"] and seen["kwargs"]["input"] == b"\x89PNG...."
    assert set(seen["kwargs"]["env"]) <= set(ocr.CHILD_ENV) | {"OMP_THREAD_LIMIT"}
    assert not any(str(tmp_path) in str(a) for a in seen["argv"])


def test_the_command_is_confined_without_network_where_the_platform_can(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "darwin")
    monkeypatch.setattr(ocr.shutil, "which", lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None)
    argv = ocr.confined(["/x/tesseract", "stdin", "stdout"])
    assert argv[0] == "/usr/bin/sandbox-exec" and "(deny network*)" in argv[2] and argv[-3:] == ["/x/tesseract", "stdin", "stdout"]


def _can_sandbox_exec() -> bool:
    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        return False
    try:
        p = subprocess.run(ocr.confined([sys.executable, "-c", "pass"]), capture_output=True)
        return p.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _can_sandbox_exec(), reason="needs macOS sandbox-exec (nested sandbox unsupported)")
def test_the_sandbox_really_denies_network_to_a_child_process():
    probe = "import socket; socket.create_connection(('127.0.0.1', 9), timeout=2)"
    plain = subprocess.run([sys.executable, "-c", probe], capture_output=True)
    boxed = subprocess.run(ocr.confined([sys.executable, "-c", probe]), capture_output=True)
    assert b"Operation not permitted" in boxed.stderr or b"PermissionError" in boxed.stderr
    assert plain.returncode != 0 and boxed.returncode != 0
    assert b"refused" in plain.stderr.lower() and b"refused" not in boxed.stderr.lower()


def test_a_missing_engine_or_failed_run_is_reported_not_guessed():
    assert ocr.ocr_image(b"x", tesseract=None, confine=False).text == "" and ocr.ocr_image(b"x", tesseract=None, confine=False).available is False
    failed = ocr.ocr_image(b"x", tesseract="/x/t", confine=False, runner=runner_returning("", code=1))
    assert failed.text == "" and failed.confidence == 0.0 and failed.available is True


def test_oversized_images_are_refused_before_any_process_starts():
    called = []
    result = ocr.ocr_image(b"x" * (ocr.MAX_IMAGE_BYTES + 1), tesseract="/x/t", confine=False,
                           runner=lambda *a, **k: called.append(1))
    assert result.text == "" and not called


def test_documents_worker_blocks_every_network_operation():
    code = ("import sys; sys.path.insert(0, %r); from catchup_core import documents; documents._deny_network()\n"
            "import socket\n"
            "try:\n    socket.create_connection(('127.0.0.1', 9), timeout=1)\nexcept PermissionError:\n    print('blocked')\n"
            "try:\n    socket.getaddrinfo('example.com', 443)\nexcept PermissionError:\n    print('blocked')\n") % str(
                Path(documents.__file__).resolve().parents[1])
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.split() == ["blocked", "blocked"], out.stderr


def test_a_scanned_page_gets_ocr_text_marked_with_its_confidence_and_low_confidence_is_incomplete():
    pages, meta = documents.merge_ocr(["", "typed text"], {0: ocr.OcrResult("Exam on 2026-10-14", 91.0, True)})
    assert pages[0].startswith("[OCR, confidence 91%]") and pages[1] == "typed text"
    assert {k: meta[k] for k in ("ocr_pages", "ocr_confidence", "complete")} == {
        "ocr_pages": [0], "ocr_confidence": {"0": 91.0}, "complete": True}
    assert meta["page_methods"] == ["ocr", "text"] and meta["ocr_languages"] == "eng"
    _, low = documents.merge_ocr(["", "t"], {0: ocr.OcrResult("blurry", 40.0, True)})
    assert low["complete"] is False
    _, none = documents.merge_ocr(["", "t"], {})
    assert none["complete"] is False


def test_image_files_are_recognised_by_their_signature_and_other_bytes_are_refused():
    assert documents.kind_of(b"\x89PNG\r\n\x1a\n....") == "image"
    assert documents.kind_of(b"\xff\xd8\xff\xe0....") == "image"
    assert documents.kind_of(b"%PDF-1.7") == "pdf"
    with pytest.raises(ValueError):
        documents.kind_of(b"MZ\x90")
    assert json.dumps(documents.MAX_BYTES)


def test_the_child_script_installs_the_network_block_but_importing_the_module_does_not():
    tail = Path(documents.__file__).read_text(encoding="utf-8").split('if __name__ == "__main__":')[1]
    assert "_deny_network()" in tail
    out = subprocess.run([sys.executable, "-c", "import socket, sys; sys.path.insert(0, %r); "
                          "from catchup_core import documents; socket.socket().close(); print('ok')" % str(
                              Path(documents.__file__).resolve().parents[1])], capture_output=True, text=True)
    assert out.stdout.strip() == "ok"


# --- real engine (skipped where Tesseract or Pillow is missing) --------------------------------------------------

def _rendered(text: str, fmt: str) -> bytes:
    from io import BytesIO  # noqa: PLC0415

    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415
    font = ImageFont.load_default(size=48)
    image = Image.new("RGB", (1400, 160), "white")
    ImageDraw.Draw(image).text((30, 40), text, fill="black", font=font)
    out = BytesIO()
    image.save(out, fmt)
    return out.getvalue()


real = pytest.mark.skipif(not ocr.find_tesseract() or not _can_sandbox_exec() or not pytest.importorskip("PIL", reason="needs Pillow"),
                          reason="Tesseract is not installed or sandbox-exec not functional")


@real
def test_real_tesseract_reads_a_synthetic_image_inside_the_sandbox():
    result = ocr.ocr_image(_rendered("Reading assessment Thursday October 8", "PNG"))
    assert result.available and "assessment" in result.text.lower() and result.confidence > 60


@real
def test_a_scanned_pdf_is_read_through_the_real_child_process_and_marked_as_ocr():
    scan = _rendered("Registration deadline October 14 2026", "PDF")
    extracted = documents.extract_pdf(scan)
    assert extracted["ocr_pages"] == [0] and extracted["pages"][0].startswith("[OCR, confidence")
    assert "deadline" in extracted["pages"][0].lower() and extracted["complete"] is True


@real
def test_a_png_goes_through_the_image_path_and_a_blank_image_stays_incomplete():
    ok = documents.extract_pdf(_rendered("Picture retakes October 30", "PNG"), mode="image")
    assert "retakes" in ok["pages"][0].lower() and ok["complete"] is True
    blank = documents.extract_pdf(_rendered("", "PNG"), mode="image")
    assert blank["complete"] is False


# --- in-process coverage of the worker and the guards (the real child runs in a subprocess) -------------------------

def _worker_output(monkeypatch, capsys, data, *argv):
    import io  # noqa: PLC0415
    monkeypatch.setattr(documents.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data)))
    monkeypatch.setattr(documents.sys, "argv", ["documents.py", *argv])
    documents._worker()
    return json.loads(capsys.readouterr().out)


def test_the_socket_guard_refuses_socket_events_and_nothing_else():
    for event in ("socket.connect", "socket.getaddrinfo", "socket.bind"):
        with pytest.raises(PermissionError):
            documents._refuse_socket(event, ())
    documents._refuse_socket("socket.__new__", ())
    documents._refuse_socket("open", ())


def test_the_worker_ocrs_an_image_in_image_mode(monkeypatch, capsys):
    monkeypatch.setattr(ocr, "ocr_image", lambda data: ocr.OcrResult("Fun Run Friday", 88.0, True))
    out = _worker_output(monkeypatch, capsys, b"\x89PNG\r\n\x1a\n....", "image")
    assert out["pages"][0].startswith("[OCR, confidence 88%]") and out["complete"] is True


def test_the_worker_ocrs_only_the_text_poor_pages_of_a_scanned_pdf(monkeypatch, capsys):
    pytest.importorskip("PIL")
    from io import BytesIO  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415
    buffer = BytesIO()
    Image.new("RGB", (200, 100), "white").save(buffer, "PDF")
    calls = []
    monkeypatch.setattr(ocr, "ocr_image", lambda data: calls.append(len(data)) or ocr.OcrResult("Scanned notice", 91.0, True))
    out = _worker_output(monkeypatch, capsys, buffer.getvalue(), "pdf")
    assert calls and out["ocr_pages"] == [0] and "Scanned notice" in out["pages"][0] and out["complete"] is True
    monkeypatch.setattr(ocr, "ocr_image", lambda data: ocr.OcrResult("", 0.0, False))
    assert _worker_output(monkeypatch, capsys, buffer.getvalue(), "pdf")["complete"] is False


def test_an_oversized_file_is_refused_before_it_is_read(tmp_path, monkeypatch):
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-" + b"x" * 64)
    monkeypatch.setattr(documents, "MAX_BYTES", 16)

    class Ledger:
        _consent_subjects = frozenset()
        _path = tmp_path / "ledger.db"
    monkeypatch.setattr("catchup_core.policy.authorize_source", lambda *a, **k: None)
    with pytest.raises(ValueError, match="smaller"):
        documents.ingest(big, "owner", Ledger(), "owner", __import__("datetime").datetime(2026, 10, 21))
