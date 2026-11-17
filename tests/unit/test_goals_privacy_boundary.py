from __future__ import annotations

import sys

import pytest


import goals_view


@pytest.mark.parametrize("scope", ["personal", "all"])
def test_personal_goal_scopes_are_denied_before_any_file_read(monkeypatch, capsys, scope):
    monkeypatch.setattr(sys, "argv", ["goals_view.py", "--scope", scope])
    monkeypatch.setattr(goals_view, "_read_goals", lambda: pytest.fail("personal State was read"))

    assert goals_view.main() == 1
    assert "trusted personal-scope binding" in capsys.readouterr().err
