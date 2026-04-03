# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/injection_detector.py — Prompt injection detection for AR-9.

Scans text (both outbound delegation prompts and inbound agent responses)
for prompt injection markers, encoded instructions, and exfiltration
attempts.

Defense-in-depth layer 3 (outbound: layer 3; inbound: first layer).

Injection patterns:
  - Direct instruction injection ("ignore previous instructions")
  - Role hijacking ("you are now", "act as", "pretend to be")
  - Encoding attempts (base64, URL-encoded payloads)
  - Exfiltration markers (data: URI, unusual URLs in structured data)
  - Delimiter confusion (system/user/assistant tags in data)

When injection is detected on an inbound response, the pipeline:
  1. Discards the response entirely
  2. Logs a P0 EXT_AGENT_INJECTION audit event
  3. Falls through to the fallback cascade

Ref: specs/subagent-ext-agent.md §9.3, EA-0e
"""
from __future__ import annotations

import base64
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# Direct prompt injection phrases (case-insensitive, word-boundary matched)
_INJECTION_PHRASES = re.compile(
    r'\b('
    # Classic ignore patterns
    r'ignore (?:previous|above|all|prior) instructions?|'
    r'disregard (?:previous|above|all|prior) instructions?|'
    r'forget (?:previous|above|all|prior) instructions?|'
    r'override (?:previous|above)? instructions?|'
    # Role hijacking
    r'you are now|act as (?:a )?(?:different|new|another)|pretend (?:to be|you are)|'
    r'new (?:system |role |persona )?prompt:|'
    r'switch (?:to )?(?:a )?new (?:role|persona|mode)|'
    # Exfil patterns
    r'send (?:all|the) (?:data|context|information|output) to|'
    r'exfiltrat|transmit (?:the )?(?:context|data|secrets)|'
    # Privilege escalation
    r'you have (?:root|admin|unrestricted) access|'
    r'bypass (?:the )?(?:filter|guard|restriction|safety)|'
    r'reveal (?:the )?(?:system prompt|instructions|context)|'
    r'print (?:the )?(?:system prompt|full context)'
    r')\b',
    re.IGNORECASE,
)

# Delimiter confusion: markdown/XML role tags embedded in content
_DELIMITER_PATTERNS = re.compile(
    r'<(?:system|user|assistant|human|ai|instruction)(?:\s|>|/)'
    r'|\[SYSTEM\]|\[USER\]|\[ASSISTANT\]'
    r'|###\s*System(?:\s|:)'
    r'|###\s*(?:New\s+)?Instructions?(?:\s|:)',
    re.IGNORECASE,
)

# Base64 payload detection: sequences that look like encoded instructions
# (long base64 strings in output are suspicious when combined with context)
_BASE64_PATTERN = re.compile(r'[A-Za-z0-9+/]{100,}={0,2}')

# URL-based exfiltration: data: URIs or unusual out-of-context URLs
_DATA_URI_PATTERN = re.compile(r'data:[a-z]+/[a-z]+;base64,', re.IGNORECASE)

# Generic suspicious URL patterns in structured data (not in prose)
_WEBHOOK_PATTERN = re.compile(
    r'https?://(?:webhook|requestbin|pipedream|ngrok|burpcollab|interact\.sh)',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class InjectionSignal(NamedTuple):
    signal_type: str    # "phrase", "delimiter", "base64", "data_uri", "webhook"
    excerpt: str        # Short excerpt of the matched text (first 80 chars)
    position: int       # Character offset in the scanned text


@dataclass
class ScanResult:
    injection_detected: bool
    signals: list[InjectionSignal] = field(default_factory=list)

    @property
    def signal_types(self) -> list[str]:
        return [s.signal_type for s in self.signals]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class InjectionDetector:
    """Scans text for prompt injection markers.

    Designed to be called on both outbound (composed delegation prompt) and
    inbound (agent response) text.  Fast path: regex-only, no LLM.
    Typical cost: <1ms per call.
    """

    def scan(self, text: str) -> ScanResult:
        """Scan text for injection signals.

        Returns a ScanResult.  If injection_detected is True, the calling
        pipeline should discard the text and fall through to fallback.
        """
        if not text or not text.strip():
            return ScanResult(injection_detected=False)

        signals: list[InjectionSignal] = []

        # 1. Direct injection phrases
        for m in _INJECTION_PHRASES.finditer(text):
            signals.append(InjectionSignal(
                signal_type="phrase",
                excerpt=text[max(0, m.start()-10):m.end()+10][:80],
                position=m.start(),
            ))

        # 2. Delimiter confusion
        for m in _DELIMITER_PATTERNS.finditer(text):
            signals.append(InjectionSignal(
                signal_type="delimiter",
                excerpt=text[max(0, m.start()-10):m.end()+10][:80],
                position=m.start(),
            ))

        # 3. Base64 payloads — only flag if the encoded content decodes to
        #    something text-like (heuristic: looks like instruction injection)
        for m in _BASE64_PATTERN.finditer(text):
            decoded = _try_decode_base64(m.group())
            if decoded and _INJECTION_PHRASES.search(decoded):
                signals.append(InjectionSignal(
                    signal_type="base64",
                    excerpt=m.group()[:40] + "...",
                    position=m.start(),
                ))

        # 4. URL-encoded payloads
        url_decoded = _try_url_decode(text)
        if url_decoded != text:
            for m in _INJECTION_PHRASES.finditer(url_decoded):
                signals.append(InjectionSignal(
                    signal_type="url_encoded",
                    excerpt=url_decoded[max(0, m.start()-5):m.end()+5][:80],
                    position=m.start(),
                ))

        # 5. data: URI exfiltration
        for m in _DATA_URI_PATTERN.finditer(text):
            signals.append(InjectionSignal(
                signal_type="data_uri",
                excerpt=text[m.start():m.start()+60],
                position=m.start(),
            ))

        # 6. Webhook URLs
        for m in _WEBHOOK_PATTERN.finditer(text):
            signals.append(InjectionSignal(
                signal_type="webhook",
                excerpt=m.group()[:80],
                position=m.start(),
            ))

        return ScanResult(
            injection_detected=bool(signals),
            signals=signals,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _try_decode_base64(s: str) -> str | None:
    """Try to base64-decode a string. Returns None if not decodable as UTF-8."""
    try:
        # Pad if needed
        padding = 4 - len(s) % 4
        padded = s + "=" * (padding % 4)
        decoded = base64.b64decode(padded).decode("utf-8", errors="strict")
        return decoded
    except Exception:
        return None


def _try_url_decode(s: str) -> str:
    """URL-decode a string for injection scanning."""
    try:
        return urllib.parse.unquote(s)
    except Exception:
        return s
