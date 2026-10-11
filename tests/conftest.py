import os
import sys
from pathlib import Path

# WP-00: hermetic bootstrap MUST run before any Artha module is imported.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402

_isolation.isolate_environment()
_isolation.install_network_guard()
_PRODUCTION_SNAPSHOT = _isolation.ProtectedSnapshot.take(
    Path(__file__).resolve().parent.parent, _isolation.PROTECTED_ROOTS
)

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


def pytest_configure(config):
    config.addinivalue_line("markers", "network: test may use the real network (guard lifted)")


def pytest_collection_modifyitems(session, config, items):
    """Refuse to run the legacy suite against a checkout that holds live data."""
    root = Path(__file__).resolve().parent.parent
    selected = sorted({Path(str(i.fspath)).resolve().relative_to(root).as_posix()
                       for i in items if str(i.fspath).startswith(str(root))})
    if not _isolation.inplace_run_allowed(root, selected):
        raise pytest.UsageError(
            "Refusing to run tests in place: this checkout holds live Artha data and the "
            "legacy suite writes to state/, briefings/ and tmp/.\n"
            "Use `make test` (isolated workspace), or set ARTHA_ALLOW_INPLACE_TESTS=1 to override."
        )


@pytest.fixture(autouse=True)
def _network_policy(request):
    """Lift the socket guard only for tests explicitly marked ``network``."""
    _isolation.set_network_allowed(request.node.get_closest_marker("network") is not None)
    yield
    _isolation.set_network_allowed(False)


def pytest_sessionfinish(session, exitstatus):
    """WP00.AC3: fail the session when a test mutated production data paths."""
    root = Path(__file__).resolve().parent.parent
    if (root / _isolation.WORKSPACE_MARKER).exists():
        return  # throw-away workspace: writing to its data directories is harmless
    changes = _PRODUCTION_SNAPSHOT.diff()
    touched = [f"{kind}: {p}" for kind, paths in changes.items() for p in paths]
    if touched:
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        lines = ["", "PRODUCTION PATHS CHANGED DURING TEST RUN (hermeticity violation):"]
        lines += [f"  {t}" for t in touched[:50]]
        if tr is not None:
            for line in lines:
                tr.write_line(line)
        session.exitstatus = 1


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
