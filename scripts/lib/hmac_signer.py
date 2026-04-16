"""lib/hmac_signer.py — HMAC-SHA256 sign/verify for the Artha ↔ OpenClaw bridge.

Spec: specs/claw-bridge.md §6.1 + §12 (HMAC Key Rotation)

Security invariants:
- Shared secret is NEVER read from a file, config, or env var — keyring only.
- Signature covers: schema:src:cmd:ts:nonce:JSON(data)  (canonical order)
- Replay prevention: ±5-minute timestamp window + per-nonce dedup cache (10 min TTL)
  DEBT-HMAC-001: nonce cache is now persisted to ~/.artha-local/hmac_nonce_cache.jsonl
  to survive process restarts. Cross-session replay is blocked within the 10-min TTL.
- Key rotation: dual-accept window via hmac_key_version / hmac_key_previous_version
  (claw_bridge.yaml). Verify tries current version first, then previous.

Dependencies: stdlib only (hmac, hashlib, time, threading, json, secrets, datetime)
              + keyring (already a project dependency).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_SCHEMA = "claw-bridge/1.0"
_TIMESTAMP_TOLERANCE_SEC = 300   # ±5 minutes
_NONCE_DEDUP_WINDOW_SEC = 600    # 10 minutes — nonces are forgotten after this
_NONCE_DEDUP_MAX_SIZE = 2048     # LRU cap — prevents unbounded memory growth

# Primary keyring key for the shared HMAC secret
_HMAC_KEYRING_SERVICE = "artha"
_HMAC_KEYRING_KEY = "artha-claw-bridge-hmac"


# ── Nonce dedup cache (module-level singleton, thread-safe) ──────────────────

# DEBT-HMAC-001: Persist nonces to disk so cross-session replay is blocked.
# Machine-local directory (not OneDrive-synced): ~/.artha-local/
_NONCE_CACHE_DIR = Path.home() / ".artha-local"
_NONCE_CACHE_FILE = _NONCE_CACHE_DIR / "hmac_nonce_cache.jsonl"


class _NonceCache:
    """Thread-safe LRU + TTL nonce cache with cross-session persistence.

    DEBT-HMAC-001: Entries are persisted to ~/.artha-local/hmac_nonce_cache.jsonl
    so nonces remain rejected across process restarts within the TTL window.
    Each process compacts the file once (removes expired entries) on first load.
    """

    def __init__(self, max_size: int = _NONCE_DEDUP_MAX_SIZE, ttl_sec: float = _NONCE_DEDUP_WINDOW_SEC):
        self._store: deque[tuple[str, float]] = deque(maxlen=max_size)
        self._seen: set[str] = set()
        self._ttl = ttl_sec
        self._lock = threading.Lock()
        self._compacted_this_session = False
        self._replay_vulnerable_until: float = 0.0  # RD-26: monotonic timestamp
        self._nonce_cache_integrity_check()
        self._load_persisted()

    def _nonce_cache_integrity_check(self) -> None:
        """RD-26: Warn and enter replay-vulnerable window if nonce cache is absent.

        If the cache file is deleted between sessions (e.g., by a failed upgrade
        or accidental rm), nonces from the previous session's TTL window will not
        be rejected — creating a replay vulnerability window equal to the nonce TTL.
        We enter a 10-minute elevated-vigilance window and log to audit.
        """
        import time as _time  # noqa: PLC0415
        if not _NONCE_CACHE_FILE.exists():
            # Cache is absent — log warning and set replay-vulnerable window
            _REPLAY_VULNERABLE_WINDOW_SEC = 600  # 10 minutes
            self._replay_vulnerable_until = _time.monotonic() + _REPLAY_VULNERABLE_WINDOW_SEC
            log.warning(
                "NONCE_CACHE_INTEGRITY: hmac_nonce_cache.jsonl not found at startup. "
                "Entering %ds replay-vulnerable window — nonces from the previous session "
                "cannot be rejected until new cache is populated. "
                "Path: %s",
                _REPLAY_VULNERABLE_WINDOW_SEC,
                _NONCE_CACHE_FILE,
            )

    def is_replay_vulnerable(self) -> bool:
        """Return True if we are currently in a replay-vulnerable window (RD-26)."""
        import time as _time  # noqa: PLC0415
        return _time.monotonic() < self._replay_vulnerable_until

    def _load_persisted(self) -> None:
        """Load non-expired nonces from disk and compact the file once per process."""
        if not _NONCE_CACHE_FILE.exists():
            return
        # Check file is not on a synced (OneDrive/iCloud) filesystem
        try:
            file_str = str(_NONCE_CACHE_FILE.resolve())
            if "OneDrive" in file_str or "iCloud" in file_str or "Dropbox" in file_str:
                log.warning("hmac_nonce_cache: file is on synced FS — in-memory only")
                return
        except Exception:
            return

        now = time.monotonic()
        wall_now = time.time()
        valid_entries: list[tuple[str, float]] = []
        try:
            with _NONCE_CACHE_FILE.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        nonce = entry["n"]
                        wall_expiry = float(entry["e"])  # wall-clock expiry (Unix time)
                        if wall_expiry > wall_now:
                            # Convert wall-clock expiry to monotonic for in-memory store
                            remaining = wall_expiry - wall_now
                            mono_expiry = now + remaining
                            valid_entries.append((nonce, mono_expiry))
                    except (KeyError, ValueError, json.JSONDecodeError):
                        continue
        except OSError:
            return

        # Populate in-memory store
        for nonce, mono_expiry in valid_entries:
            self._store.append((nonce, mono_expiry))
            self._seen.add(nonce)

        # Compact: rewrite file with only valid entries (once per process)
        if not self._compacted_this_session:
            self._compacted_this_session = True
            self._compact_file(valid_entries, wall_now)

    def _compact_file(self, valid_entries: list[tuple[str, float]], wall_now: float) -> None:
        """Rewrite the cache file with only non-expired entries."""
        try:
            _NONCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp_path = _NONCE_CACHE_FILE.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as fh:
                for nonce, mono_expiry in valid_entries:
                    remaining = max(0.0, mono_expiry - time.monotonic())
                    wall_expiry = wall_now + remaining
                    fh.write(json.dumps({"n": nonce, "e": round(wall_expiry, 3)}) + "\n")
            os.replace(str(tmp_path), str(_NONCE_CACHE_FILE))
        except OSError:
            pass  # compaction failure is non-blocking

    def _persist_nonce(self, nonce: str, wall_expiry: float) -> None:
        """Append a single nonce entry to the JSONL file (best-effort)."""
        try:
            _NONCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            entry = json.dumps({"n": nonce, "e": round(wall_expiry, 3)}) + "\n"
            with _NONCE_CACHE_FILE.open("a", encoding="utf-8") as fh:
                fh.write(entry)
        except OSError:
            pass  # persistence failure is non-blocking; in-memory cache still works

    def _evict_expired(self) -> None:
        now = time.monotonic()
        while self._store and self._store[0][1] < now:
            nonce, _ = self._store.popleft()
            self._seen.discard(nonce)

    def is_replay(self, nonce: str) -> bool:
        """Return True if nonce was seen within the TTL window (replay attack)."""
        with self._lock:
            self._evict_expired()
            if nonce in self._seen:
                return True
            mono_expiry = time.monotonic() + self._ttl
            wall_expiry = time.time() + self._ttl
            self._store.append((nonce, mono_expiry))
            self._seen.add(nonce)
            self._persist_nonce(nonce, wall_expiry)
            return False


_nonce_cache = _NonceCache()


# ── Secret loader ─────────────────────────────────────────────────────────────

def _load_secret(keyring_key: str = _HMAC_KEYRING_KEY) -> bytes:
    """Load HMAC shared secret from OS keyring.  Returns raw bytes (decoded from hex).

    Raises RuntimeError with a safe message if the secret is missing or malformed.
    Never logs the secret itself.
    """
    try:
        import keyring as _kr
        raw = _kr.get_password(_HMAC_KEYRING_SERVICE, keyring_key)
    except Exception as exc:
        raise RuntimeError(
            f"Keyring unavailable for key '{keyring_key}': {exc}"
        ) from exc

    if not raw:
        raise RuntimeError(
            f"HMAC secret not found in keyring (key='{keyring_key}'). "
            "Run Phase 0 P0.8 setup to populate the secret."
        )
    try:
        secret_bytes = bytes.fromhex(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"HMAC secret at key '{keyring_key}' is not valid hex. "
            "Re-generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        ) from exc

    if len(secret_bytes) < 16:
        raise RuntimeError(
            f"HMAC secret at key '{keyring_key}' is too short ({len(secret_bytes)} bytes). "
            "Minimum 16 bytes (32 hex chars). Recommended: 32 bytes (64 hex chars)."
        )
    return secret_bytes


# ── Canonical message construction ───────────────────────────────────────────

def _canonical_message(src: str, cmd: str, ts: str, nonce: str, data: dict[str, Any]) -> bytes:
    """Build the canonical byte string to sign.

    Format: schema:src:cmd:ts:nonce:<JSON(data) with sorted keys>
    The schema prefix ensures signatures cannot be reused across protocol versions.
    JSON is serialized with sort_keys=True and no extra whitespace for determinism.
    """
    data_json = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    parts = [_SCHEMA, src, cmd, ts, nonce, data_json]
    return ":".join(parts).encode("utf-8")


# ── Public API ────────────────────────────────────────────────────────────────

def generate_nonce() -> str:
    """Return a cryptographically secure 8-char hex nonce."""
    return secrets.token_hex(4)  # 4 bytes → 8 hex chars


def generate_trace_id() -> str:
    """Return a UUID4 trace ID for correlating HA events back to pipeline runs."""
    import uuid
    return str(uuid.uuid4())


def sign(
    src: str,
    cmd: str,
    ts: str,
    nonce: str,
    data: dict[str, Any],
    *,
    keyring_key: str = _HMAC_KEYRING_KEY,
) -> str:
    """Sign a bridge envelope.

    Args:
        src:        Source system ("artha" or "openclaw").
        cmd:        Allowlisted command string.
        ts:         ISO-8601 UTC timestamp (e.g. "2026-04-09T07:00:00Z").
        nonce:      8-char hex random nonce (use generate_nonce()).
        data:       Command payload dict.
        keyring_key: Keyring key for the HMAC secret (supports rotation).

    Returns:
        HMAC-SHA256 hex digest string.
    """
    secret = _load_secret(keyring_key)
    msg = _canonical_message(src, cmd, ts, nonce, data)
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def verify(
    sig: str,
    src: str,
    cmd: str,
    ts: str,
    nonce: str,
    data: dict[str, Any],
    *,
    tolerance_sec: int = _TIMESTAMP_TOLERANCE_SEC,
    keyring_key: str = _HMAC_KEYRING_KEY,
    previous_keyring_key: str | None = None,
    check_replay: bool = True,
) -> bool:
    """Verify a bridge envelope signature.

    Validation rules (spec §6.1):
    1. schema is implicit (canonical_message includes it)
    2. HMAC signature matches (timing-safe comparison)
    3. ts is within ±tolerance_sec of local clock
    4. nonce has not been seen in the last 10 minutes (anti-replay)
    5. cmd/src allowlist checks are the caller's responsibility

    Key rotation: if previous_keyring_key is set, tries both keys.
    Returns False (never raises) on any verification failure to prevent oracle attacks.
    """
    # ── Rule 3: Timestamp window ─────────────────────────────────────────────
    try:
        msg_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta_sec = abs((now - msg_time).total_seconds())
        if delta_sec > tolerance_sec:
            log.debug("BRIDGE_TIMESTAMP_REJECT: delta=%.1fs tolerance=%ds", delta_sec, tolerance_sec)
            return False
    except (ValueError, TypeError):
        log.debug("BRIDGE_TIMESTAMP_REJECT: unparseable ts=%r", ts)
        return False

    # ── Rule 4: Nonce replay ─────────────────────────────────────────────────
    if check_replay and _nonce_cache.is_replay(nonce):
        log.debug("BRIDGE_NONCE_REPLAY: nonce=%r", nonce)
        return False

    # ── Rule 2: HMAC verification (timing-safe, dual-key rotation support) ───
    msg = _canonical_message(src, cmd, ts, nonce, data)

    def _try_key(key: str) -> bool:
        try:
            secret = _load_secret(key)
        except RuntimeError:
            return False
        expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)

    if _try_key(keyring_key):
        return True

    if previous_keyring_key and _try_key(previous_keyring_key):
        log.debug("BRIDGE_HMAC_ACCEPTED_PREVIOUS_KEY: rotation overlap window active")
        return True

    log.debug("BRIDGE_HMAC_FAIL: signature mismatch")
    return False


def build_envelope(
    src: str,
    cmd: str,
    data: dict[str, Any],
    *,
    keyring_key: str = _HMAC_KEYRING_KEY,
) -> dict[str, Any]:
    """Build a complete signed bridge envelope ready to POST or send.

    Generates a fresh nonce, current UTC timestamp, and UUID trace_id.
    Returns the full envelope dict matching spec §6.1 schema.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = generate_nonce()
    trace_id = generate_trace_id()
    sig = sign(src, cmd, ts, nonce, data, keyring_key=keyring_key)
    return {
        "schema": _SCHEMA,
        "src": src,
        "cmd": cmd,
        "data": data,
        "sig": sig,
        "ts": ts,
        "nonce": nonce,
        "trace_id": trace_id,
    }
