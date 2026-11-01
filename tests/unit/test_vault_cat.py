from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vault_cat  # noqa: E402
from catchup_core.vault import VaultError, VaultUnavailable  # noqa: E402


class _V:
    def __init__(self, text=None, exc=None):
        self.text, self.exc = text, exc

    def read(self, name):
        if self.exc:
            raise self.exc
        return self.text


def test_prints_plaintext_to_stdout_only(capsys):
    assert vault_cat.main(["finance"], vault=_V("---\ndomain: finance\n---\n")) == 0
    assert capsys.readouterr().out.startswith("---")


def test_unavailable_and_error_exit_codes(capsys):
    assert vault_cat.main(["finance"], vault=_V(exc=VaultUnavailable("no key"))) == 2
    assert vault_cat.main(["finance"], vault=_V(exc=VaultError("bad"))) == 3
    assert vault_cat.main([], vault=_V("x")) == 3
    assert "unavailable" in capsys.readouterr().err
