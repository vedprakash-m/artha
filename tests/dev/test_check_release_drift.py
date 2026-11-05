"""Audit H3: commits that invalidate the signed release are named before they land."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_release_drift", ROOT / "tools" / "check_release_drift.py")
drift_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift_tool)


def test_core_and_capability_modules_are_flagged_but_docs_are_not():
    touched = drift_tool.drift(["scripts/catchup_core/runtime.py", "scripts/catchup_core/migrations/0001_init.sql",
                                "scripts/preflight.py", "docs/catchup-core.md", "tests/unit/test_x.py"])
    assert "scripts/catchup_core/runtime.py" in touched
    assert "scripts/catchup_core/migrations/0001_init.sql" in touched
    assert "scripts/preflight.py" in touched
    assert "docs/catchup-core.md" not in touched and "tests/unit/test_x.py" not in touched


def test_new_core_file_is_flagged_even_before_it_exists_on_disk():
    assert drift_tool.drift(["scripts/catchup_core/brand_new.py"]) == ["scripts/catchup_core/brand_new.py"]


def test_selection_matches_the_runtime_fingerprint():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import inspect

        from catchup_core import runtime
        source = inspect.getsource(runtime._implementation_fingerprint)
    finally:
        sys.path.pop(0)
    # The tool mirrors the runtime's selection; if the runtime changes, this test must be revisited.
    assert "capability.modules" in source and "catchup_core').glob('*.py')" in source
    assert "migrations').glob('*.sql')" in source


def test_never_blocks(capsys):
    assert drift_tool.main(["--files", "scripts/catchup_core/runtime.py"]) == 0
    assert "re-signs" in capsys.readouterr().err
    assert drift_tool.main(["--files", "README.md"]) == 0
