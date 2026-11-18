import os
import sys
import hashlib
from pathlib import Path

# WP-00: hermetic bootstrap MUST run before any Artha module is imported.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402

_isolation.isolate_environment()
_isolation.install_network_guard()
_isolation.install_credential_guard()
# Native account APIs ignore fake HOME and bypass Python socket guards. Tests must inject explicit fakes.
for _native_module in ("EventKit", "Contacts", "ScriptingBridge"):
    sys.modules[_native_module] = None
_BOOTSTRAP_ROOT = Path(__file__).resolve().parent.parent
_PRODUCTION_SNAPSHOT = (
    None if (_BOOTSTRAP_ROOT / _isolation.WORKSPACE_MARKER).exists()
    else _isolation.ProtectedSnapshot.take(_BOOTSTRAP_ROOT, _isolation.PROTECTED_ROOTS)
)

os.environ.setdefault("ARTHA_NO_REEXEC", "1")  # Prevent venv re-exec during tests
import pytest

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


def pytest_configure(config):
    config.addinivalue_line("markers", "network: test may use the real network (guard lifted)")
    config.addinivalue_line("markers", "slow: slow capacity, UAT, or multi-subprocess tests excluded from fast inner loop")
    config.addinivalue_line("markers", "scale: multi-thousand capacity stress tests run nightly")
    config.addinivalue_line("markers", "incumbent_only: legacy incumbent compatibility tests")
    config.addinivalue_line("markers", "os_contract: platform-specific OS contract tests")

    root = Path(__file__).resolve().parent.parent
    allowed_roots: tuple[Path, ...] = (root,) if (root / _isolation.WORKSPACE_MARKER).exists() else ()
    xml_targets = [root / "coverage.xml", root / "coverage-harness.xml"]
    cov_reports = getattr(config.option, "cov_report", None) or {}
    for target in (cov_reports.values() if isinstance(cov_reports, dict) else ()):
        if isinstance(target, str) and target.endswith(".xml"):
            xml_targets.append(Path(target) if Path(target).is_absolute() else root / target)
    _isolation.install_write_audit_hook(allowed_roots=allowed_roots, allow_coverage_xml=tuple(xml_targets))


def pytest_make_parametrize_id(val, argname):
    """Avoid putting large payloads in PYTEST_CURRENT_TEST on Windows."""
    if not isinstance(val, (str, bytes)) or len(val) <= 128:
        return None
    payload = val.encode("utf-8", "surrogatepass") if isinstance(val, str) else val
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"{argname[:32]}-{type(val).__name__}-{digest}"


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


@pytest.fixture(autouse=True, scope="module")
def _module_leaves_production_paths_alone(request):
    """Fail the module that touched state/, briefings/ and the other production paths, not just the session."""
    root = Path(__file__).resolve().parent.parent
    if (root / _isolation.WORKSPACE_MARKER).exists():
        yield
        return
    before = _PRODUCTION_SNAPSHOT.stat_marker()
    yield
    changes = _PRODUCTION_SNAPSHOT.stat_diff(before)
    touched = [f"{kind}: {p}" for kind, paths in changes.items() for p in paths]
    if touched:
        pytest.fail(f"{request.module.__name__} changed production paths (hermeticity violation): "
                    + "; ".join(touched[:10]), pytrace=False)


def pytest_sessionfinish(session, exitstatus):
    """WP00.AC3: fail the session when a test mutated production data paths."""
    root = Path(__file__).resolve().parent.parent
    if (root / _isolation.WORKSPACE_MARKER).exists():
        return  # throw-away workspace: writing to its data directories is harmless
    changes = _PRODUCTION_SNAPSHOT.diff() if _PRODUCTION_SNAPSHOT is not None else {}
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
def _guard_sys_modules_and_cwd():
    """Verify no test leaks working directory changes or mutates pre-existing sys.modules keys.

    Note: permitting first-time lazy imports during a test keeps collection fast, so this guard
    does not prove global import-order independence across unrelated modules.
    """
    before_cwd = os.getcwd()
    before_modules = dict(sys.modules)
    try:
        yield
    finally:
        after_cwd = os.getcwd()
        if after_cwd != before_cwd:
            os.chdir(before_cwd)
            pytest.fail(f"Test altered os.getcwd() from {before_cwd!r} to {after_cwd!r}", pytrace=False)
        mutated = [
            name for name, mod in before_modules.items()
            if name not in sys.modules or sys.modules[name] is not mod
        ]
        if mutated:
            for name in mutated:
                sys.modules[name] = before_modules[name]
            pytest.fail(
                "Test mutated/deleted pre-existing sys.modules entries without restoring via monkeypatch: "
                + ", ".join(sorted(mutated)[:10]),
                pytrace=False,
            )


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
