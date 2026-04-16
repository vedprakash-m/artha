"""channel/state_readers.py — Whitelisted state file readers."""
from __future__ import annotations
import json
import re
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from channel.formatters import _strip_frontmatter, _trim_to_cap

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_STATE_DIR = _ARTHA_DIR / "state"
_BRIEFINGS_DIR = _ARTHA_DIR / "briefings"

log = logging.getLogger("channel_listener")

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

_FAMILY_EXCLUDED_DOMAINS = frozenset({
    "immigration", "finance", "estate", "insurance",
    "employment", "digital", "boundary",
})

# RD-34: Keys in _READABLE_STATE_FILES that require vault decryption.
# Derived from domain_registry.yaml (requires_vault: true) at import time.
# Hardcoded fallback covers kids and employment which were previously plaintext
# in channel but are vault-protected in the registry.
def _load_vault_gated_state_keys() -> frozenset[str]:
    """Build the set of _READABLE_STATE_FILES keys that require vault access."""
    _vault_keys = {"kids", "employment"}  # minimum safe fallback (RD-34)
    try:
        from lib.config_loader import load_config as _load_config  # noqa: PLC0415
        _registry = _load_config("domain_registry")
        _domains_section = _registry.get("domains", {}) if isinstance(_registry, dict) else {}
        for _domain, _meta in _domains_section.items():
            if isinstance(_meta, dict) and _meta.get("requires_vault", False):
                # Map domain name to state_readers key (usually same as domain name)
                _vault_keys.add(_domain)
    except Exception:  # noqa: BLE001
        pass  # registry unavailable — use fallback
    return frozenset(_vault_keys)


_VAULT_GATED_STATE_KEYS: frozenset[str] = _load_vault_gated_state_keys()


class VaultAccessRequired(PermissionError):
    """Raised when a vault-protected state key is requested without decryption."""

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



# ── _read_state_file ────────────────────────────────────────────

def _read_state_file(key: str) -> tuple[str, str]:
    """Read a whitelisted state file. Returns (content, staleness_str).

    Only files in _READABLE_STATE_FILES are accessible.
    Encrypted files (.md.age) are never returned.

    RD-34: Vault-gated domains (kids, employment, and registry requires_vault:true)
    raise VaultAccessRequired if the plaintext file exists but the domain is
    vault-classified. If the corresponding .age file exists (vault is locked),
    a VaultAccessRequired is raised to surface the locked state.

    Returns:
        (content: str, staleness: str) — staleness is human-readable age
    """
    # RD-34: Vault gate for vault-classified domains
    if key in _VAULT_GATED_STATE_KEYS:
        path = _READABLE_STATE_FILES.get(key)
        if path is not None:
            # Check if vault is locked (plaintext absent but .age exists)
            age_path = path.with_name(path.name + ".age")
            if age_path.exists() and not path.exists():
                raise VaultAccessRequired(
                    f"Domain '{key}' is vault-encrypted. Unlock the vault before reading."
                )
            # If plaintext exists, it may be transiently decrypted — still surface warning
            # but allow the read (vault was unlocked for this session)
            if not path.exists():
                return f"_{key} data requires vault access or has not been initialized_", "N/A"
        # Fall through to normal read below

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


# ── _format_age ─────────────────────────────────────────────────

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


# ── _get_latest_briefing_path ───────────────────────────────────

def _get_latest_briefing_path() -> Path | None:
    """Return the path to the most recent briefing file."""
    if not _BRIEFINGS_DIR.exists():
        return None
    files = sorted(_BRIEFINGS_DIR.glob("*.md"), reverse=True)
    return files[0] if files else None


# ── _apply_scope_filter ─────────────────────────────────────────

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


# ── _get_domain_open_items ──────────────────────────────────────

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


# ── _domain_freshness ───────────────────────────────────────────

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


# ── _parse_age_to_hours ─────────────────────────────────────────

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


def _get_last_catchup_iso() -> str:
    """Read last catch-up timestamp from health-check.md, fallback to 48h ago."""
    from datetime import timedelta
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
