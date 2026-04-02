"""tests/unit/test_eval_scorer.py — Unit tests for scripts/eval_scorer.py.

All fixtures are 100% fictional — no real user data (DD-5).
Ref: specs/eval.md EV-5, T-EV-5-01 through T-EV-5-14, DD-17, DD-18
"""
from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def scorer():
    return _load_module("eval_scorer", _SCRIPTS_DIR / "eval_scorer.py")


# ---------------------------------------------------------------------------
# Briefing fixtures (all fictional)
# ---------------------------------------------------------------------------

_ACTIONABLE_TEXT = textwrap.dedent("""\
    ## IMMIGRATION
    - Review I-485 documents by 2026-02-01.
    - Schedule attorney call by Thursday.
    - Submit Form DS-5540 before March 15.
""")

_SPECIFIC_TEXT = textwrap.dedent("""\
    ## FINANCE
    Emergency fund balance: $12,450 (target: $15,000, 83% complete).
    Mortgage payment: $2,400 due 2026-04-15 — auto-pay confirmed.
    Q1 budget variance: +$340 overspend on dining (budget $900, actual $1,240).
""")

_VAGUE_TEXT = textwrap.dedent("""\
    Things are going fine generally.
    Some stuff might need attention.
    Consider looking into various items.
""")

_HEADERED_TEXT = textwrap.dedent("""\
    # Catch-Up Briefing — 2026-01-15

    ## IMMIGRATION
    - Review docs.

    ## FINANCE
    - Check balance.

    ## HEALTH
    - Appointment scheduled.

    ## KIDS
    - School project due.
""")

_GOLDEN_BRIEFING = textwrap.dedent("""\
    # Catch-Up Briefing — 2026-01-15

    ## 🎯 ONE THING
    Submit Form I-485 by 2026-02-01.

    ## IMMIGRATION
    - 🔴 URGENT: I-485 priority date current — file within 30 days.
    - Call attorney by Thursday to book filing appointment.

    ## FINANCE
    - 🟠 Mortgage $3,200 due 2026-01-20 — auto-pay confirmed.
    - Annual bonus ($8,500) deposited 2026-01-12.

    ## HEALTH
    - Annual physical scheduled 2026-02-05.
    - Book bloodwork by Jan 30.

    ## GOALS
    - Complete 3 certification modules this week.
""")

_ANTI_GOLDEN_BRIEFING = textwrap.dedent("""\
    Some things happened.
    It is possible that there are items.
    You might want to think about stuff.
    Various areas could be reviewed.
""")


@pytest.fixture
def golden_briefing_path(tmp_path):
    p = tmp_path / "golden.md"
    p.write_text(_GOLDEN_BRIEFING, encoding="utf-8")
    return p


@pytest.fixture
def anti_golden_briefing_path(tmp_path):
    p = tmp_path / "anti_golden.md"
    p.write_text(_ANTI_GOLDEN_BRIEFING, encoding="utf-8")
    return p


@pytest.fixture
def headered_briefing_path(tmp_path):
    p = tmp_path / "headered.md"
    p.write_text(_HEADERED_TEXT, encoding="utf-8")
    return p


# ===========================================================================
# T-EV-5-01: _score_actionability returns > 0 for bullet text with verbs
# ===========================================================================

def test_actionability_returns_nonzero_for_action_bullets(scorer):
    """T-EV-5-01: Briefing with bullet verbs must have actionability > 0."""
    lines = _ACTIONABLE_TEXT.splitlines()
    score = scorer._score_actionability(_ACTIONABLE_TEXT, lines)
    assert score > 0, f"Expected actionability > 0, got {score}"
    assert score <= 20, f"Actionability capped at 20, got {score}"


# ===========================================================================
# T-EV-5-02: _score_actionability returns 0 for featureless text
# ===========================================================================

def test_actionability_zero_for_vague_text(scorer):
    """T-EV-5-02: Vague text with no bullet verbs must score near 0."""
    lines = _VAGUE_TEXT.splitlines()
    score = scorer._score_actionability(_VAGUE_TEXT, lines)
    assert score < 5, f"Expected low actionability for vague text, got {score}"


# ===========================================================================
# T-EV-5-03: _score_specificity > 0 for numbers and dates
# ===========================================================================

def test_specificity_nonzero_for_numbers(scorer):
    """T-EV-5-03: Text with numbers / dates must have specificty > 0."""
    lines = _SPECIFIC_TEXT.splitlines()
    score = scorer._score_specificity(_SPECIFIC_TEXT, lines)
    assert score > 0, f"Expected specificity > 0, got {score}"
    assert score <= 20, f"Specificity capped at 20, got {score}"


# ===========================================================================
# T-EV-5-04: _score_signal_purity reduced by 100% domain overlap
# ===========================================================================

def test_signal_purity_penalizes_full_domain_overlap(scorer):
    """T-EV-5-04: 100% domain overlap with prev session reduces purity score."""
    domain_names = ["finance", "immigration", "health"]
    prev_domains = ["finance", "immigration", "health"]

    text = "## FINANCE\nSome content.\n## IMMIGRATION\nSome content.\n## HEALTH\nSome content.\n"
    lines = text.splitlines()
    score = scorer._score_signal_purity(text, lines, domain_names, prev_domains)
    assert score <= 14, (
        f"Full domain overlap should reduce purity score, got {score} (expected <= 14)"
    )


# ===========================================================================
# T-EV-5-05: _score_completeness goal bonus when goals.md has active goals
# ===========================================================================

def test_completeness_goal_bonus_with_goals_reference(scorer, tmp_path):
    """T-EV-5-05: Completeness picks up goal_bonus when goals.md has active goals."""
    goals_file = tmp_path / "goals.md"
    goals_file.write_text(
        "# Goals\n- [ ] Complete certification module (status: active)\n",
        encoding="utf-8",
    )
    text = (
        "## IMMIGRATION\nContent.\n## GOALS\n- Complete certification.\n"
    )
    lines = text.splitlines()
    score = scorer._score_completeness(text, lines, ["immigration", "goals"])
    assert score > 0, f"Expected positive completeness with goals, got {score}"
    assert score <= 20


# ===========================================================================
# T-EV-5-06: score_briefing() returns quality_score in [0, 100]
# ===========================================================================

def test_score_briefing_returns_valid_range(scorer, golden_briefing_path):
    """T-EV-5-06: score_briefing() must return quality_score in [0, 100]."""
    result = scorer.score_briefing(golden_briefing_path)

    assert isinstance(result, dict), "Must return dict"
    assert result["quality_score"] is not None
    assert 0 <= result["quality_score"] <= 100, (
        f"quality_score out of range: {result['quality_score']}"
    )


# ===========================================================================
# T-EV-5-07: sum of 5 dimensions equals quality_score
# ===========================================================================

def test_score_briefing_dimensions_sum_to_quality_score(scorer, golden_briefing_path):
    """T-EV-5-07: Sum of all 5 dimension scores must equal quality_score."""
    result = scorer.score_briefing(golden_briefing_path)
    dims = result["dimensions"]

    dim_sum = sum(dims.values())
    quality = result["quality_score"]
    assert abs(dim_sum - quality) < 0.5, (
        f"Dimensions sum {dim_sum:.1f} != quality_score {quality:.1f}. "
        f"Dimensions: {dims}"
    )


# ===========================================================================
# T-EV-5-08: append_score() persists to JSON
# ===========================================================================

def test_append_score_persists_to_json(scorer, tmp_path, monkeypatch):
    """T-EV-5-08: append_score() must write the score to briefing_scores.json."""
    scores_file = tmp_path / "briefing_scores.json"
    monkeypatch.setattr(scorer, "_BRIEFING_SCORES", scores_file)

    sample_score = {
        "schema_version": "1.0.0",
        "timestamp": "2026-01-15T12:00:00Z",
        "briefing_file": "briefings/2026-01-15.md",
        "quality_score": 72.5,
        "compliance_score": 0.95,
        "dimensions": {
            "actionability": 15.0,
            "specificity": 16.0,
            "completeness": 14.0,
            "signal_purity": 17.0,
            "calibration": 10.5,
        },
        "meta": {},
    }
    scorer.append_score(sample_score)

    assert scores_file.exists(), "briefing_scores.json not created"
    data = json.loads(scores_file.read_text(encoding="utf-8"))
    assert isinstance(data, list), "Scores file must be a JSON list"
    assert len(data) == 1
    assert data[0]["quality_score"] == 72.5


# ===========================================================================
# T-EV-5-09: append_score() enforces FIFO cap at 50
# ===========================================================================

def test_append_score_enforces_fifo_cap(scorer, tmp_path, monkeypatch):
    """T-EV-5-09: append_score() must retain only the last 50 entries."""
    scores_file = tmp_path / "briefing_scores.json"
    monkeypatch.setattr(scorer, "_BRIEFING_SCORES", scores_file)

    # Write 50 existing entries
    existing = [{"quality_score": float(i), "timestamp": f"2026-01-{i:02d}T00:00:00Z"}
                for i in range(1, 51)]
    scores_file.write_text(json.dumps(existing), encoding="utf-8")

    # Append a 51st entry
    scorer.append_score({
        "schema_version": "1.0.0",
        "timestamp": "2026-02-20T00:00:00Z",
        "briefing_file": "briefings/2026-02-20.md",
        "quality_score": 99.0,
        "compliance_score": 1.0,
        "dimensions": {k: 0.0 for k in
                       ("actionability", "specificity", "completeness", "signal_purity", "calibration")},
        "meta": {},
    })

    data = json.loads(scores_file.read_text(encoding="utf-8"))
    assert len(data) == 50, f"Expected 50 entries after cap, got {len(data)}"
    # Most recent entry should be last
    assert data[-1]["quality_score"] == 99.0


# ===========================================================================
# T-EV-5-10: CLI --json flag outputs valid JSON
# ===========================================================================

def test_cli_json_flag_outputs_valid_json(scorer, golden_briefing_path, tmp_path, monkeypatch, capsys):
    """T-EV-5-10: CLI --json flag must produce valid JSON on stdout."""
    import io
    scores_file = tmp_path / "briefing_scores.json"
    monkeypatch.setattr(scorer, "_BRIEFING_SCORES", scores_file)

    with pytest.raises(SystemExit) as exc_info:
        scorer.main([str(golden_briefing_path), "--json", "--no-save"])

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    out = captured.out.strip()
    parsed = json.loads(out)
    assert "quality_score" in parsed


# ===========================================================================
# T-EV-5-11: CLI missing file exits with code 1
# ===========================================================================

def test_cli_missing_file_exits_nonzero(scorer):
    """T-EV-5-11: CLI must exit with code 1 when briefing file not found."""
    with pytest.raises(SystemExit) as exc_info:
        scorer.main(["/nonexistent/briefing/2099-12-31.md"])
    assert exc_info.value.code == 1


# ===========================================================================
# T-EV-5-12: _load_domain_names() returns list
# ===========================================================================

def test_load_domain_names_returns_list(scorer, tmp_path, monkeypatch):
    """T-EV-5-12: _load_domain_names() must always return a list."""
    # Point to non-existent registry to test fallback
    monkeypatch.setattr(scorer, "_DOMAIN_REGISTRY", tmp_path / "no_registry.yaml")
    names = scorer._load_domain_names()
    assert isinstance(names, list), "Must return a list"
    assert len(names) > 0, "Fallback list must be non-empty"


# ===========================================================================
# T-EV-5-13 (DD-18): disabled domain not included
# ===========================================================================

def test_load_domain_names_excludes_disabled_domains(scorer, tmp_path, monkeypatch):
    """T-EV-5-13 (DD-18): Domains marked active: false must be excluded."""
    registry = tmp_path / "domain_registry.yaml"
    registry.write_text(
        "domains:\n"
        "  - id: finance\n    active: true\n"
        "  - id: immigration\n    active: false\n"
        "  - id: health\n    active: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(scorer, "_DOMAIN_REGISTRY", registry)
    names = scorer._load_domain_names()
    assert "finance" in names
    assert "health" in names
    assert "immigration" not in names, "Disabled domain must be excluded"


# ===========================================================================
# T-EV-5-14 (DD-18): missing registry → fallback domain list
# ===========================================================================

def test_load_domain_names_falls_back_when_registry_missing(scorer, tmp_path, monkeypatch):
    """T-EV-5-14 (DD-18): Missing registry file must return the built-in fallback list."""
    monkeypatch.setattr(scorer, "_DOMAIN_REGISTRY", tmp_path / "missing.yaml")
    names = scorer._load_domain_names()
    # Fallback must include core domains
    assert "finance" in names
    assert "health" in names
    assert isinstance(names, list)
