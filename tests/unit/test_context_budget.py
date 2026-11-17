"""
tests/unit/test_context_budget.py — Tests for lib.context_budget.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.context_budget import check_budget, CHARS_PER_TOKEN, estimate_token_count


def test_check_budget_allows_within_limit():
    """Text that fits within the budget returns True."""
    # 350 chars → ~100 tokens at CHARS_PER_TOKEN=3.5; budget=200 → should pass
    text = "a" * 350
    assert check_budget(text, budget_tokens=200) is True


def test_check_budget_enforces_limit():
    """Text exceeding the budget returns False."""
    # 7000 chars → 2000 tokens; budget=1000 → should fail
    text = "a" * 7000
    assert check_budget(text, budget_tokens=1000) is False


def test_check_budget_exact_boundary():
    """Text exactly at the boundary is allowed (≤, not <)."""
    budget_tokens = 500
    # Create text that estimates exactly to budget_tokens
    exact_chars = int(budget_tokens * CHARS_PER_TOKEN)
    text = "a" * exact_chars
    assert estimate_token_count(text) == budget_tokens
    assert check_budget(text, budget_tokens=budget_tokens) is True


def test_check_budget_empty_text():
    """Empty text is always within budget."""
    assert check_budget("", budget_tokens=1) is True


def test_check_budget_zero_budget():
    """Text whose estimated token count is nonzero exceeds a zero-token budget."""
    # 4 chars → int(4/3.5) = 1 token; 1 > 0 → False
    assert check_budget("xxxx", budget_tokens=0) is False


def test_check_budget_session_start_gate():
    """Simulate the G-0 gate: 37,625 estimated tokens fit in session_start_budget_tokens=35000.
    
    Note: We test that check_budget correctly rejects if the estimate exceeds budget.
    The G-0 token total of 37,625 is ABOVE session_start_budget_tokens=35000, so
    check_budget would return False for the raw concatenation — this is by design.
    The gate fires a warning, not a hard block, at session start.
    """
    # 37,625 tokens estimated → exceeds 35,000 budget → returns False (warning mode)
    g0_token_estimate = 37_625
    text = "a" * int(g0_token_estimate * CHARS_PER_TOKEN)
    assert check_budget(text, budget_tokens=35_000) is False


@pytest.mark.parametrize("filename", ["context_offloader.py", "prompt_composer.py", "session_summarizer.py"])
def test_no_hardcoded_chars_per_token_4_outside_budget(filename: str):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    src = (scripts_dir / filename).read_text(encoding="utf-8")
    assert "_CHARS_PER_TOKEN = 4" not in src
    assert "CHARS_PER_TOKEN = 4" not in src


def test_chars_per_token_only_defined_in_context_budget():
    import ast

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    canonical = (scripts_dir / "lib" / "context_budget.py").resolve()
    violations = []
    for py_file in scripts_dir.rglob("*.py"):
        if py_file.resolve() == canonical:
            continue
        src = py_file.read_text(encoding="utf-8")
        if "context_budget" in src and "CHARS_PER_TOKEN" in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            targets = node.targets if isinstance(node, ast.Assign) else ([node.target] if isinstance(node, ast.AnnAssign) else [])
            for target in targets:
                if isinstance(target, ast.Name) and "CHARS_PER_TOKEN" in target.id:
                    violations.append(f"{py_file.name}:{target.id}")
    assert violations == []
