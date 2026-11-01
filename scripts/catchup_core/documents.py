"""Bounded document ingestion: text PDFs, scanned PDFs and images (local OCR). Original bytes and page text stay
encrypted locally, and the interpreting child can neither reach the network nor leave plaintext on disk."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 40
MAX_TEXT = 200_000
MIN_OCR_CONFIDENCE = 60.0        # below this a page is kept but marked incomplete: it must be reviewed by hand
OCR_MAX_PAGES = 10
_TEXT_POOR = 20                  # a page with fewer characters than this is treated as a scan
_IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
MAX_FORM_FIELDS = 50
MAX_FORM_VALUE = 200
OCR_LANGUAGES = "eng"            # the only recognition language installed and tested; other scripts need review
_TABLE_ROWS = 3                  # layout lines with two or more wide column gaps before a page counts as a table
_RIGHT_TO_LEFT = ("R", "AL")     # Unicode bidirectional classes whose reading order extraction does not verify


def kind_of(data: bytes) -> str:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(_IMAGE_MAGIC):
        return "image"
    raise ValueError("Provide a PDF, PNG or JPEG file.")


def scripts_of(text: str) -> list[str]:
    """Writing systems present in a page, by Unicode character name; digits and punctuation count for none."""
    found: set[str] = set()
    for char in text:
        if char.isalpha():
            name = unicodedata.name(char, "UNKNOWN").split(" ")[0].lower()
            found.add("cjk" if name in ("cjk", "hiragana", "katakana", "hangul") else name)
    return sorted(found)


def form_lines(widgets: list[Any]) -> list[str]:
    """Labeled lines for filled form fields; bounded and free of control characters."""
    lines: list[str] = []
    for widget in widgets:
        if widget.get("/Subtype") != "/Widget" or widget.get("/V") in (None, ""):
            continue
        name, value = (("".join(c for c in str(widget.get(key, "")) if c.isprintable())) for key in ("/T", "/V"))
        if name and value:
            lines.append(f"Form field {name[:MAX_FORM_VALUE]}: {value[:MAX_FORM_VALUE]}")
        if len(lines) == MAX_FORM_FIELDS:
            break
    return lines


def _is_table(layout: str) -> bool:
    return sum(len(re.findall(r"\S {2,}\S", line)) >= 1 for line in layout.splitlines()) >= _TABLE_ROWS


def merge_ocr(pages: list[str], recognized: dict[int, Any],
              structure: list[str] | None = None) -> tuple[list[str], dict[str, Any]]:
    """Fold OCR results into page text with their confidence; weak or missing OCR keeps the page incomplete.

    Right-to-left pages stay ``unknown``: extraction does not verify their reading order. Text recognized by the
    English-only OCR engine that contains another writing system is incomplete."""
    merged, confidence = list(pages), {}
    methods = ["text" if page.strip() else "unknown" for page in pages]
    for index, result in recognized.items():
        if result.text.strip():
            merged[index] = f"[OCR, confidence {result.confidence:.0f}%]\n{result.text}"
            confidence[str(index)] = result.confidence
            methods[index] = "ocr"
    scripts = [scripts_of(page) for page in merged]
    for index, page in enumerate(merged):
        if methods[index] == "text" and any(unicodedata.bidirectional(c) in _RIGHT_TO_LEFT for c in page):
            methods[index] = "unknown"
    foreign_ocr = any(set(scripts[int(i)]) - {"latin"} for i in confidence)
    complete = (all(p.strip() for p in merged) and all(c >= MIN_OCR_CONFIDENCE for c in confidence.values())
                and not foreign_ocr)
    return merged, {"ocr_pages": sorted(int(i) for i in confidence), "ocr_confidence": confidence,
                    "page_methods": methods, "page_scripts": scripts, "ocr_languages": OCR_LANGUAGES,
                    "page_structure": structure or ["text"] * len(merged), "complete": complete}


def _refuse_socket(event: str, args: tuple[Any, ...]) -> None:
    if event.startswith("socket.") and event != "socket.__new__":
        raise PermissionError("document interpretation has no network access")


def _deny_network() -> None:
    """Interpreting untrusted bytes needs no network: any socket operation in this process is refused."""
    sys.addaudithook(_refuse_socket)


def extract_pdf(data: bytes, *, runner: Callable[..., Any] = subprocess.run, mode: str = "pdf") -> dict[str, Any]:
    if kind_of(data) != mode or len(data) > MAX_BYTES:
        raise ValueError("Provide a PDF, PNG or JPEG smaller than 8 MiB.")
    environment = {k: v for k, v in os.environ.items() if k in (
        "SYSTEMROOT", "WINDIR", "LANG", "TMPDIR", "TEMP", "TMP", "PYTHONHOME",
        "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH")}
    environment["ARTHA_NO_REEXEC"] = "1"
    try:
        process = runner(
            [sys.executable, str(Path(__file__).resolve()), mode],
            input=data,
            capture_output=True,
            timeout=20 + 20 * OCR_MAX_PAGES,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("PDF interpretation exceeded its limit; review the document manually.") from exc
    if process.returncode or len(process.stdout) > MAX_TEXT * 8:
        raise ValueError("PDF could not be interpreted safely; review the document manually.")
    return dict(json.loads(process.stdout))


def ingest(path: Path, subject: str, ledger: Any, viewer: str, now: datetime) -> str:
    from catchup_core.backup import atomic_bytes  # noqa: PLC0415
    from catchup_core.ids import content_hmac  # noqa: PLC0415
    from catchup_core.policy import authorize_source  # noqa: PLC0415

    authorize_source(ledger, [subject], ledger._consent_subjects, "documents:manual", viewer)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Provide a PDF, PNG or JPEG smaller than 8 MiB.")
    data = path.read_bytes()
    extracted = extract_pdf(data) if kind_of(data) == "pdf" else extract_pdf(data, mode="image")
    identity = "doc_" + content_hmac(ledger._id_key, subject + "|" + base64.b64encode(data).decode())[:24]
    if ledger._cipher is None:
        raise ValueError("Document ingestion requires encryption.")
    payload = {
        "schema": "artha.document.v1",
        "subject": subject,
        "observed_at": now.isoformat(),
        "original": base64.b64encode(data).decode(),
        **extracted,
    }
    target = ledger._path.parent / "documents" / f"{identity}.ae1"
    if not target.exists():
        atomic_bytes(target, ledger._cipher.encrypt(json.dumps(payload).encode(), aad=identity.encode())[0])
    ledger.record_capability("RA-21", identity, succeeded=bool(extracted["complete"]))
    return identity


def fetch_documents(ledger: Any, viewer: str) -> Any:
    from catchup_core.coordinator import ConnectorFetch, ConnectorRecord  # noqa: PLC0415
    from catchup_core.policy import authorize_source  # noqa: PLC0415

    records = []
    for path in sorted((ledger._path.parent / "documents").glob("doc_*.ae1")):
        payload = json.loads(ledger._cipher.decrypt(path.read_bytes(), aad=path.stem.encode()))
        authorize_source(ledger, [payload["subject"]], ledger._consent_subjects, "documents:manual", viewer)
        for number, text in enumerate(payload["pages"], 1):
            upstream = f"{path.stem}:p{number}"
            if ledger.is_tombstoned("documents", "manual", upstream):
                continue
            methods = payload.get("page_methods", [])
            method = methods[number - 1] if isinstance(methods, list) and number <= len(methods) else "unknown"
            if method not in ("text", "ocr", "unknown"):
                method = "unknown"
            records.append(
                ConnectorRecord(
                    upstream,
                    f"Subject: {payload['subject']}\nPage {number}\n{text}",
                    occurred_at=payload.get("occurred_at"),
                    incomplete=not payload["complete"],
                    document_method=method,
                )
            )
    return ConnectorFetch("documents", "manual", tuple(records), required=False)


def _worker() -> None:
    from io import BytesIO  # noqa: PLC0415

    from pypdf import PdfReader  # noqa: PLC0415

    from catchup_core import ocr  # noqa: PLC0415

    mode = sys.argv[1] if len(sys.argv) > 1 else "pdf"
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("document too large")
    if mode == "image":
        pages, meta = merge_ocr([""], {0: ocr.ocr_image(raw)})
        print(json.dumps({"pages": pages, **meta}))
        return
    reader = PdfReader(BytesIO(raw), strict=True)
    if reader.is_encrypted or not 0 < len(reader.pages) <= MAX_PAGES:
        raise ValueError("Encrypted or oversized PDF needs manual review")
    pages = []
    structure = []
    recognized: dict[int, Any] = {}
    ocr_attempted_pages = 0
    length = 0
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        kind = "text"
        if text.strip():
            layout = page.extract_text(extraction_mode="layout") or ""
            if _is_table(layout):
                text, kind = layout, "table"
        try:
            fields = form_lines([annotation.get_object() for annotation in page.get("/Annots", [])])
        except Exception:  # unreadable annotations leave the page text as extracted
            fields = []
        if fields:
            text = "\n".join([text, *fields]) if text.strip() else "\n".join(fields)
            kind = "form"
        if len(text.strip()) < _TEXT_POOR and ocr_attempted_pages < OCR_MAX_PAGES:
            ocr_attempted_pages += 1
            try:
                images = [image.data for image in list(page.images)[:3]]
            except Exception:  # an unreadable embedded image leaves the page for manual review
                images = []
            results = [ocr.ocr_image(data) for data in images]
            best = max(results, key=lambda r: (len(r.text), r.confidence), default=None)
            if best and best.text.strip():
                recognized[index] = best
        length += len(text)
        if length > MAX_TEXT:
            raise ValueError("PDF text exceeds review limit")
        pages.append(text)
        structure.append(kind)
    pages, meta = merge_ocr(pages, recognized, structure)
    print(json.dumps({"pages": pages, **meta}))


if __name__ == "__main__":
    # Run as a script, sys.path[0] is this package directory, whose module names would shadow the standard library.
    sys.path[0] = str(Path(__file__).resolve().parents[1])
    _deny_network()               # an audit hook cannot be removed, so only the child process script installs it
    _worker()
