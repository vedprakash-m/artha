from __future__ import annotations

from scripts.agents import capital_agent, logistics_agent, readiness_agent, tribe_agent


def test_precomputed_markdown_writers_use_valid_state_frontmatter(
    tmp_path, monkeypatch
):
    capital = tmp_path / "state" / "finance_forecast.md"
    logistics = tmp_path / "state" / "logistics.md"
    readiness = tmp_path / "state" / "readiness.md"
    social = tmp_path / "state" / "social.md"
    stage = tmp_path / "state" / "content_stage.md"
    capital.parent.mkdir(parents=True)
    social.write_text(
        "---\nschema_version: '1.0'\ndomain: social\nlast_updated: '2026-11-19'\n---\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(capital_agent, "_STATE_FILE", capital)
    monkeypatch.setattr(logistics_agent, "_OUT_FILE", logistics)
    monkeypatch.setattr(readiness_agent, "_STATE_FILE", readiness)
    monkeypatch.setattr(tribe_agent, "_SOCIAL_FILE", social)
    monkeypatch.setattr(tribe_agent, "_STAGE_FILE", stage)

    capital_agent._write_state_file("2026-11-19", [], [], "synthetic")
    logistics_agent._write_state_file(
        "2026-11-19", ["_none_"], ["_none_"], ["_none_"], "synthetic"
    )
    readiness_agent._write_state_file(
        "2026-11-19", -1, None, None, None, "unknown", None, 0
    )
    tribe_agent._write_state_files("2026-11-19", [], [], 0, "synthetic")

    for path, domain in (
        (capital, "finance_forecast"),
        (logistics, "logistics"),
        (readiness, "readiness"),
        (social, "social"),
    ):
        content = path.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert f"domain: {domain}" in content
