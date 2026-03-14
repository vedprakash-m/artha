import pytest
import os
import shutil
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure scripts/ is on sys.path so tests can import modules the same way
# the production code does (e.g. ``import foundation`` not
# ``import scripts.foundation``).  This prevents the Python dual-import
# problem where the same .py file gets loaded as two separate modules.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
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
