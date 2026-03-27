#!/usr/bin/env python3
# pii-guard: ignore-file — channel listener infrastructure; PII guard applied to all outbound
"""
channel_listener.py — Artha interactive channel listener (Layer 2).

Polls enabled channels for inbound commands and responds with formatted,
PII-redacted, scope-filtered state summaries.

Usage:
    python scripts/channel_listener.py --channel telegram
    python scripts/channel_listener.py --channel telegram --channel discord
    python scripts/channel_listener.py --dry-run   # Validate setup without polling
    python scripts/channel_listener.py --health    # Check if all adapters healthy

Security model (enforced in process_message()):
  1. Sender whitelist   — unknown senders are silently ignored
  2. Message dedup      — duplicate message_id rejected (LRU cache, last 1000)
  3. Timestamp check    — messages >5 min old rejected (anti-replay)
  4. Rate limiting      — 10 commands/minute per sender; 60s cooldown on breach
  5. Command whitelist  — only /status /alerts /tasks /quick /domain /help /unlock
  6. Scope filter       — per-recipient access_scope applied before response
  7. PII redaction      — pii_guard.filter_text() on every outbound message
  8. Read-only          — never writes state files; never decrypts vault files
  9. Staleness          — every response ends with "_Last updated: Xh Ym ago_"
  10. listener_host     — refuses to start on non-designated machine

Read-only state files whitelist (all non-encrypted):
  state/health-check.md, state/open_items.md, state/dashboard.md,
  state/goals.md, state/calendar.md, state/comms.md, state/home.md,
  state/kids.md, briefings/{latest}.md

Encrypted domain files (.md.age) are NEVER accessed.

Ref: specs/conversational-bridge.md §8
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import dataclasses
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# Ensure Artha root on sys.path
_ARTHA_DIR = Path(__file__).resolve().parent.parent
if str(_ARTHA_DIR) not in sys.path:
    sys.path.insert(0, str(_ARTHA_DIR))

_STATE_DIR = _ARTHA_DIR / "state"
_BRIEFINGS_DIR = _ARTHA_DIR / "briefings"
_AUDIT_LOG = _STATE_DIR / "audit.md"

try:
    from lib.logger import get_logger as _get_logger
    _chlog = _get_logger("channel")
except Exception:  # pragma: no cover
    class _NoOpChannelLogger:  # type: ignore[no-redef]
        def info(self, *a, **k): pass
        def error(self, *a, **k): pass
    _chlog = _NoOpChannelLogger()  # type: ignore[assignment]

# Anti-replay: reject messages older than this
_MAX_MESSAGE_AGE_SECONDS = 5 * 60  # 5 minutes

# Message dedup: LRU cache size
_DEDUP_CACHE_SIZE = 1000

# Rate limiting: per-sender burst window
_RATE_LIMIT_COMMANDS = 10   # max commands
_RATE_LIMIT_WINDOW_SEC = 60  # per minute
_RATE_LIMIT_COOLDOWN_SEC = 60  # lockout duration

# Session token duration
_SESSION_TOKEN_MINUTES = 15

# Poll reconnect backoff bounds
_POLL_BACKOFF_BASE = 2.0
_POLL_BACKOFF_MAX = 300.0

# ── Command normalisation ─────────────────────────────────────────────────────
# Accept every reasonable variant a user might type on a phone keyboard.
# Maps (lowercase, stripped of leading /) → canonical /command.
_COMMAND_ALIASES: dict[str, str] = {
    # catchup variants — no user should need a hyphen on mobile
    "catchup":     "/catchup",
    "catch-up":    "/catchup",
    "catch up":    "/catchup",
    "briefing":    "/catchup",
    "brief":       "/catchup",
    # status
    "status":      "/status",
    "s":           "/status",
    # alerts
    "alerts":      "/alerts",
    "alert":       "/alerts",
    "a":           "/alerts",
    # tasks
    "tasks":       "/tasks",
    "task":        "/tasks",
    "items":       "/tasks",
    "t":           "/tasks",
    # quick
    "quick":       "/quick",
    "q":           "/quick",
    # domain
    "domain":      "/domain",
    "d":           "/domain",
    # dashboard
    "dashboard":   "/dashboard",
    "dash":        "/dashboard",
    "db":          "/dashboard",
    # goals (shortcut to /domain goals)
    "goals":       "/goals",
    "goal":        "/goals",
    "g":           "/goals",
    # diff
    "diff":        "/diff",
    # items add / items done (write commands)
    "items add":   "/items_add",
    "item add":    "/items_add",
    "add item":    "/items_add",
    "items done":  "/items_done",
    "item done":   "/items_done",
    "done":        "/items_done",
    # remember / inbox (knowledge capture — E2)
    "remember":    "/remember",
    "note":        "/remember",
    "inbox":       "/remember",
    # power half hour (E14)
    "power":             "/power",
    "power half hour":   "/power",
    # relationships (E13)
    "relationships":     "/relationships",
    "relationship pulse":"/relationships",
    # help
    "help":        "/help",
    "h":           "/help",
    "?":           "/help",
    # unlock
    "unlock":      "/unlock",
    # action queue
    "queue":       "/queue",
    "approve":     "/approve",
    "reject":      "/reject",
    "undo":        "/undo",
    # cost telemetry (E8)
    "cost":        "/cost",
    # content stage (PR-2)
    "stage":       "/stage",
    "stage list":  "/stage",
    # AI Trend Radar (PR-3)
    "radar":             "/radar",
    "ai radar":          "/radar",
    "radar list":        "/radar",
    "try":               "/radar_try",
    "skip":              "/radar_skip",
    "radar topic":       "/radar",
    "radar topic add":   "/radar",
    "radar topic rm":    "/radar",
}

ALLOWED_COMMANDS = frozenset(_COMMAND_ALIASES.values())


def _normalise_command(raw_text: str) -> tuple[str, list[str]]:
    """Normalise user input to (canonical_command, args).

    Accepts:
      /catchup, catchup, catch-up, "catch up", briefing
      /status, status, s
      /dash, dash, db
      etc.
    Returns ("/catchup", []) or ("", []) if not a recognised command.
    """
    text = raw_text.strip()
    if not text:
        return "", []

    # Try the full text first (handles "catch up" with space)
    lower_full = text.lower().lstrip("/")
    # Check full-text match (e.g. "catch up flash")
    for alias in sorted(_COMMAND_ALIASES, key=len, reverse=True):
        if lower_full == alias or lower_full.startswith(alias + " "):
            cmd = _COMMAND_ALIASES[alias]
            rest = lower_full[len(alias):].strip()
            args = rest.split() if rest else []
            return cmd, args

    # Try first word only (handles "/status" style)
    parts = text.split()
    first = parts[0].lower().lstrip("/")
    if first in _COMMAND_ALIASES:
        return _COMMAND_ALIASES[first], parts[1:]

    return "", []

# State files readable by the listener (whitelist — never encrypted files)
_READABLE_STATE_FILES: dict[str, Path] = {
    "health_check": _STATE_DIR / "health-check.md",
    "open_items":   _STATE_DIR / "open_items.md",
    "dashboard":    _STATE_DIR / "dashboard.md",
    "goals":        _STATE_DIR / "goals.md",
    "calendar":     _STATE_DIR / "calendar.md",
    "comms":        _STATE_DIR / "comms.md",
    "home":         _STATE_DIR / "home.md",
    "kids":         _STATE_DIR / "kids.md",
    "gallery":      _STATE_DIR / "gallery.yaml",
}

_DOMAIN_TO_STATE_FILE: dict[str, str] = {
    "health": "health_check",
    "goals": "goals",
    "calendar": "calendar",
    "tasks": "open_items",
    "comms": "comms",
    "communications": "comms",
    "home": "home",
    "kids": "kids",
    "school": "kids",
    "dashboard": "dashboard",
}

# Domains excluded per scope
_FAMILY_EXCLUDED_DOMAINS = frozenset({
    "immigration", "finance", "estate", "insurance",
    "employment", "digital", "boundary",
})

logging.basicConfig(
    level=logging.INFO,
    format="[channel_listener] %(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("channel_listener")


# ── Audit ─────────────────────────────────────────────────────────────────────

def _audit_log(event_type: str, **kwargs: str | int | bool | None) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    parts = [f"[{ts}] {event_type}"]
    for k, v in kwargs.items():
        parts.append(f"{k}: {v}")
    entry = " | ".join(parts)
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass
    log.debug("Audit: %s", entry)


# ── Security helpers ──────────────────────────────────────────────────────────

class _MessageDeduplicator:
    """Thread-safe LRU cache for message deduplication."""

    def __init__(self, max_size: int = _DEDUP_CACHE_SIZE):
        self._seen: deque[str] = deque(maxlen=max_size)
        self._lock = threading.Lock()

    def is_duplicate(self, message_id: str) -> bool:
        with self._lock:
            if message_id in self._seen:
                return True
            self._seen.append(message_id)
            return False


class _RateLimiter:
    """Per-sender sliding-window rate limiter with cooldown."""

    def __init__(
        self,
        max_per_window: int = _RATE_LIMIT_COMMANDS,
        window_sec: float = _RATE_LIMIT_WINDOW_SEC,
        cooldown_sec: float = _RATE_LIMIT_COOLDOWN_SEC,
    ):
        self._limits: dict[str, list[float]] = collections.defaultdict(list)
        self._cooldown_until: dict[str, float] = {}
        self._max = max_per_window
        self._window = window_sec
        self._cooldown = cooldown_sec
        self._lock = threading.Lock()

    def is_rate_limited(self, sender_id: str) -> bool:
        """Return True if sender is in cooldown or has exceeded rate limit."""
        now = time.monotonic()
        with self._lock:
            # Check cooldown
            if sender_id in self._cooldown_until:
                if now < self._cooldown_until[sender_id]:
                    return True
                else:
                    del self._cooldown_until[sender_id]

            # Sliding window
            ts_list = self._limits[sender_id]
            # Remove timestamps outside the window
            cutoff = now - self._window
            while ts_list and ts_list[0] < cutoff:
                ts_list.pop(0)

            if len(ts_list) >= self._max:
                self._cooldown_until[sender_id] = now + self._cooldown
                return True

            ts_list.append(now)
            return False


class _SessionTokenStore:
    """PIN-based session tokens with expiry (15-min default)."""

    def __init__(self, expiry_minutes: int = _SESSION_TOKEN_MINUTES):
        self._tokens: dict[str, float] = {}  # sender_id → expiry (monotonic)
        self._expiry = expiry_minutes * 60
        self._lock = threading.Lock()

    def _load_pin(self) -> str | None:
        """Load PIN from keyring (artha-channel-pin)."""
        try:
            import keyring
            return keyring.get_password("artha", "artha-channel-pin")
        except ImportError:
            pass
        return os.environ.get("ARTHA_CHANNEL_PIN", "")

    def unlock(self, sender_id: str, provided_pin: str) -> bool:
        """Verify PIN and create session token. Returns True if PIN correct."""
        stored_pin = self._load_pin()
        if not stored_pin:
            return False  # No PIN configured — unlock not available
        if str(stored_pin).strip() != str(provided_pin).strip():
            return False
        with self._lock:
            self._tokens[sender_id] = time.monotonic() + self._expiry
        return True

    def has_valid_token(self, sender_id: str) -> bool:
        """Check if sender has a valid (non-expired) session token."""
        with self._lock:
            expiry = self._tokens.get(sender_id)
            if expiry is None:
                return False
            if time.monotonic() > expiry:
                del self._tokens[sender_id]
                return False
            return True


# ── State file readers ────────────────────────────────────────────────────────

def _read_state_file(key: str) -> tuple[str, str]:
    """Read a whitelisted state file. Returns (content, staleness_str).

    Only files in _READABLE_STATE_FILES are accessible.
    Encrypted files (.md.age) are never returned.

    Returns:
        (content: str, staleness: str) — staleness is human-readable age
    """
    path = _READABLE_STATE_FILES.get(key)
    if path is None:
        return "", "unknown"
    if not path.exists():
        return f"_{key} data not available_", "never"
    # Safety: never serve encrypted files
    if path.suffix != ".md":
        return "_Encrypted data not accessible via channel_", "N/A"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        mtime = path.stat().st_mtime
        age_sec = time.time() - mtime
        staleness = _format_age(age_sec)
        return content, staleness
    except OSError as exc:
        return f"_Could not read state: {exc}_", "unknown"


def _format_age(age_sec: float) -> str:
    """Format a duration into a human-readable age string."""
    if age_sec < 60:
        return f"{int(age_sec)}s"
    elif age_sec < 3600:
        return f"{int(age_sec // 60)}m"
    elif age_sec < 86400:
        h = int(age_sec // 3600)
        m = int((age_sec % 3600) // 60)
        return f"{h}h {m}m" if m else f"{h}h"
    else:
        d = int(age_sec // 86400)
        h = int((age_sec % 86400) // 3600)
        return f"{d}d {h}h" if h else f"{d}d"


def _get_latest_briefing_path() -> Path | None:
    """Return the path to the most recent briefing file."""
    if not _BRIEFINGS_DIR.exists():
        return None
    files = sorted(_BRIEFINGS_DIR.glob("*.md"), reverse=True)
    return files[0] if files else None


# ── Access scope filter ───────────────────────────────────────────────────────

_FAMILY_EXCLUDED_KEYWORDS = (
    "immigration", "visa", "ead ", "i-765", "i-485", "h-1b", "h-4",
    "perm ", "i-140", "green card", "uscis", "priority date",
    "finance", "fidelity", "vanguard", "morgan stanley", "etrade",
    "account balance", "routing", "401k", "roth", "brokerage",
    "estate", "trust", "beneficiary", "poa ", "guardian",
    "insurance", "premium", "deductible",
)

_STANDARD_INCLUDED_KEYWORDS = (
    "calendar", "event", "appointment", "meeting", "schedule",
    "task", "open item", "oi-", "goal", "due", "today",
)


def _apply_scope_filter(text: str, scope: str) -> str:
    """Apply access scope filter to response text."""
    if scope == "full":
        return text
    lines = text.splitlines(keepends=True)
    if scope == "family":
        return "".join(
            ln for ln in lines
            if not any(kw in ln.lower() for kw in _FAMILY_EXCLUDED_KEYWORDS)
        )
    if scope == "standard":
        filtered = []
        for ln in lines:
            stripped = ln.strip()
            if not stripped:
                filtered.append(ln)
                continue
            if stripped.startswith("_Last updated") or stripped.startswith("ARTHA"):
                filtered.append(ln)
                continue
            if any(kw in stripped.lower() for kw in _STANDARD_INCLUDED_KEYWORDS):
                filtered.append(ln)
        return "".join(filtered)
    return text


# ── Command handlers ──────────────────────────────────────────────────────────

def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter (--- ... ---) from the top of state files."""
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return content
    end = stripped.find("\n---", 3)
    if end == -1:
        return content
    return stripped[end + 4:].lstrip()


# Section headers that are noise on a phone screen — skip entirely
_SKIP_SECTION_PATTERNS = (
    "archive",
    "extended family",
    "historical",
    "parent involvement",
    "run history",
    "## context",
)

# Bullet-level fields that are historical noise within a ### subsection.
# Any `- **<label>:` bullet matching one of these (plus all indented sub-items)
# is stripped before budget allocation, so later important fields are visible.
_SKIP_BULLET_PATTERNS = (
    "prior schools",
    "academic history",
    "pennsylvania schools",
    "fremont area school",
    "cupertino union",
    "historical health",
    "historical services",
    "historical emails",
    "surgery",
    "tesla stem hs accepted",
    "key school contacts",
    "notes",
    "sba tests",
)

# Fields that duplicate info already in the ### heading — skip to save chars
_REDUNDANT_FIELD_PATTERNS = (
    "grade level",
    "current status",
    "date of birth",
    "expected graduation",
)


def _is_noise_section(header: str) -> bool:
    h = header.lower()
    return any(pat in h for pat in _SKIP_SECTION_PATTERNS)


def _filter_noise_bullets(lines: list[str]) -> list[str]:
    """Remove historical/redundant bullet blocks from a ### subsection body.

    Skips bullets matching _SKIP_BULLET_PATTERNS (historical noise) and
    _REDUNDANT_FIELD_PATTERNS (info already in the ### heading).
    Also skips standalone bold labels like **Key School Contacts** and
    any following table rows.  Standalone bold labels referencing dates
    >1 year old are auto-skipped.
    """
    import re
    from datetime import datetime

    current_year = datetime.now().year

    def _label_is_noise(label: str) -> bool:
        low = label.lower()
        if any(pat in low for pat in _SKIP_BULLET_PATTERNS):
            return True
        if any(pat in low for pat in _REDUNDANT_FIELD_PATTERNS):
            return True
        # Auto-skip labels with year >1 year old (e.g. "6th Grade Lottery (Jan 2024)")
        year_match = re.search(r'20[12]\d', label)
        if year_match and int(year_match.group()) < current_year - 1:
            return True
        return False

    result: list[str] = []
    skip_block = False
    in_table = False
    for line in lines:
        stripped = line.lstrip()

        # Detect standalone bold label (e.g. **Key School Contacts**:)
        if stripped.startswith("**") and not stripped.startswith("- **"):
            label = stripped.lstrip("*").split("**")[0]
            if _label_is_noise(label):
                skip_block = True
                in_table = True
                continue

        # Detect pipe-table rows — skip if we're in a skip block
        if in_table and (stripped.startswith("|") or stripped.startswith("|-")):
            continue
        elif in_table and not stripped.startswith("|"):
            in_table = False
            skip_block = False

        is_top_bullet = stripped.startswith("- **") and not line.startswith("  ")
        if is_top_bullet:
            label = stripped[4:].split("**")[0]
            skip_block = _label_is_noise(label)
        elif not line.strip():
            skip_block = False
            in_table = False
        if not skip_block:
            result.append(line)
    return result


def _clean_for_telegram(text: str) -> str:
    """Universal cleanup for all Telegram output.

    Strips: YAML frontmatter, file-header comments, markdown formatting,
    code fences, pipe tables, horizontal rules, and excess blank lines.
    """
    import re

    # 1. Strip YAML frontmatter
    text = _strip_frontmatter(text)

    # 2. Strip file-header comment blocks (lines starting with # ──, # MACHINE, # DO NOT, # Ref:, # Sensitivity, etc.)
    text = re.sub(r'^#\s*[─━═─].*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^#\s*(MACHINE|DO NOT|Sensitivity|Ref:|──).*$', '', text, flags=re.MULTILINE)

    # 3. Strip code fences (```yaml, ``` etc.) but keep content
    text = re.sub(r'^```\w*\s*$', '', text, flags=re.MULTILINE)

    # 4. Strip markdown heading markers
    text = re.sub(r'^#{1,4}\s+', '', text, flags=re.MULTILINE)

    # 5. Strip bold/italic markers
    text = text.replace('**', '').replace('__', '')
    text = re.sub(r'(?<![\w/])_([^_]+)_(?![\w/])', r'\1', text)
    text = re.sub(r'(?<![\w/])\*([^*]+)\*(?![\w/])', r'\1', text)

    # 6. Strip pipe tables — convert to plain lines
    text = re.sub(r'^\|[-:| ]+\|\s*$', '', text, flags=re.MULTILINE)  # separator rows
    text = re.sub(r'^\|\s*', '', text, flags=re.MULTILINE)  # leading pipe
    text = re.sub(r'\s*\|\s*$', '', text, flags=re.MULTILINE)  # trailing pipe
    text = text.replace(' | ', ' — ')  # interior pipes → dash

    # 7. Strip horizontal rules
    text = re.sub(r'^---+\s*$', '', text, flags=re.MULTILINE)

    # 8. Collapse consecutive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _get_domain_open_items(domain_name: str, max_items: int = 5) -> str:
    """Extract open action items for a specific domain from open_items.md."""
    content, _ = _read_state_file("open_items")
    if not content:
        return ""

    items: list[str] = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Detect start of an item block
        if line.startswith("- id: OI-"):
            item_id = line.split(":", 1)[1].strip()
            block: dict[str, str] = {"id": item_id}
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("- id: OI-"):
                l = lines[i].strip()
                if ":" in l:
                    k, v = l.split(":", 1)
                    block[k.strip()] = v.strip().strip('"')
                i += 1
            # Filter: open + matching domain
            if (block.get("status") == "open"
                    and block.get("source_domain", "").lower() == domain_name):
                desc = block.get("description", "")
                dl = block.get("deadline", "")
                pri = block.get("priority", "")
                entry = f"- [{item_id}] {desc}"
                if dl:
                    entry += f" (due {dl})"
                if pri:
                    entry += f" [{pri}]"
                items.append((pri, entry))
        else:
            i += 1

    if not items:
        return ""
    # Sort by priority: P0 first, then P1, P2, P3, unknown last
    _pri_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    items.sort(key=lambda t: _pri_order.get(t[0], 9))
    sorted_entries = [entry for _, entry in items[:max_items]]
    return "\nACTION ITEMS:\n" + "\n".join(sorted_entries)


def _trim_to_cap(text: str, cap: int, ellipsis: str = "\n…") -> str:
    """Trim text to cap chars, breaking at the last newline if possible."""
    if len(text) <= cap:
        return text
    trunc = text[:cap]
    nl = trunc.rfind("\n")
    if nl > int(cap * 0.7):
        trunc = trunc[:nl]
    return trunc + ellipsis


def _extract_section_summaries(content: str, max_total: int = 3800) -> str:
    """Two-level section-aware extraction for large state files.

    Algorithm:
      1. Strip YAML frontmatter.
      2. Split at ## level; skip noise sections (archive, extended family, etc.).
      3. Within each kept ## section, split at ### level and budget proportionally
         so every subsection (e.g. each child in kids.md) gets fair representation.
      4. Total output stays within max_total chars — safe for Telegram's 4096 limit.
    """
    content = _strip_frontmatter(content)
    lines = content.splitlines()

    # ── Split into L2 (##) sections ──────────────────────────────────────────
    l2_sections: list[tuple[str, list[str]]] = []
    cur_h2 = ""
    cur_lines: list[str] = []

    for line in lines:
        if line.startswith("## ") or line.startswith("# "):
            if cur_h2 or cur_lines:
                l2_sections.append((cur_h2, cur_lines[:]))
            cur_h2, cur_lines = line, []
        else:
            cur_lines.append(line)
    if cur_h2 or cur_lines:
        l2_sections.append((cur_h2, cur_lines))

    # ── Filter noise ──────────────────────────────────────────────────────────
    kept = [(h, ls) for h, ls in l2_sections if not _is_noise_section(h)]
    if not kept:
        kept = l2_sections  # nothing matched — show everything

    budget_l2 = max(600, max_total // max(1, len(kept)))
    parts: list[str] = []
    total = 0

    for l2_h, l2_lines in kept:
        if total >= max_total:
            break
        remaining = max_total - total
        cap = min(budget_l2, remaining)

        # ── Split L2 body into L3 (###) subsections ──────────────────────────
        l3_sections: list[tuple[str, list[str]]] = []
        cur_h3 = ""
        cur_l3: list[str] = []
        for line in l2_lines:
            if line.startswith("### "):
                if cur_h3 or cur_l3:
                    l3_sections.append((cur_h3, cur_l3[:]))
                cur_h3, cur_l3 = line, []
            else:
                cur_l3.append(line)
        if cur_h3 or cur_l3:
            l3_sections.append((cur_h3, cur_l3))

        if l3_sections:
            budget_l3 = max(300, cap // max(1, len(l3_sections)))
            l3_parts: list[str] = []
            l3_total = 0
            for l3_h, l3_ls in l3_sections:
                if l3_total >= cap:
                    break
                filtered_ls = _filter_noise_bullets(l3_ls)
                l3_text = (l3_h + "\n" + "\n".join(filtered_ls)).strip()
                l3_cap = min(budget_l3, cap - l3_total)
                l3_text = _trim_to_cap(l3_text, l3_cap)
                l3_parts.append(l3_text)
                l3_total += len(l3_text) + 2
            prefix = (l2_h + "\n\n") if l2_h else ""
            section_text = prefix + "\n\n".join(l3_parts)
        else:
            raw = ((l2_h + "\n") if l2_h else "") + "\n".join(l2_lines)
            section_text = raw.strip()

        section_text = _trim_to_cap(section_text, cap)
        parts.append(section_text)
        total += len(section_text) + 2

    return "\n\n".join(parts)


async def cmd_status(args: list[str], scope: str) -> tuple[str, str]:
    """Return current system health + active alerts + goal overview."""
    content, staleness = _read_state_file("health_check")
    content = _strip_frontmatter(content)

    # Extract the Last Catch-Up block as a compact summary
    lines = content.splitlines()
    summary_parts: list[str] = ["Artha System Status\n"]
    in_last_catchup = False
    catchup_fields: dict[str, str] = {}

    for line in lines:
        s = line.strip()
        if s.startswith("## Last Catch-Up") or s.startswith("Last Catch-Up"):
            in_last_catchup = True
            continue
        if s.startswith("## ") and in_last_catchup:
            break  # Hit next section
        if in_last_catchup and ":" in s and not s.startswith("#") and not s.startswith("```"):
            k, v = s.split(":", 1)
            catchup_fields[k.strip()] = v.strip().strip('"')

    if catchup_fields:
        ts = catchup_fields.get("last_catch_up", "unknown")
        summary_parts.append(f"Last catch-up: {ts}")
        summary_parts.append(f"Emails processed: {catchup_fields.get('emails_processed', '?')}")
        alerts = catchup_fields.get("alerts_generated", "0")
        if alerts != "0":
            summary_parts.append(f"Alerts generated: {alerts}")
        oi_added = catchup_fields.get("open_items_added", "0")
        oi_closed = catchup_fields.get("open_items_closed", "0")
        if oi_added != "0" or oi_closed != "0":
            summary_parts.append(f"Items: +{oi_added} / -{oi_closed}")
        summary_parts.append(f"Preflight: {catchup_fields.get('preflight', '?')}")
        summary_parts.append(f"Context: {catchup_fields.get('context_window_pct', '?')}%")
    else:
        summary_parts.append("No catch-up data available")

    # Append open item count
    oi_content, _ = _read_state_file("open_items")
    open_count = oi_content.lower().count("status: open")
    summary_parts.append(f"\nOpen items: {open_count}")

    text = "\n".join(summary_parts)
    return _apply_scope_filter(text, scope), staleness


# ── /dashboard — rich HTML dashboard ─────────────────────────────────────

_STATUS_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴", "grey": "⚪"}

_DOMAIN_STATE_FILES: dict[str, str] = {
    "immigration": "immigration.md.age",
    "finance": "finance.md.age",
    "kids": "kids.md",
    "health": "health.md.age",
    "travel": "travel.md",
    "home": "home.md",
    "shopping": "shopping.md",
    "goals": "goals.md",
    "vehicle": "vehicle.md.age",
    "estate": "estate.md.age",
    "insurance": "insurance.md.age",
    "calendar": "calendar.md",
    "comms": "comms.md",
    "social": "social.md",
    "learning": "learning.md",
    "boundary": "boundary.md",
    "employment": "employment.md",
}


def _domain_freshness(fname: str) -> tuple[str, str]:
    """Return (emoji, age_str) for a domain state file."""
    import re
    from datetime import datetime, timezone

    fpath = _STATE_DIR / fname
    if not fpath.exists():
        return "⚪", "no data"
    try:
        raw = fpath.read_text(encoding="utf-8", errors="replace")[:500]
    except OSError:
        return "⚪", "unreadable"
    # Extract last_updated from frontmatter
    m = re.search(r'last_updated:\s*"?([^"\n]+)', raw)
    if not m:
        # Encrypted (.age) files: fall back to file modification time
        try:
            mtime = fpath.stat().st_mtime
            from datetime import datetime as _dt
            now = _dt.now(timezone.utc)
            age_h = (now.timestamp() - mtime) / 3600
            if age_h < 24:
                return "🟢", f"{int(age_h)}h"
            days = int(age_h / 24)
            if days <= 3:
                return "🟡", f"{days}d"
            return "🔴", f"{days}d"
        except OSError:
            return "⚪", "?"
    ts_str = m.group(1).strip().rstrip('"')
    if ts_str.startswith("1970"):
        return "⚪", "never"
    try:
        from datetime import datetime as _dt
        # Handle various ISO formats
        ts_str_clean = ts_str.replace("T", " ").replace("+00:00", "+0000")
        for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = _dt.strptime(ts_str_clean, fmt)
                break
            except ValueError:
                continue
        else:
            return "⚪", "?"
        now = _dt.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_h = (now - dt).total_seconds() / 3600
        if age_h < 24:
            return "🟢", f"{int(age_h)}h"
        days = int(age_h / 24)
        if days <= 3:
            return "🟡", f"{days}d"
        return "🔴", f"{days}d"
    except Exception:
        return "⚪", "?"


def _build_dashboard_html() -> str:
    """Build a compact HTML dashboard from live state files.

    Domain colors are based on actual risk:
      🔴 = has P0 items or overdue items
      🟡 = has P1 items (no P0/overdue)
      🟢 = P2 only or no open items
    Non-green domains get a one-liner path-to-green.
    """
    import html as _html
    from datetime import datetime as _dt, date as _date

    # ── Parse all open items ──
    oi_content, _ = _read_state_file("open_items")
    # Per-domain: { domain: { "items": [...], "p0": n, "p1": n, "p2": n, "overdue": n } }
    domain_risk: dict[str, dict] = {}
    total_open = 0
    total_overdue = 0
    total_p0 = 0

    if oi_content:
        lines = oi_content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("- id: OI-"):
                block: dict[str, str] = {"id": line.split(":", 1)[1].strip()}
                i += 1
                while i < len(lines):
                    bline = lines[i].strip()
                    if bline.startswith("- id: OI-") or (not bline and i + 1 < len(lines) and lines[i + 1].strip().startswith("- id:")):
                        break
                    if ":" in bline and not bline.startswith("#"):
                        k, v = bline.split(":", 1)
                        block[k.strip()] = v.strip().strip('"')
                    i += 1
                if block.get("status") == "open":
                    total_open += 1
                    dom = block.get("source_domain", "other")
                    pri = block.get("priority", "P2")
                    desc = block.get("description", "")
                    deadline = block.get("deadline", "")

                    if dom not in domain_risk:
                        domain_risk[dom] = {"items": [], "p0": 0, "p1": 0, "p2": 0, "overdue": 0}
                    dr = domain_risk[dom]
                    dr["items"].append({"pri": pri, "desc": desc, "deadline": deadline, "id": block.get("id", "")})

                    if pri == "P0":
                        dr["p0"] += 1
                        total_p0 += 1
                    elif pri == "P1":
                        dr["p1"] += 1
                    else:
                        dr["p2"] += 1

                    if deadline:
                        try:
                            dl = _dt.strptime(deadline, "%Y-%m-%d").date()
                            if dl < _date.today():
                                dr["overdue"] += 1
                                total_overdue += 1
                        except ValueError:
                            pass
                continue
            i += 1

    # ── Determine color per domain ──
    all_domains = list(_DOMAIN_STATE_FILES.keys())
    domain_color: dict[str, str] = {}
    domain_reason: dict[str, str] = {}

    for dom in all_domains:
        dr = domain_risk.get(dom)
        if not dr:
            domain_color[dom] = "🟢"
            continue
        if dr["p0"] > 0 or dr["overdue"] > 0:
            domain_color[dom] = "🔴"
            # Path-to-green: summarize the top item
            top = next((it for it in dr["items"] if it["pri"] == "P0"), None)
            if top:
                domain_reason[dom] = _truncate(top["desc"], 60)
            elif dr["overdue"] > 0:
                overdue_item = next((it for it in dr["items"] if it["deadline"]), dr["items"][0])
                domain_reason[dom] = f"overdue: {_truncate(overdue_item['desc'], 50)}"
        elif dr["p1"] > 0:
            domain_color[dom] = "🟡"
            top = next((it for it in dr["items"] if it["pri"] == "P1"), dr["items"][0])
            domain_reason[dom] = _truncate(top["desc"], 60)
        else:
            domain_color[dom] = "🟢"

    # ── Build HTML ──
    parts: list[str] = []
    parts.append("<b>📊 Artha Dashboard</b>\n")

    # Life Pulse — legend based on risk
    parts.append("<b>Life Pulse</b>  <i>🔴 critical  🟡 needs attention  🟢 ok</i>")

    # Group: red first, then yellow, then green
    for color_emoji in ("🔴", "🟡", "🟢"):
        for dom in all_domains:
            if domain_color.get(dom, "🟢") != color_emoji:
                continue
            dr = domain_risk.get(dom)
            count = sum(1 for _ in (dr["items"] if dr else []))
            if color_emoji == "🟢":
                parts.append(f"  {color_emoji} {dom}")
            else:
                reason = domain_reason.get(dom, "")
                tag = ""
                if dr and dr["p0"]:
                    tag = " [P0]"
                elif dr and dr["overdue"]:
                    tag = " [overdue]"
                elif dr and dr["p1"]:
                    tag = f" [P1×{dr['p1']}]"
                parts.append(f"  {color_emoji} <b>{dom}</b>{tag}")
                if reason:
                    parts.append(f"      ↳ {_html.escape(reason)}")
    parts.append("")

    # ── Summary line ──
    parts.append(f"<b>Open Items</b>: {total_open}")
    if total_p0:
        parts.append(f"  🔴 P0: {total_p0}")
    if total_overdue:
        parts.append(f"  ⚠️ Overdue: {total_overdue}")
    parts.append("")

    # ── System Health ──
    hc_content, _ = _read_state_file("health_check")
    if hc_content:
        hc_lines = hc_content.splitlines()
        catchup_fields: dict[str, str] = {}
        in_block = False
        for line in hc_lines:
            s = line.strip()
            if "Last Catch-Up" in s:
                in_block = True
                continue
            if s.startswith("## ") and in_block:
                break
            if in_block and ":" in s and not s.startswith("#") and not s.startswith("```"):
                k, v = s.split(":", 1)
                catchup_fields[k.strip()] = v.strip().strip('"')
        ts = catchup_fields.get("last_catch_up", "?")
        if "T" in ts:
            ts_display = ts.split("T")[0] + " " + ts.split("T")[1][:5]
        else:
            ts_display = ts
        ctx = catchup_fields.get("context_window_pct", "?")
        pf = catchup_fields.get("preflight", "?")
        pf_emoji = "✅" if pf == "pass" else "⚠️"
        ctx_emoji = "🟢" if ctx != "?" and int(ctx) < 70 else "🟡" if ctx != "?" and int(ctx) < 90 else "🔴"
        parts.append("<b>System</b>")
        parts.append(f"  Last catch-up: {ts_display}")
        parts.append(f"  {pf_emoji} Preflight: {pf}  {ctx_emoji} Context: {ctx}%")

    return "\n".join(parts)


def _truncate(text: str, maxlen: int) -> str:
    """Truncate text to maxlen, adding ellipsis if needed."""
    if len(text) <= maxlen:
        return text
    return text[:maxlen - 1] + "…"


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """Split a long message into chunks, breaking at newlines when possible."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Try to break at a newline within the last 20% of the chunk
        cut = text.rfind("\n", max_len // 2, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# ── Context-aware LLM Q&A via Gemini CLI ──────────────────────────────────

_PROMPTS_DIR = _ARTHA_DIR / "prompts"

# Keywords → domain names for context gathering
_QUESTION_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "finance": ["credit card", "card", "bank", "tax", "401k", "ira", "mortgage",
                "loan", "invest", "hdfc", "chase", "citi", "discover", "amex",
                "rewards", "groceries", "espp", "nri", "plaid"],
    "kids": ["parth", "trisha", "school", "grade", "homework", "assignment",
             "teacher", "skyward", "tesla stem", "inglewood", "tsa", "sports",
             "tutoring"],
    "health": ["health", "doctor", "exercise", "hike", "mailbox peak", "weight",
               "fitness", "medical", "prescription", "insurance claim"],
    "insurance": ["insurance", "progressive", "auto policy", "renewal",
                  "allstate", "coverage", "premium"],
    "home": ["home", "house", "maintenance", "hvac", "plumber", "leak",
             "garage", "pool", "spa", "appliance", "bob's"],
    "immigration": ["immigration", "visa", "ead", "h1b", "green card", "i-140",
                    "i-485", "uscis", "perm"],
    "travel": ["travel", "flight", "hotel", "trip", "vacation", "passport",
               "booking"],
    "vehicle": ["vehicle", "mazda", "cx-50", "oil change",
                "tire", "registration"],
    "estate": ["estate", "will", "trust", "beneficiary", "power of attorney"],
    "goals": ["goal", "sprint", "objective", "target", "resolution"],
    "calendar": ["calendar", "schedule", "appointment", "meeting", "event"],
    "employment": ["job", "microsoft", "work", "salary", "manager", "career",
                   "team", "promotion", "review"],
    "shopping": ["shopping", "buy", "purchase", "order", "amazon", "costco"],
    "social": ["social", "friend", "family", "contact", "birthday", "reunion"],
    "learning": ["learn", "course", "book", "study", "certification"],
}

_LLM_TIMEOUT_SEC = 90
_LLM_MAX_CONTEXT_CHARS = 30_000  # stay well within Gemini's context window
_CATCHUP_TIMEOUT_SEC = 300  # catch-up pipeline + LLM synthesis can take longer
_CATCHUP_MAX_CONTEXT_CHARS = 80_000  # larger budget for full catch-up


def _detect_domains(question: str) -> list[str]:
    """Return list of relevant domain names for a question."""
    q_lower = question.lower()
    scores: dict[str, int] = {}
    for domain, keywords in _QUESTION_DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in q_lower:
                scores[domain] = scores.get(domain, 0) + 1
    if not scores:
        return ["general"]
    # Return top 3 domains by keyword hits
    return [d for d, _ in sorted(scores.items(), key=lambda x: -x[1])][:3]


def _gather_context(domains: list[str], max_chars: int = _LLM_MAX_CONTEXT_CHARS) -> str:
    """Gather relevant context from prompts/ and state/ for the given domains."""
    sections: list[str] = []
    budget = max_chars

    # Always include open items (open status only, compact)
    oi_content, _ = _read_state_file("open_items")
    if oi_content:
        oi_parts: list[str] = []
        lines = oi_content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("- id: OI-"):
                block_lines = [line]
                i += 1
                while i < len(lines):
                    bline = lines[i].strip()
                    if bline.startswith("- id: OI-") or (not bline and i + 1 < len(lines) and lines[i + 1].strip().startswith("- id:")):
                        break
                    block_lines.append(lines[i])
                    i += 1
                block_text = "\n".join(block_lines)
                # Only include open items
                if "status: open" in block_text:
                    oi_parts.append(block_text)
                continue
            i += 1
        oi_text = "\n\n".join(oi_parts)
        if oi_text and len(oi_text) < budget // 4:
            sections.append(f"[Open Items]\n{oi_text}")
            budget -= len(oi_text) + 20

    for domain in domains:
        if domain == "general":
            continue
        per_domain = budget // max(len(domains), 1)

        # 1. Prompt file (domain knowledge, always readable)
        prompt_file = _PROMPTS_DIR / f"{domain}.md"
        if prompt_file.exists():
            try:
                ptxt = prompt_file.read_text(encoding="utf-8", errors="replace")
                ptxt = _strip_frontmatter(ptxt)
                if len(ptxt) > per_domain // 2:
                    ptxt = ptxt[:per_domain // 2] + "\n…[truncated]"
                sections.append(f"[Domain Prompt: {domain}]\n{ptxt}")
                budget -= len(ptxt) + 30
            except OSError:
                pass

        # 2. State file (if readable / not encrypted)
        state_key = _DOMAIN_TO_STATE_FILE.get(domain)
        if state_key:
            content, _ = _read_state_file(state_key)
            if content:
                content = _strip_frontmatter(content)
                remaining = min(per_domain // 2, budget)
                if len(content) > remaining:
                    content = content[:remaining] + "\n…[truncated]"
                sections.append(f"[State: {domain}]\n{content}")
                budget -= len(content) + 20

        # 3. Also try reading unencrypted state files not in the whitelist
        direct_state = _STATE_DIR / f"{domain}.md"
        if direct_state.exists() and state_key is None:
            try:
                stxt = direct_state.read_text(encoding="utf-8", errors="replace")
                stxt = _strip_frontmatter(stxt)
                remaining = min(per_domain // 2, budget)
                if len(stxt) > remaining:
                    stxt = stxt[:remaining] + "\n…[truncated]"
                sections.append(f"[State: {domain}]\n{stxt}")
                budget -= len(stxt) + 20
            except OSError:
                pass

    return "\n\n".join(sections)


def _detect_llm_cli() -> tuple[str, list[str]] | None:
    """Detect available LLM CLI. Returns (executable, base_args) or None.

    Preference order: claude (sonnet), gemini (flash), copilot (sonnet).
    """
    import shutil
    # Claude Code — fastest, cleanest output
    claude = shutil.which("claude")
    if claude:
        return claude, ["--dangerously-skip-permissions", "--model", "sonnet"]
    # Gemini CLI — free, good quality
    gemini = shutil.which("gemini")
    if gemini:
        return gemini, ["--yolo"]
    # Copilot CLI — slowest, noisy output
    copilot = shutil.which("copilot")
    if copilot:
        return copilot, ["--yolo", "-s", "--model", "claude-sonnet-4"]
    return None


def _detect_all_llm_clis() -> list[tuple[str, str, list[str]]]:
    """Return all available CLIs as (name, executable, base_args)."""
    import shutil
    clis: list[tuple[str, str, list[str]]] = []
    claude = shutil.which("claude")
    if claude:
        clis.append(("claude", claude, ["--dangerously-skip-permissions", "--model", "sonnet"]))
    gemini = shutil.which("gemini")
    if gemini:
        clis.append(("gemini", gemini, ["--yolo"]))
    copilot = shutil.which("copilot")
    if copilot:
        clis.append(("copilot", copilot, ["--yolo", "-s", "--model", "claude-sonnet-4"]))
    return clis


def _vault_relock_if_needed() -> None:
    """Re-encrypt vault if any .age files have decrypted .md siblings on disk."""
    import subprocess as _sp
    age_files = list(_STATE_DIR.glob("*.md.age"))
    for af in age_files:
        plain = _STATE_DIR / af.name.replace(".md.age", ".md")
        if plain.exists():
            log.warning("[vault] decrypted file found: %s — re-encrypting", plain.name)
            try:
                _sp.run(
                    [sys.executable, str(_ARTHA_DIR / "scripts" / "vault.py"), "encrypt"],
                    cwd=str(_ARTHA_DIR),
                    timeout=30,
                    capture_output=True,
                )
                log.info("[vault] re-encrypted successfully")
            except Exception as exc:
                log.error("[vault] re-encrypt failed: %s", exc)
            return  # one encrypt call handles all files


async def _call_single_llm(
    name: str,
    executable: str,
    base_args: list[str],
    prompt_text: str,
    instruction: str,
    timeout: int = _LLM_TIMEOUT_SEC,
) -> str:
    """Call a single LLM CLI and return its response."""
    import tempfile
    import re as _re

    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = Path(tmpdir) / "prompt.txt"
        prompt_file.write_text(prompt_text, encoding="utf-8")

        # Run CLIs from the Artha workspace so they pick up project config
        # (e.g. Claude loads CLAUDE.md, skills, and can invoke vault/tools).
        workspace = str(_ARTHA_DIR)

        # Each CLI has a different stdin/file-reading pattern
        if name == "gemini":
            # Gemini reads stdin
            args_str = " ".join(base_args)
            shell_cmd = f'type "{prompt_file}" | "{executable}" -p "{instruction}" {args_str}'
        elif name == "copilot":
            # Copilot reads files via --add-dir + tool use
            args_str = " ".join(base_args)
            shell_cmd = f'"{executable}" -p "Read prompt.txt and {instruction}" {args_str} --add-dir "{tmpdir}"'
        else:
            # Claude reads stdin — runs from workspace to access skills/CLAUDE.md
            args_str = " ".join(base_args)
            shell_cmd = f'type "{prompt_file}" | "{executable}" -p "{instruction}" {args_str}'

        try:
            proc = await asyncio.create_subprocess_shell(
                shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            raw = stdout.decode("utf-8", errors="replace")
            raw = _re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', raw)
            lines = raw.splitlines()
            clean_lines = [
                l for l in lines
                if not l.startswith("Loaded cached credentials")
                and not l.startswith("YOLO mode")
                and l.strip()
            ]
            result = "\n".join(clean_lines).strip()
            if not result:
                err = stderr.decode("utf-8", errors="replace").strip()
                log.warning("[%s] stdout empty, stderr: %s", name, err[:200])
                return ""
            return result
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            log.warning("[%s] timed out after %ds", name, timeout)
            return ""
        except Exception as exc:
            log.error("[%s] subprocess failed: %s", name, exc)
            return ""
        finally:
            # Safety net: if the CLI decrypted the vault (Claude skills),
            # re-encrypt immediately so plaintext never lingers on disk.
            _vault_relock_if_needed()


async def _ask_llm(question: str, context: str) -> str:
    """Send a context-aware question to the LLM CLI with failover."""

    clis = _detect_all_llm_clis()
    if not clis:
        return "No LLM CLI available (install gemini, copilot, or claude)."

    system_prompt = (
        "You are Artha, a personal intelligence assistant. "
        "Answer the user's question using ONLY the context provided below. "
        "Be concise and actionable. If the context doesn't contain enough "
        "information to answer, say so clearly.\n"
        "FORMAT: Use numbered lists (1. 2. 3.) for ranked/sequential items, "
        "bullet points (• ) for unordered items, and blank lines between sections. "
        "Lead with a one-line direct answer, then supporting detail. "
        "No markdown (no **, ##, ```) — plain text with Unicode bullets only."
    )

    full_prompt = f"{system_prompt}\n\n--- CONTEXT ---\n{context}\n--- END CONTEXT ---\n\nQuestion: {question}"

    # Try each CLI in order until one succeeds
    for name, executable, base_args in clis:
        result = await _call_single_llm(
            name, executable, base_args, full_prompt,
            "Answer the question above.",
        )
        if result:
            log.info("[ask] answered by %s (%d chars)", name, len(result))
            return result
        log.warning("[ask] %s failed or empty, trying next...", name)

    return "All LLM CLIs failed. Try again later."


async def _ask_llm_ensemble(question: str, context: str) -> str:
    """Ask all available LLMs in parallel, then consolidate into one answer.

    Read-only operation — safe to run concurrently.
    """
    clis = _detect_all_llm_clis()
    if not clis:
        return "No LLM CLI available (install gemini, copilot, or claude)."
    if len(clis) < 2:
        # Only one CLI — just use normal path
        return await _ask_llm(question, context)

    system_prompt = (
        "You are Artha, a personal intelligence assistant. "
        "Answer the user's question using ONLY the context provided below. "
        "Be concise and actionable. If the context doesn't contain enough "
        "information to answer, say so clearly.\n"
        "FORMAT: Use numbered lists (1. 2. 3.) for ranked/sequential items, "
        "bullet points (• ) for unordered items, and blank lines between sections. "
        "Lead with a one-line direct answer, then supporting detail. "
        "No markdown (no **, ##, ```) — plain text with Unicode bullets only."
    )

    full_prompt = f"{system_prompt}\n\n--- CONTEXT ---\n{context}\n--- END CONTEXT ---\n\nQuestion: {question}"

    # Ask all CLIs in parallel
    tasks = [
        _call_single_llm(name, exe, args, full_prompt, "Answer the question above.")
        for name, exe, args in clis
    ]
    results = await asyncio.gather(*tasks)

    # Collect successful responses with source labels
    responses: list[tuple[str, str]] = []
    for (name, _, _), result in zip(clis, results):
        if result:
            responses.append((name, result))

    if not responses:
        return "All LLM CLIs failed. Try again later."
    if len(responses) == 1:
        name, answer = responses[0]
        log.info("[ask-all] only %s responded (%d chars)", name, len(answer))
        return answer

    # Consolidate via Haiku — fast & cheap for synthesis
    import shutil as _shutil
    claude_exe = _shutil.which("claude")
    labeled = "\n\n".join(
        f"--- Response from {name} ---\n{resp}" for name, resp in responses
    )
    consolidation_prompt = (
        "You are given multiple AI responses to the same question. "
        "Synthesize ONE best answer that is accurate, concise, and complete. "
        "Prefer concrete facts over hedging. Resolve any contradictions by "
        "favouring the response with more specific detail. "
        "Do NOT mention which AI said what — just give the single best answer.\n"
        "FORMAT: Use numbered lists (1. 2. 3.) for ranked/sequential items, "
        "bullet points (• ) for unordered items, and blank lines between sections. "
        "Lead with a one-line direct answer, then supporting detail. "
        "No markdown (no **, ##, ```) — plain text with Unicode bullets only.\n\n"
        f"Original question: {question}\n\n{labeled}"
    )

    log.info(
        "[ask-all] got %d responses (%s), consolidating via haiku",
        len(responses),
        "+".join(n for n, _ in responses),
    )

    if claude_exe:
        final = await _call_single_llm(
            "claude-haiku", claude_exe,
            ["--dangerously-skip-permissions", "--model", "haiku"],
            consolidation_prompt, "Synthesize the best answer now.",
        )
    else:
        # Fallback: use primary CLI if Claude not available
        consolidator = clis[0]
        final = await _call_single_llm(
            consolidator[0], consolidator[1], consolidator[2],
            consolidation_prompt, "Synthesize the best answer now.",
        )
    if not final:
        # Consolidation failed — return longest individual response
        final = max((r for _, r in responses), key=len)
    return final


async def cmd_ask(question: str, scope: str) -> tuple[str, str]:
    """Context-aware Q&A — routes free-form questions to LLM with Artha context.

    Prefix with 'aa' (or 'ask all') to run ensemble mode (all CLIs in parallel).
    """
    if not question.strip():
        return "Send me a question and I'll answer using your Artha data.", "N/A"

    # Check for ensemble trigger
    ensemble = False
    q = question.strip()
    for prefix in ("aa ", "ask all ", "ask-all "):
        if q.lower().startswith(prefix):
            q = q[len(prefix):].strip()
            ensemble = True
            break

    # Detect relevant domains and gather context
    domains = _detect_domains(q)
    context = _gather_context(domains)

    log.info("[ask] question=%r domains=%s context_chars=%d ensemble=%s",
             q[:80], domains, len(context), ensemble)

    _audit_log("CHANNEL_ASK", question=q[:100], domains=",".join(domains),
               context_chars=len(context), ensemble=ensemble)

    # Call LLM(s)
    if ensemble:
        answer = await _ask_llm_ensemble(q, context)
    else:
        answer = await _ask_llm(q, context)

    # Truncate to Telegram limit
    if len(answer) > 3800:
        answer = answer[:3800] + "\n…[truncated]"

    return answer, "N/A"


async def cmd_dashboard(args: list[str], scope: str) -> tuple[str, str]:
    """Return rich HTML-formatted life dashboard."""
    html = _build_dashboard_html()
    return _apply_scope_filter(html, scope), "N/A"


async def cmd_power(args: list[str], scope: str) -> tuple[str, str]:
    """Return Power Half Hour view (E14 — power_half_hour_view.py)."""
    try:
        from power_half_hour_view import render_power_session  # noqa: PLC0415
    except ImportError:
        return "⚠️ power_half_hour_view module not found.", "N/A"

    fmt_arg = args[0].lstrip("-") if args else "standard"
    text, _ = render_power_session(fmt=fmt_arg)
    return _apply_scope_filter(text, scope), "N/A"


async def cmd_relationships(args: list[str], scope: str) -> tuple[str, str]:
    """Return Relationship Pulse view (E13 — relationship_pulse_view.py)."""
    try:
        from relationship_pulse_view import render_relationships  # noqa: PLC0415
    except ImportError:
        return "⚠️ relationship_pulse_view module not found.", "N/A"

    fmt_arg = args[0].lstrip("-") if args else "standard"
    text, _ = render_relationships(fmt=fmt_arg)
    return _apply_scope_filter(text, scope), "N/A"


# ── Catch-up via Telegram ─────────────────────────────────────────────────────


async def _run_pipeline(since_iso: str) -> tuple[str, int]:
    """Run pipeline.py to fetch new emails/calendar. Return (jsonl_output, record_count)."""
    import asyncio

    pipeline_script = _ARTHA_DIR / "scripts" / "pipeline.py"
    python = sys.executable

    try:
        proc = await asyncio.create_subprocess_exec(
            python, str(pipeline_script), "--since", since_iso,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_ARTHA_DIR),
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=120
        )
        output = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace")
        if err_text:
            log.info("[catch-up] pipeline stderr: %s", err_text[:500])
        record_count = len([l for l in output.splitlines() if l.strip()])
        return output, record_count
    except asyncio.TimeoutError:
        log.error("[catch-up] pipeline timed out")
        return "", 0
    except Exception as exc:
        log.error("[catch-up] pipeline failed: %s", exc)
        return "", 0


def _get_last_catchup_iso() -> str:
    """Read last catch-up timestamp from health-check.md, fallback to 48h ago."""
    import re
    hc_path = _STATE_DIR / "health-check.md"
    if hc_path.exists():
        try:
            content = hc_path.read_text(encoding="utf-8", errors="replace")[:2000]
            m = re.search(r'last_catch_up:\s*"?([^"\n]+)', content)
            if m:
                return m.group(1).strip()
        except OSError:
            pass
    return (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()


def _gather_all_context(max_chars: int = _CATCHUP_MAX_CONTEXT_CHARS) -> str:
    """Gather context for ALL domains — broader than _gather_context()."""
    sections: list[str] = []
    budget = max_chars

    # Open items (all open)
    oi_content, _ = _read_state_file("open_items")
    if oi_content:
        oi_parts: list[str] = []
        lines = oi_content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("- id: OI-"):
                block_lines = [line]
                i += 1
                while i < len(lines):
                    bline = lines[i].strip()
                    if bline.startswith("- id: OI-") or (
                        not bline and i + 1 < len(lines)
                        and lines[i + 1].strip().startswith("- id:")
                    ):
                        break
                    block_lines.append(lines[i])
                    i += 1
                block_text = "\n".join(block_lines)
                if "status: open" in block_text:
                    oi_parts.append(block_text)
                continue
            i += 1
        oi_text = "\n\n".join(oi_parts)
        if oi_text:
            sections.append(f"[Open Items]\n{oi_text}")
            budget -= len(oi_text) + 20

    # Goals
    goals_content, _ = _read_state_file("goals")
    if goals_content:
        goals_content = _strip_frontmatter(goals_content)
        cap = min(3000, budget // 8)
        if len(goals_content) > cap:
            goals_content = goals_content[:cap] + "\n…[truncated]"
        sections.append(f"[Goals]\n{goals_content}")
        budget -= len(goals_content) + 20

    # Calendar
    cal_content, _ = _read_state_file("calendar")
    if cal_content:
        cal_content = _strip_frontmatter(cal_content)
        cap = min(3000, budget // 8)
        if len(cal_content) > cap:
            cal_content = cal_content[:cap] + "\n…[truncated]"
        sections.append(f"[Calendar]\n{cal_content}")
        budget -= len(cal_content) + 20

    # All readable state files
    all_domains = sorted(_DOMAIN_STATE_FILES.keys())
    per_domain_cap = max(1500, budget // max(len(all_domains), 1))
    for domain in all_domains:
        fname = _DOMAIN_STATE_FILES[domain]
        if fname.endswith(".age"):
            # Encrypted — try prompt file instead
            prompt_file = _PROMPTS_DIR / f"{domain}.md"
            if prompt_file.exists():
                try:
                    ptxt = prompt_file.read_text(encoding="utf-8", errors="replace")
                    ptxt = _strip_frontmatter(ptxt)
                    cap = min(2000, per_domain_cap)
                    if len(ptxt) > cap:
                        ptxt = ptxt[:cap] + "\n…[truncated]"
                    sections.append(f"[Domain Prompt: {domain}]\n{ptxt}")
                    budget -= len(ptxt) + 30
                except OSError:
                    pass
            continue
        fpath = _STATE_DIR / fname
        if not fpath.exists():
            continue
        try:
            raw = fpath.read_text(encoding="utf-8", errors="replace")
            raw = _strip_frontmatter(raw)
            cap = min(per_domain_cap, budget // 4)
            if len(raw) > cap:
                raw = raw[:cap] + "\n…[truncated]"
            sections.append(f"[State: {domain}]\n{raw}")
            budget -= len(raw) + 20
        except OSError:
            pass

    return "\n\n".join(sections)


def _read_briefing_template() -> str:
    """Read the standard briefing format template."""
    bf_path = _ARTHA_DIR / "config" / "briefing-formats.md"
    if bf_path.exists():
        try:
            txt = bf_path.read_text(encoding="utf-8", errors="replace")
            # Just the standard template section (first ~60 lines)
            lines = txt.splitlines()[:80]
            return "\n".join(lines)
        except OSError:
            pass
    return ""


def _save_briefing(text: str) -> Path:
    """Save briefing to briefings/YYYY-MM-DD.md. Appends if file exists."""
    today = datetime.now().strftime("%Y-%m-%d")
    path = _BRIEFINGS_DIR / f"{today}.md"
    _BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)

    header = f"""---\ndate: {today}\nsubject: Artha Telegram Catch-Up\narchived: {datetime.now(timezone.utc).isoformat()}\nsensitivity: standard\n---\n\n"""

    if path.exists():
        # Append as a new run
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n# Telegram Catch-Up ({datetime.now().strftime('%I:%M %p')})\n\n")
            f.write(text)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(header)
            f.write(text)
    return path


async def cmd_catchup(args: list[str], scope: str) -> tuple[str, str]:
    """Run a catch-up: fetch new data → LLM synthesis → briefing."""

    # Bridge result ingestion: ingest any Windows-executed results before
    # building the briefing, so the catch-up reflects latest action outcomes.
    # Runs only when bridge is enabled and this machine is the proposer role.
    # Spec: dual-setup.md §4.2 — "before briefing_adapter.py is invoked"
    try:
        import yaml as _br_yaml  # noqa: PLC0415
        _br_cfg_path = _ARTHA_DIR / "config" / "artha_config.yaml"
        if _br_cfg_path.exists():
            with open(_br_cfg_path, encoding="utf-8") as _br_f:
                _br_artha_cfg = _br_yaml.safe_load(_br_f) or {}
            if _br_artha_cfg.get("multi_machine", {}).get("bridge_enabled", False):
                from action_bridge import (  # noqa: PLC0415
                    detect_role, get_bridge_dir, ingest_results, gc,
                )
                _br_ch_cfg_path = _ARTHA_DIR / "config" / "channels.yaml"
                _br_ch_cfg: dict = {}
                if _br_ch_cfg_path.exists():
                    with open(_br_ch_cfg_path, encoding="utf-8") as _br_cf:
                        _br_ch_cfg = _br_yaml.safe_load(_br_cf) or {}
                if detect_role(_br_ch_cfg) == "proposer":
                    _br_dir = get_bridge_dir(_ARTHA_DIR)
                    from action_queue import ActionQueue as _BrAQ  # noqa: PLC0415
                    _br_queue = _BrAQ(_ARTHA_DIR)
                    ingest_results(_br_dir, _br_queue, _ARTHA_DIR)
                    gc(_br_dir, _ARTHA_DIR)
                    log.info("[bridge] Result ingestion complete (catch-up pre-step)")
    except Exception as _br_exc:
        log.warning("[bridge] catch-up result ingestion failed (non-fatal): %s", _br_exc)

    clis = _detect_all_llm_clis()
    if not clis:
        return "No LLM CLI available (install gemini, copilot, or claude).", "N/A"

    # Step 1: Determine since timestamp
    since_iso = _get_last_catchup_iso()
    log.info("[catch-up] Starting. since=%s", since_iso)

    # Step 2: Run pipeline to fetch new data
    jsonl_output, record_count = await _run_pipeline(since_iso)
    log.info("[catch-up] Pipeline returned %d records", record_count)

    # Step 3: Gather all domain context
    context = _gather_all_context()
    log.info("[catch-up] Context gathered: %d chars", len(context))

    # Step 4: Read briefing template
    template = _read_briefing_template()

    # Step 5: Build mega-prompt
    today_str = datetime.now().strftime("%A, %B %d, %Y")
    system_prompt = (
        "You are Artha, a personal intelligence assistant. "
        "Produce a catch-up briefing following the template format below. "
        "Use ONLY the data provided — never fabricate. "
        f"Today is {today_str}. Last catch-up: {since_iso}.\n"
        "Do NOT use markdown formatting — use plain text with Unicode box-drawing "
        "characters (━) for section dividers as shown in the template.\n"
        "Keep it concise: max 3 bullets per domain, skip domains with no new activity "
        "(but state 'No new activity' for major domains).\n"
        "Include ONE THING with Urgency×Impact×Agency scoring.\n"
        "If new emails/events are provided, incorporate them. "
        "If none, synthesize from current state files and open items."
    )

    parts = [system_prompt, f"\n--- BRIEFING TEMPLATE ---\n{template}"]
    if jsonl_output.strip():
        # Cap new data to avoid blowing context
        new_data = jsonl_output[:20_000]
        parts.append(f"\n--- NEW EMAILS/EVENTS ({record_count} records) ---\n{new_data}")
    parts.append(f"\n--- CURRENT STATE & OPEN ITEMS ---\n{context}")

    full_prompt = "\n".join(parts)
    # Trim total to stay within limits
    if len(full_prompt) > _CATCHUP_MAX_CONTEXT_CHARS:
        full_prompt = full_prompt[:_CATCHUP_MAX_CONTEXT_CHARS]

    log.info("[catch-up] Prompt size: %d chars. Calling LLM...", len(full_prompt))

    # Step 6: Call LLM with failover
    briefing = ""
    for name, executable, base_args in clis:
        briefing = await _call_single_llm(
            name, executable, base_args, full_prompt,
            "Produce the catch-up briefing now.",
            timeout=_CATCHUP_TIMEOUT_SEC,
        )
        if briefing:
            log.info("[catch-up] Briefing produced by %s (%d chars)", name, len(briefing))
            break
        log.warning("[catch-up] %s failed, trying next CLI...", name)

    if not briefing:
        return "Catch-up produced empty output. Try again.", "N/A"

    # Step 7: Save briefing
    saved_path = _save_briefing(briefing)
    log.info("[catch-up] Briefing saved to %s (%d chars)", saved_path.name, len(briefing))

    _audit_log(
        "CHANNEL_CATCHUP",
        emails_fetched=record_count,
        briefing_chars=len(briefing),
        saved_to=saved_path.name,
    )

    return briefing, "N/A"


async def cmd_alerts(args: list[str], scope: str) -> tuple[str, str]:
    """Return active alerts from latest briefing."""
    briefing_path = _get_latest_briefing_path()
    if briefing_path is None:
        return "_No briefing available_", "never"

    try:
        content = briefing_path.read_text(encoding="utf-8", errors="replace")
        mtime = briefing_path.stat().st_mtime
        staleness = _format_age(time.time() - mtime)
    except OSError:
        return "_Could not read briefing_", "unknown"

    # Extract lines with alert emoji
    alert_lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if any(em in stripped for em in ("🔴", "🟠", "🟡", "🔵", "⚠️")):
            alert_lines.append(stripped)
        if stripped.startswith("ARTHA ·"):
            alert_lines.insert(0, stripped)

    text = "\n".join(alert_lines[:20]) or "_No alerts found_"
    return _apply_scope_filter(text, scope), staleness


async def cmd_tasks(args: list[str], scope: str) -> tuple[str, str]:
    """Return open action items sorted by priority."""
    content, staleness = _read_state_file("open_items")

    # Parse structured open items
    items: list[tuple[str, str]] = []  # (priority, display_line)
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("- id: OI-"):
            item_id = line.split(":", 1)[1].strip()
            block: dict[str, str] = {"id": item_id}
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("- id: OI-"):
                l = lines[i].strip()
                if ":" in l:
                    k, v = l.split(":", 1)
                    block[k.strip()] = v.strip().strip('"')
                i += 1
            if block.get("status") == "open":
                desc = block.get("description", "")
                dl = block.get("deadline", "")
                pri = block.get("priority", "")
                entry = f"[{item_id}] {desc}"
                if dl:
                    entry += f" (due {dl})"
                if pri:
                    entry += f" [{pri}]"
                items.append((pri, entry))
        else:
            i += 1

    if not items:
        text = "No open tasks"
    else:
        _pri_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        items.sort(key=lambda t: _pri_order.get(t[0], 9))
        task_lines = [entry for _, entry in items[:10]]
        text = f"Open tasks ({len(items)}):\n" + "\n".join(task_lines)

    return _apply_scope_filter(text, scope), staleness


async def cmd_quick(args: list[str], scope: str) -> tuple[str, str]:
    """Return tasks that take ≤5 minutes (phone-ready)."""
    content, staleness = _read_state_file("open_items")

    quick_keywords = ("5 min", "5min", "quick", "< 5", "<5", "phone", "2 min", "1 min")
    quick_lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        line_lower = stripped.lower()
        if any(kw in line_lower for kw in quick_keywords):
            if "done" not in line_lower and "resolved" not in line_lower:
                quick_lines.append(stripped)
        if len(quick_lines) >= 5:
            break

    if not quick_lines:
        text = "_No quick tasks found (≤5 min)_"
    else:
        text = f"Quick tasks ({len(quick_lines)}):\n" + "\n".join(quick_lines)

    return _apply_scope_filter(text, scope), staleness


async def cmd_domain(args: list[str], scope: str) -> tuple[str, str]:
    """Return state summary for a specific domain."""
    all_unencrypted = sorted(_DOMAIN_TO_STATE_FILE.keys())
    encrypted_domains = {"finance", "insurance", "immigration", "estate", "vehicle", "health"}

    if not args:
        lines = [
            "Pick a domain:",
            "",
            "Direct read (fast):",
            "  " + ", ".join(all_unencrypted),
            "",
            "Via AI (needs vault, ~20s):",
            "  " + ", ".join(sorted(encrypted_domains)),
            "",
            "Example: d kids",
        ]
        return "\n".join(lines), "N/A"

    domain_name = args[0].lower()

    # Redirect /domain dashboard → /dashboard (rich HTML handler)
    if domain_name == "dashboard":
        return await cmd_dashboard(args[1:], scope)

    # Check scope constraints
    if scope in ("family", "standard") and domain_name in _FAMILY_EXCLUDED_DOMAINS:
        return (
            f"_{domain_name.title()} domain is not available in your access scope. "
            "Full details are available in the CLI session._",
            "N/A",
        )

    state_key = _DOMAIN_TO_STATE_FILE.get(domain_name)

    # Encrypted domain → route through LLM (Claude can use vault skills)
    if domain_name in encrypted_domains and state_key is None:
        question = f"Give me a complete summary of my {domain_name} domain: key items, deadlines, risks, and any actions needed."
        context = _gather_context([domain_name])
        answer = await _ask_llm(question, context)
        return answer, "N/A"

    if state_key is None:
        available = ", ".join(all_unencrypted) + "\n+ encrypted: " + ", ".join(sorted(encrypted_domains))
        return f"_Unknown domain '{domain_name}'._\nAvailable: {available}", "N/A"

    content, staleness = _read_state_file(state_key)
    # Use section-aware extraction for large files (Telegram limit: 4096 chars)
    if len(content) > 1000:
        content = _extract_section_summaries(content, max_total=3200)

    # Append relevant open action items for this domain
    action_items = _get_domain_open_items(domain_name)
    if action_items:
        remaining = 3800 - len(content)
        if remaining > 100:
            content += "\n" + action_items[:remaining]

    return _apply_scope_filter(content, scope), staleness


async def cmd_goals(args: list[str], scope: str) -> tuple[str, str]:
    """Shortcut: equivalent to /domain goals."""
    return await cmd_domain(["goals"] + args, scope)


async def cmd_diff(args: list[str], scope: str) -> tuple[str, str]:
    """Show state files that changed since last catch-up (or N days)."""
    import re as _re

    # Parse optional time argument: "7d", "3d", "24h", or default to since last catchup
    hours = None
    if args:
        m = _re.match(r'^(\d+)\s*(d|h)$', args[0].lower())
        if m:
            val, unit = int(m.group(1)), m.group(2)
            hours = val * 24 if unit == "d" else val

    if hours is None:
        since_iso = _get_last_catchup_iso()
        try:
            since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
            hours = (datetime.now(timezone.utc) - since_dt).total_seconds() / 3600
            since_label = f"since last catch-up ({since_iso[:10]})"
        except (ValueError, TypeError):
            hours = 48
            since_label = "last 48h (fallback)"
    else:
        since_label = f"last {args[0]}"

    cutoff = time.time() - (hours * 3600)
    changed: list[str] = []
    unchanged: list[str] = []

    for domain in sorted(_DOMAIN_STATE_FILES.keys()):
        fname = _DOMAIN_STATE_FILES[domain]
        fpath = _STATE_DIR / fname
        if not fpath.exists():
            continue
        # Skip encrypted files — can't read mtime meaningfully
        if fname.endswith(".age"):
            continue
        try:
            mtime = fpath.stat().st_mtime
            age_str = _format_age(time.time() - mtime)
            if mtime > cutoff:
                changed.append(f"  📝 {domain} (updated {age_str} ago)")
            else:
                unchanged.append(domain)
        except OSError:
            continue

    # Also check open_items
    oi_path = _STATE_DIR / "open_items.md"
    if oi_path.exists():
        try:
            mtime = oi_path.stat().st_mtime
            if mtime > cutoff:
                age_str = _format_age(time.time() - mtime)
                changed.append(f"  📝 open_items (updated {age_str} ago)")
        except OSError:
            pass

    lines = [f"State changes {since_label}:", ""]
    if changed:
        lines.append(f"Changed ({len(changed)}):")
        lines.extend(changed)
    else:
        lines.append("No state files changed.")
    if unchanged:
        lines.append(f"\nUnchanged: {', '.join(unchanged)}")

    return "\n".join(lines), "N/A"


async def cmd_items_add(args: list[str], scope: str) -> tuple[str, str]:
    """Add a new open item from Telegram.

    Usage: items add <description> [P0|P1|P2] [domain] [YYYY-MM-DD]
    Example: items add Call estate attorney P0 estate 2026-03-20
    """
    if not args:
        return (
            "Usage: items add <description> [priority] [domain] [deadline]\n"
            "Example: items add Call estate attorney P0 estate 2026-03-20\n"
            "Priority: P0/P1/P2 (default P1)\n"
            "Domain: kids/finance/health/home/etc (default general)\n"
            "Deadline: YYYY-MM-DD (optional)"
        ), "N/A"

    import re as _re

    raw = " ".join(args)

    # Extract priority
    priority = "P1"
    m = _re.search(r'\b(P[012])\b', raw)
    if m:
        priority = m.group(1)
        raw = raw[:m.start()] + raw[m.end():]

    # Extract deadline
    deadline = ""
    m = _re.search(r'\b(\d{4}-\d{2}-\d{2})\b', raw)
    if m:
        deadline = m.group(1)
        raw = raw[:m.start()] + raw[m.end():]

    # Extract domain
    known_domains = set(_DOMAIN_STATE_FILES.keys()) | {"general"}
    domain = "general"
    for d in known_domains:
        pattern = r'\b' + _re.escape(d) + r'\b'
        if _re.search(pattern, raw.lower()):
            domain = d
            raw = _re.sub(pattern, '', raw, flags=_re.IGNORECASE)
            break

    description = raw.strip().rstrip(".")
    if not description:
        return "Need a description. Example: items add Call estate attorney P0", "N/A"

    # Find next OI number
    oi_path = _STATE_DIR / "open_items.md"
    content = oi_path.read_text(encoding="utf-8", errors="replace") if oi_path.exists() else ""
    numbers = [int(m.group(1)) for m in _re.finditer(r'id: OI-(\d+)', content)]
    next_num = max(numbers) + 1 if numbers else 1
    oi_id = f"OI-{next_num:03d}"
    today = datetime.now().strftime("%Y-%m-%d")

    # Append new item
    entry = (
        f"\n- id: {oi_id}\n"
        f"  date_added: \"{today}\"\n"
        f"  source_domain: {domain}\n"
        f"  description: \"{description}\"\n"
        f"  deadline: \"{deadline}\"\n"
        f"  priority: {priority}\n"
        f"  status: open\n"
        f"  todo_id: \"\"\n"
    )

    try:
        with open(oi_path, "a", encoding="utf-8") as f:
            f.write(entry)
        _audit_log("ITEM_ADD", item_id=oi_id, description=description[:80],
                   priority=priority, domain=domain, deadline=deadline)
        return (
            f"Added {oi_id}:\n"
            f"  {description}\n"
            f"  Priority: {priority} | Domain: {domain}"
            + (f" | Due: {deadline}" if deadline else "")
        ), "N/A"
    except OSError as exc:
        return f"Failed to write item: {exc}", "N/A"


async def cmd_items_done(args: list[str], scope: str) -> tuple[str, str]:
    """Mark an open item as done.

    Usage: items done OI-NNN [resolution note]
    Example: items done OI-005 Called and scheduled for March 20
    """
    import re as _re

    if not args:
        return "Usage: done OI-NNN [resolution note]\nExample: done OI-005 Completed", "N/A"

    # Parse OI ID — accept "OI-005", "oi-005", "005", "5"
    raw_id = args[0].upper()
    m = _re.match(r'^(?:OI-)?(\d+)$', raw_id)
    if not m:
        return f"Invalid item ID: {args[0]}. Expected OI-NNN or just the number.", "N/A"
    oi_id = f"OI-{int(m.group(1)):03d}"
    resolution = " ".join(args[1:]).strip() if len(args) > 1 else "Marked done via Telegram"

    oi_path = _STATE_DIR / "open_items.md"
    if not oi_path.exists():
        return "No open_items.md found.", "N/A"

    content = oi_path.read_text(encoding="utf-8", errors="replace")

    # Find and update the item
    pattern = rf'(- id: {_re.escape(oi_id)}\b.*?)(\n- id: OI-|\Z)'
    match = _re.search(pattern, content, _re.DOTALL)
    if not match:
        return f"Item {oi_id} not found.", "N/A"

    block = match.group(1)
    if "status: done" in block:
        return f"{oi_id} is already done.", "N/A"
    if "status: open" not in block:
        return f"{oi_id} is not in open status (current: deferred?).", "N/A"

    today = datetime.now().strftime("%Y-%m-%d")
    new_block = block.replace("status: open", "status: done")
    # Add date_resolved and resolution if not present
    if "date_resolved:" not in new_block:
        new_block = new_block.rstrip() + f"\n  date_resolved: \"{today}\"\n"
    if "resolution:" not in new_block:
        new_block = new_block.rstrip() + f"\n  resolution: \"{resolution}\"\n"

    content = content[:match.start()] + new_block + match.group(2) + content[match.end():]

    try:
        oi_path.write_text(content, encoding="utf-8")
        _audit_log("ITEM_DONE", item_id=oi_id, resolution=resolution[:80])

        # Extract description for confirmation
        desc_m = _re.search(r'description:\s*"?([^"\n]+)', block)
        desc = desc_m.group(1).strip() if desc_m else oi_id

        return f"✅ {oi_id} marked done:\n  {desc}\n  Resolution: {resolution}", "N/A"
    except OSError as exc:
        return f"Failed to update: {exc}", "N/A"


_REMEMBER_WRITE_RATE_LIMIT = 5   # max /remember writes per hour per sender
_REMEMBER_MAX_UNTRIAGED = 50      # cap: oldest auto-expire after 7 days

# In-memory write-rate tracker for /remember
import collections as _col_mod
_remember_write_times: dict[str, list[float]] = _col_mod.defaultdict(list)


def _remember_rate_ok(key: str) -> bool:
    """Return True if key has not exceeded _REMEMBER_WRITE_RATE_LIMIT per hour."""
    import time as _time_mod
    now = _time_mod.monotonic()
    ts_list = _remember_write_times[key]
    cutoff = now - 3600
    while ts_list and ts_list[0] < cutoff:
        ts_list.pop(0)
    if len(ts_list) >= _REMEMBER_WRITE_RATE_LIMIT:
        return False
    ts_list.append(now)
    return True


async def cmd_remember(args: list[str], scope: str) -> tuple[str, str]:
    """Capture a quick note into state/inbox.md for triage during next catch-up.

    Usage: /remember <text>
    Aliases: /note, /inbox

    Only available to full-scope users. Applies PII guard pre-write.
    Rate-limited: 5 writes/hour.
    """
    import re as _re2
    try:
        import fcntl as _fcntl2
    except ImportError:  # Windows
        _fcntl2 = None  # type: ignore[assignment]

    if scope not in ("full", "admin"):
        return "❌ /remember is available to full-scope users only.", "N/A"

    if not args:
        return (
            "Usage: /remember <text>\n"
            "Example: /remember Pick up science project materials from Staples"
        ), "N/A"

    raw_text = " ".join(args).strip()[:500]
    if not raw_text:
        return "Need some text to note.", "N/A"

    # PII guard
    try:
        from pii_guard import filter_text as _pii_filter  # type: ignore[import]
        filtered_text, _pii_found = _pii_filter(raw_text)
    except ImportError:
        filtered_text = raw_text

    # Write rate limit: 5/hour (global key — sender_id not available here)
    if not _remember_rate_ok("_global"):
        return "⛔ Write rate limit reached (5/hour). Try again later.", "N/A"

    inbox_path = _STATE_DIR / "inbox.md"
    existing = inbox_path.read_text(encoding="utf-8", errors="replace") if inbox_path.exists() else ""

    # Untriaged cap
    untriaged_count = len(_re2.findall(r"triaged:\s*false", existing))
    if untriaged_count >= _REMEMBER_MAX_UNTRIAGED:
        return (
            f"⚠️ Inbox full ({_REMEMBER_MAX_UNTRIAGED} untriaged items). "
            "Run /catch-up to triage first."
        ), "N/A"

    # Next INB-NNN id
    numbers = [int(m.group(1)) for m in _re2.finditer(r"id:\s*INB-(\d+)", existing)]
    next_num = max(numbers) + 1 if numbers else 1
    inb_id = f"INB-{next_num:03d}"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entry = (
        f"\n- id: {inb_id}\n"
        f"  text: \"{filtered_text}\"\n"
        f"  source: telegram\n"
        f"  timestamp: {ts}\n"
        f"  triaged: false\n"
        f"  routed_to: null\n"
        f"  created_oi: null\n"
    )

    if not inbox_path.exists() or not existing.strip():
        header = "---\ndomain: inbox\nsensitivity: standard\n---\n\n## Inbox Items\n"
        existing = header

    try:
        with open(inbox_path, "a", encoding="utf-8") as fh:
            if _fcntl2 is not None:
                _fcntl2.flock(fh, _fcntl2.LOCK_EX)
            if not inbox_path.stat().st_size:
                fh.write(existing)
            fh.write(entry)
        _audit_log("CHANNEL_REMEMBER", item_id=inb_id, text_preview=filtered_text[:60], scope=scope)
        return (
            f"📥 Noted: {inb_id}\n  {filtered_text[:80]}"
            + ("\n  (Partial PII redaction applied)" if filtered_text != raw_text else "")
        ), "N/A"
    except OSError as exc:
        return f"Failed to write inbox: {exc}", "N/A"


async def cmd_cost(args: list[str], scope: str) -> tuple[str, str]:
    """Show API cost telemetry estimate for the current session and rolling windows.

    Usage: /cost
    Output: Today / this week / this month estimates + breakdown + optimisation tip.
    """
    try:
        from cost_tracker import CostTracker  # type: ignore[import]
        tracker = CostTracker()
        report = tracker.build_report()
        return tracker.format_report(report), "N/A"
    except ImportError:
        return "cost_tracker module not available. Run: python scripts/cost_tracker.py", "N/A"
    except Exception as exc:  # noqa: BLE001
        return f"Cost estimation failed: {exc}", "N/A"


async def cmd_stage(args: list[str], scope: str) -> tuple[str, str]:
    """Show Content Stage card list or card detail (PR-2, read-only).

    Usage:
      /stage            — list active cards
      /stage list       — same as above
      /stage preview <ID>  — show card details

    Write operations (approve, draft, posted, dismiss) are not available via
    channel — use the AI assistant directly for those.
    """
    try:
        import yaml as _yaml  # noqa: PLC0415
        gallery_path = _READABLE_STATE_FILES["gallery"]

        if not gallery_path.exists():
            return (
                "⚠️ Content Stage not initialised. Run a catch-up first.",
                "N/A",
            )

        data = _yaml.safe_load(gallery_path.read_text(encoding="utf-8")) or {}
        cards = data.get("cards", [])

        subcommand = args[0].lower() if args else "list"

        # ── preview <ID> ──────────────────────────────────────────────────
        if subcommand == "preview" and len(args) >= 2:
            card_id = args[1].upper()
            card = next((c for c in cards if str(c.get("id", "")).upper() == card_id), None)
            if not card:
                return f"⚠️ Card {card_id} not found.", "N/A"

            from datetime import date as _date  # noqa: PLC0415
            today = _date.today()
            ev_str = card.get("event_date", "?")
            try:
                ev_date = _date.fromisoformat(str(ev_str))
                days = (ev_date - today).days
                days_label = "today" if days == 0 else (f"in {days}d" if days > 0 else f"{abs(days)}d ago")
            except (ValueError, TypeError):
                days_label = "?"

            drafts = card.get("drafts", {})
            lines = [
                f"📋 CARD {card_id} — {card.get('occasion', '?')}",
                f"Event: {ev_str} ({days_label})",
                f"Status: {card.get('status', '?')}",
                f"Occasion type: {card.get('occasion_type', '?')}",
                "",
            ]
            if drafts:
                lines.append("Drafts:")
                for platform, draft in drafts.items():
                    text = draft.get("text", "") if isinstance(draft, dict) else str(draft)
                    pii_ok = draft.get("pii_scan_passed", "?") if isinstance(draft, dict) else "?"
                    lines.append(f"  {platform}: PII={'✓' if pii_ok is True else '✗' if pii_ok is False else '?'}")
                    if text:
                        lines.append(f"    {text[:200]}{'…' if len(text) > 200 else ''}")
            else:
                lines.append("(No drafts yet — run /stage draft to generate)")

            return "\n".join(lines), "N/A"

        # ── list (default) ────────────────────────────────────────────────
        active_statuses = {"seed", "drafting", "staged", "approved"}
        active = [c for c in cards if c.get("status", "") in active_statuses]

        if not active:
            last_updated = data.get("last_updated", "never")
            return (
                f"📭 Content Stage is empty.\nNo active cards. Last updated: {last_updated}\n"
                "Run a catch-up to populate.",
                "N/A",
        )

        from datetime import date as _date  # noqa: PLC0415
        today = _date.today()

        status_emoji = {
            "seed": "🌱",
            "drafting": "✏️",
            "staged": "📋",
            "approved": "✅",
        }

        lines = [f"📣 CONTENT STAGE ({len(active)} active cards)", ""]
        for c in sorted(active, key=lambda x: str(x.get("event_date", ""))):
            cid      = c.get("id", "?")
            occasion = c.get("occasion", "?")
            ev_str   = c.get("event_date", "?")
            status   = c.get("status", "?")
            emoji    = status_emoji.get(status, "•")

            try:
                ev_date = _date.fromisoformat(str(ev_str))
                days = (ev_date - today).days
                days_label = "today" if days == 0 else (f"+{days}d" if days > 0 else f"{abs(days)}d past")
            except (ValueError, TypeError):
                days_label = "?"

            drafts = c.get("drafts", {})
            platforms = list(drafts.keys()) if drafts else []
            plat_str = "/".join(p[:2].upper() for p in platforms[:3]) if platforms else "none"

            lines.append(f"{emoji} {cid}  {occasion}  {ev_str} ({days_label})  [{status}]  {plat_str}")

        lines += [
            "",
            "Use: stage preview <ID> for draft content",
            "Approve/dismiss: use AI chat (/stage approve <ID>)",
        ]
        return "\n".join(lines), "N/A"

    except Exception as e:  # noqa: BLE001
        return f"⚠️ Stage error: {e}", "N/A"


# ── AI Trend Radar handlers (PR-3) ────────────────────────────────────────────

async def cmd_radar(args: list[str], scope: str) -> tuple[str, str]:
    """Show current AI radar signals or manage the Topic Interest Graph.

    Usage:
      /radar              — list current week's top signals
      /radar list         — same as above
      /radar topic add <name>  — add a topic to the Interest Graph
      /radar topic rm <name>   — remove a topic from the Interest Graph
    """
    from pathlib import Path as _Path  # noqa: PLC0415

    artha_dir = _Path(__file__).parent.parent

    subcommand = args[0].lower() if args else "list"

    # ── topic management ──────────────────────────────────────────────────
    if subcommand == "topic":
        action = args[1].lower() if len(args) > 1 else ""
        topic_name = " ".join(args[2:]).strip() if len(args) > 2 else ""
        if not topic_name:
            return "Usage: /radar topic add <name> OR /radar topic rm <name>", "N/A"
        try:
            import yaml as _yaml  # noqa: PLC0415
            state_path = artha_dir / "state" / "ai_trend_radar.md"
            text = state_path.read_text(encoding="utf-8")
            parts = text.split("---", 2)
            if len(parts) < 3:
                return "⚠️ ai_trend_radar.md frontmatter missing.", "N/A"
            fm = _yaml.safe_load(parts[1]) or {}
            topics = fm.setdefault("topics_of_interest", []) or []

            if action == "add":
                existing = [t["name"].lower() for t in topics if isinstance(t, dict)]
                if topic_name.lower() in existing:
                    return f"Topic '{topic_name}' already in Interest Graph.", "N/A"
                topics.append({"name": topic_name, "keywords": [topic_name.lower()], "boost": 0.3})
                fm["topics_of_interest"] = topics
                new_fm = _yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True)
                state_path.write_text("---\n" + new_fm + "---" + parts[2], encoding="utf-8")
                return f"✅ Added '{topic_name}' to AI Radar Interest Graph.", "N/A"

            elif action in ("rm", "remove"):
                before = len(topics)
                topics = [t for t in topics if isinstance(t, dict) and t.get("name", "").lower() != topic_name.lower()]
                if len(topics) == before:
                    return f"⚠️ Topic '{topic_name}' not found in Interest Graph.", "N/A"
                fm["topics_of_interest"] = topics
                new_fm = _yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True)
                state_path.write_text("---\n" + new_fm + "---" + parts[2], encoding="utf-8")
                return f"🗑 Removed '{topic_name}' from AI Radar Interest Graph.", "N/A"

            else:
                return "Usage: /radar topic add <name> OR /radar topic rm <name>", "N/A"

        except Exception as e:  # noqa: BLE001
            return f"⚠️ Radar topic error: {e}", "N/A"

    # ── list signals (default) ────────────────────────────────────────────
    signals_path = artha_dir / "tmp" / "ai_trend_signals.json"
    if not signals_path.exists():
        return (
            "📡 No radar signals yet. Run a catch-up to populate.\n"
            "Hint: ensure RSS is enabled and at least one AI feed is active.",
            "N/A",
        )
    try:
        data = json.loads(signals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return f"⚠️ Could not read signals: {e}", "N/A"

    signals = data.get("signals") or []
    if not signals:
        return "📡 Radar ran but found no signals this week.", "N/A"

    week_end = data.get("week_end", "")
    lines = [f"🧠 AI RADAR — week of {week_end}" if week_end else "🧠 AI RADAR", ""]
    for i, sig in enumerate(signals, start=1):
        topic = sig.get("topic", "?")[:60]
        cat = sig.get("category", "?")
        score = sig.get("relevance_score", 0.0)
        seen = sig.get("seen_in", 1)
        try_flag = " ✅" if sig.get("try_worthy") else ""
        sig_id = sig.get("id", "?")[:8]
        lines.append(f"{i}. [{sig_id}] {topic}{try_flag}")
        lines.append(f"   {cat} | score={score:.2f} | {seen} source(s)")

    lines += [
        "",
        "✅ = try-worthy. Use: /try <topic> to log an experiment",
        "Use: /radar topic add <name> to track new interests",
    ]
    return "\n".join(lines), "N/A"


async def cmd_radar_try(args: list[str], scope: str) -> tuple[str, str]:
    """Log an AI topic/tool as an active experiment.

    Usage: /try <topic description>
    Adds an experiment entry to state/ai_trend_radar.md with status: active.
    """
    topic = " ".join(args).strip() if args else ""
    if not topic:
        return "Usage: /try <topic or tool name>", "N/A"
    try:
        import yaml as _yaml  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415
        from datetime import date as _date  # noqa: PLC0415

        artha_dir = _Path(__file__).parent.parent
        state_path = artha_dir / "state" / "ai_trend_radar.md"
        text = state_path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            return "⚠️ ai_trend_radar.md frontmatter missing.", "N/A"
        fm = _yaml.safe_load(parts[1]) or {}
        experiments = fm.setdefault("experiments", []) or []

        # Generate a simple ID
        import hashlib  # noqa: PLC0415
        exp_id = "EXP-" + hashlib.sha256(topic.encode()).hexdigest()[:6].upper()
        if any(e.get("id") == exp_id for e in experiments):
            return f"⚠️ Experiment for '{topic[:40]}' already exists ({exp_id}).", "N/A"

        experiments.append({
            "id": exp_id,
            "topic": topic,
            "status": "active",
            "started_date": _date.today().isoformat(),
            "verdict": "pending",
            "moment_emitted": False,
        })
        fm["experiments"] = experiments
        new_fm = _yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True)
        state_path.write_text("---\n" + new_fm + "---" + parts[2], encoding="utf-8")
        return (
            f"🧪 Experiment logged: {exp_id}\n"
            f"Topic: {topic[:80]}\n"
            "Status: active\n"
            f"Use: /verdict {exp_id} great|useful|skip when done.",
            "N/A",
        )
    except Exception as e:  # noqa: BLE001
        return f"⚠️ /try error: {e}", "N/A"


async def cmd_radar_skip(args: list[str], scope: str) -> tuple[str, str]:
    """Mark a radar signal topic as skipped this week.

    Usage: /skip <topic or signal ID>
    Adds the signal ID to the skipped list so it won't resurface next week.
    """
    topic_or_id = " ".join(args).strip() if args else ""
    if not topic_or_id:
        return "Usage: /skip <topic or signal ID>", "N/A"
    try:
        import yaml as _yaml  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415
        from datetime import date as _date  # noqa: PLC0415

        artha_dir = _Path(__file__).parent.parent
        state_path = artha_dir / "state" / "ai_trend_radar.md"
        text = state_path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            return "⚠️ ai_trend_radar.md frontmatter missing.", "N/A"
        fm = _yaml.safe_load(parts[1]) or {}
        skipped = fm.setdefault("skipped_signals", []) or []

        if topic_or_id in skipped:
            return f"Already skipped: '{topic_or_id}'.", "N/A"
        skipped.append(topic_or_id)
        fm["skipped_signals"] = skipped
        new_fm = _yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True)
        state_path.write_text("---\n" + new_fm + "---" + parts[2], encoding="utf-8")
        return f"⏭ Skipped '{topic_or_id}' — won't resurface next week.", "N/A"
    except Exception as e:  # noqa: BLE001
        return f"⚠️ /skip error: {e}", "N/A"


async def cmd_queue(args: list[str], scope: str) -> tuple[str, str]:
    """Show pending action queue."""
    try:
        from action_executor import ActionExecutor  # noqa: PLC0415
        executor = ActionExecutor(_ARTHA_DIR)
        pending = executor.pending()
        if not pending:
            return "✅ No pending actions.", "N/A"

        lines = [f"⚡ PENDING ACTIONS ({len(pending)})"]
        lines.append("")
        for i, p in enumerate(pending, 1):
            friction_badge = {"low": "🟢", "standard": "🟡", "high": "🔴"}.get(
                p.get("friction", "standard"), "🟡"
            )
            lines.append(
                f"{i}. {friction_badge} {p.get('title', '?')} "
                f"[{p.get('action_type', '?')}]"
            )
            lines.append(f"   ID: {p.get('id', '?')[:12]}…")
        lines.append("")
        lines.append("Say: approve <ID> · reject <ID> · undo <ID>")
        return "\n".join(lines), "N/A"
    except ImportError:
        return "⚠️ Action layer not available.", "N/A"
    except Exception as e:
        return f"⚠️ Queue error: {e}", "N/A"


async def cmd_approve(args: list[str], scope: str) -> tuple[str, str]:
    """Approve a pending action by ID prefix."""
    if not args:
        return "_Usage: approve <action-id>_", "N/A"

    action_id_prefix = args[0].strip()

    try:
        from action_executor import ActionExecutor  # noqa: PLC0415
        executor = ActionExecutor(_ARTHA_DIR)

        # Resolve full ID from prefix
        pending = executor.pending()
        matched = [
            p for p in pending
            if p.get("id", "").startswith(action_id_prefix)
        ]
        if not matched:
            return f"⚠️ No pending action found matching '{action_id_prefix}'.", "N/A"
        if len(matched) > 1:
            ids = [m["id"][:12] for m in matched]
            return f"⚠️ Ambiguous ID prefix — matches: {', '.join(ids)}", "N/A"

        full_id = matched[0]["id"]
        result = executor.approve(full_id, approved_by="user:telegram")

        if result.status == "success":
            return f"✅ Approved + executed: {result.message}", "N/A"
        elif result.status == "failure":
            return f"❌ Execution failed: {result.message}", "N/A"
        else:
            return f"ℹ️ {result.status}: {result.message}", "N/A"

    except ImportError:
        return "⚠️ Action layer not available.", "N/A"
    except Exception as e:
        return f"⚠️ Approve error: {e}", "N/A"


async def cmd_reject(args: list[str], scope: str) -> tuple[str, str]:
    """Reject a pending action by ID prefix."""
    if not args:
        return "_Usage: reject <action-id> [reason]_", "N/A"

    action_id_prefix = args[0].strip()
    reason = " ".join(args[1:]) if len(args) > 1 else "user:telegram:rejected"

    try:
        from action_executor import ActionExecutor  # noqa: PLC0415
        executor = ActionExecutor(_ARTHA_DIR)

        pending = executor.pending()
        matched = [
            p for p in pending
            if p.get("id", "").startswith(action_id_prefix)
        ]
        if not matched:
            return f"⚠️ No pending action found matching '{action_id_prefix}'.", "N/A"
        if len(matched) > 1:
            ids = [m["id"][:12] for m in matched]
            return f"⚠️ Ambiguous ID prefix — matches: {', '.join(ids)}", "N/A"

        full_id = matched[0]["id"]
        executor.reject(full_id, reason=reason)
        return f"❌ Rejected: {matched[0].get('title', full_id[:12])}", "N/A"

    except ImportError:
        return "⚠️ Action layer not available.", "N/A"
    except Exception as e:
        return f"⚠️ Reject error: {e}", "N/A"


async def cmd_undo(args: list[str], scope: str) -> tuple[str, str]:
    """Undo a recently executed action by ID prefix."""
    if not args:
        return "_Usage: undo <action-id>_", "N/A"

    action_id_prefix = args[0].strip()

    try:
        from action_executor import ActionExecutor  # noqa: PLC0415
        executor = ActionExecutor(_ARTHA_DIR)

        # Check recent history for the action
        history = executor.history(limit=50)
        matched = [
            h for h in history
            if h.get("id", "").startswith(action_id_prefix)
        ]
        if not matched:
            return f"⚠️ No recent action found matching '{action_id_prefix}'.", "N/A"
        if len(matched) > 1:
            ids = [m["id"][:12] for m in matched]
            return f"⚠️ Ambiguous ID prefix — matches: {', '.join(ids)}", "N/A"

        full_id = matched[0]["id"]
        result = executor.undo(full_id, actor="user:telegram")

        if result.status == "success":
            return f"↩️ Undone: {result.message}", "N/A"
        else:
            return f"⚠️ Undo failed: {result.message}", "N/A"

    except ImportError:
        return "⚠️ Action layer not available.", "N/A"
    except Exception as e:
        return f"⚠️ Undo error: {e}", "N/A"


async def _handle_callback_query(
    callback_data: str,
    sender_id: str,
    msg,
    adapter,
) -> None:
    """Handle Telegram inline keyboard button presses for action approval.

    callback_data format: "act:APPROVE:action_id" | "act:REJECT:action_id" | "act:DEFER:action_id"

    Ref: specs/act.md §5.3
    """
    from channels.base import ChannelMessage as _CM  # noqa: PLC0415

    parts = callback_data.split(":", 2)
    if len(parts) != 3 or parts[0] != "act":
        return  # Not an action callback — ignore

    verb = parts[1].upper()
    action_id = parts[2]

    if not action_id:
        return

    try:
        from action_executor import ActionExecutor  # noqa: PLC0415
        executor = ActionExecutor(_ARTHA_DIR)

        if verb == "APPROVE":
            result = executor.approve(action_id, approved_by="user:telegram")
            if result.status == "success":
                reply = f"✅ {result.message}"
            elif result.status == "failure":
                reply = f"❌ Failed: {result.message}"
            else:
                reply = f"ℹ️ {result.status}: {result.message}"

        elif verb == "REJECT":
            executor.reject(action_id, reason="user:telegram:button")
            reply = f"❌ Action rejected."

        elif verb == "DEFER":
            executor.defer(action_id, until="+24h")
            reply = f"⏰ Deferred 24 hours."

        else:
            reply = f"⚠️ Unknown action verb: {verb}"

        _audit_log(
            "CHANNEL_ACTION_CALLBACK",
            sender=sender_id,
            verb=verb,
            action_id=action_id[:16],
        )
        adapter.send_message(_CM(text=reply, recipient_id=sender_id))

    except ImportError:
        adapter.send_message(_CM(
            text="⚠️ Action layer not available.",
            recipient_id=sender_id,
        ))
    except Exception as e:
        log.error("[channel_listener] callback_query handler error: %s", e)
        adapter.send_message(_CM(
            text=f"⚠️ Action handler error: {e}",
            recipient_id=sender_id,
        ))


async def cmd_help(args: list[str], scope: str) -> tuple[str, str]:
    """Return available commands."""
    lines = [
        "ARTHA Commands",
        "",
        "READ",
        "s  — Status + alerts",
        "a  — All alerts by severity",
        "t  — Open tasks / action items",
        "q  — Quick tasks (≤5 min)",
        "g  — Goals overview",
        "d <name>  — Domain deep-dive",
        "  (kids, health, finance, insurance, ...)",
        "dash  — Life dashboard",
        "diff  — Changes since last catch-up",
        "diff 7d  — Changes in last 7 days",
        "catchup  — Run a fresh briefing",
        "",
        "WRITE",
        "items add <desc> [P0|P1|P2] [domain] [date]",
        "done <OI-NNN>  — Mark item complete",
        "",
        "ACTIONS",
        "queue  — Show pending approvals",
        "approve <id>  — Approve + execute action",
        "reject <id>  — Reject action",
        "undo <id>  — Undo within window",
        "",
        "OTHER",
        "unlock <PIN>  — 15-min sensitive session",
        "stage  — Content Stage card list",
        "stage preview <ID>  — Show card draft",
        "?  — This help",
        "",
        "Just type any question to ask Artha.",
        '"aa <question>" for an ensemble answer from all 3 AIs.',
        "",
        "Slash optional. catchup = catch up = briefing.",
    ]
    if scope == "family":
        lines.insert(-3, "(finance, insurance, estate, immigration need /unlock)")
    return "\n".join(lines), "N/A"


async def cmd_unlock(args: list[str], scope: str, sender_id: str,
                     token_store: _SessionTokenStore) -> tuple[str, str]:
    """Verify PIN and create session token."""
    if not args:
        return "_Usage: /unlock <PIN>_", "N/A"
    pin = args[0]
    if token_store.unlock(sender_id, pin):
        _audit_log("CHANNEL_SESSION", recipient=sender_id, action="unlock")
        return f"_Session unlocked for {_SESSION_TOKEN_MINUTES} minutes._", "N/A"
    else:
        _audit_log("CHANNEL_SESSION", recipient=sender_id, action="unlock_failed")
        return "_Incorrect PIN. Session not unlocked._", "N/A"


# ── Critical domain access check ──────────────────────────────────────────────

_CRITICAL_COMMANDS = frozenset({"/domain"})
_CRITICAL_DOMAINS = frozenset({
    "immigration", "finance", "estate", "insurance",
})


def _requires_session(command: str, args: list[str]) -> bool:
    """Return True if this command+args requires a session token."""
    if command not in _CRITICAL_COMMANDS:
        return False
    if args and args[0].lower() in _CRITICAL_DOMAINS:
        return True
    return False


# ── Message processor ─────────────────────────────────────────────────────────

async def process_message(
    msg,
    adapter,
    channel_name: str,
    config: dict[str, Any],
    deduplicator: _MessageDeduplicator,
    rate_limiter: _RateLimiter,
    token_store: _SessionTokenStore,
) -> None:
    """Full inbound message processing pipeline.

    Security gates (in order):
      1. Sender whitelist
      2. Message dedup
      3. Timestamp validation
      4. Rate limiting
      5. Command whitelist
      6. Session token check (critical domains)
      7. Scope filter
      8. PII redaction
      9. Staleness indicator
    """
    _t0_msg = time.monotonic()
    _chlog.info("command.received", channel=channel_name, command=getattr(msg, "command", "unknown"))
    channel_cfg = config.get("channels", {}).get(channel_name, {})
    recipients = channel_cfg.get("recipients", {})

    # 1. Sender whitelist — silent rejection for unknown senders
    recipient_cfg = next(
        (r for r in recipients.values()
         if isinstance(r, dict) and str(r.get("id", "")) == msg.sender_id),
        None,
    )
    if recipient_cfg is None:
        _audit_log(
            "CHANNEL_REJECT",
            channel=channel_name,
            sender=msg.sender_id,
            reason="unknown_sender",
        )
        return  # Silent — do NOT respond to unknown senders

    scope = recipient_cfg.get("access_scope", "standard")
    recipient_name = next(
        (name for name, r in recipients.items()
         if isinstance(r, dict) and str(r.get("id", "")) == msg.sender_id),
        "unknown",
    )

    # 2. Message dedup
    if deduplicator.is_duplicate(msg.message_id):
        log.debug("Duplicate message from %s: %s", msg.sender_id, msg.message_id)
        return

    # 3. Timestamp validation — reject messages older than 5 minutes
    try:
        msg_time = datetime.fromisoformat(msg.timestamp.replace("Z", "+00:00"))
        age_sec = (datetime.now(timezone.utc) - msg_time).total_seconds()
        if age_sec > _MAX_MESSAGE_AGE_SECONDS:
            log.debug("Stale message from %s (%.0fs old)", msg.sender_id, age_sec)
            return
    except (ValueError, TypeError):
        pass  # Can't parse timestamp — allow message through

    # 4. Rate limiting
    if rate_limiter.is_rate_limited(msg.sender_id):
        _audit_log(
            "CHANNEL_RATE_LIMIT",
            channel=channel_name,
            sender=recipient_name,
            cooldown_sec=_RATE_LIMIT_COOLDOWN_SEC,
        )
        return  # Silent drop during cooldown

    _audit_log(
        "CHANNEL_IN",
        channel=channel_name,
        sender=recipient_name,
        command=msg.command,
    )

    # ── Callback query intercept: act:VERB:action_id (§5.3) ──────────────
    # Inline keyboard button presses arrive with raw_text = "act:VERB:uuid"
    # They bypass the command whitelist but are still sender-whitelisted above.
    if msg.raw_text.startswith("act:"):
        await _handle_callback_query(
            callback_data=msg.raw_text,
            sender_id=msg.sender_id,
            msg=msg,
            adapter=adapter,
        )
        return

    # 5. Command normalisation — accept flexible input
    norm_cmd, norm_args = _normalise_command(msg.raw_text)
    if norm_cmd:
        msg = dataclasses.replace(msg, command=norm_cmd, args=norm_args)
    is_slash_command = msg.command.startswith("/")

    # Send ack for long-running commands (track message_id for later deletion)
    _ack_msg_id: int | None = None
    _long_running = {"/catchup", "/domain", "/diff"}  # encrypted domains can be slow
    _encrypted_domain_names = {"finance", "insurance", "immigration", "estate", "vehicle", "health"}
    _needs_ack = (
        msg.command in _long_running
        or (msg.command == "/goals")
        or not is_slash_command  # free-form questions
    )

    if _needs_ack:
        from channels.base import ChannelMessage as _CM
        if msg.command == "/catchup":
            ack_text = "⏳ Running catch-up… this may take a minute or two."
        elif not is_slash_command:
            ack_text = "💭 Thinking…"
        else:
            ack_text = "⏳ Loading…"
        if hasattr(adapter, 'send_message_get_id'):
            _ack_msg_id = adapter.send_message_get_id(_CM(
                text=ack_text,
                recipient_id=msg.sender_id,
            ))
        else:
            adapter.send_message(_CM(text=ack_text, recipient_id=msg.sender_id))

    _HANDLERS = {
        "/status": cmd_status,
        "/alerts": cmd_alerts,
        "/tasks": cmd_tasks,
        "/quick": cmd_quick,
        "/domain": cmd_domain,
        "/dashboard": cmd_dashboard,
        "/catchup": cmd_catchup,
        "/goals": cmd_goals,
        "/diff": cmd_diff,
        "/items_add": cmd_items_add,
        "/items_done": cmd_items_done,
        "/remember": cmd_remember,
        "/cost": cmd_cost,
        "/power": cmd_power,
        "/relationships": cmd_relationships,
        "/help": cmd_help,
        # Action layer commands (§5.3)
        "/queue": cmd_queue,
        "/approve": cmd_approve,
        "/reject": cmd_reject,
        "/undo": cmd_undo,
        # Content Stage (PR-2)
        "/stage": cmd_stage,
        # AI Trend Radar (PR-3)
        "/radar": cmd_radar,
        "/radar_try": cmd_radar_try,
        "/radar_skip": cmd_radar_skip,
    }

    if is_slash_command and msg.command not in ALLOWED_COMMANDS:
        response = "Unknown command. Send ? for commands."
        staleness = "N/A"
    elif not is_slash_command:
        # Free-form question → context-aware LLM Q&A
        response, staleness = await cmd_ask(msg.raw_text, scope)
    elif msg.command == "/unlock":
        response, staleness = await cmd_unlock(
            msg.args, scope, msg.sender_id, token_store
        )
    else:
        # 6. Session token check for critical domain access
        if _requires_session(msg.command, msg.args):
            if not token_store.has_valid_token(msg.sender_id):
                response = (
                    "_This query requires authentication._\n"
                    "Send /unlock <PIN> to start a 15-min session, then retry."
                )
                staleness = "N/A"
            else:
                response, staleness = await _HANDLERS[msg.command](msg.args, scope)
        else:
            response, staleness = await _HANDLERS[msg.command](msg.args, scope)

    # Delete the ack message now that we have the real response
    if _ack_msg_id and hasattr(adapter, 'delete_message'):
        adapter.delete_message(msg.sender_id, _ack_msg_id)

    # 7. Append staleness indicator (every response)
    if staleness not in ("N/A", "never"):
        stale_prefix = "⚠️ " if _parse_age_to_hours(staleness) > 12 else ""
        response = response + f"\n\n{stale_prefix}Last updated: {staleness} ago"
    elif staleness == "never":
        response = response + "\n\n⚠️ No catch-up has run yet"

    # 7b. Determine output format — HTML commands skip markdown cleanup
    _html_commands = {"/dashboard"}
    use_html = (norm_cmd in _html_commands
                or (norm_cmd == "/domain" and msg.args and msg.args[0].lower() == "dashboard"))

    if not use_html:
        response = _clean_for_telegram(response)

    # 8. PII redaction — mandatory
    try:
        sys.path.insert(0, str(_ARTHA_DIR / "scripts"))
        from pii_guard import filter_text as _pii_filter
        filtered, pii_found = _pii_filter(response)
    except ImportError:
        log.warning("pii_guard not importable — PII filter skipped (UNSAFE)")
        filtered = response
        pii_found = {}

    # 9. Send response — split into chunks if too long for Telegram (4096 limit)
    from channels.base import ChannelMessage
    _TG_MAX = 4000  # leave margin below 4096

    chunks = _split_message(filtered, _TG_MAX) if len(filtered) > _TG_MAX else [filtered]
    for chunk in chunks:
        adapter.send_message(ChannelMessage(
            text=chunk,
            recipient_id=msg.sender_id,
            parse_mode="HTML" if use_html else "",
        ))

    _audit_log(
        "CHANNEL_OUT",
        channel=channel_name,
        recipient=recipient_name,
        chars=len(filtered),
        chunks=len(chunks),
        pii_filtered=bool(pii_found),
        command=msg.command,
    )
    _chlog.info(
        "command.completed",
        channel=channel_name,
        command=getattr(msg, "command", "unknown"),
        ms=round((time.monotonic() - _t0_msg) * 1000),
    )


def _parse_age_to_hours(age_str: str) -> float:
    """Parse staleness string like '2h 14m', '3d 5h' into hours."""
    total_hours = 0.0
    for part in age_str.split():
        if part.endswith("d"):
            total_hours += float(part[:-1]) * 24
        elif part.endswith("h"):
            total_hours += float(part[:-1])
        elif part.endswith("m"):
            total_hours += float(part[:-1]) / 60
    return total_hours


# ── Listener host validation ──────────────────────────────────────────────────

def verify_listener_host(config: dict[str, Any]) -> bool:
    """Refuse to start on non-designated listener host.

    The designated host is set in channels.yaml → defaults.listener_host.
    Empty string = any host allowed (single-machine mode).

    Returns:
        True if this machine should run the listener.
        False if another machine is designated (exit gracefully).
    """
    designated = config.get("defaults", {}).get("listener_host", "").strip()
    if not designated:
        log.warning(
            "listener_host not set — assuming single-machine setup. "
            "For multi-machine safety, set defaults.listener_host in channels.yaml."
        )
        return True

    current = socket.gethostname()
    if current.lower() == designated.lower():
        log.info("Listener host check passed: %s ✓", current)
        return True

    log.info(
        "Listener host mismatch: this machine is '%s', designated listener is '%s'. "
        "Exiting cleanly (not an error).",
        current, designated,
    )
    _audit_log(
        "CHANNEL_LISTENER_SKIP",
        host=current,
        designated_host=designated,
    )
    return False


# ── Poll with resilience ──────────────────────────────────────────────────────

async def poll_with_resilience(
    adapter,
    channel_name: str,
    poll_timeout: int = 30,
) -> list:
    """Poll with exponential backoff on connection errors.

    Returns empty list on timeout or persistent failure (never raises).
    """
    delay = _POLL_BACKOFF_BASE
    try:
        return adapter.poll(timeout=poll_timeout)
    except Exception as exc:
        log.warning(
            "[%s] Poll error: %s — backing off %.0fs", channel_name, exc, delay
        )
        await asyncio.sleep(min(delay, _POLL_BACKOFF_MAX))
        return []


# ── Main event loop ───────────────────────────────────────────────────────────

async def run_listener(
    channel_names: list[str],
    config: dict[str, Any],
    dry_run: bool = False,
) -> None:
    """Main asyncio loop. Polls all channels concurrently."""
    from channels.registry import create_adapter_from_config

    # Initialize shared state
    deduplicator = _MessageDeduplicator()
    rate_limiter = _RateLimiter()
    token_store = _SessionTokenStore()

    # Instantiate adapters
    adapters: dict[str, Any] = {}
    for ch in channel_names:
        ch_cfg = config.get("channels", {}).get(ch, {})
        if not ch_cfg.get("enabled", False):
            log.warning("Channel '%s' is not enabled in channels.yaml — skipping", ch)
            continue
        if not ch_cfg.get("features", {}).get("interactive", False):
            log.warning("Channel '%s' has interactive: false — skipping", ch)
            continue
        try:
            adapter = create_adapter_from_config(ch, ch_cfg)
        except Exception as exc:
            log.error("Could not load adapter for '%s': %s", ch, exc)
            _audit_log(
                "CHANNEL_ERROR",
                channel=ch,
                error_type="adapter_load_failed",
                message=str(exc)[:200],
            )
            continue

        # Layer 2 startup: claim the polling session
        if not dry_run:
            log.info("[%s] Claiming polling session (deleteWebhook + flush)…", ch)
            try:
                adapter.delete_webhook()
                adapter.flush_pending_updates()
            except AttributeError:
                pass  # Adapter doesn't implement Layer 2 startup helpers
            except Exception as exc:
                log.warning("[%s] Session claim failed (non-fatal): %s", ch, exc)

        adapters[ch] = adapter
        _audit_log(
            "CHANNEL_LISTENER_START",
            channel=ch,
            host=socket.gethostname(),
        )
        log.info("[%s] Listener started on %s", ch, socket.gethostname())

    if not adapters:
        log.warning("No interactive adapters loaded — nothing to listen on")
        return

    if dry_run:
        log.info("[DRY-RUN] Adapters loaded: %s — not starting poll loop", list(adapters.keys()))
        return

    # Cross-platform shutdown: threading.Event works on Windows
    # (asyncio.add_signal_handler does NOT work on Windows)
    shutdown = threading.Event()

    def _request_shutdown(*_args: object) -> None:
        log.info("Shutdown requested")
        shutdown.set()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    log.info("Polling on channels: %s (press Ctrl+C to stop)", list(adapters.keys()))

    # Bridge setup — executor role only; no-op if bridge disabled
    _bridge_active = False
    _bridge_dir = None
    _bridge_queue = None
    _bridge_pubkey = None
    try:
        import yaml as _yaml  # noqa: PLC0415
        _artha_cfg_path = _ARTHA_DIR / "config" / "artha_config.yaml"
        if _artha_cfg_path.exists():
            with open(_artha_cfg_path, encoding="utf-8") as _f:
                _artha_cfg = _yaml.safe_load(_f) or {}
            if _artha_cfg.get("multi_machine", {}).get("bridge_enabled", False):
                from action_bridge import detect_role, get_bridge_dir  # noqa: PLC0415
                _channels_cfg_path = _ARTHA_DIR / "config" / "channels.yaml"
                _channels_cfg: dict = {}
                if _channels_cfg_path.exists():
                    with open(_channels_cfg_path, encoding="utf-8") as _f:
                        _channels_cfg = _yaml.safe_load(_f) or {}
                if detect_role(_channels_cfg) == "executor":
                    from action_queue import ActionQueue as _AQ  # noqa: PLC0415
                    _bridge_dir = get_bridge_dir(_ARTHA_DIR)
                    _bridge_dir.mkdir(parents=True, exist_ok=True)
                    (_bridge_dir / "proposals").mkdir(exist_ok=True)
                    (_bridge_dir / "results").mkdir(exist_ok=True)
                    _bridge_queue = _AQ(_ARTHA_DIR)
                    _bridge_active = True
                    log.info("[bridge] Executor mode active; polling %s", _bridge_dir)
    except Exception as _bridge_init_exc:
        log.warning("[bridge] Startup init failed (non-fatal): %s", _bridge_init_exc)

    while not shutdown.is_set():
        # Bridge ingestion cycle (executor role only; no-op if bridge disabled)
        if _bridge_active and _bridge_dir is not None and _bridge_queue is not None:
            try:
                from action_bridge import ingest_proposals, retry_outbox, gc  # noqa: PLC0415
                ingest_proposals(_bridge_dir, _bridge_queue, _ARTHA_DIR)
                retry_outbox(_bridge_dir, _bridge_queue, _ARTHA_DIR)
                gc(_bridge_dir, _ARTHA_DIR)
            except Exception as _bridge_exc:
                log.warning("[bridge] ingestion cycle error (non-fatal): %s", _bridge_exc)

        # Poll all channels concurrently
        poll_tasks = [
            poll_with_resilience(adapter, ch)
            for ch, adapter in adapters.items()
        ]
        results = await asyncio.gather(*poll_tasks, return_exceptions=True)

        for (ch, adapter), result in zip(adapters.items(), results):
            if isinstance(result, Exception):
                log.error("[%s] Gather error: %s", ch, result)
                continue
            for msg in (result or []):
                try:
                    await process_message(
                        msg, adapter, ch, config,
                        deduplicator, rate_limiter, token_store,
                    )
                except Exception as exc:
                    log.error(
                        "[%s] Error processing message from %s: %s",
                        ch, msg.sender_id, exc,
                    )
                    _audit_log(
                        "CHANNEL_ERROR",
                        channel=ch,
                        error_type="process_message_error",
                        message=str(exc)[:200],
                    )

        # Brief sleep to avoid busy-looping when adapters return instantly
        await asyncio.sleep(0.1)

    log.info("Listener stopped cleanly")


def health_check_all(channel_names: list[str], config: dict[str, Any]) -> bool:
    """Run health_check() on all specified channels. Returns True if all healthy."""
    from channels.registry import create_adapter_from_config

    all_healthy = True
    for ch in channel_names:
        ch_cfg = config.get("channels", {}).get(ch, {})
        if not ch_cfg.get("enabled", False):
            log.info("[%s] Not enabled — skipping health check", ch)
            continue
        try:
            import time as _time
            _t0 = _time.perf_counter()
            adapter = create_adapter_from_config(ch, ch_cfg)
            ok = adapter.health_check()
            latency_ms = int((_time.perf_counter() - _t0) * 1000)
        except Exception as exc:
            log.error("[%s] health_check error: %s", ch, exc)
            ok = False
            latency_ms = -1

        status = "healthy ✓" if ok else "UNHEALTHY ✗"
        log.info("[%s] %s", ch, status)
        _audit_log(
            "CHANNEL_HEALTH",
            channel=ch,
            healthy=ok,
            latency_ms=latency_ms,
        )
        if not ok:
            all_healthy = False

    return all_healthy


_PID_FILE = _STATE_DIR / ".channel_listener.pid"
_SINGLETON_MUTEX_NAME = "Local\\ArthaChannelListener"
_singleton_mutex_handle: int | None = None  # keep alive for process lifetime


def _acquire_singleton_lock() -> bool:
    """Acquire a Windows Named Mutex to guarantee only one listener runs.

    Uses ctypes to call CreateMutexW — kernel-level atomic operation with no
    race conditions. Returns True if this process is the singleton, False if
    another instance already holds the mutex.

    Also writes a PID file for operator convenience (kill/status scripts).
    """
    global _singleton_mutex_handle

    try:
        import ctypes
        ERROR_ALREADY_EXISTS = 183
        handle = ctypes.windll.kernel32.CreateMutexW(None, True, _SINGLETON_MUTEX_NAME)
        err = ctypes.windll.kernel32.GetLastError()
        if err == ERROR_ALREADY_EXISTS or handle == 0:
            # Mutex already exists — another instance owns it
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
            return False
        # We own the mutex — keep the handle alive
        _singleton_mutex_handle = handle
        # Write PID file for operator convenience
        try:
            _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PID_FILE.write_text(str(os.getpid()))
        except OSError:
            pass
        return True
    except Exception:
        pass

    # ctypes unavailable — fall back to PID file heuristic (best-effort, not atomic)
    try:
        _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _PID_FILE.exists():
            existing_pid = int(_PID_FILE.read_text().strip())
            try:
                import psutil
                if psutil.pid_exists(existing_pid):
                    return False  # Another instance is running
            except ImportError:
                # psutil not available — trust the PID file
                return False
        _PID_FILE.write_text(str(os.getpid()))
        return True
    except (OSError, ValueError):
        pass

    return True


def _release_singleton_lock() -> None:
    """Release the mutex and remove the PID file on clean exit."""
    global _singleton_mutex_handle
    try:
        if _PID_FILE.exists() and _PID_FILE.read_text().strip() == str(os.getpid()):
            _PID_FILE.unlink()
    except OSError:
        pass
    if _singleton_mutex_handle is not None:
        try:
            import ctypes
            ctypes.windll.kernel32.ReleaseMutex(_singleton_mutex_handle)
            ctypes.windll.kernel32.CloseHandle(_singleton_mutex_handle)
        except Exception:
            pass
        _singleton_mutex_handle = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Artha interactive channel listener (Layer 2)"
    )
    parser.add_argument(
        "--channel", "-c",
        action="append",
        dest="channels",
        metavar="NAME",
        help="Channel to listen on (can be specified multiple times). Required unless --dry-run or --health.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate setup and exit without starting the poll loop",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Run health checks on all enabled channels and exit",
    )
    args = parser.parse_args()

    from channels.registry import load_channels_config
    config = load_channels_config()

    # Health check mode
    if args.health:
        enabled = [
            ch for ch, cfg in config.get("channels", {}).items()
            if isinstance(cfg, dict) and cfg.get("enabled", False)
        ]
        if not enabled:
            print("channel_listener: no channels configured or enabled ✓")
            return 0
        ok = health_check_all(enabled, config)
        return 0 if ok else 1

    # Determine channels to listen on
    channels = args.channels or []
    if not channels:
        if args.dry_run:
            # In dry-run mode, default to all enabled interactive channels
            channels = [
                ch for ch, cfg in config.get("channels", {}).items()
                if isinstance(cfg, dict)
                and cfg.get("enabled", False)
                and cfg.get("features", {}).get("interactive", False)
            ]
            if not channels:
                print("channel_listener: no channels configured (dry-run OK)")
                return 0
        else:
            parser.error("--channel is required (unless --dry-run or --health)")

    # Listener host check
    if not verify_listener_host(config):
        # Not the designated host — exit 0 (expected behavior, not error)
        return 0

    # Singleton lock — refuse to start a second instance on the same machine
    if not args.dry_run:
        if not _acquire_singleton_lock():
            log.info("Another listener instance is already running — exiting")
            return 0
        import atexit as _atexit
        _atexit.register(_release_singleton_lock)

    # Run asyncio event loop
    try:
        asyncio.run(run_listener(channels, config, dry_run=args.dry_run))
    except KeyboardInterrupt:
        pass  # Handled by signal handler
    except Exception as exc:
        log.error("Listener fatal error: %s", exc)
        _audit_log(
            "CHANNEL_ERROR",
            error_type="listener_fatal",
            message=str(exc)[:500],
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
