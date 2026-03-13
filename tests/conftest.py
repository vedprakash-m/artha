import pytest
import os
import shutil
import tempfile
from pathlib import Path

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
