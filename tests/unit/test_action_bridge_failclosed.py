"""WP-01 / RA-05: the action bridge never writes plaintext on an encryption failure and
never accepts a proposal with a missing or malformed expiry."""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import action_bridge as ab  # noqa: E402


@pytest.fixture
def helper(monkeypatch):
    """Control the delegated action_queue encryption helpers."""
    import action_queue as aq  # noqa: PLC0415
    state = {"enc": lambda v, k: "age1:CIPHER", "dec": lambda v, k: "plain"}
    monkeypatch.setattr(aq, "_encrypt_field", lambda v, k: state["enc"](v, k))
    monkeypatch.setattr(aq, "_decrypt_field", lambda v, k: state["dec"](v, k))
    return state


# ── encryption ──────────────────────────────────────────────────────────────

def test_encrypt_ok(helper):
    assert ab._encrypt_field("secret", "age1pub") == "age1:CIPHER"


def test_encrypt_empty_value_passes_through(helper):
    assert ab._encrypt_field("", None) == ""


def test_encrypt_without_recipient_raises_instead_of_returning_plaintext(helper):
    with pytest.raises(ab.BridgeEncryptionError):
        ab._encrypt_field("secret", None)


def test_encrypt_helper_exception_raises(helper):
    def boom(v, k):
        raise RuntimeError("age not installed")
    helper["enc"] = boom
    with pytest.raises(ab.BridgeEncryptionError):
        ab._encrypt_field("secret", "age1pub")


def test_encrypt_helper_returning_plaintext_raises(helper):
    helper["enc"] = lambda v, k: v      # the delegate's silent fallback
    with pytest.raises(ab.BridgeEncryptionError):
        ab._encrypt_field("secret", "age1pub")


def test_write_proposal_writes_nothing_when_encryption_fails(tmp_path, helper):
    proposal = types.SimpleNamespace(
        id="a1b2c3d4-0000-4000-8000-0000000000aa", action_type="email_send", domain="finance",
        friction="low", min_trust=1, sensitivity="high", expires_at="2099-01-01T00:00:00+00:00",
        reversible=False, undo_window_sec=None, source_step=None, source_skill=None, linked_oi=None,
        title="t", description="d", parameters={"to": "x"},
    )
    bridge = tmp_path / "state" / ".action_bridge"
    with pytest.raises(ab.BridgeEncryptionError):
        ab.write_proposal(bridge, proposal, pubkey=None)
    assert not list(bridge.rglob("*.json")) if bridge.exists() else True


def test_write_result_writes_nothing_when_encryption_fails(tmp_path, helper):
    bridge = tmp_path / "bridge"
    with pytest.raises(ab.BridgeEncryptionError):
        ab.write_result(bridge, "a1", "succeeded", "sent to someone", pubkey=None)
    assert not list(bridge.rglob("*.json")) if bridge.exists() else True


# ── decryption ──────────────────────────────────────────────────────────────

def test_decrypt_plain_value_passes_through(helper):
    assert ab._decrypt_field("not encrypted", "AGE-SECRET") == "not encrypted"


def test_decrypt_failure_on_encrypted_value_raises(helper):
    helper["dec"] = lambda v, k: v       # delegate returns the ciphertext unchanged
    with pytest.raises(ab.BridgeEncryptionError):
        ab._decrypt_field("age1:CIPHERTEXT", "AGE-SECRET")


def test_decrypt_encrypted_value_without_key_raises(helper):
    with pytest.raises(ab.BridgeEncryptionError):
        ab._decrypt_field("age1:CIPHERTEXT", None)


# ── expiry ──────────────────────────────────────────────────────────────────

class _Queue:
    def __init__(self):
        self.ingested = []

    def ingest_remote(self, proposal, pubkey=None):
        self.ingested.append(proposal.id)
        return True


def _bridge_with(tmp_path, **overrides):
    bridge = tmp_path / "state" / ".action_bridge"
    (bridge / "proposals").mkdir(parents=True)
    data = {
        "bridge_version": 1, "action_id": "act-0001", "action_type": "reminder_create",
        "domain": "calendar", "title": "t", "description": "", "parameters": "{}",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    }
    data.update(overrides)
    data = {k: v for k, v in data.items() if v is not ...}
    (bridge / "proposals" / "2026-10-11T00-00-00Z_act00001.json").write_text(json.dumps(data))
    return bridge


@pytest.mark.parametrize("override", [
    {"expires_at": "not-a-date"},
    {"expires_at": ...},          # missing
    {"expires_at": ""},
    {"expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},
])
def test_invalid_or_expired_expiry_is_never_ingested(tmp_path, helper, monkeypatch, override):
    monkeypatch.setattr(ab, "_get_privkey", lambda d: None)
    bridge = _bridge_with(tmp_path, **override)
    q = _Queue()
    assert ab.ingest_proposals(bridge, q, tmp_path, pubkey=None) == 0
    assert q.ingested == []


def test_valid_future_expiry_is_ingested(tmp_path, helper, monkeypatch):
    monkeypatch.setattr(ab, "_get_privkey", lambda d: None)
    bridge = _bridge_with(tmp_path)
    q = _Queue()
    assert ab.ingest_proposals(bridge, q, tmp_path, pubkey=None) == 1


def test_undecryptable_proposal_is_skipped_not_ingested(tmp_path, helper, monkeypatch):
    monkeypatch.setattr(ab, "_get_privkey", lambda d: None)
    bridge = _bridge_with(tmp_path, title="age1:CIPHERTEXT")
    q = _Queue()
    assert ab.ingest_proposals(bridge, q, tmp_path, pubkey=None) == 0
    assert q.ingested == []
