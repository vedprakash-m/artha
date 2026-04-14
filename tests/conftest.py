import os
os.environ.setdefault("ARTHA_NO_REEXEC", "1")  # Prevent venv re-exec during tests
import pytest
import shutil
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Standalone scripts — exclude from pytest collection.
# These use sys.exit() at module level and run directly as: python tests/<name>.py
# Collecting them via pytest would trigger module-level code and crash collection.
# ---------------------------------------------------------------------------
collect_ignore = [
    "test_career_search.py",
    "test_career_content.py",
]

# ---------------------------------------------------------------------------
# Ensure:
#   1. scripts/ is on sys.path so tests can import modules directly,
#      e.g. ``from foundation import ...`` not ``from scripts.foundation import``
#      This prevents the Python dual-import problem.
#   2. The project root is on sys.path so tests can also import via the full
#      package path, e.g. ``from scripts.skills.X import Y``.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

@pytest.fixture
def temp_artha_dir():
    """Create a temporary Artha-like directory structure for safe testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        (tmp_path / "scripts").mkdir()
        (tmp_path / "state").mkdir()
        (tmp_path / "config").mkdir()
        (tmp_path / "prompts").mkdir()
        (tmp_path / ".tokens").mkdir()
        
        # Add a dummy audit log
        (tmp_path / "state" / "audit.md").write_text("# Audit Log\n")
        
        yield tmp_path


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Invalidate config_loader cache after every test.

    Prevents config cache bleed between tests when test fixtures
    write different config values to temp directories.

    Ref: specs/pay-debt-reloaded.md §4.3 WS-2-C step 3
    """
    yield
    try:
        from lib.config_loader import invalidate
        invalidate()
    except ImportError:
        pass
