"""Bounded text-PDF ingestion. Original bytes and page text stay encrypted locally."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 40
MAX_TEXT = 200_000


def extract_pdf(data: bytes, *, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if not data.startswith(b"%PDF-") or len(data) > MAX_BYTES:
        raise ValueError("Provide a PDF smaller than 8 MiB.")
    environment = {k: v for k, v in os.environ.items() if k in (
        "SYSTEMROOT", "WINDIR", "LANG", "TMPDIR", "TEMP", "TMP", "PYTHONHOME",
        "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH")}
    try:
        process = runner(
            [sys.executable, str(Path(__file__).resolve())],
            input=data,
            capture_output=True,
            timeout=20,
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
        raise ValueError("Provide a PDF smaller than 8 MiB.")
    data = path.read_bytes()
    extracted = extract_pdf(data)
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
            records.append(
                ConnectorRecord(
                    upstream,
                    f"Subject: {payload['subject']}\nPage {number}\n{text}",
                    occurred_at=payload["observed_at"],
                    incomplete=not payload["complete"],
                )
            )
    return ConnectorFetch("documents", "manual", tuple(records), required=False)


def _worker() -> None:
    from io import BytesIO  # noqa: PLC0415

    from pypdf import PdfReader  # noqa: PLC0415

    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("PDF too large")
    reader = PdfReader(BytesIO(raw), strict=True)
    if reader.is_encrypted or not 0 < len(reader.pages) <= MAX_PAGES:
        raise ValueError("Encrypted or oversized PDF needs manual review")
    pages = []
    length = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        length += len(text)
        if length > MAX_TEXT:
            raise ValueError("PDF text exceeds review limit")
        pages.append(text)
    print(json.dumps({"pages": pages, "complete": all(p.strip() for p in pages)}))


if __name__ == "__main__":
    _worker()
