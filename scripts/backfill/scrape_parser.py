"""scripts/backfill/scrape_parser.py — Work-scrape corpus parser.

Parses individual work-scrape markdown files into structured data
for the Backfill Engine.  Supports 4 format families discovered across
the 82-file corpus (2024-W34 through 2026-W11).

Format family detection:
  A (early):   2024 W34-W40 — "Key Decisions & Outcomes" section present
  B-early:     2024 W41 – 2025 W8 — mixed ## headings, no Q-numbering
  B-mid:       2025 W9 – W30 — Q4/Q5/Q6-numbered sections
  B-late:      2025 W31 – 2026 W11 — stabilized Q-format + richer people data

Ref: specs/reflection-loop.md §6.2
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class ParsedScrapeWeek:
    """Structured extraction from one work-scrape file."""
    week_id: str                                 # e.g. "2025-W14"
    date_range: str                              # e.g. "Apr 7–11 2025"
    format_family: str                           # "A" | "B-early" | "B-mid" | "B-late"
    source_path: str                             # relative path for citation
    meetings: list[dict[str, Any]] = field(default_factory=list)
    email_items: list[dict[str, Any]] = field(default_factory=list)
    chat_items: list[dict[str, Any]] = field(default_factory=list)
    people_signals: list[dict[str, Any]] = field(default_factory=list)
    key_highlights: list[str] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)
    authored_docs: list[str] = field(default_factory=list)
    extraction_rate: float = 0.0  # 0.0–1.0; fraction of expected sections found


# ---------------------------------------------------------------------------
# Format family detection
# ---------------------------------------------------------------------------

# Markers that distinguish each family
_Q_SECTION = re.compile(r"^## Q[456]\b", re.MULTILINE)
_KEY_DECISIONS_SECTION = re.compile(r"^## Key Decisions", re.MULTILINE | re.IGNORECASE)
_WEEK_HEADER = re.compile(r"^#\s+Week\s+\d+", re.MULTILINE | re.IGNORECASE)
_RICH_PEOPLE = re.compile(
    r"^## (?:People|Stakeholders|New People|Relationship Pulse)",
    re.MULTILINE | re.IGNORECASE,
)
# B-late has a richer stakeholder / relationship section with structured tables
_STAKEHOLDER_TABLE = re.compile(r"^\|\s*Name\s*\|.*Relationship", re.MULTILINE | re.IGNORECASE)


def detect_format_family(content: str) -> str:
    """Detect which of the 4 format families a scrape file belongs to.

    Priority order: A > B-mid > B-late > B-early
    (B-mid/B-late both have Q-sections; B-late is differentiated by richer
    people/stakeholder tables.  A is uniquely identified by the Key Decisions
    heading.)
    """
    if not content:
        return "B-early"  # safe default

    has_key_decisions = bool(_KEY_DECISIONS_SECTION.search(content))
    has_q_sections = bool(_Q_SECTION.search(content))
    has_stakeholder_table = bool(_STAKEHOLDER_TABLE.search(content))

    if has_key_decisions and not has_q_sections:
        return "A"

    if has_q_sections:
        if has_stakeholder_table or has_key_decisions:
            return "B-late"
        # Count Q-section labels to distinguish B-mid (Q4/Q5/Q6 trio) from B-late
        q_labels = set(re.findall(r"^## (Q[456])\b", content, re.MULTILINE))
        # B-mid vs B-late: B-late may have additional sections beyond Q4-Q6
        # Use presence of rich people signal as tiebreaker
        rich_people = bool(_RICH_PEOPLE.search(content))
        if rich_people and len(q_labels) >= 3:
            return "B-late"
        return "B-mid"

    return "B-early"


# ---------------------------------------------------------------------------
# Section extraction helpers
# ---------------------------------------------------------------------------

def _extract_section(text: str, heading_pattern: str) -> str:
    """Return text of a section matching heading_pattern (until next ## or end)."""
    m = re.search(heading_pattern, text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return ""
    start = m.end()
    rest = text[start:]
    end_match = re.search(r"^\s*## ", rest, re.MULTILINE)
    if end_match:
        return rest[: end_match.start()]
    return rest


def _extract_q_section(text: str, q_label: str) -> str:
    """Extract a numbered Q-section (e.g. '## Q4') until the next ## heading."""
    pattern = rf"^## {re.escape(q_label)}\b"
    return _extract_section(text, pattern)


# ---------------------------------------------------------------------------
# Calendar / meetings parsing
# ---------------------------------------------------------------------------

_SELF_NAMES = {"ved", "vemishra", "ved mishra", "vm"}


def _is_self_organizer(name: str) -> bool:
    return name.strip().lower() in _SELF_NAMES or name.strip().lower().startswith("ved")


def _is_ved_marker(line: str) -> bool:
    """Return True if line contains VED-organizer markers."""
    return "🟩" in line or "**(VED)**" in line or "(VED)" in line


def _clean_title(raw: str) -> str:
    """Strip markdown/emoji noise from a meeting title."""
    t = raw.strip().strip("*").strip()
    t = re.sub(r"[🟩❌✅🔴🟡📣📊🔒]", "", t).strip()
    t = re.sub(r"\s+NEW$", "", t).strip()
    return t


def _parse_attendee_count(raw: str) -> int:
    """Extract an integer attendee count from a cell like '26', '~32', '0 (solo)'."""
    m = re.match(r"~?(\d+)", raw.strip())
    return int(m.group(1)) if m else 0


# ---- Strategy 1: Smart markdown-table parser (auto-detects column layout) ----

_COL_TITLE_WORDS = {"meeting", "event", "title"}
_COL_ORG_WORDS = {"organizer"}
_COL_ATT_WORDS = {"attendee", "attendees"}


def _detect_column_mapping(headers: list[str]) -> dict[str, int]:
    """Map normalised header names to column indices."""
    col_map: dict[str, int] = {}
    for i, h in enumerate(headers):
        h_low = h.strip().lower().strip("# ")
        if any(w in h_low for w in _COL_TITLE_WORDS):
            col_map.setdefault("title", i)
        elif any(w in h_low for w in _COL_ORG_WORDS):
            col_map["organizer"] = i
        elif any(w in h_low for w in _COL_ATT_WORDS):
            col_map["attendees"] = i
    return col_map


def _parse_calendar_tables_smart(source: str) -> list[dict[str, Any]]:
    """Parse meeting tables with auto-detected column layout.

    Handles all observed table variants:
      - |#|Title|Organizer|Attendees|Start–End|Notes|
      - |#|Meeting|Time|Duration|Organizer|Attendees|Notes|
      - |Time|Event|Organizer|Attendees|Notes|
    """
    events: list[dict[str, Any]] = []
    headers: Optional[list[str]] = None
    col_map: dict[str, int] = {}

    for line in source.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            headers = None  # table boundary — reset
            continue

        cells = [c.strip() for c in stripped.split("|")[1:-1]]

        # Separator row (---|---)
        if all(re.match(r"^[-:]+$", c) for c in cells if c):
            continue

        # Header row detection: if we haven't locked headers yet
        if headers is None:
            headers = cells
            col_map = _detect_column_mapping(headers)
            continue

        # Data row — need at least a title column
        if "title" not in col_map:
            continue

        ti = col_map["title"]
        if ti >= len(cells):
            continue

        title = _clean_title(cells[ti])
        if not title or len(title) < 3:
            continue

        organizer = ""
        if "organizer" in col_map and col_map["organizer"] < len(cells):
            organizer = cells[col_map["organizer"]].strip().strip("*").strip()

        att = 0
        if "attendees" in col_map and col_map["attendees"] < len(cells):
            att = _parse_attendee_count(cells[col_map["attendees"]])

        ved_org = _is_self_organizer(organizer) or _is_ved_marker(stripped)

        events.append({
            "title": title,
            "organizer": organizer,
            "attendee_count": att,
            "ved_organizer": ved_org,
        })

    return events


# ---- Strategy 2: Numbered-bullet inline ("N. **Title** — time | Organizer: ...") ----

_NUM_BULLET_RE = re.compile(
    r"^\d+\.\s+\*\*(.+?)\*\*",
    re.MULTILINE,
)
_ORG_INLINE = re.compile(r"Organizer:\s+([A-Za-z][A-Za-z .,'\-]+?)\s*(?:\||$)")
_ATT_INLINE = re.compile(r"(\d+)\s+attendees?", re.IGNORECASE)


def _parse_numbered_bullets(source: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for m in _NUM_BULLET_RE.finditer(source):
        title = _clean_title(m.group(1))
        if not title or len(title) < 3:
            continue
        rest = source[m.start():m.start() + 500]  # context window
        org_m = _ORG_INLINE.search(rest)
        att_m = _ATT_INLINE.search(rest)
        organizer = org_m.group(1).strip() if org_m else ""
        att = int(att_m.group(1)) if att_m else 0
        ved_org = _is_self_organizer(organizer) or _is_ved_marker(rest[:200])
        events.append({
            "title": title, "organizer": organizer,
            "attendee_count": att, "ved_organizer": ved_org,
        })
    return events


# ---- Strategy 3: Bold-numbered block ("**N. Title**\n- Organizer: ...\n- Attendees: N") ----

_BOLD_NUM_RE = re.compile(
    r"^\*\*\d+\.\s+(.+?)\*\*\s*$",
    re.MULTILINE,
)
_ATT_LINE = re.compile(r"Attendees\s*(?:\(([^)]+)\))?:\s*(.*)", re.IGNORECASE)


def _parse_bold_numbered_blocks(source: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for m in _BOLD_NUM_RE.finditer(source):
        title = _clean_title(m.group(1))
        if not title or len(title) < 3:
            continue
        # Scan next ~10 lines for Organizer / Attendees
        block = source[m.end():m.end() + 600]
        block_lines = block.split("\n")[:12]
        organizer = ""
        att = 0
        for bl in block_lines:
            bl_s = bl.strip()
            if bl_s.startswith("- Organizer:"):
                organizer = bl_s.split(":", 1)[1].strip()
            att_m = _ATT_LINE.search(bl_s)
            if att_m:
                # e.g. "Attendees (32 total): names" or "Attendees: 26"
                total = att_m.group(1) or att_m.group(2)
                num = re.search(r"(\d+)", total or "")
                if num:
                    att = int(num.group(1))
        ved_org = _is_self_organizer(organizer) or _is_ved_marker(title)
        events.append({
            "title": title, "organizer": organizer,
            "attendee_count": att, "ved_organizer": ved_org,
        })
    return events


# ---- Strategy 4: Dash-bullet multiline ("- **Title**\n  Organizer: Name\n  Attendees: N") ----

_DASH_BULLET_RE = re.compile(
    r"^-\s+\*\*([^*]+)\*\*.*?$",
    re.MULTILINE,
)


def _parse_dash_bullet_multiline(source: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for m in _DASH_BULLET_RE.finditer(source):
        title = _clean_title(m.group(1))
        if not title or len(title) < 3:
            continue
        block = source[m.end():m.end() + 400]
        block_lines = block.split("\n")[:8]
        organizer = ""
        att = 0
        for bl in block_lines:
            bl_s = bl.strip()
            if bl_s.startswith("Organizer:"):
                organizer = bl_s.split(":", 1)[1].strip()
            elif re.match(r"Attendees:\s*(\d+)", bl_s, re.IGNORECASE):
                num = re.search(r"(\d+)", bl_s)
                if num:
                    att = int(num.group(1))
        # Also check inline on same line as title
        rest_line = source[m.start():m.end()]
        org_inline = _ORG_INLINE.search(rest_line)
        if org_inline and not organizer:
            organizer = org_inline.group(1).strip()
        att_inline = _ATT_INLINE.search(rest_line)
        if att_inline and not att:
            att = int(att_inline.group(1))
        ved_org = _is_self_organizer(organizer) or _is_ved_marker(rest_line)
        events.append({
            "title": title, "organizer": organizer,
            "attendee_count": att, "ved_organizer": ved_org,
        })
    return events


# ---- Strategy 5: Dash-bullet inline ("- **Title** — time | Organizer: Name | N attendees") ----

_DASH_INLINE_RE = re.compile(
    r"^-\s+\*\*([^*]+)\*\*\s*[—–\-].*$",
    re.MULTILINE,
)


def _parse_dash_bullet_inline(source: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for m in _DASH_INLINE_RE.finditer(source):
        full_line = m.group(0)
        title = _clean_title(m.group(1))
        if not title or len(title) < 3:
            continue
        org_m = _ORG_INLINE.search(full_line)
        att_m = _ATT_INLINE.search(full_line)
        organizer = org_m.group(1).strip() if org_m else ""
        att = int(att_m.group(1)) if att_m else 0
        ved_org = _is_self_organizer(organizer) or _is_ved_marker(full_line)
        events.append({
            "title": title, "organizer": organizer,
            "attendee_count": att, "ved_organizer": ved_org,
        })
    return events


# ---- Unified calendar dispatcher ----

def _parse_calendar_section(text: str, family: str) -> list[dict[str, Any]]:
    """Extract calendar/meeting entries from a scrape section.

    Tries 5 strategies in order, returns first non-empty result.
    """
    # Locate the calendar section
    if family in ("B-mid", "B-late"):
        section = _extract_q_section(text, "Q4")
        if not section:
            section = _extract_section(text, r"## (?:Calendar|Meetings|Q4)")
        source = section or text
    else:
        source = _extract_section(text, r"## (?:Q4|Calendar|Meetings|Week)")
        if not source:
            source = text

    for strategy in (
        _parse_calendar_tables_smart,
        _parse_numbered_bullets,
        _parse_bold_numbered_blocks,
        _parse_dash_bullet_multiline,
        _parse_dash_bullet_inline,
    ):
        events = strategy(source)
        if events:
            return events

    return []


# ---------------------------------------------------------------------------
# Email parsing
# ---------------------------------------------------------------------------

_EMAIL_SUBJECT_BOLD = re.compile(
    r"\*\*([^*]{5,120})\*\*(?:.*?from\s+([A-Za-z ,.]+?))?(?:\n|$)",
    re.MULTILINE,
)
_EMAIL_BULLET = re.compile(
    r"^-\s+(?:\*\*)?([^\n*]{10,200})(?:\*\*)?(?:\s*—\s*(.{5,100}))?$",
    re.MULTILINE,
)
_ACTION_REQUIRED = re.compile(r"\baction\s+required\b|\breply\s+needed\b|\brespond\b", re.IGNORECASE)
_URGENT_MARKERS = re.compile(r"\burgent\b|\bASAP\b|\bcritical\b|\btime.sensitive\b", re.IGNORECASE)


def _parse_email_section(text: str, family: str) -> list[dict[str, Any]]:
    """Extract email highlights from a scrape section.

    Note: Q6 is 'Files Touched/Shared', NOT emails.
    Emails live in Q2 (Sent), '## Notable Emails', '## Emails Received', etc.
    """
    items: list[dict[str, Any]] = []

    # Try dedicated email headings first
    section = _extract_section(
        text,
        r"## (?:Q2|Notable Emails?|Emails? Received|Sent Emails?|Email|Emails|Key Emails|Received)",
    )
    if not section and family in ("B-mid", "B-late"):
        section = _extract_q_section(text, "Q2")
    # Also try Q6 only if its heading contains "Email" (some early files mixed)
    if not section:
        q6_head = re.search(r"^## Q6[^\n]*(?:Email|email)", text, re.MULTILINE)
        if q6_head:
            section = _extract_section(text, r"## Q6")

    if not section:
        return items

    # Bold subject lines
    for m in _EMAIL_SUBJECT_BOLD.finditer(section):
        subject = m.group(1).strip()
        sender = (m.group(2) or "").strip()
        ctx = m.group(0)
        action_req = bool(_ACTION_REQUIRED.search(ctx))
        urgency = "high" if bool(_URGENT_MARKERS.search(ctx)) else (
            "medium" if action_req else "low"
        )
        items.append({
            "subject": subject,
            "sender": sender,
            "action_required": action_req,
            "urgency": urgency,
        })

    # Bullet fallback
    if not items:
        for m in _EMAIL_BULLET.finditer(section):
            subject = m.group(1).strip()
            ctx = m.group(0)
            action_req = bool(_ACTION_REQUIRED.search(ctx))
            urgency = "high" if bool(_URGENT_MARKERS.search(ctx)) else (
                "medium" if action_req else "low"
            )
            if len(subject) > 8:
                items.append({
                    "subject": subject,
                    "sender": "",
                    "action_required": action_req,
                    "urgency": urgency,
                })

    return items[:30]  # cap to avoid noise


# ---------------------------------------------------------------------------
# Chat parsing
# ---------------------------------------------------------------------------

_CHAT_CHANNEL = re.compile(
    r"\*\*([^*]{3,60})\*\*\s*\(([^)]{2,100})\)",
    re.MULTILINE,
)
_CHAT_1ON1 = re.compile(
    r"\*\*(?:Direct Chat|1:1|1-1)[^*]*↔\s*([A-Z][A-Za-z ]+)\s*\*\*",
)


# ---- Chat subsection patterns ----

# "### 1. Thread Title" or "### Thread Title"
_CHAT_SUBSECTION_RE = re.compile(
    r"^###\s+(?:\d+\.\s+)?(.+?)$",
    re.MULTILINE,
)
# "**Participants:** Name1, Name2, ..."
_PARTICIPANTS_RE = re.compile(
    r"\*\*Participants?:\*\*\s*(.+?)$",
    re.MULTILINE,
)
# "### Chat — Name1 + Name2 + Name3"
_CHAT_NAMED_RE = re.compile(
    r"^###\s+Chat\s*[—–\-]\s*(.+?)$",
    re.MULTILINE,
)
# "- **1:1 — [Speaker Name] ↔ Name**"
_CHAT_1ON1_ARROW = re.compile(
    r"-\s+\*\*1:1\s*[—–\-]\s*.*?↔\s*([A-Z][A-Za-z ]+?)\*\*",
)


def _parse_chat_section(text: str, family: str) -> list[dict[str, Any]]:
    """Extract Teams/chat interactions from a scrape section."""
    items: list[dict[str, Any]] = []

    if family in ("B-mid", "B-late"):
        section = _extract_q_section(text, "Q5")
        if not section:
            section = _extract_section(text, r"## (?:Q5|Chats?|Teams|Channels?)")
    else:
        section = _extract_section(text, r"## (?:Q5|Chats?|Teams|Channels?|Microsoft Teams)")

    if not section:
        return items

    # Strategy 1: Original inline-paren format: **Channel** (P1, P2)
    for m in _CHAT_CHANNEL.finditer(section):
        channel, participants_str = m.groups()
        participants = [p.strip() for p in participants_str.split(",")]
        items.append({
            "channel": channel.strip(),
            "participants": participants,
            "action_required": False,
        })

    # Strategy 2: Subsection headings (### N. Thread Title)
    if not items:
        for m in _CHAT_SUBSECTION_RE.finditer(section):
            heading = m.group(1).strip()
            # Skip noise headings like "Active Threads"
            if heading.lower() in ("active threads", "summary", "overview"):
                continue
            # Look for participants line within next 5 lines
            block = section[m.end():m.end() + 500]
            p_match = _PARTICIPANTS_RE.search(block.split("\n###")[0][:400])
            participants = []
            if p_match:
                participants = [p.strip() for p in p_match.group(1).split(",")]
            items.append({
                "channel": heading,
                "participants": participants,
                "action_required": False,
            })

    # Strategy 3: Named-chat header (### Chat — Name1 + Name2)
    if not items:
        for m in _CHAT_NAMED_RE.finditer(section):
            names_str = m.group(1).strip()
            participants = [n.strip() for n in re.split(r"\s*\+\s*", names_str)]
            items.append({
                "channel": f"Chat: {names_str[:60]}",
                "participants": participants,
                "action_required": False,
            })

    # Strategy 4: 1:1 arrow pattern (- **1:1 — Ved ↔ Name**)
    for m in _CHAT_1ON1_ARROW.finditer(section):
        name = m.group(1).strip()
        if not any(name in i.get("channel", "") for i in items):
            items.append({
                "channel": f"1:1 with {name}",
                "participants": [name],
                "action_required": False,
                "direct_1on1": True,
            })

    # Strategy 5: Original 1:1 pattern
    for m in _CHAT_1ON1.finditer(section):
        name = m.group(1).strip()
        if not any(name in i.get("channel", "") for i in items):
            items.append({
                "channel": f"1:1 with {name}",
                "participants": [name],
                "action_required": False,
                "direct_1on1": True,
            })

    return items[:30]


# ---------------------------------------------------------------------------
# People signals parsing
# ---------------------------------------------------------------------------

_PEOPLE_TABLE_ROW = re.compile(
    r"^\|\s*([A-Z][A-Za-z ,'.\-]{2,50}?)\s*\|\s*([^|]{5,200}?)\s*\|",
    re.MULTILINE,
)
_PEOPLE_BULLET = re.compile(
    r"^-\s+\*?\*?([A-Z][A-Za-z ,'.\-]{2,40}?)\*?\*?\s*[—:]\s*(.{10,200})$",
    re.MULTILINE,
)
_NOISE_NAMES = {
    "name", "context", "role", "person", "attendees", "organizer",
    "participants", "title", "notes", "type",
}


def _is_noise_name(name: str) -> bool:
    return name.strip().lower() in _NOISE_NAMES or len(name.strip()) < 3


def _parse_people_section(text: str, family: str) -> list[dict[str, Any]]:
    """Extract people / stakeholder signals."""
    people: list[dict[str, Any]] = []

    section = _extract_section(
        text, r"## (?:People|Stakeholders?|New People|Relationship Pulse|Key People)"
    )
    if not section:
        return people

    # Table rows
    for m in _PEOPLE_TABLE_ROW.finditer(section):
        name, context = m.groups()
        name = name.strip()
        if _is_noise_name(name) or _is_self_organizer(name):
            continue
        people.append({
            "name": name,
            "context": context.strip()[:200],
            "interaction_type": "mention",
        })

    # Bullet fallback
    if not people:
        for m in _PEOPLE_BULLET.finditer(section):
            name, note = m.groups()
            name = name.strip()
            if _is_noise_name(name) or _is_self_organizer(name):
                continue
            people.append({
                "name": name,
                "context": note.strip()[:200],
                "interaction_type": "mention",
            })

    return people[:30]


# ---------------------------------------------------------------------------
# Key highlights + decisions
# ---------------------------------------------------------------------------

def _parse_key_highlights(text: str) -> list[str]:
    section = _extract_section(text, r"## Key Highlights?")
    if not section:
        return []
    results = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("-") and len(line) > 5:
            results.append(line.lstrip("- ").strip())
    return results[:15]


def _parse_key_decisions(text: str) -> list[str]:
    section = _extract_section(text, r"## Key Decisions?")
    if not section:
        return []
    results = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("-") and len(line) > 5:
            results.append(line.lstrip("- ").strip())
    return results[:10]


# ---------------------------------------------------------------------------
# Authored documents
# ---------------------------------------------------------------------------

_DOC_LINE = re.compile(
    r"^-\s+\*?\*?([^\n*]{5,80}\.(docx?|pptx?|xlsx?|pdf|md|txt))\*?\*?",
    re.MULTILINE | re.IGNORECASE,
)


def _parse_authored_docs(text: str) -> list[str]:
    docs = []
    for m in _DOC_LINE.finditer(text):
        docs.append(m.group(1).strip())
    return docs[:15]


# ---------------------------------------------------------------------------
# Week ID extraction
# ---------------------------------------------------------------------------

_MONTH_MAP: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Matches "Jan 6" / "March 3" / "September 16" in the header.
_HEADER_DATE_START = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})\b",
    re.IGNORECASE,
)
_HEADER_YEAR = re.compile(r"\b(20\d{2})\b")

# Capture full date-range string for display purposes.
_DATE_RANGE_RE = re.compile(
    r"(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}[,\s]*[–\-]\s*"
    r"(?:[A-Za-z]+\s+)?\d{1,2}[,\s]*,?\s*\d{4})",
    re.IGNORECASE,
)


def _week_id_from_date(year: int, month: int, day: int) -> str:
    """Compute ISO week-id from a calendar date."""
    import calendar as _cal
    from datetime import date as _date
    max_day = _cal.monthrange(year, month)[1]
    d = _date(year, month, min(day, max_day))
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _extract_week_id(path: Path, content: str) -> tuple[str, str]:
    """Return (week_id, date_range) from content header date-range.

    Priority:
      1. Parse actual date from header ("March 3–9, 2025") → compute ISO week.
      2. Direct YYYY-WNN in header (test fixtures like "# Week 2025-W10").
      3. Fallback: approximate from filename month + week-in-month.
    """
    header = content[:600]
    week_id = ""
    date_range = ""

    # Extract display date-range
    dr = _DATE_RANGE_RE.search(header)
    if dr:
        date_range = dr.group(1).strip()

    # Strategy 1: Parse start-date from header
    dm = _HEADER_DATE_START.search(header)
    if dm:
        month_str = dm.group(1).lower()[:3]
        day = int(dm.group(2))
        month = _MONTH_MAP.get(month_str, 0)
        if month:
            # Find the year: prefer one AFTER the date, then before
            year_after = _HEADER_YEAR.search(header[dm.end():])
            year_before = _HEADER_YEAR.search(header[:dm.start()])
            year = 0
            if year_after:
                year = int(year_after.group(1))
            elif year_before:
                year = int(year_before.group(1))

            if year:
                try:
                    week_id = _week_id_from_date(year, month, day)
                except ValueError:
                    pass

    # Strategy 2: Direct YYYY-WNN in header (backward-compat for test fixtures)
    if not week_id:
        iso_direct = re.search(r"(\d{4})-W(\d{2})", header)
        if iso_direct:
            week_id = f"{iso_direct.group(1)}-W{iso_direct.group(2)}"

    # Strategy 3: Approximate from filename month + week-in-month
    if not week_id:
        parts = path.parts
        year_part = next((p for p in parts if p.isdigit() and len(p) == 4), None)
        stem_match = re.match(r"(\d{1,2})-w(\d+)", path.stem, re.IGNORECASE)
        if year_part and stem_match:
            year = int(year_part)
            month = int(stem_match.group(1))
            week_in_month = int(stem_match.group(2))
            # Approximate: 1st of month + (week-1)*7 days
            approx_day = min(1 + (week_in_month - 1) * 7, 28)
            if 1 <= month <= 12:
                try:
                    week_id = _week_id_from_date(year, month, approx_day)
                except ValueError:
                    pass

    return week_id, date_range


# ---------------------------------------------------------------------------
# Extraction rate calculator
# ---------------------------------------------------------------------------

def _calculate_extraction_rate(
    meetings: list,
    email_items: list,
    chat_items: list,
    family: str,
    *,
    people: Optional[list] = None,
    highlights: Optional[list] = None,
    decisions: Optional[list] = None,
    docs: Optional[list] = None,
) -> float:
    """Calculate fraction of expected sections successfully extracted (0.0–1.0).

    Weights: meetings 1.5, email 1.0, chat 1.0, people 0.5,
    highlights 0.5, decisions 0.5, docs 0.25.
    """
    weights = {
        "meetings": 1.5,
        "email": 1.0,
        "chat": 1.0,
        "people": 0.5,
        "highlights": 0.5,
        "decisions": 0.5,
        "docs": 0.25,
    }
    total_weight = sum(weights.values())
    obtained = 0.0
    if meetings:
        obtained += weights["meetings"]
    if email_items:
        obtained += weights["email"]
    if chat_items:
        obtained += weights["chat"]
    if people:
        obtained += weights["people"]
    if highlights:
        obtained += weights["highlights"]
    if decisions:
        obtained += weights["decisions"]
    if docs:
        obtained += weights["docs"]
    return obtained / total_weight if total_weight > 0 else 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_scrape_file(path: Path) -> Optional[ParsedScrapeWeek]:
    """Parse a single work-scrape markdown file.

    Returns None if the file is unreadable or cannot be parsed.
    Source citation uses path relative to the corpus root if possible.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    if not content.strip():
        return None

    family = detect_format_family(content)
    week_id, date_range = _extract_week_id(path, content)

    if not week_id:
        # Cannot determine week — skip
        return None

    # Try to build source citation relative to corpus root
    # Corpus lives at .../knowledge/work-scrape/YYYY/MM-wN.md
    try:
        # Walk up looking for "work-scrape" dir
        parts = path.parts
        idx = next(
            (i for i, p in enumerate(parts) if p.lower() == "work-scrape"),
            None,
        )
        if idx is not None:
            source_path = "/".join(parts[idx:])
        else:
            source_path = path.name
    except Exception:
        source_path = path.name

    meetings = _parse_calendar_section(content, family)
    email_items = _parse_email_section(content, family)
    chat_items = _parse_chat_section(content, family)
    people_signals = _parse_people_section(content, family)
    key_highlights = _parse_key_highlights(content)
    key_decisions = _parse_key_decisions(content)
    authored_docs = _parse_authored_docs(content)

    extraction_rate = _calculate_extraction_rate(
        meetings, email_items, chat_items, family,
        people=people_signals, highlights=key_highlights,
        decisions=key_decisions, docs=authored_docs,
    )

    return ParsedScrapeWeek(
        week_id=week_id,
        date_range=date_range,
        format_family=family,
        source_path=source_path,
        meetings=meetings,
        email_items=email_items,
        chat_items=chat_items,
        people_signals=people_signals,
        key_highlights=key_highlights,
        key_decisions=key_decisions,
        authored_docs=authored_docs,
        extraction_rate=extraction_rate,
    )
