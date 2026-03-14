# pii-guard: ignore-file — utility code only; no personal data
"""
scripts/lib/html_processing.py — Shared HTML stripping and email footer removal.

Consolidates the HTML-to-plain-text pipeline that was duplicated across
gmail_fetch.py, icloud_mail_fetch.py, msgraph_fetch.py, and
deep_mail_review*.py.

Usage:
    from scripts.lib.html_processing import strip_html, strip_footers, clean_email_body

Ref: remediation.md §6.3, standardization.md §7.5.2
"""
from __future__ import annotations

import re
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# Internal: HTML → plain text
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Minimal HTML → text converter that:
    - Skips <script>, <style>, <head> content entirely
    - Inserts newlines for block-level elements
    - Decodes numeric and named HTML entities via html.parser's built-in
    """

    _SKIP_TAGS = frozenset(["script", "style", "head"])
    _BLOCK_TAGS = frozenset([
        "p", "div", "br", "tr", "td", "th", "li", "h1", "h2", "h3",
        "h4", "h5", "h6", "blockquote", "pre", "hr",
    ])

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._result: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS and not self._skip_depth:
            self._result.append("\n")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS and not self._skip_depth:
            self._result.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._result.append(data)

    def get_text(self) -> str:
        return "".join(self._result)


def strip_html(html_content: str) -> str:
    """Convert HTML to plain text, collapsing excess blank lines.

    Args:
        html_content: Raw HTML string (may include <!DOCTYPE>, <html>, etc.)

    Returns:
        Plain text with at most two consecutive blank lines.
    """
    if not html_content:
        return ""
    stripper = _HTMLStripper()
    try:
        stripper.feed(html_content)
    except Exception:
        # Partial parse is fine — return what we got
        pass
    text = stripper.get_text()
    # Normalise whitespace: collapse 3+ blank lines → 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Email footer stripping
# ---------------------------------------------------------------------------

# Max email body length (chars) — shared across connectors.
MAX_BODY_CHARS: int = 8_000

# Compiled regex markers for reply-chain / signature detection.
# Used by connectors to truncate at reply boundaries before the main
# footer-pattern scan.  Superset of Google, Outlook, and IMAP patterns.
REPLY_CHAIN_MARKERS: list[re.Pattern] = [
    re.compile(r"^[-_*]{3,}\s*$", re.MULTILINE),
    re.compile(r"^On .+wrote:\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^From:\s+.+\nSent:\s+", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^-----Original Message-----", re.MULTILINE | re.IGNORECASE),
    re.compile(r"\nSent from my (iPhone|iPad|Android|Galaxy|Samsung)", re.IGNORECASE),
    re.compile(r"\nGet Outlook for ", re.IGNORECASE),
    re.compile(r"\nMicrosoft Teams meeting", re.IGNORECASE),
    re.compile(r"\nTo unsubscribe .{0,80}\n", re.IGNORECASE),
    re.compile(r"\nThis (email|message) (was sent|is intended|contains confidential)", re.IGNORECASE),
]

# Simple lowercase-substring markers for lighter IMAP-style footer detection.
SIMPLE_FOOTER_MARKERS: list[str] = [
    "get outlook for ios", "get outlook for android",
    "sent from my iphone", "sent from my ipad", "sent from my mac",
    "sent from iphone", "sent from ipad",
    "unsubscribe", "you received this email because",
    "to unsubscribe from this list", "privacy policy",
    "view in browser",
]


def trim_body(text: str, *, max_chars: int = MAX_BODY_CHARS) -> str:
    """Truncate *text* at reply-chain / footer markers, then hard-cap.

    This is the canonical body-trimming function.  All email connectors
    should delegate to this instead of defining local copies.
    """
    for pattern in REPLY_CHAIN_MARKERS:
        m = pattern.search(text)
        if m:
            text = text[: m.start()].strip()
            break
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... truncated ...]"
    return text


# Patterns that reliably indicate the start of boilerplate/footer content.
# Ordered by specificity: more specific patterns first.
_FOOTER_PATTERNS: list[re.Pattern] = [
    # Unsubscribe links (very reliable sentinel)
    re.compile(r"^[-_]{2,}\s*$", re.MULTILINE),               # -- or ___
    re.compile(r"\bunsubscribe\b", re.IGNORECASE),
    re.compile(r"\bview\s+in\s+browser\b", re.IGNORECASE),
    re.compile(r"\bconfidentiality\s+notice\b", re.IGNORECASE),
    re.compile(r"\bthis\s+(?:e-?mail|message)\s+(?:is|was)\s+(?:sent|intended)\b", re.IGNORECASE),
    re.compile(r"\bif\s+you\s+(?:received|receive)\s+this\s+(?:e-?mail|message)\s+in\s+error\b", re.IGNORECASE),
    re.compile(r"\bprivileged\s+and\s+confidential\b", re.IGNORECASE),
    re.compile(r"\ball\s+rights?\s+reserved\b", re.IGNORECASE),
    re.compile(r"^\s*©\s*\d{4}", re.MULTILINE),               # © 2024
    # Reply/forward delimiters
    re.compile(r"^-{5,}\s*original\s+message\s*-{5,}", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^_{5,}$", re.MULTILINE),
    re.compile(r"^On .+ wrote:\s*$", re.MULTILINE),
    re.compile(r"^From:\s+.+\s+Sent:\s+", re.IGNORECASE | re.MULTILINE),
]


def strip_footers(text: str) -> str:
    """Remove standard email footer / disclaimer / reply-chain content.

    Splits the text at the first detected footer sentinel and returns only
    the portion before it. If no footer is found, returns the input unchanged.

    Args:
        text: Plain text email body (after HTML stripping, if applicable).

    Returns:
        Text with footer content removed.
    """
    if not text:
        return text
    earliest = len(text)
    for pattern in _FOOTER_PATTERNS:
        match = pattern.search(text)
        if match and match.start() < earliest:
            earliest = match.start()
    return text[:earliest].rstrip()


# ---------------------------------------------------------------------------
# Combined convenience function
# ---------------------------------------------------------------------------

def clean_email_body(
    body: str,
    *,
    is_html: bool | None = None,
    strip_reply_chain: bool = True,
) -> str:
    """Full pipeline: HTML strip → footer removal → whitespace normalisation.

    Args:
        body:               Raw email body (HTML or plain text).
        is_html:            Force HTML mode (True) or plain-text mode (False).
                            If None (default), auto-detected from content.
        strip_reply_chain:  If True (default), remove quoted reply content.

    Returns:
        Clean, human-readable plain text ready for LLM consumption and
        JSONL output.
    """
    if not body:
        return ""

    if is_html is None:
        is_html = "<html" in body[:200].lower() or "<div" in body[:500].lower()

    text = strip_html(body) if is_html else body

    if strip_reply_chain:
        text = strip_footers(text)

    # Final whitespace cleanup
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
