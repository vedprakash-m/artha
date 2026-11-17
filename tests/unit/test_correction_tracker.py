"""tests/unit/test_correction_tracker.py — Weighted correction scoring & precision gate tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.correction_tracker import (
    _CORRECTION_THRESHOLD,
    compute_quality_metrics,
    correction_score,
)


# ---------------------------------------------------------------------------
# correction_score tests
# ---------------------------------------------------------------------------

def test_strong_correction_scores_high():
    assert correction_score("That's wrong, it should be Tuesday") >= _CORRECTION_THRESHOLD


def test_explicit_wrong_scores_high():
    assert correction_score("Wrong — the meeting is at 3pm not 2pm") >= _CORRECTION_THRESHOLD


def test_actually_with_context_scores_above_threshold():
    assert correction_score("Actually, it's the third floor") >= _CORRECTION_THRESHOLD


def test_command_excluded_from_scoring():
    assert correction_score("goals") == 0.0
    assert correction_score("brief") == 0.0
    assert correction_score("catch me up") == 0.0


def test_non_correction_scores_zero():
    assert correction_score("Please show me the finance summary") < _CORRECTION_THRESHOLD


def test_empty_text_scores_zero():
    assert correction_score("") == 0.0


# ---------------------------------------------------------------------------
# compute_quality_metrics tests
# ---------------------------------------------------------------------------

def test_metrics_empty_list():
    m = compute_quality_metrics([])
    assert m["total_count"] == 0
    assert m["correction_count"] == 0
    assert m["correction_rate"] == 0.0


def test_metrics_all_corrections():
    texts = [
        "Wrong, it should be 5",
        "Incorrect: that's Tuesday not Monday",
        "Actually, that's wrong",
    ]
    m = compute_quality_metrics(texts)
    assert m["correction_count"] == 3
    assert m["total_count"] == 3
    assert m["correction_rate"] == 1.0


def test_metrics_mixed():
    texts = [
        "Wrong, it should be 5",      # correction
        "Show me the goals",           # not correction
        "Please continue",             # not correction
    ]
    m = compute_quality_metrics(texts)
    assert m["correction_count"] == 1
    assert m["total_count"] == 3
    assert m["correction_rate"] < 1.0


def test_precision_on_curated_corpus():
    """G-2 gate: precision on correction_corpus.jsonl must be ≥ 0.85."""
    corpus_path = (
        Path(__file__).resolve().parent.parent / "fixtures" / "correction_corpus.jsonl"
    )
    if not corpus_path.exists():
        pytest.skip("correction_corpus.jsonl not found")

    entries = [
        json.loads(line)
        for line in corpus_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    tp = fp = 0
    for entry in entries:
        predicted = correction_score(entry["text"]) >= _CORRECTION_THRESHOLD
        actual = entry["is_correction"]
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    assert precision >= 0.85, f"Precision {precision:.3f} < 0.85 (TP={tp}, FP={fp})"
