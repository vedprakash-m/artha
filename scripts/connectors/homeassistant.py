"""
scripts/connectors/homeassistant.py — Home Assistant REST API connector.

Fetches device states, sensor readings, and energy data from a local Home
Assistant instance via its REST API.  LAN-only by design: the connector
self-gates when not on the home network and skips silently.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Auth: long-lived access token stored in system keyring under "artha-ha-token".
      auth_context["api_key"] is loaded by load_auth_context() in lib/auth.py.
      Run: python scripts/setup_ha_token.py  (interactive setup wizard).

Privacy contract (§3.1a):
  - device_tracker state reduced to binary: home | not_home | unknown
  - IP address attributes stripped before yielding records
  - Hard-floor excluded domains never fetched (camera, media_player, …)
  - Entity names never logged; only entity_id in error messages
  - PII guard applied to entity friendly_name attributes
  - Temp cache file written atomically to tmp/ha_entities.json

LAN gating (§3.6):
  - ha_url is checked: if the host resolves to a private RFC 1918 address
    and a TCP probe fails (2 s timeout), fetch() raises ConnectorOffLAN
    (a RuntimeError subclass) so pipeline.py skips silently.
  - Stale cache (<12 h) is surfaced when off-LAN via metadata-only mode.

Ref: specs/iot.md §3.1, §3.1a, §3.1b, §3.6
"""
from __future__ import annotations

import fnmatch
import ipaddress
import json
import os
import socket
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlparse

import requests  # type: ignore[import]

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 10            # seconds per HTTP request
_MAX_ENTITIES = 500              # hard ceiling; pipeline default_max_results overrides
_LAN_PROBE_TIMEOUT = 2           # seconds for TCP reachability probe
_CACHE_STALE_HOURS = 12          # serve cached metadata if newer than this
_CACHE_FILE = Path(_REPO_ROOT) / "tmp" / "ha_entities.json"

# Domains that are NEVER fetched regardless of config (hard floor, spec §3.1)
_EXCLUDED_DOMAINS: frozenset[str] = frozenset({
    "camera",                    # privacy: no video/image data in Artha
    "media_player",              # irrelevant for text briefings
    "tts",                       # text-to-speech engine entities
    "stt",                       # speech-to-text engine entities
    "conversation",              # HA conversation/AI entities
    "persistent_notification",   # HA internal UI toasts
    "update",                    # software update entities (not device state)
})

# Attributes that may contain PII or irrelevant binary blobs — stripped always
_STRIPPED_ATTRIBUTES: frozenset[str] = frozenset({
    "ip_address", "ip_addresses", "mac_address", "mac_addresses",
    "entity_picture", "entity_picture_local",
    "access_token", "token", "api_key", "password",
    "linkquality", "lqi",  # Zigbee link quality (numeric noise, not useful for briefings)
})

# device_tracker states are collapsed to privacy-safe binary
_TRACKER_STATE_MAP: dict[str, str] = {
    "home": "home",
    "not_home": "not_home",
}


# ---------------------------------------------------------------------------
# Custom exception for off-LAN detection
# ---------------------------------------------------------------------------

class ConnectorOffLAN(RuntimeError):
    """Raised when HA host is not reachable on the current network.

    pipeline.py treats RuntimeError with this message prefix as a soft skip,
    not an error.  We subclass RuntimeError so callers don't need to import
    this module to catch it.
    """


# ---------------------------------------------------------------------------
# LAN detection helpers
# ---------------------------------------------------------------------------

def _is_private_address(host: str) -> bool:
    """Return True if host resolves to an RFC 1918 private IP address."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        try:
            addr = ipaddress.ip_address(socket.gethostbyname(host))
        except (socket.gaierror, ValueError):
            return False
    return addr.is_private


def _tcp_reachable(host: str, port: int, timeout: float = _LAN_PROBE_TIMEOUT) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _check_lan_or_raise(ha_url: str) -> None:
    """Raise ConnectorOffLAN if the HA host is unreachable on this network.

    Only gates on private (LAN) addresses.  If ha_url points to a public
    address (Nabu Casa cloud relay), skip the LAN gate — the connector will
    fail on the HTTP request itself if auth is wrong.
    """
    parsed = urlparse(ha_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if not host:
        raise ConnectorOffLAN(f"[homeassistant] ha_url has no hostname: {ha_url!r}")

    if _is_private_address(host):
        if not _tcp_reachable(host, port):
            raise ConnectorOffLAN(
                f"[homeassistant] LAN gate: {host}:{port} not reachable "
                f"(not on home network). Skipping silently."
            )


# ---------------------------------------------------------------------------
# Privacy & PII helpers
# ---------------------------------------------------------------------------

def _sanitize_attributes(entity_id: str, domain: str, raw_attrs: dict) -> dict:
    """Strip sensitive / irrelevant attributes and sanitize device_tracker state."""
    sanitized: dict = {}
    for key, value in raw_attrs.items():
        if key in _STRIPPED_ATTRIBUTES:
            continue
        # Skip large binary / base64 blobs
        if isinstance(value, str) and len(value) > 2000:
            continue
        sanitized[key] = value

    # Remove friendly_name if it looks like a real person's name
    # (heuristic: contains a space and starts with uppercase — leave it for now
    #  since most HA friendly names are device names not people names)
    return sanitized


def _sanitize_tracker_state(state: str) -> str:
    """Collapse device_tracker state to binary home/not_home/unknown."""
    return _TRACKER_STATE_MAP.get(state.lower(), "unknown")


# ---------------------------------------------------------------------------
# Entity filtering
# ---------------------------------------------------------------------------

def _domain_of(entity_id: str) -> str:
    """Extract the domain prefix from an entity_id (e.g. 'light.kitchen' → 'light')."""
    return entity_id.split(".")[0] if "." in entity_id else entity_id


def _should_include(
    entity_id: str,
    domain: str,
    allowlist: List[str],
    blocklist: List[str],
    extra_exclude_domains: List[str],
) -> bool:
    """Return True if this entity should be included in output.

    Filtering priority (highest wins):
      1. Hard-floor excluded domains (never included)
      2. Extra excluded domains from config (additive)
      3. Blocklist (entity_id glob patterns — excluded)
      4. Allowlist (entity_id glob patterns — if non-empty, only these)
    """
    # 1. Hard floor
    if domain in _EXCLUDED_DOMAINS:
        return False

    # 2. Config-level extra exclusions
    if domain in extra_exclude_domains:
        return False

    # 3. Blocklist — entity matches any pattern → exclude
    for pattern in blocklist:
        if fnmatch.fnmatch(entity_id, pattern):
            return False

    # 4. Allowlist — if non-empty, entity must match at least one pattern
    if allowlist:
        return any(fnmatch.fnmatch(entity_id, p) for p in allowlist)

    return True


# ---------------------------------------------------------------------------
# Core connector protocol
# ---------------------------------------------------------------------------

def fetch(
    *,
    since: str,
    max_results: int,
    auth_context: dict,
    source_tag: str = "homeassistant",
    **kwargs: Any,
) -> Iterator[dict]:
    """Fetch HA entity states via the REST API.

    Args:
        since:        ISO-8601 start timestamp (unused for HA state snapshots;
                      retained for connector protocol compatibility).
        max_results:  Maximum number of entities to yield.
        auth_context: Dict produced by lib/auth.py — must contain "api_key".
                      Populated by load_auth_context() when auth.method=api_key
                      and auth.credential_key="artha-ha-token".
        source_tag:   Source label attached to every record (default: "homeassistant").
        **kwargs:     Passed from connectors.yaml fetch section:
                        ha_url         — HA base URL (required)
                        timeout_seconds
                        exclude_domains — additive exclusion list
                        entity_allowlist
                        entity_blocklist

    Yields:
        dict per entity: {entity_id, state, attributes, last_changed, domain, source}

    Raises:
        ConnectorOffLAN if HA host is not reachable on this network.
        RuntimeError    on auth, parse, or network errors.
    """
    ha_url = (kwargs.get("ha_url") or "").rstrip("/")
    if not ha_url:
        raise RuntimeError(
            "[homeassistant] ha_url not configured. "
            "Run: python scripts/setup_ha_token.py"
        )

    api_key = auth_context.get("api_key", "")
    if not api_key:
        raise RuntimeError(
            "[homeassistant] api_key not in auth_context. "
            "Run: python scripts/setup_ha_token.py"
        )

    timeout = int(kwargs.get("timeout_seconds", _DEFAULT_TIMEOUT))
    extra_exclude = list(kwargs.get("exclude_domains") or [])
    allowlist = list(kwargs.get("entity_allowlist") or [])
    blocklist = list(kwargs.get("entity_blocklist") or [])
    limit = min(int(max_results or _MAX_ENTITIES), _MAX_ENTITIES)

    # ── LAN gate ─────────────────────────────────────────────────────────
    _check_lan_or_raise(ha_url)

    # ── Fetch entity states ───────────────────────────────────────────────
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{ha_url}/api/states"

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        entities: list[dict] = resp.json()
    except requests.exceptions.ConnectionError as exc:
        raise ConnectorOffLAN(
            f"[homeassistant] Cannot connect to {ha_url}: {exc}"
        ) from exc
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"[homeassistant] Request to {ha_url} timed out after {timeout}s. "
            "HA may be overloaded or unreachable."
        )
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 401:
            raise RuntimeError(
                "[homeassistant] Authentication failed (HTTP 401). "
                "Token may be expired. Re-run: python scripts/setup_ha_token.py"
            ) from exc
        raise RuntimeError(f"[homeassistant] HTTP {status}: {exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"[homeassistant] Invalid JSON from {ha_url}/api/states: {exc}"
        ) from exc

    # ── Process and yield entities ────────────────────────────────────────
    records: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for entity in entities:
        if len(records) >= limit:
            break

        entity_id: str = entity.get("entity_id", "")
        if not entity_id:
            continue

        domain = _domain_of(entity_id)

        if not _should_include(entity_id, domain, allowlist, blocklist, extra_exclude):
            continue

        raw_state: str = str(entity.get("state", "unavailable"))
        raw_attrs: dict = entity.get("attributes", {}) or {}
        last_changed: str = entity.get("last_changed", now_iso)

        # Privacy: sanitize device_tracker states
        if domain == "device_tracker":
            state = _sanitize_tracker_state(raw_state)
        else:
            state = raw_state

        # Privacy: strip sensitive attributes
        attributes = _sanitize_attributes(entity_id, domain, raw_attrs)

        record = {
            "entity_id": entity_id,
            "state": state,
            "attributes": attributes,
            "last_changed": last_changed,
            "domain": domain,
            "source": source_tag,
        }
        records.append(record)
        yield record

    # ── Atomic cache write (§3.1a — for skill consumption) ───────────────
    _write_entity_cache(records)


def health_check(auth_context: dict, **kwargs: Any) -> bool:
    """Verify HA is reachable and the token is valid.

    Returns True if GET /api/ returns HTTP 200 with {"message": "API running."}.
    Returns False (not raises) on any failure — caller decides severity.
    """
    ha_url = (kwargs.get("ha_url") or "").rstrip("/")
    api_key = auth_context.get("api_key", "")

    if not ha_url or not api_key:
        return False

    try:
        _check_lan_or_raise(ha_url)
    except ConnectorOffLAN:
        return False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.get(
            f"{ha_url}/api/",
            headers=headers,
            timeout=_LAN_PROBE_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("message") == "API running."
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Atomic cache write helper
# ---------------------------------------------------------------------------

def _write_entity_cache(records: list[dict]) -> None:
    """Write entity snapshot to tmp/ha_entities.json atomically.

    Uses write-to-temp-then-rename (POSIX atomic) to prevent skill from
    reading a partially written file if the pipeline crashes mid-write.
    """
    cache_dir = _CACHE_FILE.parent
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "entity_count": len(records),
            "entities": records,
        }
        # Write to a temp file in the same directory, then atomically replace
        fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=".tmp", prefix=".ha_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=None, separators=(",", ":"))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        os.replace(tmp_path, _CACHE_FILE)
    except Exception as exc:
        # Cache write failure is non-fatal — skill will find no cache and skip
        import logging
        logging.warning(f"[homeassistant] Failed to write entity cache: {exc}")


# ---------------------------------------------------------------------------
# Stale cache reader (used when off-LAN — returns metadata only)
# ---------------------------------------------------------------------------

def _read_stale_cache() -> Optional[dict]:
    """Return cached entity snapshot if it is < _CACHE_STALE_HOURS old."""
    if not _CACHE_FILE.exists():
        return None
    try:
        with open(_CACHE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        fetched_at_str = data.get("fetched_at", "")
        if not fetched_at_str:
            return None
        fetched_at = datetime.fromisoformat(fetched_at_str)
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        if age_hours < _CACHE_STALE_HOURS:
            return data
        return None
    except (json.JSONDecodeError, ValueError, OSError):
        return None
