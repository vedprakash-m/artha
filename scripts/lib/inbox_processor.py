#!/usr/bin/env python3
"""scripts/lib/inbox_processor.py — Artha inbox drop-folder processor.

8-step processing pipeline:
  1. File detection (glob inbox/**/*.{md,txt,eml})
  2. Filename-level PII guard scan
  3. Content hash dedup (SHA256; state/connectors/inbox_notes_state.yaml)
  4. Format detection (markdown, plaintext, email)
  5. Text extraction
  5.5. Content-level PII guard scan  ← R21/HIGH — MUST run BEFORE add_episode()
  6. Domain routing (subfolder hint + keyword match)
  7. Graph upsert: add_episode() → upsert_entity() with source_episode_id
  8. Archive to inbox/_processed/{timestamp}_{filename}

Ref: specs/kb-graph.md §10.2–§10.8
"""
from __future__ import annotations

import email as _email_module
import hashlib
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_ROOT_DIR = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB hard cap
SUPPORTED_EXTENSIONS = {".txt", ".md", ".eml"}

_INBOX_STATE_FILE = _ROOT_DIR / "state" / "connectors" / "inbox_notes_state.yaml"
_INBOX_NOTES_DIR  = _ROOT_DIR / "state" / "connectors" / "inbox_notes"

# Subfolder → domain hint mapping (§10.6 tier 1)
_SUBFOLDER_DOMAIN_MAP: dict[str, str] = {
    "work":     "work",
    "personal": "personal",
    "finance":  "finance",
    "health":   "health",
    "learning": "learning",
    "travel":   "travel",
    "home":     "home",
    "kids":     "kids",
}

# Filename keyword → domain (§10.6 tier 2, simplified)
_FILENAME_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "work":     ["work", "meeting", "sprint", "team", "project", "ado", "azure", "standup"],
    "finance":  ["finance", "budget", "tax", "invoice", "expense", "bill", "receipt"],
    "health":   ["health", "medical", "doctor", "appointment", "prescription", "lab"],
    "learning": ["course", "book", "study", "learn", "class", "lecture", "notes"],
}


# ---------------------------------------------------------------------------
# State persistence helpers
# ---------------------------------------------------------------------------

def _load_inbox_state() -> dict:
    """Load inbox dedup state from YAML. Returns empty dict on missing/invalid file."""
    try:
        import yaml
        if _INBOX_STATE_FILE.exists():
            with open(_INBOX_STATE_FILE, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception as exc:
        _log.warning("Failed to load inbox state: %s", exc)
    return {}


def _save_inbox_state_atomic(state: dict) -> None:
    """Persist inbox state to YAML atomically (.yaml.tmp → rename)."""
    import yaml
    _INBOX_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _INBOX_STATE_FILE.with_suffix(".yaml.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(state, f, default_flow_style=False, allow_unicode=True)
        tmp.replace(_INBOX_STATE_FILE)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# Domain routing (§10.6)
# ---------------------------------------------------------------------------

def _route_domain(file_path: Path) -> str:
    """Route a file to a domain using subfolder hint, then filename keyword fallback.

    Priority (§10.6):
    1. Subfolder name → _SUBFOLDER_DOMAIN_MAP
    2. Filename stem keyword match → _FILENAME_DOMAIN_KEYWORDS
    3. Fallback: "personal"
    """
    # Tier 1: Check each path component against subfolder map
    for part in file_path.parts:
        if part.lower() in _SUBFOLDER_DOMAIN_MAP:
            return _SUBFOLDER_DOMAIN_MAP[part.lower()]

    # Tier 2: Filename keyword match
    stem = file_path.stem.lower()
    for domain, keywords in _FILENAME_DOMAIN_KEYWORDS.items():
        if any(kw in stem for kw in keywords):
            return domain

    return "personal"


# ---------------------------------------------------------------------------
# Text extraction (§10.5)
# ---------------------------------------------------------------------------

def _extract_text(file_path: Path, content_bytes: bytes) -> tuple[str, str]:
    """Extract text from file bytes. Returns (text, format_type).

    format_type is one of: "markdown", "plaintext", "eml", "unknown".
    """
    ext = file_path.suffix.lower()

    if ext == ".md":
        return content_bytes.decode("utf-8", errors="replace"), "markdown"

    if ext == ".txt":
        return content_bytes.decode("utf-8", errors="replace"), "plaintext"

    if ext == ".eml":
        return _extract_eml(content_bytes)

    return "", "unknown"


def _extract_eml(content_bytes: bytes) -> tuple[str, str]:
    """Parse .eml file; extract subject + body as pseudo-markdown."""
    try:
        msg = _email_module.message_from_bytes(content_bytes)
    except Exception as exc:
        _log.warning("Failed to parse .eml: %s", exc)
        return "", "eml"

    subject  = msg.get("Subject", "(no subject)")
    from_hdr = msg.get("From", "")
    to_hdr   = msg.get("To", "")
    date_hdr = msg.get("Date", "")

    # Walk parts for plain text body
    body_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                charset = part.get_content_charset("utf-8") or "utf-8"
                raw = part.get_payload(decode=True)
                if raw:
                    try:
                        body_parts.append(raw.decode(charset, errors="replace"))
                    except Exception:
                        pass
    else:
        charset = msg.get_content_charset("utf-8") or "utf-8"
        raw = msg.get_payload(decode=True)
        if raw:
            try:
                body_parts.append(raw.decode(charset, errors="replace"))
            except Exception:
                pass

    body = "\n".join(body_parts).strip()

    lines = [
        f"# {subject}",
        "",
        f"**From:** {from_hdr}",
        f"**To:** {to_hdr}",
        f"**Date:** {date_hdr}",
        "",
        "## Body",
        "",
        body,
    ]
    return "\n".join(lines), "eml"


# ---------------------------------------------------------------------------
# InboxProcessor
# ---------------------------------------------------------------------------

class InboxProcessor:
    """8-step inbox drop-folder processor.

    Args:
        artha_dir: Root Artha directory (parent of inbox/).
        kg: KnowledgeGraph instance (or NullKnowledgeGraph for dry-run).
        dry_run: If True, process and log but do not write to KB or archive files.
    """

    def __init__(self, artha_dir: Path, kg: object, *, dry_run: bool = False) -> None:
        self._artha_dir = Path(artha_dir)
        self._inbox_dir = self._artha_dir / "inbox"
        self._kg = kg
        self._dry_run = dry_run
        self._state: dict = {}
        self._stats: dict[str, int] = {
            "processed":    0,
            "skipped_pii":  0,
            "skipped_dup":  0,
            "skipped_size": 0,
            "failed":       0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> Iterator[Path]:
        """Yield all pending inbox files (no side effects)."""
        if not self._inbox_dir.exists():
            return
        for ext in SUPPORTED_EXTENSIONS:
            yield from (
                p for p in self._inbox_dir.rglob(f"*{ext}")
                if "_processed"     not in p.parts
                and "_unclassified" not in p.parts
            )

    def process_all(self) -> dict[str, int]:
        """Process all pending inbox files. Returns stats dict."""
        self._state = _load_inbox_state()
        for file_path in self.scan():
            try:
                self._process_one(file_path)
            except Exception as exc:
                _log.error("Failed to process %s: %s", file_path, exc)
                self._stats["failed"] += 1
        if not self._dry_run:
            _save_inbox_state_atomic(self._state)
        return dict(self._stats)

    def process_file(self, file_path: Path) -> bool:
        """Process a single file. Returns True on success."""
        self._state = _load_inbox_state()
        ok = False
        try:
            ok = self._process_one(file_path)
        except Exception as exc:
            _log.error("Failed to process %s: %s", file_path, exc)
        if not self._dry_run:
            _save_inbox_state_atomic(self._state)
        return ok

    def stats(self) -> dict:
        """Return current processing stats including pending queue count."""
        pending = sum(1 for _ in self.scan())
        return {"pending": pending, **self._stats}

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _process_one(self, file_path: Path) -> bool:
        """Run the 8-step pipeline for a single file. Returns True on success."""
        _log.debug("Processing: %s", file_path)

        # Step 1: File detection — size check
        try:
            size = file_path.stat().st_size
        except OSError as exc:
            _log.warning("Cannot stat %s: %s", file_path, exc)
            return False

        if size > MAX_FILE_SIZE:
            _log.warning("Skipping %s: size %d bytes exceeds 2MB limit", file_path, size)
            self._stats["skipped_size"] += 1
            return False

        # Step 2: Filename-level PII scan
        import pii_guard
        fname_pii_found, _ = pii_guard.scan(file_path.name)
        if fname_pii_found:
            _log.warning("Skipping %s: PII detected in filename", file_path.name)
            self._stats["skipped_pii"] += 1
            return False

        # Read file content
        content_bytes = file_path.read_bytes()

        # Step 3: Content hash dedup
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        ingested_files = self._state.get("ingested_files", {})
        if content_hash in ingested_files:
            _log.debug(
                "Skipping %s: already ingested (hash %s…)", file_path.name, content_hash[:12]
            )
            self._stats["skipped_dup"] += 1
            return False

        # Step 4: Format detection + text extraction
        content_text, fmt = _extract_text(file_path, content_bytes)
        if not content_text.strip():
            _log.warning("Skipping %s: empty or unextractable content (fmt=%s)", file_path.name, fmt)
            return False

        # Step 5.5: Content-level PII scan — MUST run BEFORE add_episode() (R21/HIGH)
        # This is the critical gate: episodes.raw_content must be PII-safe at write time.
        content_pii_found, pii_types = pii_guard.scan(content_text)
        if content_pii_found:
            _log.warning(
                "Skipping %s: PII detected in content (%s) — file left in inbox for review",
                file_path.name,
                ", ".join(pii_types.keys()),
            )
            self._stats["skipped_pii"] += 1
            return False

        # Step 6: Domain routing
        domain = _route_domain(file_path)

        if self._dry_run:
            _log.info(
                "[dry-run] Would ingest: %s  domain=%s  fmt=%s  size=%d",
                file_path.name, domain, fmt, size,
            )
            self._stats["processed"] += 1
            return True

        # Step 7: Graph upsert
        episode_key = f"inbox:{content_hash[:24]}"
        episode_id  = self._kg.add_episode(
            episode_key,
            "inbox",
            raw_content=content_text,
        )

        from lib.document_extractor import DocumentExtractor, write_markdown_stub

        ex = DocumentExtractor.from_text(
            content_text,
            source_ref=str(file_path),
            domain=domain,
            episode_key=episode_key,
        )
        ex.run()

        for entity in ex.entities:
            self._kg.upsert_entity(
                entity,
                source=str(file_path),
                confidence=entity.get("confidence", 0.60),
                source_episode_id=episode_id,
            )

        # Write §4.13 markdown stub
        _INBOX_NOTES_DIR.mkdir(parents=True, exist_ok=True)
        safe_stem = re.sub(r"[^\w\-]", "_", file_path.stem)[:60]
        stub_path = _INBOX_NOTES_DIR / f"{safe_stem}-{content_hash[:12]}.md"
        write_markdown_stub(
            stub_path,
            title=file_path.stem,
            source_ref=str(file_path),
            source_type="inbox",
            content_hash=content_hash,
            domain=domain,
            extracted_text=content_text[:2000],
            entities=ex.entities,
            script_name="inbox_processor",
        )

        # Update dedup state
        if "ingested_files" not in self._state:
            self._state["ingested_files"] = {}
        self._state["ingested_files"][content_hash] = {
            "filename":    file_path.name,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "domain":      domain,
            "format":      fmt,
        }

        # Step 8: Archive
        self._archive(file_path)

        self._stats["processed"] += 1
        _log.info(
            "Ingested %s → %s  (domain=%s, fmt=%s, entities=%d)",
            file_path.name, stub_path.name, domain, fmt, len(ex.entities),
        )
        return True

    def _archive(self, file_path: Path) -> None:
        """Move processed file to inbox/_processed/{timestamp}_{filename}."""
        processed_dir = self._inbox_dir / "_processed"
        processed_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = processed_dir / f"{ts}_{file_path.name}"
        # Avoid collision if two files share a timestamp
        counter = 0
        while dest.exists():
            counter += 1
            dest = processed_dir / f"{ts}_{counter}_{file_path.name}"
        shutil.move(str(file_path), str(dest))
        _log.debug("Archived %s → %s", file_path.name, dest.name)
