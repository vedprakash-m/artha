#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/domain_index.py — Domain index builder for progressive domain disclosure.

Reads YAML frontmatter from state/*.md files and produces a compact domain
index card suitable for injection into the AI context window, replacing the
need to load full domain prompt files unconditionally.

Phase 2 of the Deep Agents Architecture adoption (specs/deep-agents.md §5 Phase 2).

Output format (one line per domain, ~30–40 tokens each):
    immigration | ACTIVE  | last: 2026-03-14 | alerts: 2 🔴 | src: state/immigration.md.age
    finance     | ACTIVE  | last: 2026-03-15 | alerts: 1 🟠 | src: state/finance.md.age
    travel      | STALE   | last: 2026-01-10 | alerts: 0    | src: state/travel.md
    estate      | ARCHIVE | last: 2025-09-01 | alerts: 0    | src: state/estate.md.age

Usage:
    from domain_index import build_domain_index, should_load_prompt

    index_card = build_domain_index(artha_dir)
    if should_load_prompt("immigration", index, command="/catch-up"):
        # load prompts/immigration.md

Config flag: harness.progressive_disclosure.enabled (default: true)

Ref: specs/deep-agents.md Phase 2, Artha.core.md Step 6b
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from lib.common import ARTHA_DIR
from context_offloader import load_harness_flag

# Commands that never load domain-specific prompts
_NO_PROMPT_COMMANDS: frozenset[str] = frozenset({
    "/status", "artha status",
    "/items", "show open items", "open items",
    "/items quick", "anything quick i can knock out",
    "/dashboard", "show dashboard", "artha dashboard",
    "/scorecard", "life scorecard",
    "/diff",
    "/cost",
    "/health", "system health",
    "/power", "power half hour",
})

# Commands that load all active prompts
_ALL_PROMPTS_COMMANDS: frozenset[str] = frozenset({
    "/catch-up deep",
    "catch-up deep",
    "/catch-up --deep",
})

# Status tier thresholds (days since last_activity)
_ACTIVE_DAYS = 30
_REFERENCE_DAYS = 180

# Alert severity emoji mapping
_ALERT_EMOJI = {0: "", 1: "🟡", 2: "🟠", 3: "🔴"}


def _parse_frontmatter(path: Path) -> dict[str, Any]:
    """Extract YAML frontmatter from a state/*.md file.

    Reads only up to the second ``---`` delimiter to avoid loading the full
    file body into memory.  Returns an empty dict on any parse failure.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    # Find frontmatter block: content between the first two `---` lines
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    frontmatter_text = text[3:end].strip()

    try:
        import yaml  # noqa: PLC0415

        result = yaml.safe_load(frontmatter_text)
        if isinstance(result, dict):
            return result
    except Exception:  # noqa: BLE001
        pass
    return {}


def _days_since(date_value: Any) -> int | None:
    """Return days elapsed since a date value (string, date, or datetime).

    Returns None if the value cannot be parsed.
    """
    if not date_value:
        return None
    try:
        if isinstance(date_value, datetime):
            d = date_value.date()
        elif isinstance(date_value, date):
            d = date_value
        else:
            # Try ISO-8601 date string (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS...)
            clean = str(date_value)[:10]
            d = date.fromisoformat(clean)
        return (date.today() - d).days
    except (ValueError, TypeError):
        return None


def _domain_status(days: int | None) -> str:
    """Map days-since-activity to a status label."""
    if days is None:
        return "UNKNOWN"
    if days <= _ACTIVE_DAYS:
        return "ACTIVE"
    if days <= _REFERENCE_DAYS:
        return "STALE"
    return "ARCHIVE"


def _alert_emoji(alert_count: int) -> str:
    return _ALERT_EMOJI.get(min(alert_count, 3), "🔴")


def _discover_state_files(state_dir: Path) -> list[Path]:
    """Return all state/*.md files, excluding system/template files."""
    _EXCLUDED = frozenset({
        "health-check.md",
        "audit.md",
        "memory.md",
        "open_items.md",
        "decisions.md",
        "scenarios.md",
        "dashboard.md",
        "work-calendar.md",
    })
    files = []
    for p in sorted(state_dir.glob("*.md")):
        if p.name not in _EXCLUDED:
            files.append(p)
    return files


def build_domain_index(artha_dir: Path | None = None) -> tuple[str, dict[str, dict]]:
    """Scan state/*.md frontmatter and return a compact index card + data dict.

    The index card is a human-readable string of ~30–40 tokens per domain,
    suitable for direct injection into the AI context.  The data dict maps
    domain name → parsed metadata (for programmatic use by should_load_prompt).

    Args:
        artha_dir: Override the Artha project root (used in tests).

    Returns:
        (index_card: str, index_data: dict[str, dict])
        index_data keys per domain:
            status (str), last_activity_days (int|None), alerts (int),
            src (str), last_updated (str|None)
    """
    base_dir = artha_dir if artha_dir is not None else ARTHA_DIR
    state_dir = base_dir / "state"

    if not state_dir.exists():
        return "(no state directory found)", {}

    index_data: dict[str, dict] = {}
    rows: list[str] = []

    for path in _discover_state_files(state_dir):
        fm = _parse_frontmatter(path)
        domain = fm.get("domain") or path.stem

        last_activity = fm.get("last_activity") or fm.get("last_updated")
        days = _days_since(last_activity)
        status = _domain_status(days)

        raw_alerts = fm.get("alerts", fm.get("alert_count", 0))
        try:
            alert_count = int(raw_alerts) if raw_alerts else 0
        except (ValueError, TypeError):
            alert_count = 0

        # Prefer encrypted path if it exists alongside the plaintext
        encrypted_path = state_dir / f"{path.name}.age"
        src_file = encrypted_path if encrypted_path.exists() else path
        src = f"state/{src_file.name}"

        last_date = str(last_activity)[:10] if last_activity else "unknown"
        emoji = _alert_emoji(alert_count) if alert_count > 0 else ""
        alert_str = f"{alert_count} {emoji}".strip() if alert_count else "0"

        row = (
            f"{domain:<15} | {status:<7} | last: {last_date} "
            f"| alerts: {alert_str:<6} | src: {src}"
        )
        rows.append(row)

        index_data[domain] = {
            "status": status,
            "last_activity_days": days,
            "alerts": alert_count,
            "src": src,
            "last_updated": str(last_activity)[:10] if last_activity else None,
        }

    if not rows:
        return "(no domain state files found)", {}

    header = "DOMAIN INDEX (progressive disclosure — load prompt only when needed)"
    separator = "─" * 80
    card = "\n".join([header, separator] + rows + [separator])
    return card, index_data


def should_load_prompt(domain: str, index_data: dict[str, dict], command: str) -> bool:
    """Decide whether to load the full prompts/{domain}.md for this command.

    Args:
        domain: Domain name (e.g. "immigration", "finance").
        index_data: Output from build_domain_index()[1].
        command: The current command string (e.g. "/catch-up", "/status").

    Returns:
        True if the full prompt file should be loaded; False to skip.
    """
    if not load_harness_flag("progressive_disclosure.enabled"):
        return True  # Feature disabled — always load

    # Force-load list overrides everything
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        cfg = load_config("artha_config")
        force_list = cfg.get("harness", {}).get("progressive_disclosure", {}).get("force_load", [])
        if domain in (force_list or []):
            return True
    except Exception:  # noqa: BLE001
        pass

    cmd_lower = command.lower().strip()

    # Commands with no prompts at all
    if cmd_lower in _NO_PROMPT_COMMANDS:
        return False

    # /goals only loads goals prompt
    if cmd_lower in {"/goals", "show goals", "goal pulse"}:
        return domain == "goals"

    # /domain <X> loads only that domain
    domain_match = re.match(r"^/domain\s+(\w+)$", cmd_lower)
    if domain_match:
        return domain == domain_match.group(1).lower()

    # deep catch-up loads all active prompts
    if cmd_lower in _ALL_PROMPTS_COMMANDS:
        info = index_data.get(domain, {})
        return info.get("status", "ARCHIVE") != "ARCHIVE"

    # Standard catch-up: load only if domain has activity (ACTIVE status or alerts)
    if "/catch-up" in cmd_lower or "catch me up" in cmd_lower or "morning briefing" in cmd_lower:
        info = index_data.get(domain, {})
        return (
            info.get("status") == "ACTIVE"
            or info.get("alerts", 0) > 0
        )

    # Default: load the prompt (unknown command → safe default)
    return True


def get_prompt_load_list(
    index_data: dict[str, dict],
    command: str,
    routed_domains: list[str] | None = None,
) -> list[str]:
    """Return the list of domain names whose prompts should be loaded.

    Args:
        index_data: Output from build_domain_index()[1].
        command: The current command string.
        routed_domains: If provided, these domains are always included
            (they received routed emails in Step 6).

    Returns:
        Sorted list of domain names to load prompts for.
    """
    routed = set(routed_domains or [])
    to_load = set()
    for domain in index_data:
        if domain in routed or should_load_prompt(domain, index_data, command):
            to_load.add(domain)
    return sorted(to_load)


# ---------------------------------------------------------------------------
# AFW-2: Registry-based progressive disclosure (specs/agent-fw.md §3.2)
# ---------------------------------------------------------------------------


def _validate_domain_registry(registry: dict) -> None:  # noqa: C901
    """Emit WARNING for domains violating the B1 keyword ordering convention.

    B1 (§4.2): ``routing_keywords`` must list multi-word phrases FIRST.
    Single-word first keywords reduce Stage 1 specificity and increase
    multi-match routing collisions.  Advisory only — does not block routing.
    Visible in preflight and session logs.
    """
    import logging as _logging  # noqa: PLC0415

    _log = _logging.getLogger(__name__)
    for name, cfg in (registry.get("domains") or {}).items():
        if not cfg.get("enabled_by_default", True):
            continue
        keywords = cfg.get("routing_keywords") or []
        if keywords:
            first_kw = str(keywords[0])
            if " " not in first_kw:
                _log.warning(
                    "Domain '%s': first routing keyword '%s' is single-word — "
                    "move multi-word phrases first (B1 convention, "
                    "domain_registry.yaml)",
                    name,
                    first_kw,
                )


def rank_domains_by_relevance(
    query: str,
    registry: dict,
    top_n: int = 5,
) -> list[tuple[str, float]]:
    """EAR-4 Layer 2: rank candidate domains by TF-IDF-style token overlap.

    Pure stdlib implementation using :class:`collections.Counter`.
    Applied when Layer 1 keyword matching yields no match or multiple matches.
    Returns a ranked list for tie-breaking; caller decides final selection.

    Args:
        query:    The user query or combined signal text.
        registry: Parsed ``domain_registry.yaml`` dict.
        top_n:    Maximum number of results to return (default 5).

    Returns:
        List of ``(domain_name, score)`` tuples sorted descending by score.
        Score is normalised overlap count divided by query token count.
    """
    from collections import Counter  # noqa: PLC0415

    query_tokens = Counter(query.lower().split())
    if not query_tokens:
        return []

    scores: list[tuple[str, float]] = []
    for name, cfg in (registry.get("domains") or {}).items():
        if not cfg.get("enabled_by_default", True):
            continue
        desc = cfg.get("description", "")
        keywords = cfg.get("routing_keywords") or []
        doc_text = (desc + " " + " ".join(str(k) for k in keywords)).lower()
        doc_tokens = Counter(doc_text.split())
        overlap = sum(
            min(query_tokens[t], doc_tokens[t])
            for t in query_tokens
            if t in doc_tokens
        )
        if overlap > 0:
            score = overlap / max(len(query_tokens), 1)
            scores.append((name, score))

    return sorted(scores, key=lambda x: x[1], reverse=True)[:top_n]


def load_domain_registry(artha_dir: Path | None = None) -> dict:
    """Load ``config/domain_registry.yaml`` and return the parsed dict.

    Args:
        artha_dir: Artha project root.  Defaults to ``ARTHA_DIR``.

    Returns:
        Parsed registry dict, or empty dict on any error.
    """
    try:
        import sys  # noqa: PLC0415

        base = artha_dir if artha_dir is not None else ARTHA_DIR
        _scripts = str(base / "scripts")
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        from lib.config_loader import load_config  # noqa: PLC0415

        result = load_config("domain_registry", str(base / "config"))
        _validate_domain_registry(result)
        return result
    except Exception:  # noqa: BLE001
        return {}


def build_domain_menu(registry: dict) -> str:
    """Build the Stage 1 domain advertisement text for the system prompt.

    Produces a compact menu (~50 tokens per domain) listing enabled domains
    with descriptions and up to 5 routing keywords.  Suitable for raw
    injection into the system prompt; the model uses it to decide which
    domain's full prompt file to request.

    A-1 validated: 39 enabled domains → ~1286 tokens (under 1500-token budget).

    Args:
        registry: Parsed ``domain_registry.yaml`` dict
            (see :func:`load_domain_registry`).

    Returns:
        Multi-line string.  First line is a header; subsequent lines are
        ``- <domain>: <description> [keywords: k1, k2, ...]``.
    """
    lines = ["Available domains (load prompt only when needed):"]
    for name, cfg in (registry.get("domains") or {}).items():
        if not cfg.get("enabled_by_default", True):
            continue
        desc = cfg.get("description", "")
        kw = cfg.get("routing_keywords", [])[:5]
        entry = f"- {name}: {desc}"
        if kw:
            entry += f" [keywords: {', '.join(kw)}]"
        lines.append(entry)
    return "\n".join(lines)


def should_load_domain(
    domain_name: str,
    signals: list[str],
    user_query: str,
    registry: dict,
) -> bool:
    """Determine whether a domain's full prompt must be loaded.

    Returns ``True`` if:

    - The domain is marked ``always_load`` (Tier-1 always-on domain), **or**
    - Any of the domain's ``routing_keywords`` appears in the combined
      signals text or user query (case-insensitive substring match).

    Args:
        domain_name: Domain name key as used in ``domain_registry.yaml``
            (e.g. ``"immigration"``).
        signals: List of signal strings from connectors / pattern engine.
        user_query: The current user command or query string.
        registry: Parsed domain_registry.yaml dict.

    Returns:
        ``True`` if the domain prompt should be loaded for this request.
    """
    domains = registry.get("domains") or {}
    cfg = domains.get(domain_name, {})
    if cfg.get("always_load", False):
        return True
    keywords = cfg.get("routing_keywords", [])
    if not keywords:
        return False
    all_text = (" ".join(signals) + " " + user_query).lower()
    return any(str(kw).lower() in all_text for kw in keywords)

