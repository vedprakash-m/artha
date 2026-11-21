"""Work state integrity helpers and the preflight check that uses them."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from lib.work_state_integrity import find_integrity_issues, refresh_blockers  # noqa: E402
from preflight.work_privacy_checks import check_work_state_integrity  # noqa: E402

_GOOD = "---\nschema_version: '1.0'\n---\n# body\n"


def _write(tmp_path: Path, name: str, data: bytes) -> None:
    (tmp_path / name).write_bytes(data)


def test_missing_dir_has_no_issues(tmp_path):
    assert find_integrity_issues(tmp_path / "absent") == []


def test_valid_file_has_no_issues(tmp_path):
    _write(tmp_path, "ok.md", _GOOD.encode())
    assert find_integrity_issues(tmp_path) == []


def test_each_defect_is_reported(tmp_path):
    _write(tmp_path, "cr.md", b"---\r\r\na: 1\r\r\n---\r\r\n")
    _write(tmp_path, "nofm.md", b"# no frontmatter\n")
    _write(tmp_path, "open.md", b"---\na: 1\n")
    _write(tmp_path, "badyaml.md", b"---\na: [unclosed\n---\n")
    _write(tmp_path, "list.md", b"---\n- a\n- b\n---\n")
    _write(tmp_path, "binary.md", b"\xff\xfe\x00")
    text = "\n".join(find_integrity_issues(tmp_path))
    for expected in (
        "cr.md: doubled carriage returns",
        "nofm.md: missing frontmatter",
        "open.md: frontmatter is not closed",
        "badyaml.md: frontmatter is not valid YAML",
        "list.md: frontmatter is not a mapping",
        "binary.md: unreadable",
    ):
        assert expected in text


def test_refresh_blockers():
    assert refresh_blockers(True, True) == []
    assert refresh_blockers(True, None) == []
    out = refresh_blockers(False, False)
    assert "setup_msgraph_oauth.py --work" in out[0]
    assert "work bootstrap" in out[1]


def test_preflight_check_passes_on_clean_state(tmp_path):
    _write(tmp_path, "ok.md", _GOOD.encode())
    result = check_work_state_integrity(tmp_path)
    assert result.passed


def test_preflight_check_fails_on_bad_state(tmp_path):
    _write(tmp_path, "bad.md", b"# no frontmatter\n")
    result = check_work_state_integrity(tmp_path)
    assert not result.passed
    assert "bad.md" in result.message
    assert "work_reader.py --command health" in result.fix_hint
