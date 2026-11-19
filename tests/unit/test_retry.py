from __future__ import annotations

from unittest.mock import patch

import pytest

from lib.retry import with_retry


class StructuredFailure(RuntimeError):
    def __init__(self, marker: object, status: int):
        super().__init__("structured provider failure")
        self.marker = marker
        self.status = status


def test_exhausted_retry_preserves_original_exception_identity():
    failure = StructuredFailure(object(), 503)
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        raise failure

    with patch("lib.retry.time.sleep"), patch("lib.retry.random.uniform", return_value=0.0):
        with pytest.raises(StructuredFailure) as caught:
            with_retry(fail, max_retries=1, base_delay=0.1)

    assert caught.value is failure
    assert calls == 2


@pytest.mark.parametrize("message", ["invoice 500", "quota reached", "request 429"])
def test_unstructured_message_text_is_not_retry_authority(message):
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match=message):
        with_retry(fail, max_retries=3, base_delay=0.0)
    assert calls == 1


def test_deadline_prevents_an_additional_attempt_or_sleep():
    failure = StructuredFailure(object(), 503)
    with patch("lib.retry.time.sleep") as sleep, patch(
        "lib.retry.time.monotonic", side_effect=[0.0, 2.0]
    ):
        with pytest.raises(StructuredFailure) as caught:
            with_retry(lambda: (_ for _ in ()).throw(failure),
                       max_retries=5, deadline=1.0)
    assert caught.value is failure
    sleep.assert_not_called()


def test_retry_after_is_bounded_by_remaining_delay():
    class HeadersFailure(StructuredFailure):
        headers = {"Retry-After": "60"}

    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HeadersFailure(object(), 429)
        return "ok"

    with patch("lib.retry.time.sleep") as sleep, patch(
        "lib.retry.random.uniform", return_value=0.0
    ):
        assert with_retry(operation, max_retries=1, base_delay=1.0, max_delay=2.0) == "ok"
    sleep.assert_called_once_with(2.0)


def test_non_idempotent_operation_does_not_retry():
    failure = StructuredFailure(object(), 503)
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(StructuredFailure) as caught:
        with_retry(operation, max_retries=3, idempotent=False)
    assert caught.value is failure
    assert calls == 1


@pytest.mark.parametrize("base_delay", [float("inf"), float("nan"), -1.0])
def test_retry_rejects_unbounded_delay(base_delay):
    with pytest.raises(ValueError, match="delay"):
        with_retry(lambda: None, base_delay=base_delay)
