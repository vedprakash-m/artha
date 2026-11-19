"""prompt_composer.py — Dynamic few-shot injection from state/audit.md.

Spec: §2.1.4 — Lean-Context Injection (Dynamic Few-Shotting).

Retrieves the most recently accepted actions for a domain from the operational
audit log
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
from collections.abc import Mapping
from pathlib import Path
from lib.local_paths import audit_log_path
from lib.context_budget import chars_for_tokens, estimate_token_count

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AUDIT_MD = audit_log_path()
_MAX_FEW_SHOT_EXAMPLES = 3
_MAX_TOKENS_PER_EXAMPLE = 200
_MAX_FEW_SHOT_TOKENS = 600

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

def _one_line(value: object, *, limit: int | None = None) -> str:
    text = " ".join(str(value).split())
    return text if limit is None else text[:limit]


def _truncate_tokens(text: str, token_budget: int) -> str:
    maximum = chars_for_tokens(max(0, int(token_budget)))
    if len(text) <= maximum:
        return text
    if maximum == 0:
        return ""
    return text[: maximum - 1] + "…"


def _bounded_examples(
    examples: list[str],
    *,
    max_examples: int,
    max_tokens_each: int,
    total_tokens: int = _MAX_FEW_SHOT_TOKENS,
) -> list[str]:
    """Apply the fixed few-shot count, per-example and aggregate budgets."""
    try:
        count = min(max(0, int(max_examples)), _MAX_FEW_SHOT_EXAMPLES)
        per_example = min(max(0, int(max_tokens_each)), _MAX_TOKENS_PER_EXAMPLE)
        total = max(0, int(total_tokens))
    except (TypeError, ValueError):
        return []
    if count == 0 or per_example == 0 or total == 0:
        return []

    bounded: list[str] = []
    for value in list(examples)[-count:]:
        row = _truncate_tokens(_one_line(value), per_example)
        if not row:
            continue
        candidate = "\n".join([*bounded, row])
        if estimate_token_count(candidate) <= total:
            bounded.append(row)
            continue
        used = estimate_token_count("\n".join(bounded))
        remaining = max(0, total - used)
        row = _truncate_tokens(row, remaining)
        if row and estimate_token_count("\n".join([*bounded, row])) <= total:
            bounded.append(row)
        break
    return bounded


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

    Each returned string is a single formatted row truncated through the
    canonical context-budget helper.

    Args:
        domain:         Domain key (e.g. ``"finance"``, ``"immigration"``).
        audit_path:     Override path to audit.md.  Defaults to
                        machine-local operational audit log.
        max_examples:   Maximum rows to return (default: 3).
        max_tokens_each: Per-example token cap (default: 200).

    Returns:
        List of formatted example strings.  Empty list if audit.md
        is missing, unreadable, or has no matching accepted rows.
        Never raises; never fabricates examples.
    """
    path = audit_path or _AUDIT_MD
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
                row_text = (
                    f"| {m.group('timestamp').strip()} "
                    f"| {m.group('session_id').strip()} "
                    f"| {m.group('domain').strip()} "
                    f"| {m.group('action_type').strip()} "
                    f"| {m.group('status').strip()} "
                    f"| {m.group('payload_summary').strip()} |"
                )
                matching.append(row_text)

        # Audit is append-only, so bounding the tail preserves the newest evidence.
        return _bounded_examples(
            matching,
            max_examples=max_examples,
            max_tokens_each=max_tokens_each,
        )

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
        examples = _bounded_examples(
            list(examples),
            max_examples=max_examples,
            max_tokens_each=max_tokens_each,
        )

        # 2. Signal summary. Keep each bullet structurally on one line.
        signal_lines: list[str] = []
        for sig in signals[:20]:  # Cap at 20 signals per worker invocation
            if not isinstance(sig, Mapping):
                continue
            sid = _one_line(sig.get("signal_id") or sig.get("id") or "?", limit=32)
            text = _one_line(sig.get("text") or sig.get("summary") or "", limit=120)
            signal_lines.append(f"  - [{sid}] {text}")

        # 3. Assemble context block
        domain_text = _one_line(domain)
        session_text = _one_line(session_id)
        state_text = str(state_delta).strip() if state_delta else "(no state delta)"
        parts: list[str] = [
            f"## Domain: {domain_text}  |  Session: {session_text}\n",
            "### State Delta\n",
            state_text,
            "\n",
        ]

        if signal_lines:
            parts.append("### Signals\n")
            parts.extend(signal_lines)
            parts.append("\n")

        if examples:
            heading = "### Few-Shot Examples (accepted actions for this domain)\n"
            heading_tokens = estimate_token_count(heading)
            examples = _bounded_examples(
                examples,
                max_examples=max_examples,
                max_tokens_each=max_tokens_each,
                total_tokens=_MAX_FEW_SHOT_TOKENS - heading_tokens - 3,
            )
            parts.append(heading)
            for ex in examples:
                parts.append(f"  {ex}")
            parts.append("\n")

        return "\n".join(parts)

    except Exception:
        # Always return something usable even if composition fails
        return (
            f"## Domain: {_one_line(domain)}  |  Session: {_one_line(session_id)}"
            f"\n\n{str(state_delta or '')}"
        )
