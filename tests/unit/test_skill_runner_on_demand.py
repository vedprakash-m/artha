"""On-demand skills (career PDF, portal scanner) must not run in the catch-up sweep."""
from __future__ import annotations

import skill_runner as sr


def test_on_demand_skills_are_never_due_in_the_sweep():
    for cadence in ("on_demand", "on-demand"):
        assert sr._cadence_elapsed("career_pdf_generator", cadence, {}) is False


def test_other_cadences_are_unchanged():
    assert sr._cadence_elapsed("x", "every_run", {}) is True
    assert sr._cadence_elapsed("x", "weekly", {}) is True   # cold start still runs


def test_force_still_runs_an_on_demand_skill():
    cfg = {"skills": {"career_pdf_generator": {"enabled": True, "cadence": "on_demand", "priority": "P1"}}}
    assert sr.should_run("career_pdf_generator", cfg, {}) is False
    assert sr.should_run("career_pdf_generator", cfg, {}, force=True) is True
