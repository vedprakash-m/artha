"""RA-21 UAT: document ingestion (text layer and local OCR).

Commands, in order (``--shadow --json`` on every CLI command):

  ingest <tmp>/uat_notice.pdf --subject S     local-write   one encrypted documents/doc_*.ae1 in local storage
  ingest <tmp>/uat_notice.pdf --subject S     local-write   same document identity; nothing new
  ingest <tmp>/uat_notice.txt --subject S     local-write   refused: only PDF, PNG or JPEG
  ingest <tmp>/uat_large.pdf --subject S      local-write   refused: 8 MiB limit
  ingest <tmp>/uat_notice.pdf --subject uat-nobody
                                              local-write   refused: unresolved person
  health                                      read          RA-21 capability state recorded by the ingest

The input files are synthetic and written to a temporary directory that is deleted afterwards.  Text extraction
runs in a network-denied local subprocess.  Live (no ``--scratch``): the ingest steps run only with
``--allow-local-writes`` (S is the subject of the first visible Fact); the synthetic document then waits for review
at the next Catch-up.  No step uploads anything.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as h  # noqa: E402

TEXT = "UAT SYNTHETIC NOTICE passport renewal due 2027-01-15"
LIMITATIONS = (
    "Local OCR of a scanned image needs tesseract and an image containing text; this script does not generate "
    "one, so OCR is recorded as skipped. Run a real scan by hand on the live host and record the result.",
    "Accuracy floors need a labelled field-review set; the CLI records extraction but cannot score it.",
    "Extracted Facts appear only after the next Catch-up reviews the document; no CLI step shows them sooner.",
)


def setup(runner: h.ScratchRunner) -> None:
    runner.seed(h.seed_school_fact)


def scenario(runner, report: h.Report) -> None:
    fact = h.first_fact(runner, report)
    if fact is None:
        return
    subject = fact["subject"]
    directory = Path(tempfile.mkdtemp(prefix="artha_uat_", dir=None if runner.live else runner.base))
    try:
        _ingest_steps(runner, report, directory, subject)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    report.skip("ingest <scan>.png --subject S", "local OCR extracts page text",
                "tesseract and a scanned test image are not provided by this script", kind=h.LOCAL_WRITE)
    h.run_step(runner, report, ["health", h.SHADOW], "health reports the RA-21 capability state",
               lambda o: o.ok and any(c["id"] == "RA-21" and (runner.live or c["state"] == "last succeeded")
                                      for c in o.data["capabilities"]))


def _ingest_steps(runner, report: h.Report, directory: Path, subject: str) -> None:
    pdf = directory / "uat_notice.pdf"
    pdf.write_bytes(h.text_pdf(TEXT))
    first = h.run_step(runner, report, ["ingest", str(pdf), "--subject", subject, h.SHADOW],
                       "document accepted with a document identity",
                       lambda o: o.ok and o.data["document"].startswith("doc_"), kind=h.LOCAL_WRITE)
    if first is not None and first.ok:
        ident = first.data["document"]
        h.run_step(runner, report, ["ingest", str(pdf), "--subject", subject, h.SHADOW],
                   "re-ingesting the same file returns the same identity",
                   lambda o: o.ok and o.data["document"] == ident, kind=h.LOCAL_WRITE)
        if not runner.live:
            stored = runner.local() / "documents" / f"{ident}.ae1"
            report.check(f"inspect documents/{stored.name}", "stored encrypted; the document text is absent",
                         {"exists": stored.exists()}, stored.exists() and not h.contains(stored, "passport"),
                         kind=h.LIBRARY)
            runner.seed(lambda deps: _next_catchup_view(deps, report, ident))
    text = directory / "uat_notice.txt"
    text.write_text(TEXT, encoding="utf-8")
    h.run_step(runner, report, ["ingest", str(text), "--subject", subject, h.SHADOW],
               "a non-PDF/PNG/JPEG file is refused", lambda o: h.refused(o, "PDF, PNG or JPEG"), kind=h.LOCAL_WRITE)
    large = directory / "uat_large.pdf"
    large.write_bytes(b"%PDF-1.4\n" + b"0" * (8 * 1024 * 1024))
    h.run_step(runner, report, ["ingest", str(large), "--subject", subject, h.SHADOW],
               "a file over 8 MiB is refused", lambda o: h.refused(o, "8 MiB"), kind=h.LOCAL_WRITE)
    h.run_step(runner, report, ["ingest", str(pdf), "--subject", "uat-nobody", h.SHADOW],
               "an unknown person is refused before anything is stored",
               lambda o: h.refused(o, "unresolved person"), kind=h.LOCAL_WRITE)


def _next_catchup_view(deps, report: h.Report, ident: str) -> None:
    from catchup_core.documents import fetch_documents  # noqa: PLC0415
    pages = [r for r in fetch_documents(deps.ledger, deps.viewer).records if r.upstream_id.startswith(ident)]
    report.check("(library) documents.fetch_documents", "the next Catch-up receives the page text, read from the "
                 "text layer, with the subject attached",
                 [{"upstream": p.upstream_id, "method": p.document_method, "incomplete": p.incomplete} for p in pages],
                 len(pages) == 1 and TEXT in pages[0].text and pages[0].document_method == "text"
                 and not pages[0].incomplete, kind=h.LIBRARY)


if __name__ == "__main__":
    sys.exit(h.main("RA-21", "Document ingestion, text and local OCR", scenario, setup=setup,
                    limitations=LIMITATIONS))
