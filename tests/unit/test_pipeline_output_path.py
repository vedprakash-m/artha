"""WP-03 / P1: plaintext pipeline output never lands in the synced workspace."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pipeline  # noqa: E402
from lib import local_paths  # noqa: E402


@pytest.fixture
def local(tmp_path, monkeypatch):
    root = tmp_path / "local"
    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(root))
    return root


def test_default_output_is_inside_local_dir(local):
    assert local_paths.pipeline_output_path() == local / "pipeline_output.jsonl"
    assert oct(stat.S_IMODE(os.stat(local).st_mode)) == "0o700"


def test_relative_output_resolves_under_local_dir(local, tmp_path):
    assert pipeline._validate_output_path("pipeline_output.jsonl", tmp_path) == \
        (local / "pipeline_output.jsonl").resolve()


def test_legacy_tmp_workspace_path_is_rejected(local, tmp_path):
    (tmp_path / "tmp").mkdir()
    with pytest.raises(ValueError, match="machine-local"):
        pipeline._validate_output_path(str(tmp_path / "tmp" / "pipeline_output.jsonl"), tmp_path)


def test_traversal_out_of_local_dir_is_rejected(local, tmp_path):
    with pytest.raises(ValueError):
        pipeline._validate_output_path("../../etc/passwd", tmp_path)
    with pytest.raises(ValueError):
        pipeline._validate_output_path("/etc/passwd", tmp_path)


def test_home_expansion_is_allowed_inside_local_dir(local, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(local.parent))
    inside = local / "x.jsonl"
    assert pipeline._validate_output_path(str(inside), tmp_path) == inside.resolve()
