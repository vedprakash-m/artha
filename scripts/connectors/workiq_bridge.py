"""
scripts/connectors/workiq_bridge.py — WorkIQ multi-query connector.

Fetches M365 corporate data (calendar, email, Teams, people, documents)
via WorkIQ (npx @microsoft/workiq), which bypasses the Graph API 403 blocks
on managed corporate tenants.

Modes:
  calendar  — corporate calendar events (default: 7-day window)
  email     — inbox triage with priority + needs_response classification
  teams     — Teams DMs + channel messages needing action
  people    — person profile lookup with collaboration context  (Wave 2)
  documents — recently edited docs and shared Loop pages  (Wave 2)

Handler contract: implements fetch() and health_check() per connectors/base.py.

Auth: method=none — WorkIQ manages its own M365 enterprise auth via npx.

Cache: tmp/.workiq_cache.json — tiered TTL per mode
  calendar:  4h    email: 1h    teams: 2h    people: 7d    documents: 4h

Ref: specs/work-domain-assessment.md §18.1
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)

WORKIQ_VERSION_PIN = "0.x"
SUBPROCESS_TIMEOUT = 90  # seconds — WorkIQ queries take 37-72s each
PARSE_RETRY_ONCE = True  # retry once with explicit format reminder if 0 items parsed

# Cache TTLs per mode (seconds)
_CACHE_TTL: dict[str, int] = {
    "calendar":  4 * 3600,    # 4 hours
    "email":     1 * 3600,    # 1 hour
    "teams":     2 * 3600,    # 2 hours
    "teams_ai":  6 * 3600,    # 6 hours — AI channel scan, less volatile
    "people":    7 * 86400,   # 7 days
    "documents": 4 * 3600,    # 4 hours
}

# Target channels for teams_ai mode — queried individually for best WorkIQ results
_TEAMS_AI_CHANNELS: list[str] = [
    "AI Tools - SIG",
    "AI Learning Champs",
    "XStore AI Enthusiasts",
    "Connect with an AI Pioneer",
    "GitHub Copilot at Microsoft",
    "Small Language Model Forum",
]

# Query templates — NO user-specific data embedded here.
# All user context comes from user_profile.yaml or runtime parameters.
_QUERIES: dict[str, str] = {
    "calendar": (
        "List all my calendar events from {start_date} through {end_date}. "
        "Format each event as one line: "
        "DATE | START_TIME | END_TIME | TITLE | ORGANIZER | LOCATION | TEAMS(yes/no)"
    ),
    "email": (
        "List emails in my inbox from the last {lookback} hours that need a response from me. "
        "Format as one line per email: SENDER | SUBJECT | RECEIVED_DATE | NEEDS_RESPONSE(yes/no)"
    ),
    "teams": (
        "List my Teams messages from the last {lookback} hours that need my attention. "
        "Format as one line per message: SENDER | CHANNEL_OR_DM | MESSAGE_PREVIEW | NEEDS_ACTION(yes/no)"
    ),
    "teams_ai": (
        "What were the most recent messages posted in the {channel_name} channel "
        "in Teams during the last {lookback_days} days? Show up to 15 messages. "
        "Format each as one line: "
        "SENDER | MESSAGE_DATE(YYYY-MM-DD) | TOPIC_OR_SUBJECT | VERBATIM_FIRST_SENTENCE | TEAMS_PERMALINK_OR_LINK_SHARED"
        "\nIMPORTANT: VERBATIM_FIRST_SENTENCE must be a direct quote from the message, not a paraphrase."
    ),
    "people": (
        "Who is {person_name}? Include: job title, department, manager, "
        "how we have collaborated recently, and any shared documents or Loop pages."
    ),
    "documents": (
        "What documents and Loop pages did I edit or were shared with me "
        "in the last {lookback_days} days? "
        "Format as one line per item: TITLE | TYPE | LAST_MODIFIED | LINK"
    ),
}

# ── Rich prompt templates (narrative-first, no pipe-table constraints) ──────
# Used when context_depth="rich" — preserves thread context, decision arcs,
# urgency signals, and attendee roles that pipe-table format destroys.
_RICH_QUERIES: dict[str, str] = {
    "calendar": (
        "List every meeting on my calendar for the next {lookback_days} days. "
        "For each meeting include: title, date/time, duration, organizer name "
        "and role, full attendee list with their roles/titles if known, "
        "agenda items or pre-read links from the invite body, "
        "any decision context or background from prior meetings in the same "
        "series, and whether I accepted/declined/tentatively accepted. "
        "Group by day. Be thorough — include every detail from the invite body."
    ),
    "email": (
        "Summarize every important email thread from the last {lookback_days} days. "
        "For each thread include: subject, sender and all participants, "
        "the full discussion arc (who said what in what order), "
        "any action items requested of me or by me, "
        "urgency signals (deadlines mentioned, escalation language, exec involvement), "
        "and links or attachments referenced. "
        "Skip automated notifications, calendar accepts/declines, and newsletters. "
        "Preserve the narrative — do NOT compress into single-line summaries."
    ),
    "teams": (
        "Summarize every important Teams conversation from the last {lookback_days} days. "
        "For each conversation include: channel or chat name, participants, "
        "the full discussion arc with key quotes, "
        "decisions made or pending, unresolved questions, "
        "@mentions of me, action items assigned to me or by me, "
        "and any links/files shared. "
        "Skip trivial chats (greetings, acknowledgements). "
        "Preserve the narrative flow — do NOT compress into single-line summaries."
    ),
    # people + documents + teams_ai already produce good free-form output
    "people": _QUERIES["people"],
    "documents": _QUERIES["documents"],
    "teams_ai": _QUERIES.get("teams_ai", ""),
}

# Pre-filter patterns for email/Teams noise suppression
_PREFILTER_PATTERNS: list[str] = [
    r"(?i)^no-reply@",
    r"(?i)^noreply@",
    r"(?i)^donotreply@",
    r"(?i)calendar (accept|decline|tentative)",
    r"(?i)^accepted:",
    r"(?i)^declined:",
    r"(?i)^tentative:",
]


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path() -> Path:
    return Path(_REPO_ROOT) / "tmp" / ".workiq_cache.json"


def _load_cache() -> dict:
    p = _cache_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _cache_key_for_mode(mode: str, **params) -> str:
    """Build a cache key that includes mode + relevant params."""
    if mode == "calendar":
        return f"workiq_{mode}_{params.get('start_date', '')}_{params.get('end_date', '')}"
    if mode == "people":
        return f"workiq_{mode}_{params.get('person_name', '').lower().replace(' ', '_')}"
    return f"workiq_{mode}"


def _get_cached(
    mode: str, key: str, cache: dict, *, include_raw: bool = False,
) -> Optional[list] | tuple[Optional[list], Optional[str]]:
    """Return cached data if still fresh, else None.

    When *include_raw* is True, returns ``(records, raw_narrative)`` tuple
    so callers in rich/two-pass mode can access the original WorkIQ prose.
    """
    entry = cache.get(key)
    if not entry:
        return (None, None) if include_raw else None
    try:
        ts = datetime.fromisoformat(entry["cached_at"])
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age < _CACHE_TTL.get(mode, 3600):
            records = entry.get("data", [])
            if include_raw:
                return records, entry.get("raw_narrative")
            return records
    except (KeyError, ValueError):
        pass
    return (None, None) if include_raw else None


def _set_cached(
    mode: str, key: str, data: list, cache: dict, *,
    raw_narrative: Optional[str] = None,
) -> None:
    payload: dict = {
        "mode": mode,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    if raw_narrative is not None:
        payload["raw_narrative"] = raw_narrative
    cache[key] = payload


# ---------------------------------------------------------------------------
# WorkIQ subprocess invocation
# ---------------------------------------------------------------------------

def _find_npx() -> Optional[str]:
    """Locate npx, refreshing Windows PATH from registry if needed."""
    import platform
    search_path = os.environ.get("PATH", "")
    if platform.system() == "Windows":
        try:
            import winreg
            for scope, hive, sub in [
                ("Machine", winreg.HKEY_LOCAL_MACHINE,
                 r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                ("User", winreg.HKEY_CURRENT_USER, r"Environment"),
            ]:
                try:
                    key = winreg.OpenKey(hive, sub)
                    val, _ = winreg.QueryValueEx(key, "Path")
                    winreg.CloseKey(key)
                    for p in val.split(";"):
                        if p and p not in search_path:
                            search_path += ";" + p
                except (OSError, FileNotFoundError):
                    pass
        except ImportError:
            pass
    return shutil.which("npx", path=search_path)


def _ask_workiq(question: str) -> str:
    """Invoke WorkIQ via npx and return the raw stdout response."""
    npx = _find_npx()
    if not npx:
        raise RuntimeError(
            "[workiq_bridge] npx not found — Node.js is required. "
            "Install from https://nodejs.org/"
        )
    result = subprocess.run(
        [npx, "-y", f"@microsoft/workiq@{WORKIQ_VERSION_PIN}", "ask", "-q", question],
        capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
    )
    if result.returncode != 0:
        stderr_snippet = result.stderr.strip()[:200]
        if "auth" in stderr_snippet.lower() or "login" in stderr_snippet.lower():
            raise RuntimeError(
                "[workiq_bridge] WorkIQ M365 auth expired or not configured. "
                "Run: npx workiq logout  then  npx workiq login"
            )
        raise RuntimeError(
            f"[workiq_bridge] WorkIQ returned non-zero exit ({result.returncode}): "
            f"{stderr_snippet}"
        )
    return result.stdout


# ---------------------------------------------------------------------------
# Parsers per mode
# ---------------------------------------------------------------------------

def _parse_pipe_table(text: str, num_fields: int) -> list[list[str]]:
    """Parse a pipe-delimited table response.  Returns list of field lists."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= num_fields:
            rows.append(parts[:num_fields])
    return rows


def _apply_redact(text: str, keywords: list[str], replacement: str = "[REDACTED]") -> str:
    """Replace keyword substrings with replacement, case-insensitive."""
    for kw in keywords:
        if kw:
            text = re.sub(re.escape(kw), replacement, text, flags=re.IGNORECASE)
    return text


def _parse_calendar(raw: str, redact_kws: list[str]) -> list[dict]:
    """Parse WorkIQ calendar response into structured event records."""
    rows = _parse_pipe_table(raw, 7)
    events = []
    for row in rows:
        date_str, start, end, title, organizer, location, teams_raw = row
        # Skip header rows
        if "DATE" in date_str.upper() and "START" in start.upper():
            continue
        title = _apply_redact(title, redact_kws)
        is_teams = teams_raw.strip().lower() in ("yes", "true", "1")
        # Detect Teams meetings from location string if flag not set
        if not is_teams and location:
            is_teams = bool(re.search(
                r"microsoft teams|teams meeting|online meeting",
                location, re.IGNORECASE,
            ))
        events.append({
            "date": date_str.strip(),
            "start": start.strip(),
            "end": end.strip(),
            "title": title.strip(),
            "organizer": organizer.strip(),
            "location": location.strip(),
            "is_teams": is_teams,
            "source": "workiq",
        })
    return events


def _parse_email(raw: str, redact_kws: list[str]) -> list[dict]:
    """Parse WorkIQ email triage response."""
    rows = _parse_pipe_table(raw, 4)
    emails = []
    for row in rows:
        sender, subject, received, needs_resp = row
        if "SENDER" in sender.upper():
            continue
        # Pre-filter noise
        if any(re.search(pat, sender) for pat in _PREFILTER_PATTERNS):
            continue
        subject = _apply_redact(subject, redact_kws)
        emails.append({
            "sender": sender.strip(),
            "subject": subject.strip(),
            "received": received.strip(),
            "needs_response": needs_resp.strip().lower() in ("yes", "true"),
            "source": "workiq_email",
        })
    return emails


def _parse_teams(raw: str, redact_kws: list[str]) -> list[dict]:
    """Parse WorkIQ Teams message response."""
    rows = _parse_pipe_table(raw, 4)
    messages = []
    for row in rows:
        sender, channel, preview, needs_action = row
        if "SENDER" in sender.upper():
            continue
        preview = _apply_redact(preview, redact_kws)
        messages.append({
            "sender": sender.strip(),
            "channel": channel.strip(),
            "preview": preview.strip(),
            "needs_action": needs_action.strip().lower() in ("yes", "true"),
            "source": "workiq_teams",
        })
    return messages


def _parse_teams_ai(raw: str, redact_kws: list[str],
                    channel_override: str = "") -> list[dict]:
    """Parse WorkIQ Teams AI channel scan response into radar-compatible records.

    Handles 5-field (SENDER|DATE|TOPIC|VERBATIM|URL), 4-field (SENDER|TOPIC|PREVIEW|URL),
    and 3-field (SENDER|TOPIC|PREVIEW) responses. Also handles free-form prose.
    Deduplicates by (sender, topic) before returning.
    """
    if not raw or not raw.strip():
        return []

    messages: list[dict] = []
    channel = channel_override

    # Try 5-field first (new format: SENDER|DATE|TOPIC|VERBATIM|URL)
    rows = _parse_pipe_table(raw, 5)
    for row in rows:
        sender, msg_date, topic, verbatim, url = row
        if "SENDER" in sender.upper() or "DATE" in sender.upper():
            continue
        verbatim = _apply_redact(verbatim, redact_kws)
        topic = _apply_redact(topic, redact_kws)
        # Validate date: must look like YYYY-MM-DD
        clean_date = msg_date.strip() if re.match(r"\d{4}-\d{2}-\d{2}", msg_date.strip()) else ""
        messages.append(_teams_ai_record(sender.strip(), channel, topic.strip(),
                                         verbatim.strip(), url.strip(), clean_date))

    # Try 4-field (SENDER|TOPIC|PREVIEW|URL) — legacy / fallback
    if not messages:
        rows = _parse_pipe_table(raw, 4)
        for row in rows:
            sender, topic, preview, url = row
            if "SENDER" in sender.upper() or "TOPIC" in sender.upper():
                continue
            preview = _apply_redact(preview, redact_kws)
            topic = _apply_redact(topic, redact_kws)
            messages.append(_teams_ai_record(sender.strip(), channel, topic.strip(),
                                             preview.strip(), url.strip(), ""))

    # Try 3-field (SENDER|TOPIC|PREVIEW)
    if not messages:
        rows = _parse_pipe_table(raw, 3)
        for row in rows:
            sender, topic, preview = row
            if "SENDER" in sender.upper():
                continue
            preview = _apply_redact(preview, redact_kws)
            topic = _apply_redact(topic, redact_kws)
            messages.append(_teams_ai_record(sender.strip(), channel, topic.strip(),
                                             preview.strip(), "", ""))

    # Fallback: extract numbered items from prose (1. **Name** — description)
    if not messages:
        for m in re.finditer(
            r"(?:^|\n)\s*\d+\.\s+\*{0,2}(.+?)\*{0,2}\s*(?:\n|$)",
            raw,
        ):
            line = m.group(1).strip()
            if len(line) > 10:
                parts = re.split(r"\s*[—–:]\s*", line, maxsplit=1)
                sender = parts[0].strip("* ") if len(parts) > 1 else ""
                topic = parts[-1].strip("* ")
                messages.append(_teams_ai_record(sender, channel, topic, topic, "", ""))

    # Deduplicate by (sender, topic prefix)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for rec in messages:
        key = (rec.get("from", ""), rec.get("subject", "")[:40])
        if key not in seen:
            seen.add(key)
            deduped.append(rec)

    return deduped


def _teams_ai_record(sender: str, channel: str, topic: str,
                     preview: str, url: str, msg_date: str = "") -> dict:
    """Build a radar-compatible record from a Teams AI channel message.

    msg_date: actual message date (YYYY-MM-DD) from WorkIQ response if available.
              If empty, date_iso is left blank — do NOT substitute run-time clock.
    """
    clean_url = url if url.startswith("http") else ""
    # Confidence: bump to "medium" only if we have a real link (verifiable source)
    confidence = "medium" if clean_url else "low"
    return {
        "subject": topic or preview,
        "from": f"{sender} via {channel}" if sender else channel,
        "body": preview or topic,
        "date_iso": f"{msg_date}T00:00:00Z" if msg_date else "",  # blank if unknown
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "link": clean_url,
        "source": "workiq_teams_ai",
        "summary_source": "workiq_llm",   # marks as AI-summarized, not verbatim
        "verbatim": False,                 # downstream consumers must not treat as quoted
        "confidence": confidence,
        "channel": channel,
        "tag": f"teams:{channel.lower().replace(' ', '_').replace('-', '_')}",
    }


def _parse_people(raw: str, name_query: str) -> list[dict]:
    """Parse WorkIQ person profile response — returns a single-item list."""
    if not raw.strip():
        return []
    return [{
        "name": name_query,
        "profile": raw.strip(),
        "source": "workiq_people",
    }]


def _parse_documents(raw: str, redact_kws: list[str]) -> list[dict]:
    """Parse WorkIQ document activity response."""
    rows = _parse_pipe_table(raw, 4)
    docs = []
    for row in rows:
        title, doc_type, last_modified, link = row
        if "TITLE" in title.upper():
            continue
        title = _apply_redact(title, redact_kws)
        docs.append({
            "title": title.strip(),
            "type": doc_type.strip(),
            "last_modified": last_modified.strip(),
            "link": link.strip(),
            "source": "workiq_documents",
        })
    return docs


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------

def _get_profile_value(path: str, default=None):
    """Read a dot-notation path from user_profile.yaml."""
    try:
        sys.path.insert(0, _SCRIPTS_DIR)
        from profile_loader import load_profile  # type: ignore[import]
        profile = load_profile() or {}
        parts = path.split(".")
        node = profile
        for p in parts:
            if not isinstance(node, dict):
                return default
            node = node.get(p, default)
            if node is None:
                return default
        return node
    except Exception:
        return default


def _redact_keywords() -> list[str]:
    return _get_profile_value("integrations.workiq.redact_keywords", []) or []


def _redact_replacement() -> str:
    return _get_profile_value("integrations.workiq.redact_replacement", "[REDACTED]") or "[REDACTED]"


# ---------------------------------------------------------------------------
# Public connector interface
# ---------------------------------------------------------------------------

def _ask_workiq_mcp(question: str) -> Optional[str]:
    """Direct MCP path for interactive Copilot sessions.

    When running inside a Copilot agent session the MCP tool
    ``workiq-ask_work_iq`` can be invoked directly — bypassing the npx
    subprocess entirely.  This is faster, more reliable, and preserves
    richer response fidelity.

    Returns the raw response string, or *None* if the MCP path is
    unavailable (e.g. running in batch mode from ``work_loop.py``).
    """
    # Stub — the MCP tool is only callable from the Copilot agent runtime.
    # In batch/CLI contexts this always returns None so the subprocess path
    # is used instead.  A future integration layer will detect the runtime
    # and delegate automatically.
    return None


def fetch(
    *,
    since: str = "",
    max_results: int = 200,
    auth_context: dict,
    source_tag: str = "workiq",
    mode: str = "calendar",
    lookback: str = "48",
    lookback_days: int = 7,
    start_date: str = "",
    end_date: str = "",
    person_name: str = "",
    context_depth: str = "brief",
    **kwargs: Any,
) -> Iterator[dict]:
    """Fetch work M365 data via WorkIQ.

    Args:
        mode:          One of: calendar | email | teams | people | documents
        lookback:      Hours to look back (email, teams).  Default "48".
        lookback_days: Days to look back (documents, calendar fallback).
        start_date:    YYYY-MM-DD — calendar range start (default: today).
        end_date:      YYYY-MM-DD — calendar range end (default: today + 6d).
        person_name:   Name to look up (required for mode=people).
        context_depth: "brief" (default, backward-compatible pipe-table) or
                       "rich" (two-pass: narrative + structured).  "rich"
                       caches the full WorkIQ narrative alongside parsed
                       records so downstream consumers can access the
                       uncompressed context.
    """
    redact_kws = _redact_keywords()
    repl = _redact_replacement()

    cache = _load_cache()

    # Resolve calendar date range
    if mode == "calendar":
        today = date.today()
        if not start_date:
            start_date = today.strftime("%Y-%m-%d")
        if not end_date:
            end_date = (today + timedelta(days=lookback_days - 1)).strftime("%Y-%m-%d")

    cache_key = _cache_key_for_mode(
        mode, start_date=start_date, end_date=end_date, person_name=person_name,
    )

    # ------------------------------------------------------------------
    # Determine whether to use rich (narrative-first) or brief (pipe-table)
    # prompts.  Rich mode = two-pass: first a narrative call (cached as
    # raw_narrative), then a structured call for parseable records.
    # ------------------------------------------------------------------
    use_rich = context_depth == "rich" and mode in _RICH_QUERIES
    query_bank = _RICH_QUERIES if use_rich else _QUERIES

    # Try MCP direct path first (interactive sessions only)
    use_mcp = kwargs.get("use_mcp", False)

    # Cache — in rich mode also return any stored narrative
    if use_rich:
        cache_result = _get_cached(mode, cache_key, cache, include_raw=True)
        if cache_result is not None:
            cached_records, _cached_narrative = cache_result
            if cached_records is not None:
                for record in cached_records:
                    yield record
                return
    else:
        cached = _get_cached(mode, cache_key, cache)
        if cached is not None:
            for record in cached:
                yield record
            return

    raw_narrative: Optional[str] = None  # populated by rich pass 1

    def _call_workiq(question: str) -> str:
        """Call WorkIQ via MCP (if available) or subprocess fallback."""
        if use_mcp:
            mcp_result = _ask_workiq_mcp(question)
            if mcp_result is not None:
                return mcp_result
        return _ask_workiq(question)

    # Build query from template
    if mode == "teams_ai":
        # Multi-channel strategy: query each channel separately for better
        # WorkIQ hit rate (combined topic+channel queries return 0 results).
        channels = kwargs.get("channels", _TEAMS_AI_CHANNELS)
        records: list[dict] = []
        for ch_name in channels:
            ch_query = _QUERIES["teams_ai"].format(
                channel_name=ch_name, lookback_days=lookback_days,
            )
            try:
                ch_raw = _call_workiq(ch_query)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                print(f"[workiq_bridge] teams_ai channel {ch_name!r} failed: {exc}",
                      file=sys.stderr)
                continue
            ch_records = _parse_teams_ai(ch_raw, redact_kws, channel_override=ch_name)
            records.extend(ch_records)
    else:
        # Standard single-query modes
        if mode == "calendar":
            query = query_bank["calendar"].format(start_date=start_date, end_date=end_date)
        elif mode == "email":
            query = query_bank["email"].format(lookback=lookback)
        elif mode == "teams":
            query = query_bank["teams"].format(lookback=lookback)
        elif mode == "people":
            if not person_name:
                return
            query = query_bank["people"].format(person_name=person_name)
        elif mode == "documents":
            query = query_bank["documents"].format(lookback_days=lookback_days)
        else:
            raise ValueError(f"[workiq_bridge] Unknown mode: {mode!r}")

        # ---- Two-pass prompting (rich mode) ----
        # Pass 1: narrative call with rich prompt → raw_narrative
        # Pass 2: structured call with pipe-table prompt → records
        if use_rich:
            try:
                raw_narrative = _call_workiq(query)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                print(f"[workiq_bridge] rich pass-1 failed ({mode}): {exc}",
                      file=sys.stderr)
                # Graceful degradation: fall through to structured-only

            # Pass 2 always uses the structured _QUERIES template
            structured_query: str
            if mode == "calendar":
                structured_query = _QUERIES["calendar"].format(
                    start_date=start_date, end_date=end_date)
            elif mode == "email":
                structured_query = _QUERIES["email"].format(lookback=lookback)
            elif mode == "teams":
                structured_query = _QUERIES["teams"].format(lookback=lookback)
            elif mode == "people":
                structured_query = _QUERIES["people"].format(person_name=person_name)
            elif mode == "documents":
                structured_query = _QUERIES["documents"].format(
                    lookback_days=lookback_days)
            else:
                structured_query = query  # fallback

            try:
                raw = _call_workiq(structured_query)
            except RuntimeError as exc:
                print(f"[workiq_bridge] rich pass-2 failed ({mode}): {exc}",
                      file=sys.stderr)
                return
            except subprocess.TimeoutExpired:
                print(
                    f"[workiq_bridge] WorkIQ timed out on pass-2 ({mode})",
                    file=sys.stderr,
                )
                return
        else:
            # ---- Single-pass (brief mode, backward-compatible) ----
            try:
                raw = _call_workiq(query)
            except RuntimeError as exc:
                print(f"[workiq_bridge] fetch failed ({mode}): {exc}",
                      file=sys.stderr)
                return
            except subprocess.TimeoutExpired:
                print(
                    f"[workiq_bridge] WorkIQ timed out after {SUBPROCESS_TIMEOUT}s ({mode})",
                    file=sys.stderr,
                )
                return

        # Parse
        records = []
        if mode == "calendar":
            records = _parse_calendar(raw, redact_kws)
            if not records and raw.strip() and PARSE_RETRY_ONCE:
                # Retry with explicit format reminder
                explicit_query = query + (
                    " IMPORTANT: Do NOT include a header row. "
                    "Each line must have exactly 7 fields separated by pipes."
                )
                try:
                    raw2 = _call_workiq(explicit_query)
                    records = _parse_calendar(raw2, redact_kws)
                    if not records:
                        print(
                            "[workiq_bridge] calendar parse still 0 after retry — "
                            "possible WorkIQ format change. Session skipped.",
                            file=sys.stderr,
                        )
                except (RuntimeError, subprocess.TimeoutExpired):
                    pass
        elif mode == "email":
            records = _parse_email(raw, redact_kws)
        elif mode == "teams":
            records = _parse_teams(raw, redact_kws)
        elif mode == "people":
            records = _parse_people(raw, person_name)
        elif mode == "documents":
            records = _parse_documents(raw, redact_kws)

    if source_tag:
        for r in records:
            r.setdefault("source", source_tag)

    # Cache and yield — include raw_narrative when available
    _set_cached(mode, cache_key, records, cache, raw_narrative=raw_narrative)
    _save_cache(cache)

    for record in records:
        yield record


def health_check(auth_context: dict) -> bool:
    """Verify WorkIQ is available and M365 auth is valid.

    Uses a simple identity check ("What is my name?") as the probe.
    Non-Windows platforms always return True (graceful degradation).
    """
    import platform
    if platform.system() != "Windows":
        return True  # Mac / Linux: graceful skip

    npx = _find_npx()
    if not npx:
        print("[workiq_bridge] health_check: npx not found", file=sys.stderr)
        return False

    try:
        result = subprocess.run(
            [npx, "-y", f"@microsoft/workiq@{WORKIQ_VERSION_PIN}",
             "ask", "-q", "What is my name?"],
            capture_output=True, text=True, timeout=30,
        )
        ok = result.returncode == 0 and len(result.stdout.strip()) > 5
        if not ok:
            print(
                f"[workiq_bridge] health_check failed: {result.stderr.strip()[:120]}",
                file=sys.stderr,
            )
        return ok
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"[workiq_bridge] health_check error: {exc}", file=sys.stderr)
        return False
