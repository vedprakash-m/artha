"""channel/m2m_handler.py — Inbound M2M message handler for the Artha ↔ OpenClaw bridge.

Spec: specs/claw-bridge.md §P2.2

Validates and routes inbound envelopes from OpenClaw:
  1. Schema detection (`is_m2m_message`)
  2. Envelope validation: allowlist → sender ID → timestamp → nonce replay → HMAC
  3. Per-command routing → `~/.artha-local/home_events_buffer.jsonl` (JSONL append, DEBT-037)

Security invariants:
  - HMAC is the primary guard for all inbound messages.
  - Injection filter is NOT applied to inbound (HMAC is the guard — spec §6.2).
  - Only known sender_id bot IDs are accepted; all others silently dropped.
  - Advisory fcntl lock prevents concurrent writes on macOS/Linux.
  - On Windows (fcntl unavailable): lock is skipped — single-listener enforcement
    via channels.yaml → defaults.listener_host is the guard on Windows.
  - All outcomes logged to state/audit.md via channel.audit._audit_log.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

# ── Path setup ────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent       # scripts/channel/
_ARTHA_DIR  = _SCRIPT_DIR.parent.parent             # Artha root

# ── fcntl (Windows falls back gracefully) ─────────────────────────────────────
try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None  # type: ignore[assignment]

# ── Codebase imports ──────────────────────────────────────────────────────────
import sys as _sys
if str(_SCRIPT_DIR.parent) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPT_DIR.parent))

from channel.audit import _audit_log               # noqa: E402
from channel.security import _RateLimiter          # noqa: E402
from channel.security import _MessageDeduplicator  # noqa: E402
from lib import hmac_signer                        # noqa: E402

try:
    import keyring as _keyring
except ImportError:
    _keyring = None  # type: ignore[assignment]

log = logging.getLogger("artha.m2m_handler")

# ── Constants ─────────────────────────────────────────────────────────────────
_BRIDGE_SCHEMA          = "claw-bridge/1.0"
_TIMESTAMP_TOLERANCE_SEC = 300          # ±5 minutes — must match hmac_signer
# DEBT-037: Buffer lives in local-only ~/.artha-local/ to prevent OneDrive cloud-sync of IoT telemetry.
_BUFFER_FILE            = "home_events_buffer.jsonl"
_CLOCK_WARN_MIN         = 2            # log BRIDGE_CLOCK_DRIFT if drift > this
_CLOCK_CRITICAL_MIN     = 5            # escalate to CRITICAL if drift > this

# Domains that OpenClaw may query via query_artha (spec §9 + Appendix A).
# SECURITY: blocked domains must NEVER appear in the allowed set.
QUERY_ALLOWED_DOMAINS: frozenset[str] = frozenset({
    "goals", "calendar", "open_items", "home", "learning",
})
QUERY_BLOCKED_DOMAINS: frozenset[str] = frozenset({
    "health", "kids", "vehicle", "travel", "comms",
    "finance", "estate", "insurance", "immigration",
})

# ── Module-level singletons ───────────────────────────────────────────────────
# Rate limiter: 20 messages per hour per spec §6.3.
_rate_limiter  = _RateLimiter(max_per_window=20, window_sec=3600.0, cooldown_sec=600.0)
# Nonce dedup: rejects seen nonces to emit BRIDGE_NONCE_REPLAY audit events.
_nonce_dedup   = _MessageDeduplicator(max_size=2048)


# ── Public API ─────────────────────────────────────────────────────────────────

def is_m2m_message(text: str) -> bool:
    """Return True if *text* looks like a signed bridge envelope.

    Performs a lightweight JSON parse + schema field check.
    Returns False (never raises) for any non-JSON or non-bridge input.
    """
    try:
        data = json.loads(text)
        return isinstance(data, dict) and data.get("schema") == _BRIDGE_SCHEMA
    except (json.JSONDecodeError, ValueError, TypeError):
        return False


async def handle_m2m(text: str, sender_id: str) -> Optional[dict[str, Any]]:
    """Entry point called from channel_listener.py for bridge envelopes.

    Validates the envelope then routes to per-command handlers.
    Returns None  — handled internally (buffer write, pong, etc.).
    Returns dict  — caller must act: {"action": "brief_request"} or
                    {"action": "query_artha", "question": ..., "correlation_id": ...}.
    All failures are silently dropped + audited; never raises.
    """
    cfg = _load_m2m_cfg(_ARTHA_DIR)
    if not cfg.get("enabled", False):
        log.debug("Bridge disabled — dropping M2M message from sender_id=%s", sender_id)
        return

    # ── Parse envelope ────────────────────────────────────────────────────────
    try:
        env: dict[str, Any] = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        log.debug("M2M JSON parse error: %s", exc)
        return

    cmd    = env.get("cmd", "")
    nonce  = env.get("nonce", "")
    src    = env.get("src", "")
    trace  = env.get("trace_id", "")

    _audit_log("BRIDGE_M2M_RECEIVED", cmd=cmd, src=src, sender_id=sender_id, trace_id=trace)

    if not _validate_envelope(env, sender_id, cfg):
        return

    # ── Route to per-command handler ──────────────────────────────────────────
    try:
        if cmd == "presence_detected":
            _handle_presence_detected(env, cfg, _ARTHA_DIR)
            return None
        elif cmd == "energy_event":
            _handle_energy_event(env, cfg, _ARTHA_DIR)
            return None
        elif cmd == "home_alert":
            _handle_home_alert(env, cfg, _ARTHA_DIR)
            return None
        elif cmd == "pong":
            _handle_pong(env, cfg)
            return None
        elif cmd == "brief_request":
            return _handle_brief_request(env)
        elif cmd == "query_artha":
            return _handle_query_artha(env, cfg)
        else:
            # Allowlist check already rejected unknown cmds; shouldn't reach here
            log.warning("M2M unhandled cmd after allowlist check: %r", cmd)
            return None
    except Exception as exc:  # noqa: BLE001
        log.exception("M2M handler error for cmd=%r: %s", cmd, exc)
        return None


# ── Configuration loader ───────────────────────────────────────────────────────

def _load_m2m_cfg(artha_dir: Path) -> dict[str, Any]:
    """Load claw_bridge.yaml via direct yaml.safe_load.

    NOTE: config_loader._CONFIG_FILES does NOT include claw_bridge — this is
    intentional (infrastructure exclusion pattern). Returns {} on any error.
    """
    cfg_path = artha_dir / "config" / "claw_bridge.yaml"
    try:
        with cfg_path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        log.error("claw_bridge.yaml not found at %s", cfg_path)
        return {}
    except yaml.YAMLError as exc:
        log.error("claw_bridge.yaml parse error: %s", exc)
        return {}


# ── Keyring helper ─────────────────────────────────────────────────────────────

def _keyring_get(key: str) -> str | None:
    """Retrieve a secret from OS keyring.  Returns None on any failure."""
    if _keyring is None:
        return None
    try:
        return _keyring.get_password("artha", key)
    except Exception as exc:
        log.debug("Keyring lookup failed for key=%r: %s", key, exc)
        return None


# ── Envelope validation ────────────────────────────────────────────────────────

def _validate_envelope(env: dict[str, Any], sender_id: str, cfg: dict[str, Any]) -> bool:
    """Run all validation gates in order.

    Gate order (matches spec §6.1 + §P2.2):
    1. Schema field
    2. Allowlist (cmd from_openclaw)
    3. Rate limit
    4. Sender ID check
    5. Timestamp window (checked manually to emit distinct audit event)
    6. Nonce replay (checked manually to emit distinct audit event)
    7. HMAC verification

    Returns False (never raises) for any failed gate.
    """
    cmd     = env.get("cmd", "")
    src     = env.get("src", "")
    ts      = env.get("ts", "")
    nonce   = env.get("nonce", "")
    sig     = env.get("sig", "")
    data    = env.get("data", {})
    trace   = env.get("trace_id", "")

    # 1. Schema
    if env.get("schema") != _BRIDGE_SCHEMA:
        log.debug("M2M schema mismatch: %r", env.get("schema"))
        return False

    # 2. Allowlist
    allowed_cmds: list[str] = cfg.get("allowlists", {}).get("from_openclaw", [])
    if cmd not in allowed_cmds:
        _audit_log("BRIDGE_ALLOWLIST_REJECT", cmd=cmd, src=src, trace_id=trace)
        return False

    # 3. Rate limit
    if _rate_limiter.is_rate_limited(src or sender_id):
        _audit_log("BRIDGE_RATE_LIMIT", cmd=cmd, src=src, sender_id=sender_id, trace_id=trace)
        return False

    # 4. Sender ID check
    expected_bot_id = _keyring_get(
        cfg.get("openclaw", {}).get("m2m_bot_id_keyring_key", "artha-openclaw-bot-id")
    )
    if expected_bot_id and str(sender_id) != str(expected_bot_id):
        _audit_log("BRIDGE_UNKNOWN_SENDER", sender_id=sender_id, trace_id=trace)
        return False
    if not expected_bot_id:
        # Keyring miss — fail open with a warning (keyring may not be configured yet)
        log.warning("M2M sender ID not configured in keyring — skipping sender check")

    # 5. Timestamp window (manual check to emit BRIDGE_TIMESTAMP_REJECT)
    try:
        msg_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta_sec = abs((datetime.now(timezone.utc) - msg_time).total_seconds())
        if delta_sec > _TIMESTAMP_TOLERANCE_SEC:
            _audit_log("BRIDGE_TIMESTAMP_REJECT", delta_sec=round(delta_sec), trace_id=trace)
            return False
    except (ValueError, TypeError):
        _audit_log("BRIDGE_TIMESTAMP_REJECT", reason="unparseable_ts", ts=ts, trace_id=trace)
        return False

    # 6. Nonce replay (uses our own dedup so we get BRIDGE_NONCE_REPLAY audit)
    if _nonce_dedup.is_duplicate(nonce):
        _audit_log("BRIDGE_NONCE_REPLAY", nonce=nonce, cmd=cmd, trace_id=trace)
        return False

    # 7. HMAC verification — check_replay=False because we handled replay above
    hmac_key = cfg.get("hmac_secret_keyring_key", "artha-claw-bridge-hmac")
    prev_version = cfg.get("hmac_key_previous_version")
    prev_key: str | None = None
    if prev_version is not None:
        prev_key = f"{hmac_key}-v{prev_version}"

    try:
        ok = hmac_signer.verify(
            sig, src, cmd, ts, nonce, data,
            keyring_key=hmac_key,
            previous_keyring_key=prev_key,
            check_replay=False,   # nonce already checked above
        )
    except Exception as exc:
        log.debug("HMAC verify raised: %s", exc)
        ok = False

    if not ok:
        _audit_log("BRIDGE_HMAC_FAIL", cmd=cmd, src=src, trace_id=trace)
        return False

    return True


# ── Per-command handlers ───────────────────────────────────────────────────────

def _handle_presence_detected(
    env: dict[str, Any], cfg: dict[str, Any], artha_dir: Path
) -> None:
    """Buffer a presence_detected event from OpenClaw."""
    data = env.get("data", {})
    event_rec = {
        "ts":    env.get("ts"),
        "event": "presence",
        "who":   data.get("who", "unknown"),
        "state": data.get("state", "unknown"),
    }
    _append_to_buffer(artha_dir, event_rec)
    _audit_log("BRIDGE_M2M_BUFFERED", cmd="presence_detected",
               who=event_rec["who"], trace_id=env.get("trace_id", ""))


def _handle_energy_event(
    env: dict[str, Any], cfg: dict[str, Any], artha_dir: Path
) -> None:
    """Buffer an energy_event from OpenClaw."""
    data = env.get("data", {})
    event_rec: dict[str, Any] = {
        "ts":     env.get("ts"),
        "event":  "energy_spike",
        "kwh_hr": data.get("kwh_hr"),
        "note":   data.get("note", ""),
    }
    _append_to_buffer(artha_dir, event_rec)
    _audit_log("BRIDGE_M2M_BUFFERED", cmd="energy_event",
               kwh_hr=event_rec["kwh_hr"], trace_id=env.get("trace_id", ""))


def _handle_home_alert(
    env: dict[str, Any], cfg: dict[str, Any], artha_dir: Path
) -> None:
    """Buffer a home_alert from OpenClaw.

    NOTE: Injection filter is intentionally NOT applied to inbound messages.
    HMAC is the guard (spec §6.2).  The text is stored verbatim in the buffer.
    """
    data = env.get("data", {})
    event_rec: dict[str, Any] = {
        "ts":    env.get("ts"),
        "event": "home_alert",
        "text":  data.get("text", ""),
        "level": data.get("level", "info"),
    }
    _append_to_buffer(artha_dir, event_rec)
    _audit_log("BRIDGE_M2M_BUFFERED", cmd="home_alert",
               level=event_rec["level"], trace_id=env.get("trace_id", ""))


def _handle_pong(env: dict[str, Any], cfg: dict[str, Any]) -> None:
    """Process a pong response — compute clock drift and log if significant."""
    data       = env.get("data", {})
    remote_ts  = data.get("utc_now", "")
    trace      = env.get("trace_id", "")

    if not remote_ts:
        log.debug("pong missing utc_now field — skipping clock drift check")
        return

    try:
        remote_dt = datetime.fromisoformat(remote_ts.replace("Z", "+00:00"))
        local_dt  = datetime.now(timezone.utc)
        drift_sec = abs((local_dt - remote_dt).total_seconds())
        drift_min = drift_sec / 60.0

        warn_min     = cfg.get("clock_drift_warn_minutes",     _CLOCK_WARN_MIN)
        critical_min = cfg.get("clock_drift_critical_minutes", _CLOCK_CRITICAL_MIN)

        if drift_min > critical_min:
            _audit_log("BRIDGE_CLOCK_DRIFT", level="CRITICAL",
                       drift_sec=round(drift_sec), trace_id=trace)
            log.critical("Bridge clock drift CRITICAL: %.1f sec (%.2f min)", drift_sec, drift_min)
        elif drift_min > warn_min:
            _audit_log("BRIDGE_CLOCK_DRIFT", level="WARNING",
                       drift_sec=round(drift_sec), trace_id=trace)
            log.warning("Bridge clock drift WARNING: %.1f sec (%.2f min)", drift_sec, drift_min)
        else:
            log.debug("pong clock drift OK: %.1f sec", drift_sec)

    except (ValueError, TypeError) as exc:
        log.debug("pong utc_now parse error: %s", exc)


def _handle_brief_request(env: dict[str, Any]) -> dict[str, Any]:
    """Handle a brief_request from OpenClaw.

    Returns a dict that channel_listener.py acts on; no buffer write needed.
    The caller (channel_listener) is responsible for generating and sending the briefing.
    """
    trace = env.get("trace_id", "")
    _audit_log("BRIDGE_M2M_BRIEF_REQUEST", trace_id=trace)
    log.info("M2M brief_request received (trace_id=%s)", trace)
    return {"action": "brief_request"}


def _handle_query_artha(
    env: dict[str, Any], cfg: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Handle a query_artha command from OpenClaw.

    Validates question length and domain allowlist, then returns a dict for
    the caller (channel_listener) to execute the LLM query asynchronously.

    Security:
    - question is truncated to max_question_chars before returning.
    - Only QUERY_ALLOWED_DOMAINS may be mentioned; QUERY_BLOCKED_DOMAINS are
      rejected immediately (defence-in-depth; allowlist is the primary gate).
    - If keyring is unavailable the envelope was already rejected by HMAC gate;
      this function never needs to re-check keyring.

    Returns None if the request is malformed / blocked (logged + audited).
    Returns dict on success: {"action": "query_artha", "question": str, "correlation_id": str}.
    """
    data         = env.get("data", {})
    trace        = env.get("trace_id", "")
    question_raw = data.get("question", "")
    correlation  = data.get("correlation_id", trace)

    qa_cfg           = cfg.get("query_artha", {})
    max_chars: int   = int(qa_cfg.get("max_question_chars", 200))

    if not question_raw or not question_raw.strip():
        _audit_log("BRIDGE_QUERY_INVALID", reason="empty_question", trace_id=trace)
        return None

    # Truncate to configured maximum before any further processing
    question = question_raw.strip()[:max_chars]

    _audit_log("BRIDGE_QUERY_ARTHA", question_len=len(question), trace_id=trace,
               correlation_id=correlation)
    log.info("M2M query_artha received (len=%d, trace=%s)", len(question), trace)

    return {
        "action":         "query_artha",
        "question":       question,
        "correlation_id": correlation,
    }


# ── Buffer writer ──────────────────────────────────────────────────────────────

def _append_to_buffer(artha_dir: Path, event_dict: dict[str, Any]) -> None:
    """Append a JSON event line to ~/.artha-local/home_events_buffer.jsonl.

    Uses an advisory fcntl exclusive lock on macOS/Linux.
    If the lock cannot be acquired (another process holds it), the write is
    dropped with a BRIDGE_M2M_LOCK_SKIP audit event rather than blocking.
    On Windows (_fcntl is None): the lock is skipped entirely — single-listener
    enforcement via channels.yaml is the guard on Windows (spec §P2.2).

    Local-only path writes are exempt from state_writer.write() (spec §P2.4 mandate).
    """
    # DEBT-037: Write to ~/.artha-local/ (outside OneDrive) rather than artha_dir/tmp/
    buf_path = Path.home() / ".artha-local" / _BUFFER_FILE
    buf_path.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(event_dict, sort_keys=True, ensure_ascii=True) + "\n"

    try:
        with buf_path.open("a", encoding="utf-8") as fh:
            if _fcntl is not None:
                try:
                    _fcntl.flock(fh, _fcntl.LOCK_EX | _fcntl.LOCK_NB)  # type: ignore[attr-defined]
                except OSError:
                    _audit_log("M2M_LOCK_SKIP", buf=str(buf_path))
                    return
            fh.write(line)
            if _fcntl is not None:
                _fcntl.flock(fh, _fcntl.LOCK_UN)  # type: ignore[attr-defined]
    except OSError as exc:
        log.error("Failed to write to home_events buffer %s: %s", buf_path, exc)
