"""scripts/lib/briefing_archive.py — Canonical briefing archive helper.

Single source of truth for writing briefings to briefings/YYYY-MM-DD.md
across all surfaces: Telegram, VS Code, Gemini CLI, Claude Code, Gmail, MCP.

Public API:
    save(text, *, source, runtime, subject, session_id, briefing_format, model_version) -> dict

Idempotency: SHA-256 hash of *normalized* text is checked before writing.
Normalization: strip trailing whitespace per line, normalize line endings to \\n,
mask the `archived:` timestamp field.

Locking: fcntl.flock (LOCK_EX) on POSIX; .lock sentinel file on Windows.

Observability: failures log to state/audit.md and increment briefing_archive_failed
counter in state/health-check.md. Returns JSON-serializable dict in all paths.

Security:
- InjectionDetector binary gate: any signal → refuse write, return status "failed"
- pii_guard.scan(): log warning, do not block (defense-in-depth)

Self-cleanup: when source="vscode", deletes tmp/briefing_draft.md on success.

Ref: specs/brief.md §4.1
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_BRIEFINGS_DIR = _ARTHA_DIR / "briefings"
_STATE_DIR = _ARTHA_DIR / "state"
_TMP_DIR = _ARTHA_DIR / "tmp"
_AUDIT_LOG = _STATE_DIR / "audit.md"
_HEALTH_CHECK = _STATE_DIR / "health-check.md"
_DRAFT_PATH = _TMP_DIR / "briefing_draft.md"

# ---------------------------------------------------------------------------
# Text normalization for idempotency hashing
# ---------------------------------------------------------------------------

_ARCHIVED_LINE_RE = re.compile(r"^(archived:\s*).*$", re.MULTILINE)


def _normalize_for_hash(text: str) -> str:
    """Normalize text before hashing for dedup.

    - Strip trailing whitespace per line
    - Normalize line endings to \\n
    - Mask `archived:` timestamp field (changes each run)
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    normalized = "\n".join(lines)
    normalized = _ARCHIVED_LINE_RE.sub(r"\1<masked>", normalized)
    return normalized


def _content_hash(text: str) -> str:
    """Return sha256:<hex> of normalized text."""
    normalized = _normalize_for_hash(text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# Hash extraction from existing file
# ---------------------------------------------------------------------------

def _extract_hashes_from_file(path: Path) -> set[str]:
    """Extract all content_hash values from an existing briefings file."""
    hashes: set[str] = set()
    if not path.exists():
        return hashes
    try:
        content = path.read_text(encoding="utf-8")
        for m in re.finditer(r"^content_hash:\s*(sha256:[0-9a-f]+)", content, re.MULTILINE):
            hashes.add(m.group(1))
    except OSError:
        pass
    return hashes


# ---------------------------------------------------------------------------
# Observability helpers
# ---------------------------------------------------------------------------

def _write_audit_event(event: str, fields: dict) -> None:
    """Append a structured audit line to state/audit.md (best-effort, non-fatal)."""
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        field_str = " | ".join(f"{k}:{v}" for k, v in fields.items())
        line = f"| {now} | {event} | {field_str} |\n"
        with _AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:  # noqa: BLE001
        pass


def _increment_health_counter(counter_key: str) -> None:
    """Increment a named counter in state/health-check.md (best-effort, non-fatal).

    Searches for a line matching `counter_key: <int>` and increments it.
    If not found, appends the counter at the end.
    """
    try:
        if not _HEALTH_CHECK.exists():
            return
        content = _HEALTH_CHECK.read_text(encoding="utf-8")
        pattern = re.compile(r"^(" + re.escape(counter_key) + r":\s*)(\d+)", re.MULTILINE)
        m = pattern.search(content)
        if m:
            new_val = int(m.group(2)) + 1
            new_content = content[: m.start(2)] + str(new_val) + content[m.end(2):]
            _HEALTH_CHECK.write_text(new_content, encoding="utf-8")
        else:
            # Append as a new counter line
            sep = "\n" if content.endswith("\n") else "\n\n"
            _HEALTH_CHECK.write_text(content + sep + f"{counter_key}: 1\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# POSIX file locking
# ---------------------------------------------------------------------------

class _FileLock:
    """Context manager: exclusive POSIX flock (LOCK_EX) on a sidecar .lock file, or Windows sentinel.

    Uses a sidecar .lock file (not the target itself) to avoid creating or
    truncating the target file as a side-effect of acquiring the lock.
    """

    def __init__(self, path: Path) -> None:
        self._target = path
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._fh = None
        self._use_sentinel = sys.platform == "win32"

    def __enter__(self) -> "_FileLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self._use_sentinel:
            # Windows: spin on a sentinel file with PID
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                try:
                    fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, f"{os.getpid()}\n".encode())
                    os.close(fd)
                    return self
                except FileExistsError:
                    try:
                        if time.time() - self._lock_path.stat().st_mtime > 60:
                            self._lock_path.unlink(missing_ok=True)
                            continue
                    except OSError:
                        pass
                    time.sleep(0.1)
            raise TimeoutError(f"Could not acquire lock on {self._target} within 30 s")
        else:
            import fcntl  # noqa: PLC0415 — POSIX only
            # Lock the sidecar .lock file — NOT the target briefing file.
            # This prevents the lock from creating/truncating the target as a side effect.
            self._fh = open(self._lock_path, "a", encoding="utf-8")  # noqa: SIM115
            fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_: object) -> None:
        if self._use_sentinel:
            try:
                self._lock_path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            if self._fh is not None:
                import fcntl  # noqa: PLC0415
                fcntl.flock(self._fh, fcntl.LOCK_UN)
                self._fh.close()
                self._fh = None
                try:
                    self._lock_path.unlink(missing_ok=True)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Session ID generation (fallback for non-pipeline callers)
# ---------------------------------------------------------------------------

def _generate_session_id_fallback() -> str:
    """Generate a session ID: YYYYMMDD_hex8 (same pattern as pipeline.py)."""
    import uuid  # noqa: PLC0415
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    hex_part = uuid.uuid4().hex[:8]
    return f"{date_part}_{hex_part}"


# ---------------------------------------------------------------------------
# Security checks
# ---------------------------------------------------------------------------

def _run_injection_check(text: str) -> bool:
    """Return True if injection detected. Binary gate: any signal → True.

    Fail-closed: raises RuntimeError if the detector cannot be imported or
    raises an unexpected exception. This ensures injection-check failures
    block the write rather than silently pass potentially injected content.
    """
    try:
        sys.path.insert(0, str(_ARTHA_DIR / "scripts"))
        from lib.injection_detector import InjectionDetector  # noqa: PLC0415
        result = InjectionDetector().scan(text)
        return result.injection_detected
    except ImportError as exc:
        raise RuntimeError(f"Injection detector unavailable: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Injection check failed: {exc}") from exc


def _run_pii_warning(text: str, source: str) -> None:
    """Log a warning (non-blocking) if high-severity PII is detected."""
    try:
        import subprocess  # noqa: PLC0415
        result = subprocess.run(
            [sys.executable, str(_ARTHA_DIR / "scripts" / "pii_guard.py"), "scan"],
            input=text,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            _write_audit_event(
                "briefing_pii_warning",
                {"source": source, "detail": result.stderr[:120].strip()},
            )
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save(
    text: str,
    *,
    source: str,
    runtime: Optional[str] = None,
    subject: Optional[str] = None,
    session_id: Optional[str] = None,
    briefing_format: Optional[str] = None,
    model_version: Optional[str] = None,
) -> dict:
    """Write briefing text to briefings/YYYY-MM-DD.md.

    - Appends (with separator + per-entry frontmatter) if file for today exists.
    - Idempotent: if normalized SHA-256 already in today's file, returns skipped.
    - Thread- and process-safe via exclusive file lock.
    - Returns JSON-serializable dict: {"status": "ok"|"skipped"|"failed", ...}

    Args:
        text:             Full briefing prose (required).
        source:           "telegram" | "vscode" | "email" | "mcp" | "interactive_cli"
        runtime:          LLM client identity: "vscode" | "gemini" | "claude" | "copilot".
                          Stamped by pipeline code from filename — not self-reported by
                          the model. Optional; omitted from frontmatter if None.
        subject:          Optional subject line for frontmatter.
        session_id:       Pipeline session ID; auto-generated if None.
        briefing_format:  "flash" | "standard" | "deep" (optional).
        model_version:    LLM model version string (optional; omitted if None).
    """
    if not text or not text.strip():
        return {"status": "failed", "error": "empty text"}

    # ── 1. Security: injection gate ───────────────────────────────────────
    if _run_injection_check(text):
        _write_audit_event(
            "briefing_injection_refused",
            {"source": source, "bytes": len(text)},
        )
        return {"status": "failed", "error": "injection_detected"}

    # ── 2. Security: PII warning (non-blocking) ───────────────────────────
    _run_pii_warning(text, source)

    # ── 3. Resolve metadata ───────────────────────────────────────────────
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()  # local time — "today" is user-local, not UTC
    today = now_local.strftime("%Y-%m-%d")
    archived_ts = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    if subject is None:
        day_name = now_local.strftime("%a %b %-d") if sys.platform != "win32" else now_local.strftime("%a %b %d").lstrip("0")
        subject = f"Artha Brief · {day_name}"

    if session_id is None:
        session_id = _generate_session_id_fallback()

    content_hash = _content_hash(text)

    # ── 4. Ensure briefings/ dir ──────────────────────────────────────────
    _BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    target = _BRIEFINGS_DIR / f"{today}.md"

    # ── 5. Acquire exclusive lock then read-modify-write ──────────────────
    try:
        with _FileLock(target):
            # Idempotency check inside the lock
            existing_hashes = _extract_hashes_from_file(target)
            if content_hash in existing_hashes:
                return {
                    "status": "skipped",
                    "reason": "duplicate",
                    "path": str(target),
                }

            # Build per-entry frontmatter
            fm_lines = [
                "---",
                f"date: {today}",
                f"source: {source}",
                f"subject: {subject}",
                f"archived: {archived_ts}",
                "sensitivity: standard",
                f"session_id: {session_id}",
            ]
            if runtime is not None:
                fm_lines.append(f"runtime: {runtime}")
            if briefing_format is not None:
                fm_lines.append(f"briefing_format: {briefing_format}")
            if model_version is not None:
                fm_lines.append(f"model_version: {model_version}")
            fm_lines.append(f"content_hash: {content_hash}")
            fm_lines.append("---")
            frontmatter = "\n".join(fm_lines) + "\n\n"

            if target.exists():
                # Append with separator
                entry = f"\n\n---\n\n{frontmatter}{text.rstrip()}\n"
                with open(target, "a", encoding="utf-8") as f:
                    f.write(entry)
            else:
                # Fresh file
                with open(target, "w", encoding="utf-8") as f:
                    f.write(frontmatter)
                    f.write(text.rstrip() + "\n")

            bytes_written = target.stat().st_size
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc)
        _write_audit_event(
            "briefing_archive_failed",
            {"source": source, "error": err_msg[:120]},
        )
        _increment_health_counter("briefing_archive_failed")
        return {"status": "failed", "error": err_msg, "path": str(target)}

    # ── 6. Self-cleanup: delete vscode draft on success ──────────────────
    if source == "vscode":
        try:
            _DRAFT_PATH.unlink(missing_ok=True)
        except OSError:
            pass  # Non-fatal; Step 18a cleanup handles it

    return {
        "status": "ok",
        "path": str(target),
        "bytes_written": bytes_written,
    }


# ---------------------------------------------------------------------------
# Preflight: garbage-collect stale drafts (called by preflight.py)
# ---------------------------------------------------------------------------

def gc_stale_drafts(max_age_seconds: int = 86400) -> int:
    """Delete tmp/briefing_draft.md if older than max_age_seconds (default 24h).

    Returns 1 if deleted, 0 if not present or not stale.
    Called by preflight.py to prevent PII persistence on repeated failures.
    """
    try:
        if not _DRAFT_PATH.exists():
            return 0
        age = time.time() - _DRAFT_PATH.stat().st_mtime
        if age > max_age_seconds:
            _DRAFT_PATH.unlink(missing_ok=True)
            _write_audit_event(
                "briefing_draft_gc",
                {"age_seconds": int(age), "path": str(_DRAFT_PATH)},
            )
            return 1
    except OSError:
        pass
    return 0
