"""pii_guard must re-exec into the venv BEFORE reading stdin, or a lazy re-exec leaves the filter with empty input."""
from __future__ import annotations

import io
import sys

import pytest

import pii_guard


@pytest.mark.parametrize("cmd", ["filter", "scan"])
def test_reexec_runs_before_stdin_is_read(monkeypatch, cmd):
    import _bootstrap
    order: list[str] = []
    monkeypatch.setattr(_bootstrap, "reexec_in_venv", lambda *a, **k: order.append("reexec"))

    class Spy(io.StringIO):
        def read(self, *a):
            order.append("read")
            return super().read(*a)

    monkeypatch.setattr(sys, "stdin", Spy("nothing sensitive here\n"))
    monkeypatch.setattr(sys, "argv", ["pii_guard.py", cmd])
    with pytest.raises(SystemExit):
        pii_guard.main()
    assert order[:2] == ["reexec", "read"]


def test_version_and_test_commands_do_not_reexec(monkeypatch):
    import _bootstrap
    called = []
    monkeypatch.setattr(_bootstrap, "reexec_in_venv", lambda *a, **k: called.append(1))
    monkeypatch.setattr(sys, "argv", ["pii_guard.py", "version"])
    with pytest.raises(SystemExit):
        pii_guard.main()
    assert called == []
