"""export_bridge_context.py — Artha → OpenClaw context push.

Spec: specs/claw-bridge.md §P1.3 (Phase 1)

Transport stack (tried in order):
  Layer 3 — SSH   : scp artha_context.json → RPi4 workspace (Mac + home LAN only)
  Layer 2 — REST  : POST https://192.168.50.90:18790 /v1/chat/completions (Mac + home LAN only)
  Layer 1 — Telegram : urllib to api.telegram.org (all machines)
  Layer 0 — DLQ   : ~/.artha-local/bridge_dlq.yaml (local only, NOT OneDrive)

Security invariants:
  - No secrets in files or env vars — keyring only.
  - PII stripped via pii_guard.filter_text() before signing.
  - Prompt-injection filter (80-char + blocked-pattern list) on all free-text fields.
  - HMAC signature covers full canonical payload; replay prevented by ±5-min window.
  - All outcomes logged to state/audit.md via channel.audit._audit_log.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ── Path setup ────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# ── Imports from Artha codebase ───────────────────────────────────────────────
from channel.audit import _audit_log  # noqa: E402
from lib.hmac_signer import build_envelope  # noqa: E402
from pii_guard import filter_text as pii_filter_text  # noqa: E402

try:
    import keyring as _keyring
except ImportError:  # pragma: no cover
    _keyring = None  # type: ignore[assignment]

log = logging.getLogger("artha.export_bridge_context")

# ── Constants ─────────────────────────────────────────────────────────────────
_LOCAL_DIR = Path.home() / ".artha-local"
_PUSH_STATE_FILE = _LOCAL_DIR / "bridge_push_state.yaml"
_DLQ_FILE_DEFAULT = _LOCAL_DIR / "bridge_dlq.yaml"
_NUDGE_QUEUE_FILE = "tmp/bridge_nudge_queue.yaml"
_BRIDGE_SCHEMA = "claw-bridge/1.0"
_SRC_ARTHA = "artha"
_SKIP_INTERVAL_HOURS = 6   # skip push if version_hash unchanged within this window
_REST_TIMEOUT_SEC = 5

# Telegram Bot API base
_TG_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"


# ══════════════════════════════════════════════════════════════════════════════
# Config loading
# ══════════════════════════════════════════════════════════════════════════════

def _load_bridge_cfg(artha_dir: Path) -> dict[str, Any]:
    """Load config/claw_bridge.yaml. Returns empty dict on error (never raises)."""
    config_path = artha_dir / "config" / "claw_bridge.yaml"
    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return dict(data) if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("bridge_config_load_failed path=%s error=%s", config_path, exc)
        return {}


def _keyring_get(key: str) -> str | None:
    """Read a secret from OS keyring. Returns None if unavailable (never raises)."""
    if _keyring is None:
        log.warning("keyring_unavailable key=%s", key)
        return None
    try:
        return _keyring.get_password("artha", key) or None
    except Exception as exc:  # noqa: BLE001
        log.warning("keyring_read_failed key=%s error=%s", key, exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Network detection
# ══════════════════════════════════════════════════════════════════════════════

def _detect_is_home_lan(cfg: dict[str, Any]) -> bool:
    """Return True if home LAN is reachable (socket probe, 1s timeout).

    Probes HA (port 8123) — reliably reachable from Mac on home LAN.
    Falls back to the OpenClaw REST host if HA URL is not configured.
    Returns False on any failure (no raises).
    """
    # Prefer HA URL — always accessible from Mac on home LAN
    ha_url: str = (cfg.get("ha") or {}).get("url", "")
    if ha_url:
        host = ha_url.replace("https://", "").replace("http://", "").split("/")[0]
        host_part, _, port_str = host.partition(":")
        port = int(port_str) if port_str.isdigit() else 8123
        try:
            with socket.create_connection((host_part, port), timeout=1.0):
                return True
        except OSError:
            pass

    # Fallback: probe OpenClaw REST host
    rest_url: str = (cfg.get("openclaw") or {}).get("rest_url", "https://192.168.50.90:18790")
    host = rest_url.replace("https://", "").replace("http://", "").split("/")[0]
    host_part, _, port_str = host.partition(":")
    port = int(port_str) if port_str.isdigit() else 443
    try:
        with socket.create_connection((host_part, port), timeout=1.0):
            return True
    except OSError:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Injection filter
# ══════════════════════════════════════════════════════════════════════════════

def _injection_filter_check(text: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    """Return (passes: bool, reason: str).

    Checks max_title_chars and blocked_patterns from the bridge config.
    Safe to call with empty config — falls back to spec defaults.
    """
    inj_cfg: dict = (cfg.get("injection_filter") or {})
    max_chars: int = int(inj_cfg.get("max_title_chars", 80))
    blocked: list[str] = inj_cfg.get("blocked_patterns", [
        "ignore", "system:", "<|", "[INST]", "```",
        "<script", "ASSISTANT:", "USER:", "<s>", "</s>",
    ])

    if len(text) > max_chars:
        return False, f"length {len(text)} > max {max_chars}"

    text_lower = text.lower()
    for pattern in blocked:
        if pattern.lower() in text_lower:
            return False, f"blocked_pattern={pattern!r}"

    return True, ""


def _apply_injection_filter(texts: list[str], cfg: dict[str, Any], context: str = "") -> list[str]:
    """Filter a list of text strings through the injection filter.

    Items that fail the filter are dropped. Each rejection is audit-logged.
    Returns the passing items only.
    """
    passed: list[str] = []
    for text in texts:
        ok, reason = _injection_filter_check(text, cfg)
        if ok:
            passed.append(text)
        else:
            log.warning("injection_filter_blocked context=%s reason=%s", context, reason)
            _audit_log(
                "BRIDGE_INJECTION_BLOCKED",
                context=context,
                reason=reason,
                text_len=len(text),
            )
    return passed


# ══════════════════════════════════════════════════════════════════════════════
# State file readers
# ══════════════════════════════════════════════════════════════════════════════

def _read_open_items_p1(artha_dir: Path, limit: int, cfg: dict[str, Any]) -> list[str]:
    """Read state/open_items.md → return up to `limit` P1 item titles (filtered)."""
    path = artha_dir / "state" / "open_items.md"
    if not path.exists():
        return []

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("open_items_read_failed error=%s", exc)
        return []

    # Strategy 1: parse YAML frontmatter items block
    items: list[str] = []
    try:
        fm_text = _extract_frontmatter(text)
        if fm_text:
            fm: dict = yaml.safe_load(fm_text) or {}
            raw_items = fm.get("items", []) if isinstance(fm, dict) else []
            for item in (raw_items if isinstance(raw_items, list) else []):
                if not isinstance(item, dict):
                    continue
                if str(item.get("status", "open")).lower() not in ("open", "p1"):
                    continue
                priority = str(item.get("priority", "")).lower()
                if priority != "p1":
                    continue
                title = str(item.get("description", item.get("title", ""))).strip()
                if title:
                    items.append(title)
    except (yaml.YAMLError, Exception):  # noqa: BLE001
        pass

    # Strategy 2: markdown fallback — look for P1 markers
    if not items:
        for line in text.splitlines():
            stripped = line.strip()
            if re.search(r"\[P1\]|priority.*p1|🔴", stripped, re.IGNORECASE):
                # Extract the meaningful part
                cleaned = re.sub(r"\[.*?\]|\*\*|`", "", stripped).strip(" -•")
                if cleaned and len(cleaned) > 3:
                    items.append(cleaned)

    # PII filter + injection filter
    filtered: list[str] = []
    for title in items[:limit * 2]:  # over-fetch to account for filter drops
        clean_title, _ = pii_filter_text(title)
        filtered.append(clean_title)

    filtered = _apply_injection_filter(filtered, cfg, context="p1_items")
    return filtered[:limit]


def _read_active_goals(artha_dir: Path, limit: int, cfg: dict[str, Any]) -> list[str]:
    """Read state/goals.md → return up to `limit` active goal labels (filtered)."""
    path = artha_dir / "state" / "goals.md"
    if not path.exists():
        return []

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("goals_read_failed error=%s", exc)
        return []

    goals: list[str] = []

    # Strategy 1: YAML frontmatter
    try:
        fm_text = _extract_frontmatter(text)
        if fm_text:
            fm: dict = yaml.safe_load(fm_text) or {}
            raw_goals = fm.get("goals", []) if isinstance(fm, dict) else []
            for goal in (raw_goals if isinstance(raw_goals, list) else []):
                if not isinstance(goal, dict):
                    continue
                status = str(goal.get("status", "")).lower()
                if status not in ("active", "in_progress", "in-progress", "sprinting"):
                    continue
                label = str(goal.get("name", goal.get("label", ""))).strip()
                if label:
                    goals.append(label)
    except (yaml.YAMLError, Exception):  # noqa: BLE001
        pass

    # Strategy 2: markdown fallback — look for active/in-progress markers
    if not goals:
        for line in text.splitlines():
            if re.search(r"active|in.progress|sprinting", line, re.IGNORECASE):
                cleaned = re.sub(r"\[.*?\]|\*\*|`|#{1,6}", "", line).strip(" -•")
                if cleaned and len(cleaned) > 3:
                    goals.append(cleaned)

    # PII filter + injection filter
    filtered: list[str] = []
    for label in goals[:limit * 2]:
        clean_label, _ = pii_filter_text(label)
        filtered.append(clean_label)

    filtered = _apply_injection_filter(filtered, cfg, context="goals_active")
    return filtered[:limit]


def _read_deadlines_7d(artha_dir: Path, limit: int, cfg: dict[str, Any]) -> list[dict[str, str]]:
    """Read state/calendar.md and state/open_items.md → return deadlines in next 7 days.

    Returns list of dicts with 'date' (ISO date string) and 'label' (filtered text).
    Sorted by date ascending.
    """
    from datetime import date, timedelta
    today = date.today()
    cutoff = today + timedelta(days=7)

    events: list[tuple[date, str]] = []  # (date, label)

    # Scan calendar.md
    cal_path = artha_dir / "state" / "calendar.md"
    if cal_path.exists():
        try:
            cal_text = cal_path.read_text(encoding="utf-8", errors="replace")
            for line in cal_text.splitlines():
                # Match YYYY-MM-DD anywhere in line
                m = re.search(r"(\d{4}-\d{2}-\d{2})", line)
                if m:
                    try:
                        evt_date = date.fromisoformat(m.group(1))
                    except ValueError:
                        continue
                    if today <= evt_date <= cutoff:
                        label = re.sub(r"\d{4}-\d{2}-\d{2}|\[.*?\]|\*\*|`", "", line).strip(" -•:")
                        if label and len(label) > 3:
                            events.append((evt_date, label))
        except OSError as exc:
            log.warning("calendar_read_failed error=%s", exc)

    # Scan open_items.md deadlines
    oi_path = artha_dir / "state" / "open_items.md"
    if oi_path.exists():
        try:
            oi_text = oi_path.read_text(encoding="utf-8", errors="replace")
            fm_text = _extract_frontmatter(oi_text)
            if fm_text:
                fm: dict = yaml.safe_load(fm_text) or {}
                for item in (fm.get("items", []) if isinstance(fm, dict) else []):
                    if not isinstance(item, dict):
                        continue
                    deadline_str = str(item.get("deadline", "")).strip()
                    if not deadline_str:
                        continue
                    try:
                        dl_date = date.fromisoformat(deadline_str[:10])
                    except ValueError:
                        continue
                    if today <= dl_date <= cutoff:
                        label = str(item.get("description", item.get("title", ""))).strip()
                        if label:
                            events.append((dl_date, label))
        except (OSError, yaml.YAMLError):  # noqa: BLE001
            pass

    # Sort by date, deduplicate by label
    events.sort(key=lambda x: x[0])
    seen: set[str] = set()
    unique_events: list[tuple[date, str]] = []
    for evt_date, label in events:
        if label not in seen:
            seen.add(label)
            unique_events.append((evt_date, label))

    # PII + injection filter
    result: list[dict[str, str]] = []
    for evt_date, label in unique_events[:limit * 2]:
        clean_label, _ = pii_filter_text(label)
        ok, reason = _injection_filter_check(clean_label, cfg)
        if not ok:
            _audit_log("BRIDGE_INJECTION_BLOCKED", context="deadlines_7d", reason=reason)
            continue
        result.append({"date": evt_date.isoformat(), "label": clean_label})
        if len(result) >= limit:
            break

    return result


def _read_kid_flags(artha_dir: Path, limit: int, cfg: dict[str, Any]) -> list[str]:
    """Read state/kids.md or kids-relevant open items → return up to `limit` flag labels."""
    path = artha_dir / "state" / "kids.md"
    if not path.exists():
        return []

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("kids_read_failed error=%s", exc)
        return []

    flags: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"\[.*?\]|\*\*|`|#{1,6}", "", line).strip(" -•")
        if cleaned and len(cleaned) > 3:
            # Only include lines that have flag-like markers
            if re.search(r"⚠|flag|alert|remind|action|todo", cleaned, re.IGNORECASE):
                flags.append(cleaned)

    # PII filter + injection filter
    filtered: list[str] = []
    for flag in flags[:limit * 2]:
        clean_flag, _ = pii_filter_text(flag)
        ok, _ = _injection_filter_check(clean_flag, cfg)
        if ok:
            filtered.append(clean_flag)
    return filtered[:limit]


def _extract_frontmatter(text: str) -> str | None:
    """Extract YAML frontmatter block from markdown. Returns None if not present."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = -1
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end < 0:
        return None
    return "\n".join(lines[1:end])


# ══════════════════════════════════════════════════════════════════════════════
# Payload assembly
# ══════════════════════════════════════════════════════════════════════════════

def _build_payload(artha_dir: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    """Assemble the full load_context data payload from state files.

    Applies all cardinality limits, PII filtering, and injection filtering.
    Returns a plain dict suitable for passing to hmac_signer.build_envelope().
    """
    limits: dict = (cfg.get("payload_limits") or {})
    p1_limit = int(limits.get("p1_items", 5))
    goals_limit = int(limits.get("goals_active", 3))
    deadlines_limit = int(limits.get("deadlines_7d", 5))
    kid_limit = int(limits.get("kid_flags", 4))

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "p1_items": _read_open_items_p1(artha_dir, p1_limit, cfg),
        "goals_active": _read_active_goals(artha_dir, goals_limit, cfg),
        "deadlines_7d": _read_deadlines_7d(artha_dir, deadlines_limit, cfg),
        "kid_flags": _read_kid_flags(artha_dir, kid_limit, cfg),
    }
    return payload


# ══════════════════════════════════════════════════════════════════════════════
# Version hash + skip logic
# ══════════════════════════════════════════════════════════════════════════════

def _compute_version_hash(data: dict[str, Any]) -> str:
    """Compute SHA-256 of the canonical JSON representation of the payload."""
    # Exclude generated_at from hash — it changes every run
    stable = {k: v for k, v in data.items() if k != "generated_at"}
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_push_state() -> dict[str, Any]:
    """Load ~/.artha-local/bridge_push_state.yaml. Returns empty dict if missing."""
    if not _PUSH_STATE_FILE.exists():
        return {}
    try:
        with open(_PUSH_STATE_FILE, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return dict(data) if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("push_state_load_failed error=%s", exc)
        return {}


def _save_push_state(version_hash: str, pushed_at: str) -> None:
    """Persist push state to ~/.artha-local/bridge_push_state.yaml."""
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    state = {"version_hash": version_hash, "pushed_at": pushed_at}
    try:
        with open(_PUSH_STATE_FILE, "w", encoding="utf-8") as f:
            yaml.dump(state, f, allow_unicode=True)
    except OSError as exc:
        log.warning("push_state_save_failed error=%s", exc)


def _should_skip_push(version_hash: str) -> tuple[bool, str]:
    """Return (skip: bool, reason: str).

    Skip if hash is unchanged AND last push was < _SKIP_INTERVAL_HOURS ago.
    """
    state = _load_push_state()
    last_hash = state.get("version_hash", "")
    pushed_at_str = str(state.get("pushed_at", ""))

    if last_hash != version_hash:
        return False, "payload_changed"

    if not pushed_at_str:
        return False, "no_push_state"

    try:
        pushed_at = datetime.fromisoformat(pushed_at_str.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - pushed_at).total_seconds() / 3600
        if age_hours < _SKIP_INTERVAL_HOURS:
            return True, f"unchanged hash, last push {age_hours:.1f}h ago"
    except ValueError:
        return False, "push_state_parse_error"

    return False, f"stale: {_SKIP_INTERVAL_HOURS}h elapsed"


# ══════════════════════════════════════════════════════════════════════════════
# Transport Layer 3 — SSH file write (fastest, home LAN only)
# ══════════════════════════════════════════════════════════════════════════════

def _push_via_ssh(payload: dict[str, Any], cfg: dict[str, Any], dry_run: bool = False) -> bool:
    """Write artha_context.json directly to RPi4 workspace via SSH.

    Bypasses the bot polling direction problem entirely. Claw reads the file
    from its workspace on next interaction. Home LAN only.
    Returns True on success, False on any failure (never raises).
    """
    oc_cfg: dict = (cfg.get("openclaw") or {})
    ssh_host: str = oc_cfg.get("ssh_host", "homeassistant.local")
    ssh_user: str = oc_cfg.get("ssh_user", "root")
    ssh_port: int = int(oc_cfg.get("ssh_port", 22))
    remote_path: str = oc_cfg.get(
        "workspace_context_path",
        "/config/.openclaw/workspace/artha_context.json",
    )

    context_doc = {
        "received_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema": _BRIDGE_SCHEMA,
        "cmd": "load_context",
        "payload": payload,
    }
    json_bytes = json.dumps(context_doc, indent=2, ensure_ascii=False).encode("utf-8")

    if dry_run:
        log.info("[DRY-RUN] Would SSH %s@%s:%s write %d bytes", ssh_user, ssh_host, remote_path, len(json_bytes))
        return True

    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout=5",
        "-p", str(ssh_port),
        f"{ssh_user}@{ssh_host}",
        f"mkdir -p $(dirname {remote_path}) && cat > {remote_path}",
    ]
    try:
        result = subprocess.run(
            ssh_cmd,
            input=json_bytes,
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            log.info("bridge_push_ssh_ok host=%s path=%s bytes=%d", ssh_host, remote_path, len(json_bytes))
            return True
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        log.warning("bridge_push_ssh_nonzero rc=%d stderr=%s", result.returncode, stderr[:120])
        return False
    except subprocess.TimeoutExpired:
        log.warning("bridge_push_ssh_timeout host=%s", ssh_host)
        return False
    except OSError as exc:
        log.warning("bridge_push_ssh_os_error error=%s", exc)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Transport Layer 2 — REST
# ══════════════════════════════════════════════════════════════════════════════

def _push_via_rest(envelope: dict[str, Any], cfg: dict[str, Any], dry_run: bool = False) -> bool:
    """POST signed envelope to OpenClaw REST endpoint.

    Mac + home LAN only (caller must gate with _detect_is_home_lan()).
    Returns True on success, False on any failure (never raises).
    """
    oc_cfg: dict = (cfg.get("openclaw") or {})
    rest_url: str = oc_cfg.get("rest_url", "https://192.168.50.90:18789")
    token_key: str = oc_cfg.get("rest_token_keyring_key", "artha-openclaw-rest-token")
    timeout: int = int((cfg.get("push") or {}).get("rest_timeout_sec", _REST_TIMEOUT_SEC))

    if dry_run:
        log.info("[DRY-RUN] Would POST to REST: %s", rest_url)
        return True

    bearer_token = _keyring_get(token_key)
    if not bearer_token:
        log.warning("rest_token_missing keyring_key=%s", token_key)
        return False

    # Send as standard chat completions request — Claw reads ARTHA_BRIDGE.md from workspace.
    # No custom model/skill needed; OpenClaw routes to its configured default LLM.
    cmd = envelope.get("cmd", "load_context")
    body = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are OpenClaw on Home Assistant. "
                    "Process the following Artha bridge message according to "
                    "ARTHA_BRIDGE.md in your workspace."
                ),
            },
            {
                "role": "user",
                "content": f"[artha-bridge cmd={cmd}]\n{json.dumps(envelope)}",
            },
        ],
        "stream": False,
    }
    body_bytes = json.dumps(body).encode("utf-8")
    url = rest_url.rstrip("/") + "/v1/chat/completions"

    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
        if status in (200, 201, 202):
            log.info("bridge_push_rest_ok status=%s", status)
            return True
        log.warning("bridge_push_rest_non200 status=%s", status)
        return False
    except urllib.error.HTTPError as exc:
        log.warning("bridge_push_rest_http_error status=%s error=%s", exc.code, exc)
        return False
    except OSError as exc:
        log.warning("bridge_push_rest_os_error error=%s", exc)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Transport Layer 1 — Telegram M2M
# ══════════════════════════════════════════════════════════════════════════════

def _push_via_telegram(envelope: dict[str, Any], cfg: dict[str, Any], dry_run: bool = False) -> bool:
    """Send signed envelope to OpenClaw via Telegram M2M bot.

    Universal (all machines). Returns True on success, False on failure (never raises).
    """
    oc_cfg: dict = (cfg.get("openclaw") or {})
    token_key: str = oc_cfg.get("m2m_token_keyring_key", "openclaw-m2m-bot-token")
    chat_id_key: str = oc_cfg.get("m2m_outbound_chat_id_keyring_key", "artha-claw-tg-chat-id")

    if dry_run:
        log.info("[DRY-RUN] Would send via Telegram M2M")
        return True

    bot_token = _keyring_get(token_key)
    if not bot_token:
        log.warning("tg_token_missing keyring_key=%s", token_key)
        return False

    chat_id = _keyring_get(chat_id_key)
    if not chat_id:
        log.warning("tg_chat_id_missing keyring_key=%s", chat_id_key)
        return False

    # Validate chat_id is numeric (positive or negative integer — spec §P1.3 tg_send.sh pattern)
    if not re.fullmatch(r"-?\d+", chat_id.strip()):
        log.warning("tg_chat_id_invalid value=<redacted>")
        _audit_log("BRIDGE_TG_CHAT_ID_INVALID")
        return False

    message_text = json.dumps(envelope, separators=(",", ":"))

    # Telegram max message length is 4096 chars; M2M envelopes are typically ~1KB
    if len(message_text) > 4096:
        log.warning("tg_message_too_long len=%d — truncating not allowed, failing", len(message_text))
        return False

    url = _TG_API_BASE.format(token=bot_token)
    body = json.dumps({"chat_id": chat_id.strip(), "text": message_text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            response_body = json.loads(resp.read().decode("utf-8"))
        if response_body.get("ok"):
            log.info("bridge_push_tg_ok")
            return True
        err = response_body.get("description", "unknown")
        log.warning("bridge_push_tg_api_error error=%s", err)
        return False
    except urllib.error.HTTPError as exc:
        log.warning("bridge_push_tg_http_error status=%s error=%s", exc.code, exc)
        return False
    except OSError as exc:
        log.warning("bridge_push_tg_os_error error=%s", exc)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Transport Layer 0 — Dead Letter Queue
# ══════════════════════════════════════════════════════════════════════════════

def _dlq_path(cfg: dict[str, Any]) -> Path:
    """Resolve the DLQ file path (platform-local, not OneDrive)."""
    raw: str = ((cfg.get("push") or {}).get("dead_letter") or {}).get("file", "")
    if raw:
        return Path(raw.replace("~", str(Path.home())))
    return _DLQ_FILE_DEFAULT


def _load_dlq(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Load DLQ YAML. Returns empty list on error. Auto-expires entries older than max_age_hours."""
    dlq_file = _dlq_path(cfg)
    if not dlq_file.exists():
        return []
    try:
        with open(dlq_file, encoding="utf-8") as f:
            entries = yaml.safe_load(f) or []
        if not isinstance(entries, list):
            return []
    except (OSError, yaml.YAMLError) as exc:
        log.warning("dlq_load_failed error=%s", exc)
        return []

    max_age_hours = int(((cfg.get("push") or {}).get("dead_letter") or {}).get("max_age_hours", 24))
    now = datetime.now(timezone.utc)
    valid: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            queued_at = datetime.fromisoformat(
                str(entry.get("queued_at", "")).replace("Z", "+00:00")
            )
            age_hours = (now - queued_at).total_seconds() / 3600
            if age_hours <= max_age_hours:
                valid.append(entry)
        except (ValueError, TypeError):
            # Corrupt entry — discard
            pass
    return valid


def _save_dlq(entries: list[dict[str, Any]], cfg: dict[str, Any]) -> None:
    """Persist DLQ entries to disk (platform-local)."""
    dlq_file = _dlq_path(cfg)
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    dlq_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(dlq_file, "w", encoding="utf-8") as f:
            yaml.dump(entries, f, allow_unicode=True, default_flow_style=False)
    except OSError as exc:
        log.warning("dlq_save_failed error=%s", exc)


def _write_dlq(envelope: dict[str, Any], error: str, cfg: dict[str, Any]) -> None:
    """Append failed envelope to DLQ. Prunes expired entries on write."""
    entries = _load_dlq(cfg)
    new_entry: dict[str, Any] = {
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "envelope": envelope,
        "last_error": str(error)[:200],
        "attempts": 1,
    }
    entries.append(new_entry)
    _save_dlq(entries, cfg)
    log.info("bridge_dlq_write entries_now=%d", len(entries))
    _audit_log("BRIDGE_DLQ_WRITE", attempts=1, error=str(error)[:80])


def _retry_dlq(cfg: dict[str, Any], is_home_lan: bool, dry_run: bool = False) -> int:
    """Retry all non-expired DLQ entries. Returns number of successfully retried entries."""
    entries = _load_dlq(cfg)
    if not entries:
        return 0

    remaining: list[dict[str, Any]] = []
    retried = 0
    for entry in entries:
        envelope: dict[str, Any] = entry.get("envelope", {})
        if not envelope:
            continue
        # Try REST if on home LAN, then Telegram
        ok = False
        if is_home_lan:
            ok = _push_via_rest(envelope, cfg, dry_run=dry_run)
        if not ok:
            ok = _push_via_telegram(envelope, cfg, dry_run=dry_run)

        if ok:
            retried += 1
            _audit_log(
                "BRIDGE_DLQ_RETRY_OK",
                cmd=str(envelope.get("cmd", "?")),
                attempts=int(entry.get("attempts", 1)) + 1,
            )
        else:
            entry["attempts"] = int(entry.get("attempts", 1)) + 1
            entry["last_error"] = "retry_failed"
            remaining.append(entry)

    _save_dlq(remaining, cfg)
    return retried


# ══════════════════════════════════════════════════════════════════════════════
# Nudge queue
# ══════════════════════════════════════════════════════════════════════════════

def _process_nudge_queue(
    artha_dir: Path, cfg: dict[str, Any], is_home_lan: bool, dry_run: bool = False
) -> int:
    """Read tmp/bridge_nudge_queue.yaml; push announce for urgency≥3 entries.

    On successful delivery, renames the file to tmp/bridge_nudge_queue.processed.yaml
    to prevent re-processing. If delivery fails and DLQ also fails, the queue
    file is left in place for the next run.

    Returns number of nudge announces pushed.
    """
    queue_file = artha_dir / _NUDGE_QUEUE_FILE
    processed_file = queue_file.with_stem(queue_file.stem + ".processed")

    if not queue_file.exists():
        return 0

    try:
        with open(queue_file, encoding="utf-8") as f:
            entries = yaml.safe_load(f) or []
    except (OSError, yaml.YAMLError) as exc:
        log.warning("nudge_queue_read_failed error=%s", exc)
        return 0

    if not isinstance(entries, list):
        return 0

    max_chars: int = int((cfg.get("payload_limits") or {}).get("announce_text_max_chars", 200))
    pushed = 0
    all_ok = True

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        urgency = int(entry.get("urgency", 0))
        if urgency < 3:
            continue

        nudge_type = str(entry.get("nudge_type", ""))
        domain = str(entry.get("domain", ""))
        triggered_at = str(entry.get("triggered_at", ""))

        # Build announce text from nudge metadata (no PII — no domain-specific data)
        announce_text = f"Artha alert: {nudge_type} in {domain} domain (urgency {urgency})"
        if len(announce_text) > max_chars:
            announce_text = announce_text[:max_chars - 1] + "…"

        announce_data: dict[str, Any] = {
            "text": announce_text,
            "nudge_type": nudge_type,
            "domain": domain,
            "urgency": urgency,
            "triggered_at": triggered_at,
        }

        hmac_key = str(cfg.get("hmac_secret_keyring_key", "artha-claw-bridge-hmac"))
        try:
            envelope = build_envelope(_SRC_ARTHA, "announce", announce_data, keyring_key=hmac_key)
        except Exception as exc:  # noqa: BLE001
            log.warning("nudge_envelope_build_failed error=%s", exc)
            all_ok = False
            continue

        ok = False
        if is_home_lan:
            ok = _push_via_rest(envelope, cfg, dry_run=dry_run)
        if not ok:
            ok = _push_via_telegram(envelope, cfg, dry_run=dry_run)

        if ok:
            pushed += 1
            _audit_log(
                "BRIDGE_NUDGE_ANNOUNCE_SENT",
                nudge_type=nudge_type,
                domain=domain,
                urgency=urgency,
            )
        else:
            all_ok = False
            # Try DLQ
            _write_dlq(envelope, "nudge_push_failed", cfg)
            _audit_log(
                "BRIDGE_NUDGE_ANNOUNCE_DLQ",
                nudge_type=nudge_type,
                domain=domain,
                urgency=urgency,
            )

    if all_ok and pushed > 0:
        # Rename to .processed to prevent re-processing
        try:
            processed_file.unlink(missing_ok=True)
            queue_file.rename(processed_file)
            log.info("nudge_queue_processed pushed=%d", pushed)
        except OSError as exc:
            log.warning("nudge_queue_rename_failed error=%s", exc)

    return pushed


# ══════════════════════════════════════════════════════════════════════════════
# Alexa announcement
# ══════════════════════════════════════════════════════════════════════════════

def _announce_via_alexa(cfg: dict[str, Any], dry_run: bool = False) -> bool:
    """Fire a TTS announcement on an Alexa device via HA notify service.

    Only called after a successful bridge push when on home LAN.
    Uses notify.alexa_media_{device} which is the verified working method.
    Silently no-ops on any error — announcement is best-effort.
    """
    ha_cfg = cfg.get("ha") or {}
    if not ha_cfg.get("announce_on_push", False):
        return False

    ha_url = ha_cfg.get("url", "http://homeassistant.local:8123").rstrip("/")
    token_key = ha_cfg.get("token_keyring_key", "artha-ha-token")
    device = ha_cfg.get("announce_device", "everywhere")
    message = ha_cfg.get("announce_text", "Artha context updated")
    timeout = int(ha_cfg.get("announce_timeout_sec", 5))

    if _keyring is None:
        log.debug("alexa_announce_skip reason=no_keyring")
        return False

    try:
        token = _keyring.get_password("artha", token_key) or ""
        if not token:
            log.warning("alexa_announce_skip reason=no_ha_token key=%s", token_key)
            return False

        if dry_run:
            log.info("alexa_announce_dryrun device=%s message=%r", device, message)
            return True

        service_url = f"{ha_url}/api/services/notify/alexa_media_{device}"
        body = json.dumps({"message": message, "data": {"type": "tts"}}).encode()
        req = urllib.request.Request(
            service_url,
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        log.info("alexa_announce_ok device=%s", device)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("alexa_announce_failed device=%s error=%s", device, exc)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Main orchestrator
# ══════════════════════════════════════════════════════════════════════════════

def run_bridge_push(artha_dir: Path, cfg: dict[str, Any], dry_run: bool = False) -> int:
    """Execute the full bridge push cycle.

    Sequence:
      1. Probe home LAN reachability
      2. Retry any DLQ entries
      3. Check nudge queue for urgency≥3 announces
      4. Build payload + compute version_hash
      5. Skip push if payload unchanged + last push < 6h
      6. Build signed envelope
      7. Try REST (home LAN) → fall back to Telegram
      8. On both fail → write to DLQ

    Returns 0 on success (or skip), 1 on both-transport failure.
    """
    if not cfg.get("enabled", False):
        log.debug("bridge_push_skipped: bridge not enabled")
        return 0

    is_home_lan = _detect_is_home_lan(cfg)
    log.debug("home_lan_reachable=%s", is_home_lan)

    # ── Step 1: Retry DLQ ──────────────────────────────────────────────────
    if (cfg.get("push") or {}).get("dead_letter", {}).get("retry_on_next_pipeline", True):
        retried = _retry_dlq(cfg, is_home_lan, dry_run=dry_run)
        if retried:
            _audit_log("BRIDGE_DLQ_RETRY", count=retried)

    # ── Step 2: Nudge queue ────────────────────────────────────────────────
    nudge_count = _process_nudge_queue(artha_dir, cfg, is_home_lan, dry_run=dry_run)
    if nudge_count:
        log.info("bridge_nudge_announces_sent count=%d", nudge_count)

    # ── Step 3: Build payload ─────────────────────────────────────────────
    try:
        data = _build_payload(artha_dir, cfg)
    except Exception as exc:  # noqa: BLE001
        log.error("bridge_payload_build_failed error=%s", exc)
        _audit_log("BRIDGE_PAYLOAD_BUILD_FAILED", error=str(exc)[:120])
        return 1

    # ── Step 4: Version hash + skip check ────────────────────────────────
    version_hash = _compute_version_hash(data)
    skip, skip_reason = _should_skip_push(version_hash)
    if skip:
        log.info("bridge_push_skipped reason=%s", skip_reason)
        _audit_log("BRIDGE_PUSH_SKIPPED", reason=skip_reason, hash=version_hash[:16])
        return 0

    # ── Step 5: Build signed envelope ────────────────────────────────────
    hmac_key = str(cfg.get("hmac_secret_keyring_key", "artha-claw-bridge-hmac"))
    try:
        envelope = build_envelope(_SRC_ARTHA, "load_context", data, keyring_key=hmac_key)
    except Exception as exc:  # noqa: BLE001
        log.error("bridge_envelope_build_failed error=%s", exc)
        _audit_log("BRIDGE_ENVELOPE_BUILD_FAILED", error=str(exc)[:120])
        return 1

    trace_id = envelope.get("trace_id", "?")

    # ── Step 6: Transport ─────────────────────────────────────────────────
    # Priority: SSH (direct file write) → REST → Telegram → DLQ
    # SSH and REST are home-LAN-only; Telegram is universal.
    ok = False
    transport_used = "none"

    if is_home_lan:
        ok = _push_via_ssh(data, cfg, dry_run=dry_run)
        if ok:
            transport_used = "ssh"

    if not ok and is_home_lan:
        ok = _push_via_rest(envelope, cfg, dry_run=dry_run)
        if ok:
            transport_used = "rest"

    if not ok:
        ok = _push_via_telegram(envelope, cfg, dry_run=dry_run)
        if ok:
            transport_used = "telegram"

    if ok:
        pushed_at = datetime.now(timezone.utc).isoformat()
        _save_push_state(version_hash, pushed_at)
        _audit_log(
            "BRIDGE_PUSH_OK",
            transport=transport_used,
            trace_id=trace_id,
            p1_items=len(data.get("p1_items", [])),
            goals_active=len(data.get("goals_active", [])),
            deadlines_7d=len(data.get("deadlines_7d", [])),
        )
        log.info("bridge_push_ok transport=%s trace_id=%s", transport_used, trace_id)
        if is_home_lan:
            _announce_via_alexa(cfg, dry_run=dry_run)
        return 0
    else:
        # Both transports failed → DLQ
        last_error = "REST+Telegram both failed"
        _write_dlq(envelope, last_error, cfg)
        _audit_log(
            "BRIDGE_PUSH_FAILED",
            trace_id=trace_id,
            transport=transport_used,
            error=last_error,
        )
        log.error("bridge_push_failed trace_id=%s — queued to DLQ", trace_id)
        return 1


# ══════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="export_bridge_context",
        description="Artha → OpenClaw context push (bridge Layer 1/2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Build and sign payload but do not send. Logs actions instead.",
    )
    parser.add_argument(
        "--artha-dir",
        type=Path,
        default=_ARTHA_DIR,
        metavar="DIR",
        help="Override Artha workspace root (default: auto-detected from script location).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable DEBUG logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    artha_dir = args.artha_dir.resolve()
    cfg = _load_bridge_cfg(artha_dir)

    if not cfg:
        log.error("bridge_config_empty — cannot proceed")
        return 1

    return run_bridge_push(artha_dir, cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
