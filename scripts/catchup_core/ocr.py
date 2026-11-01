"""Local-only OCR (re-artha RA-21): Tesseract reads the image from stdin, nothing is written to disk, and on macOS the
process runs in a sandbox that denies all network access.  No image or recognized text leaves this machine."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MAX_IMAGE_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 30
# Interpreter and locale plumbing only, never credentials.
CHILD_ENV = ("LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "HOME", "SYSTEMROOT", "WINDIR", "PATH",
             "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "TESSDATA_PREFIX")
_KNOWN = ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract")
_AUTO: Any = object()


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float          # mean word confidence, 0-100; 0.0 when nothing was recognized
    available: bool            # False when no OCR engine is installed


def find_tesseract() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    return next((p for p in _KNOWN if os.path.isfile(p) and os.access(p, os.X_OK)), None)


def confined(argv: list[str]) -> list[str]:
    """Run ``argv`` without network access where the platform provides a sandbox (macOS ``sandbox-exec``)."""
    sandbox = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
    if sandbox:
        return [sandbox, "-p", "(version 1)(allow default)(deny network*)", *argv]
    return argv


def _parse_tsv(tsv: str) -> tuple[str, float]:
    lines: dict[tuple[str, str, str], list[str]] = {}
    scores: list[float] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
            continue
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if conf < 0:
            continue
        lines.setdefault((cols[2], cols[3], cols[4]), []).append(cols[11].strip())
        scores.append(conf)
    text = "\n".join(" ".join(words) for words in lines.values())
    return text, round(sum(scores) / len(scores), 1) if scores else 0.0


def ocr_image(data: bytes, *, tesseract: str | None = _AUTO, confine: bool = True,
              runner: Callable[..., Any] = subprocess.run, languages: str = "eng") -> OcrResult:
    engine = find_tesseract() if tesseract is _AUTO else tesseract
    if not engine:
        return OcrResult("", 0.0, False)
    if not data or len(data) > MAX_IMAGE_BYTES:
        return OcrResult("", 0.0, True)
    argv = [engine, "stdin", "stdout", "-l", languages, "tsv"]
    env = {k: v for k, v in os.environ.items() if k in CHILD_ENV}
    env["OMP_THREAD_LIMIT"] = "1"
    try:
        proc = runner(confined(argv) if confine else argv, input=data, capture_output=True,
                      timeout=TIMEOUT_SECONDS, check=False, env=env)
    except (OSError, subprocess.SubprocessError):
        return OcrResult("", 0.0, True)
    if proc.returncode or len(proc.stdout) > 2_000_000:
        return OcrResult("", 0.0, True)
    text, confidence = _parse_tsv(proc.stdout.decode("utf-8", "replace"))
    return OcrResult(text, confidence, True)
