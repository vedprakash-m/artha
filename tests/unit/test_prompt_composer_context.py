from __future__ import annotations

import pytest

from lib.context_budget import chars_for_tokens, estimate_token_count
from scripts.prompt_composer import (
    _bounded_examples,
    _truncate_tokens,
    compose_worker_context,
    load_few_shot_examples,
)


def _accepted_row(payload: str, *, timestamp: str = "2026-11-18T10:00:00Z") -> str:
    return f"| {timestamp} | session-1 | finance | review | accepted | {payload} |"


def test_oversized_audit_payload_obeys_integer_token_budget(tmp_path):
    audit = tmp_path / "audit.md"
    audit.write_text(_accepted_row("x" * 10_000), encoding="utf-8")

    examples = load_few_shot_examples(
        "finance",
        audit_path=audit,
        max_examples=1,
        max_tokens_each=10,
    )

    assert len(examples) == 1
    assert len(examples[0]) <= chars_for_tokens(10)


def test_zero_examples_is_a_real_empty_result(tmp_path):
    audit = tmp_path / "audit.md"
    audit.write_text(_accepted_row("keep"), encoding="utf-8")

    assert load_few_shot_examples(
        "finance",
        audit_path=audit,
        max_examples=0,
    ) == []


def test_load_and_composition_enforce_few_shot_hard_caps(tmp_path):
    audit = tmp_path / "audit.md"
    audit.write_text(
        "\n".join(_accepted_row(f"row-{index}") for index in range(20)),
        encoding="utf-8",
    )

    examples = load_few_shot_examples(
        "finance",
        audit_path=audit,
        max_examples=20,
        max_tokens_each=1_000,
    )

    assert len(examples) == 3
    assert all(estimate_token_count(example) <= 200 for example in examples)
    assert estimate_token_count("\n".join(examples)) <= 600


@pytest.mark.parametrize(
    "examples",
    [
        ["x" * 10_000],
        [f"example-{index}" for index in range(20)],
        ["x" * 700, "y" * 700, "z" * 700],
    ],
)
def test_supplied_examples_are_bounded_before_composition(examples):
    context = compose_worker_context(
        "finance",
        "state",
        [],
        "session-1",
        few_shot_examples=examples,
    )

    section = context.split("### Few-Shot Examples", 1)[1]
    assert estimate_token_count(section) <= 600
    assert section.count("\n  |") <= 3


def test_signal_values_and_identity_fields_remain_single_line():
    context = compose_worker_context(
        "finance\ninjected heading",
        "state",
        [{"signal_id": "SIG-1\ninjected id", "text": "first\nsecond"}],
        "session-1\ninjected session",
    )

    assert "## Domain: finance injected heading  |  Session: session-1 injected session" in context
    assert "  - [SIG-1 injected id] first second" in context
    assert context.count("\n  - [") == 1


def test_non_mapping_signals_are_omitted_without_losing_structure():
    context = compose_worker_context(
        "finance",
        "state",
        ["not a mapping", {"id": "SIG-1", "text": "valid"}],
        "session-1",
    )

    assert "  - [SIG-1] valid" in context
    assert "not a mapping" not in context


def test_zero_token_truncation_and_empty_example_are_bounded():
    assert _truncate_tokens("text", 0) == ""
    assert _bounded_examples(
        [""],
        max_examples=3,
        max_tokens_each=200,
    ) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_examples": -1},
        {"max_examples": "invalid"},
        {"max_tokens_each": 0},
        {"max_tokens_each": "invalid"},
    ],
)
def test_invalid_or_empty_example_budgets_return_empty(tmp_path, kwargs):
    audit = tmp_path / "audit.md"
    audit.write_text(_accepted_row("row"), encoding="utf-8")

    assert load_few_shot_examples(
        "finance",
        audit_path=audit,
        **kwargs,
    ) == []


def test_missing_audit_and_nonmatching_rows_are_empty(tmp_path):
    assert load_few_shot_examples("finance", audit_path=tmp_path / "missing") == []

    audit = tmp_path / "audit.md"
    audit.write_text(
        "\n".join(
            [
                "---",
                "not a row",
                _accepted_row("wrong status").replace("accepted", "pending"),
                _accepted_row("wrong domain").replace("finance", "health"),
            ]
        ),
        encoding="utf-8",
    )
    assert load_few_shot_examples("finance", audit_path=audit) == []


def test_audit_reader_failure_returns_empty():
    class BrokenAudit:
        def exists(self):
            raise OSError("metadata unavailable")

    assert load_few_shot_examples("finance", audit_path=BrokenAudit()) == []


def test_composition_failure_returns_structural_fallback():
    context = compose_worker_context(
        "finance\ninjected",
        "state",
        None,
        "session\ninjected",
    )

    assert context.startswith(
        "## Domain: finance injected  |  Session: session injected\n\nstate"
    )
