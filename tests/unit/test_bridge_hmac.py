"""tests/unit/test_bridge_hmac.py — Unit tests for lib/hmac_signer.py.

Spec: specs/claw-bridge.md §6.1, §12, §15.3
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from lib.hmac_signer import (
    _HMAC_KEYRING_KEY,
    _canonical_message,
    build_envelope,
    generate_nonce,
    generate_trace_id,
    sign,
    verify,
)

# ── Fixtures / helpers ────────────────────────────────────────────────────────

_FAKE_SECRET = b"\xde\xad\xbe\xef" * 8  # 32 bytes
_FAKE_SECRET_HEX = _FAKE_SECRET.hex()

_PREV_SECRET = b"\xca\xfe\xba\xbe" * 8
_PREV_SECRET_HEX = _PREV_SECRET.hex()

_PREV_KEY = "artha-claw-bridge-hmac-v1"


def _keyring_side_effect(service, key):
    if key == _HMAC_KEYRING_KEY:
        return _FAKE_SECRET_HEX
    if key == _PREV_KEY:
        return _PREV_SECRET_HEX
    return None


@pytest.fixture(autouse=True)
def _patch_keyring(monkeypatch):
    """Patch keyring.get_password for all tests in this module."""
    with patch("keyring.get_password", side_effect=_keyring_side_effect):
        yield


def _fresh_nonce():
    """Return a nonce that won't collide with previous test nonces."""
    import secrets
    return secrets.token_hex(4)


def _current_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Sign / verify round-trip ──────────────────────────────────────────────────

def test_sign_verify_roundtrip():
    ts = _current_ts()
    nonce = _fresh_nonce()
    data = {"zone": "living_room", "state": "occupied"}
    sig = sign("openclaw", "presence_detected", ts, nonce, data)
    assert verify(sig, "openclaw", "presence_detected", ts, nonce, data, check_replay=False)


def test_verify_rejects_tampered_data():
    ts = _current_ts()
    nonce = _fresh_nonce()
    data = {"zone": "kitchen", "state": "occupied"}
    sig = sign("openclaw", "presence_detected", ts, nonce, data)
    tampered = {"zone": "bedroom", "state": "occupied"}
    assert not verify(sig, "openclaw", "presence_detected", ts, nonce, tampered, check_replay=False)


def test_verify_rejects_tampered_cmd():
    ts = _current_ts()
    nonce = _fresh_nonce()
    data = {"watts": 2500}
    sig = sign("openclaw", "energy_event", ts, nonce, data)
    assert not verify(sig, "openclaw", "home_alert", ts, nonce, data, check_replay=False)


def test_verify_rejects_tampered_src():
    ts = _current_ts()
    nonce = _fresh_nonce()
    data = {}
    sig = sign("openclaw", "pong", ts, nonce, data)
    assert not verify(sig, "artha", "pong", ts, nonce, data, check_replay=False)


# ── Timestamp window ──────────────────────────────────────────────────────────

def test_verify_rejects_expired_timestamp():
    """Timestamp older than 5 minutes must be rejected."""
    from datetime import datetime, timedelta, timezone
    old_dt = datetime.now(timezone.utc) - timedelta(seconds=310)
    ts = old_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = _fresh_nonce()
    data = {}
    sig = sign("openclaw", "pong", ts, nonce, data, keyring_key=_HMAC_KEYRING_KEY)
    assert not verify(sig, "openclaw", "pong", ts, nonce, data, check_replay=False)


def test_verify_accepts_timestamp_within_window():
    """Timestamp within 4 minutes should pass."""
    from datetime import datetime, timedelta, timezone
    recent_dt = datetime.now(timezone.utc) - timedelta(seconds=180)
    ts = recent_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = _fresh_nonce()
    data = {}
    sig = sign("openclaw", "pong", ts, nonce, data)
    assert verify(sig, "openclaw", "pong", ts, nonce, data, check_replay=False)


# ── Nonce replay ──────────────────────────────────────────────────────────────

def test_verify_blocks_nonce_replay():
    """Second call with same nonce must be rejected (check_replay=True)."""
    ts = _current_ts()
    nonce = _fresh_nonce()
    data = {}
    sig = sign("openclaw", "pong", ts, nonce, data)
    # First call — accepted and nonce is cached
    assert verify(sig, "openclaw", "pong", ts, nonce, data, check_replay=True)
    # Second call — same nonce → replay rejected
    assert not verify(sig, "openclaw", "pong", ts, nonce, data, check_replay=True)


def test_verify_allows_different_nonces():
    """Different nonces should both succeed."""
    ts = _current_ts()
    data = {}
    nonce1 = _fresh_nonce()
    nonce2 = _fresh_nonce()
    sig1 = sign("openclaw", "pong", ts, nonce1, data)
    sig2 = sign("openclaw", "pong", ts, nonce2, data)
    assert verify(sig1, "openclaw", "pong", ts, nonce1, data, check_replay=True)
    assert verify(sig2, "openclaw", "pong", ts, nonce2, data, check_replay=True)


# ── Key not found ─────────────────────────────────────────────────────────────

def test_verify_fails_for_wrong_key():
    """If keyring returns None for the key, verify returns False."""
    ts = _current_ts()
    nonce = _fresh_nonce()
    data = {}
    sig = sign("openclaw", "pong", ts, nonce, data)
    with patch("keyring.get_password", return_value=None):
        assert not verify(sig, "openclaw", "pong", ts, nonce, data, check_replay=False)


# ── Key rotation ──────────────────────────────────────────────────────────────

def test_verify_accepts_previous_key_during_rotation():
    """Signature made with previous key must verify during rotation overlap."""
    ts = _current_ts()
    nonce = _fresh_nonce()
    data = {"zone": "front_door"}
    # Sign with the PREVIOUS key
    sig = sign("openclaw", "presence_detected", ts, nonce, data, keyring_key=_PREV_KEY)
    # Verify with current key + previous key (rotation overlap)
    accepted = verify(
        sig,
        "openclaw",
        "presence_detected",
        ts,
        nonce,
        data,
        keyring_key=_HMAC_KEYRING_KEY,
        previous_keyring_key=_PREV_KEY,
        check_replay=False,
    )
    assert accepted


def test_verify_rejects_previous_key_signature_without_rotation():
    """Signature from previous key is rejected when no rotation key is passed."""
    ts = _current_ts()
    nonce = _fresh_nonce()
    data = {}
    sig = sign("openclaw", "pong", ts, nonce, data, keyring_key=_PREV_KEY)
    assert not verify(
        sig,
        "openclaw",
        "pong",
        ts,
        nonce,
        data,
        keyring_key=_HMAC_KEYRING_KEY,
        previous_keyring_key=None,
        check_replay=False,
    )


# ── Timing-safe comparison ────────────────────────────────────────────────────

def test_hmac_compare_digest_is_used(monkeypatch):
    """Verify that hmac.compare_digest is called (timing-safe comparison)."""
    import hmac as _hmac
    calls: list[tuple] = []
    real_compare = _hmac.compare_digest

    def _spy(a, b):
        calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(_hmac, "compare_digest", _spy)
    ts = _current_ts()
    nonce = _fresh_nonce()
    data = {}
    sig = sign("openclaw", "pong", ts, nonce, data)
    verify(sig, "openclaw", "pong", ts, nonce, data, check_replay=False)
    assert len(calls) >= 1, "hmac.compare_digest was never called"


# ── build_envelope ────────────────────────────────────────────────────────────

def test_build_envelope_structure():
    env = build_envelope("artha", "ping", {"trace_id": "abc"})
    assert env["schema"] == "claw-bridge/1.0"
    assert env["src"] == "artha"
    assert env["cmd"] == "ping"
    assert "ts" in env
    assert "nonce" in env
    assert "sig" in env
    assert isinstance(env["sig"], str) and len(env["sig"]) == 64  # SHA-256 hex


def test_build_envelope_sig_verifies():
    env = build_envelope("artha", "ping", {})
    assert verify(
        env["sig"],
        env["src"],
        env["cmd"],
        env["ts"],
        env["nonce"],
        env["data"],
        check_replay=False,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def test_generate_nonce_length():
    nonce = generate_nonce()
    assert len(nonce) == 8
    assert all(c in "0123456789abcdef" for c in nonce)


def test_generate_trace_id_is_uuid4():
    import re
    trace_id = generate_trace_id()
    pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    assert pattern.match(trace_id), f"Not a valid UUID4: {trace_id}"
