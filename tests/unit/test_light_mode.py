from __future__ import annotations

from lib.light_mode import effective_cutover


def test_cutover_file_can_only_be_forced_off():
    assert effective_cutover(configured=True, environment=None) is True
    assert effective_cutover(configured=True, environment="1") is True
    assert effective_cutover(configured=True, environment="0") is False
    assert effective_cutover(configured=False, environment=None) is False
    assert effective_cutover(configured=False, environment="1") is False


def test_cutover_description_names_ignored_opt_in(monkeypatch):
    from lib.light_mode import describe_cutover

    description = describe_cutover(configured=False, environment="1")
    assert description["enabled"] is False
    assert description["mode"] == "light"
    assert "ignored" in description["detail"].lower()


def _config(root, text):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "catchup_core.yaml").write_text(text, encoding="utf-8")


def test_configured_cutover_reads_the_real_file_shape(tmp_path):
    from lib.light_mode import configured_cutover

    assert configured_cutover(tmp_path) is False  # missing file
    _config(tmp_path, "schema: 1\ncutover: true  # reviewed\nrelease: {}\n")
    assert configured_cutover(tmp_path) is True
    _config(tmp_path, "cutover: false\n")
    assert configured_cutover(tmp_path) is False
    _config(tmp_path, "cutover: [unterminated\n")
    assert configured_cutover(tmp_path) is False  # unreadable never enables the Engine


def test_preflight_handoff_check_follows_the_config_file(monkeypatch, tmp_path):
    from preflight import runtime_checks

    monkeypatch.delenv("ARTHA_CUTOVER", raising=False)
    monkeypatch.setattr(runtime_checks, "ARTHA_DIR", str(tmp_path))
    _config(tmp_path, "cutover: true\n")
    assert runtime_checks.check_light_handoff().passed
    assert "Authoritative" in runtime_checks.check_effective_cutover().message

    _config(tmp_path, "cutover: false\n")
    result = runtime_checks.check_light_handoff()
    assert not result.passed
    assert result.severity == "P1"  # unverified handoff means read-only use, never a NO-GO
    assert "Light mode" in runtime_checks.check_effective_cutover().message
