"""channel/formatters.py — Telegram-safe text formatting utilities."""
from __future__ import annotations
import html
import re
from datetime import datetime

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



# ── _strip_frontmatter ──────────────────────────────────────────

def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter (--- ... ---) from the top of state files."""
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return content
    end = stripped.find("\n---", 3)
    if end == -1:
        return content
    return stripped[end + 4:].lstrip()


# ── _is_noise_section ───────────────────────────────────────────

def _is_noise_section(header: str) -> bool:
    h = header.lower()
    return any(pat in h for pat in _SKIP_SECTION_PATTERNS)


# ── _filter_noise_bullets ───────────────────────────────────────

def _filter_noise_bullets(lines: list[str]) -> list[str]:
    """Remove historical/redundant bullet blocks from a ### subsection body.

    Skips bullets matching _SKIP_BULLET_PATTERNS (historical noise) and
    _REDUNDANT_FIELD_PATTERNS (info already in the ### heading).
    Also skips standalone bold labels like **Key School Contacts** and
    any following table rows.  Standalone bold labels referencing dates
    >1 year old are auto-skipped.
    """
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


# ── _clean_for_telegram ─────────────────────────────────────────

def _clean_for_telegram(text: str) -> str:
    """Universal cleanup for all Telegram output.

    Strips: YAML frontmatter, file-header comments, markdown formatting,
    code fences, pipe tables, horizontal rules, and excess blank lines.
    """
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


# ── _trim_to_cap ────────────────────────────────────────────────

def _trim_to_cap(text: str, cap: int, ellipsis: str = "\n…") -> str:
    """Trim text to cap chars, breaking at the last newline if possible."""
    if len(text) <= cap:
        return text
    trunc = text[:cap]
    nl = trunc.rfind("\n")
    if nl > int(cap * 0.7):
        trunc = trunc[:nl]
    return trunc + ellipsis


# ── _extract_section_summaries ──────────────────────────────────

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


# ── _truncate ───────────────────────────────────────────────────

def _truncate(text: str, maxlen: int) -> str:
    """Truncate text to maxlen, adding ellipsis if needed."""
    if len(text) <= maxlen:
        return text
    return text[:maxlen - 1] + "…"


# ── _split_message ──────────────────────────────────────────────

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
