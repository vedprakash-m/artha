"""Security modules must not inherit the incumbent lib.* typing exemption."""
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('name', ['credentials', 'credential_migration', 'action_credentials'])
def test_new_credential_modules_reject_untyped_functions_under_the_repository_config(tmp_path, name):
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / 'scripts/lib' / (name + '.py')
    target.parent.mkdir(parents=True)
    target.write_text('def missing_types(value):\n    return value\n')
    result = subprocess.run([sys.executable, '-m', 'mypy', '--strict', '--explicit-package-bases',
        '--config-file', str(root / 'pyproject.toml'), str(target)], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and 'no-untyped-def' in result.stdout, result.stdout + result.stderr
