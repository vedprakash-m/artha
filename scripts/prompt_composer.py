"""prompt_composer.py — Dynamic few-shot injection from state/audit.md.

Spec: §2.1.4 — Lean-Context Injection (Dynamic Few-Shotting).

Retrieves the most recently accepted actions for a domain from state/audit.md
and formats them as few-shot examples for worker context.

Design constraints:
  - stdlib only (re, pathlib, json)
  - Never fabricates examples — returns [] if audit.md is missing or empty
  - Hard cap: max_examples=3, max_tokens_each=200 (≈800 chars)
  - Token budget: total few-shot injection ≤ 600 tokens (3 × 200)
  - Never raises — all errors return safe empty/default values
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_AUDIT_MD = _REPO_ROOT / "state" / "audit.md"

# audit.md row format (pipe-delimited Markdown table):
#   | timestamp | session_id | domain | action_type | status | payload_summary |
# Minimum 6 pipe-delimited columns; leading/trailing pipes optional.
_AUDIT_ROW_RE = re.compile(
    r"^\s*\|?\s*"
    r"(?P<timestamp>[^|]+?)\s*\|\s*"
    r"(?P<session_id>[^|]+?)\s*\|\s*"
    r"(?P<domain>[^|]+?)\s*\|\s*"
    r"(?P<action_type>[^|]+?)\s*\|\s*"
    r"(?P<status>[^|]+?)\s*\|\s*"
    r"(?P<payload_summary>[^|]*?)\s*\|?\s*$",
    re.IGNORECASE,
)

# RD-50: Import from single source of truth (context_budget.py).
# RD-21: Corrected estimate is 3.5 chars/token (was 4).
try:
    from lib.context_budget import CHARS_PER_TOKEN as _CHARS_PER_TOKEN
except ImportError:
    _CHARS_PER_TOKEN = 3.5  # fallback if lib not on path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_few_shot_examples(
    domain: str,
    *,
    audit_path: Path | None = None,
    max_examples: int = 3,
    max_tokens_each: int = 200,
) -> list[str]:
    """Return up to ``max_examples`` accepted audit rows for ``domain``.

    Reads ``state/audit.md`` and returns the most recent rows where:
      - column 3 (domain) matches ``domain`` (case-insensitive strip)
      - column 5 (status) == ``accepted`` (case-insensitive strip)

    Each returned string is a single formatted row truncated to
    ``max_tokens_each * _CHARS_PER_TOKEN`` characters.

    Args:
        domain:         Domain key (e.g. ``"finance"``, ``"immigration"``).
        audit_path:     Override path to audit.md.  Defaults to
                        ``state/audit.md`` relative to repo root.
        max_examples:   Maximum rows to return (default: 3).
        max_tokens_each: Per-example token cap (default: 200).

    Returns:
        List of formatted example strings.  Empty list if audit.md
        is missing, unreadable, or has no matching accepted rows.
        Never raises; never fabricates examples.
    """
    path = audit_path or _AUDIT_MD
    char_cap = max_tokens_each * _CHARS_PER_TOKEN

    try:
        if not path.exists():
            return []

        domain_lower = domain.strip().lower()
        matching: list[str] = []

        with path.open(encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.rstrip("\n")
                if not line.strip() or line.strip().startswith("---") or "---" * 3 in line:
                    continue
                m = _AUDIT_ROW_RE.match(line)
                if not m:
                    continue
                if m.group("domain").strip().lower() != domain_lower:
                    continue
                if m.group("status").strip().lower() != "accepted":
                    continue
                # Truncate to char cap
                row_text = (
                    f"| {m.group('timestamp').strip()} "
                    f"| {m.group('session_id').strip()} "
                    f"| {m.group('domain').strip()} "
                    f"| {m.group('action_type').strip()} "
                    f"| {m.group('status').strip()} "
                    f"| {m.group('payload_summary').strip()} |"
                )
                if len(row_text) > char_cap:
                    row_text = row_text[:char_cap - 3] + "..."
                matching.append(row_text)

        # Return the most recent max_examples (audit.md is append-only → last = newest)
        return matching[-max_examples:]

    except Exception:
        return []


def compose_worker_context(
    domain: str,
    state_delta: str,
    signals: list[dict],
    session_id: str,
    *,
    few_shot_examples: list[str] | None = None,
    audit_path: Path | None = None,
    max_examples: int = 3,
    max_tokens_each: int = 200,
) -> str:
    """Assemble the lean context block for a domain worker invocation.

    Contract (§2.1.4):
        [Core Identity block] + [domain state delta] + [few-shot examples]

    Args:
        domain:         Domain key.
        state_delta:    Current domain state snapshot (state/<domain>.md diff
                        or compact representation).
        signals:        List of signal dicts for this domain (with at least
                        a ``text`` or ``summary`` field).
        session_id:     Current session ID — links to reasoning trace.
        few_shot_examples:  Pre-loaded examples (skips audit.md if provided).
        audit_path:     Override audit.md path for few-shot loading.
        max_examples:   Max few-shot examples (default: 3).
        max_tokens_each: Per-example token cap (default: 200).

    Returns:
        Formatted context string ready for injection into a worker prompt.
    """
    try:
        # 1. Few-shot examples
        examples = few_shot_examples
        if examples is None:
            examples = load_few_shot_examples(
                domain,
                audit_path=audit_path,
                max_examples=max_examples,
                max_tokens_each=max_tokens_each,
            )

        # 2. Signal summary (no PII — signal IDs and text fragments only)
        signal_lines: list[str] = []
        for sig in signals[:20]:  # Cap at 20 signals per worker invocation
            sid = str(sig.get("signal_id") or sig.get("id") or "?")[:32]
            text = str(sig.get("text") or sig.get("summary") or "")[:120]
            signal_lines.append(f"  - [{sid}] {text}")

        # 3. Assemble context block
        parts: list[str] = [
            f"## Domain: {domain}  |  Session: {session_id}\n",
            "### State Delta\n",
            state_delta.strip() if state_delta else "(no state delta)",
            "\n",
        ]

        if signal_lines:
            parts.append("### Signals\n")
            parts.extend(signal_lines)
            parts.append("\n")

        if examples:
            parts.append("### Few-Shot Examples (accepted actions for this domain)\n")
            for ex in examples:
                parts.append(f"  {ex}")
            parts.append("\n")

        return "\n".join(parts)

    except Exception:
        # Always return something usable even if composition fails
        return f"## Domain: {domain}  |  Session: {session_id}\n\n{state_delta or ''}"
