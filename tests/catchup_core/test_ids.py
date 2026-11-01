from __future__ import annotations

import pytest

from catchup_core import ids

KEY = ids.HmacKey(version=1, secret=b"k" * 32)
KEY2 = ids.HmacKey(version=2, secret=b"z" * 32)


def test_signal_id_is_stable_and_prefixed():
    a = ids.signal_id(KEY, "gmail", "acct-a", "msg-1", "v1")
    assert a == ids.signal_id(KEY, "gmail", "acct-a", "msg-1", "v1")
    assert a.startswith("sig_") and len(a) == 4 + 16
    int(a[4:], 16)


@pytest.mark.parametrize("change", [
    ("gmail", "acct-b", "msg-1", "v1"),
    ("gmail", "acct-a", "msg-2", "v1"),
    ("gmail", "acct-a", "msg-1", "v2"),
    ("outlook", "acct-a", "msg-1", "v1"),
])
def test_signal_id_changes_with_every_tuple_member(change):
    assert ids.signal_id(KEY, *change) != ids.signal_id(KEY, "gmail", "acct-a", "msg-1", "v1")


def test_signal_id_depends_on_key():
    assert ids.signal_id(KEY, "g", "a", "u", "v") != ids.signal_id(KEY2, "g", "a", "u", "v")


def test_tuple_encoding_is_unambiguous():
    # "ab|c" + "d" must not collide with "ab" + "c|d"
    assert ids.signal_id(KEY, "ab|c", "d", "u", "v") != ids.signal_id(KEY, "ab", "c|d", "u", "v")


def test_content_hmac_is_keyed():
    assert ids.content_hmac(KEY, "text") != ids.content_hmac(KEY2, "text")
    assert ids.content_hmac(KEY, "text") == ids.content_hmac(KEY, "text")
    assert ids.content_hmac(KEY, "text") != ids.content_hmac(KEY, "text2")


def test_version_token_prefers_provider_version():
    assert ids.version_token("etag-7", "abcd" * 8) == "etag-7"


def test_version_token_falls_back_to_content_hash():
    assert ids.version_token(None, "abcd1234" * 4) == "c:abcd1234abcd1234"


def test_content_change_under_same_provider_version_creates_new_version():
    first = ids.version_token("etag-7", "aaaa" * 8)
    changed = ids.version_token("etag-7", "bbbb" * 8, known_content_hmac="aaaa" * 8)
    assert first == "etag-7"
    assert changed != first and changed.startswith("etag-7+")


def test_same_content_under_same_provider_version_is_unchanged():
    assert ids.version_token("etag-7", "aaaa" * 8, known_content_hmac="aaaa" * 8) == "etag-7"


def test_time_ordered_ids_sort_by_time():
    a = ids.new_id("fct", now_ms=1_000, rand=b"\x00\x00\x00\x01")
    b = ids.new_id("fct", now_ms=2_000, rand=b"\x00\x00\x00\x00")
    assert a < b and a.startswith("fct_")


def test_time_ordered_ids_are_unique_for_same_ms():
    seen = {ids.new_id("alt", now_ms=5) for _ in range(200)}
    assert len(seen) == 200


def test_collision_is_a_hard_failure():
    registry = ids.CollisionGuard()
    sid = ids.signal_id(KEY, "g", "a", "u", "v")
    registry.check(sid, ("g", "a", "u", "v"))
    registry.check(sid, ("g", "a", "u", "v"))          # same tuple again: fine
    with pytest.raises(ids.IdCollisionError):
        registry.check(sid, ("g", "a", "OTHER", "v"))


def test_evidence_and_signal_namespaces_differ():
    assert ids.evidence_id(KEY, "x").startswith("evd_")
    assert not ids.evidence_id(KEY, "x").startswith("SIG-")


def test_rejects_empty_members():
    with pytest.raises(ValueError):
        ids.signal_id(KEY, "", "a", "u", "v")


def test_rejects_short_key():
    with pytest.raises(ValueError):
        ids.HmacKey(version=1, secret=b"short")


def test_key_version_must_be_positive():
    with pytest.raises(ValueError):
        ids.HmacKey(version=0, secret=b"k" * 32)


def test_evidence_id_requires_locator_and_prefix_validated():
    with pytest.raises(ValueError):
        ids.evidence_id(KEY, "")
    with pytest.raises(ValueError):
        ids.new_id("not ok")
    with pytest.raises(ValueError):
        ids.new_id("")
