"""tests/unit/test_architectural_boundaries.py — Phase 1+2: DAG enforcement.

Verifies:
  1. No cross-domain imports between work/, channel/, and preflight/ subpackages
  2. No top-level script imports connectors directly (only pipeline.py is exempt)
  3. No connector imports from domain subpackages (upward imports)
  4. No connector-to-connector imports (cross-connector imports)

AST analysis of import statements throughout.

Ref: specs/pay-debt.md §3.1, §5.2 T1-10
     specs/pay-debt-reloaded.md §6 WS-4 Rules 1-3
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Root of the scripts directory
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"

# Domain subpackages that must NOT import each other
DOMAIN_PACKAGES = {"work", "channel", "preflight"}


def _get_imports(source_path: Path) -> list[str]:
    """Return all module names imported in a Python file."""
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.append(node.module)
    return imported


def _package_of(module_name: str) -> str | None:
    """Return the domain package name if the module is in a domain subpackage."""
    # e.g. "work.helpers" → "work", "channel.router" → "channel"
    parts = module_name.split(".")
    if parts[0] in DOMAIN_PACKAGES:
        return parts[0]
    return None


def test_no_cross_domain_imports():
    """No domain subpackage (work/, channel/, preflight/) imports another.

    Architectural boundary rule from specs/pay-debt.md §3.1:
      work/* must NOT import channel/*, channel/* must NOT import work/*, etc.
    """
    violations: list[str] = []

    for pkg in DOMAIN_PACKAGES:
        pkg_dir = SCRIPTS_DIR / pkg
        if not pkg_dir.exists():
            continue  # Package doesn't exist yet — skip (will matter post-Phase 3/4/5)

        for py_file in pkg_dir.rglob("*.py"):
            imports = _get_imports(py_file)
            for imp in imports:
                target_pkg = _package_of(imp)
                if target_pkg is not None and target_pkg != pkg:
                    rel = py_file.relative_to(SCRIPTS_DIR)
                    violations.append(
                        f"{rel} imports {imp!r} (cross-domain: {pkg} → {target_pkg})"
                    )

    assert violations == [], (
        "Cross-domain imports detected (violates DAG from specs/pay-debt.md §3.1):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# WS-4 Rule 1: Top-level scripts must not import connectors directly
# ---------------------------------------------------------------------------

def test_no_direct_connector_imports():
    """Top-level scripts must not import connector modules directly.

    Connector access must go through pipeline.py's security handler abstraction,
    which enforces the allowlist, retry, and structured logging.

    Exception: pipeline.py itself (loads handlers dynamically via importlib).

    Ref: specs/pay-debt-reloaded.md §6.2 Rule 1
    """
    # pipeline.py IS the gateway; connectors/ dir itself is excluded (self-imports OK)
    EXEMPT = {
        "pipeline.py",
        "preflight.py",         # pre-flight health checker — lazy HA connector import only for
                                # health_check(), not for data fetch; pre-dates WS-1 migration
        "sharepoint_kb_sync.py",  # standalone KB sync orchestrator — spec-mandated to call
                                  # msgraph_sharepoint.fetch() directly (NOT via pipeline.py);
                                  # see specs/kb-graph.md §sharepoint_kb_sync architecture note
    }
    violations: list[str] = []

    for py_file in SCRIPTS_DIR.glob("*.py"):
        if py_file.name in EXEMPT:
            continue
        imports = _get_imports(py_file)
        for imp in imports:
            if imp.startswith("connectors."):
                violations.append(f"{py_file.name} imports {imp!r}")

    assert violations == [], (
        "Direct connector imports detected in top-level scripts "
        "(use pipeline.fetch_single() instead — specs/pay-debt-reloaded.md §6.2 Rule 1):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# WS-4 Rule 2: No connector shall import from domain subpackages
# ---------------------------------------------------------------------------

def test_no_connector_upward_imports():
    """Connectors must not import from work/, channel/, or preflight/ subpackages.

    Upward imports create hidden coupling between the transport layer
    (connectors/) and the domain logic layer (work/, channel/, preflight/).
    Connectors must remain stateless transport adapters.

    Ref: specs/pay-debt-reloaded.md §6.2 Rule 2
    """
    connector_dir = SCRIPTS_DIR / "connectors"
    if not connector_dir.exists():
        pytest.skip("connectors/ directory not found")

    violations: list[str] = []
    for py_file in connector_dir.rglob("*.py"):
        imports = _get_imports(py_file)
        for imp in imports:
            target_pkg = _package_of(imp)
            if target_pkg in DOMAIN_PACKAGES:
                rel = py_file.relative_to(SCRIPTS_DIR)
                violations.append(f"{rel} imports {imp!r} (upward import into {target_pkg}/)")

    assert violations == [], (
        "Connector upward imports detected (connectors must not import from domain subpackages — "
        "specs/pay-debt-reloaded.md §6.2 Rule 2):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# WS-4 Rule 3: No connector-to-connector imports
# ---------------------------------------------------------------------------

def test_no_connector_cross_imports():
    """A connector module must not import another connector module.

    Each connector is an independent transport adapter. Cross-connector
    imports create hidden ordering dependencies and coupling.
    Exception: connectors/__init__.py (packaging boilerplate).

    Ref: specs/pay-debt-reloaded.md §6.2 Rule 3
    """
    connector_dir = SCRIPTS_DIR / "connectors"
    if not connector_dir.exists():
        pytest.skip("connectors/ directory not found")

    violations: list[str] = []
    for py_file in connector_dir.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        this_module = f"connectors.{py_file.stem}"
        imports = _get_imports(py_file)
        for imp in imports:
            if imp.startswith("connectors.") and imp != this_module:
                rel = py_file.relative_to(SCRIPTS_DIR)
                violations.append(f"{rel} imports {imp!r} (cross-connector import)")

    assert violations == [], (
        "Connector cross-imports detected (connectors must not import each other — "
        "specs/pay-debt-reloaded.md §6.2 Rule 3):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# WS-2D Rule 4: No direct config YAML file reads — must use lib.config_loader
# ---------------------------------------------------------------------------

def test_no_direct_config_yaml_open():
    """No script may open config YAML files directly via yaml.safe_load / open().

    All config reads must go through lib.config_loader.load_config() so that
    the two-layer cache, invalidation, and error normalisation are applied
    consistently across the codebase.

    Exemptions (intentionally frozen / fail-degraded / write-path):
      - lib/config_loader.py  (the implementation itself)
      - action_executor.py    (fail-degraded frozen fallback contract)
      - setup_*.py / upgrade.py  (write-path bootstrappers)

    Detection heuristic: for each ``yaml.safe_load`` call in a file, the tool
    looks back up to 12 lines to find any known config file name, catching both
    inline patterns (``yaml.safe_load(cfg.read_text())``) and multi-line
    ``with open(path) as f: yaml.safe_load(f)`` blocks without false-flagging
    files that merely mention config names in docstrings or exception messages.

    Ref: specs/pay-debt-reloaded.md §6.2 Rule 4
    """
    _CONFIG_NAMES = {
        "connectors.yaml",
        "artha_config.yaml",
        "channels.yaml",
        "actions.yaml",
        "user_profile.yaml",
        "domain_registry.yaml",
    }
    _EXEMPT = {
        "config_loader.py",
        "action_executor.py",
        "pipeline.py",              # spec-explicit: fail-degraded frozen fallback contract
        "setup_slack.py",
        "setup_ha_token.py",
        "setup_todoist.py",
        "setup_plaid.py",
        "setup_channel.py",         # setup/write-path bootstrapper
        "upgrade.py",
        "migrate.py",               # write-path: generates user_profile.yaml from settings.md
        "migrate_state.py",         # write-path: migration tool that bumps schema_version
        "migrate_actions_yaml.py",  # write-path: rewrites actions.yaml schema
        "work_bootstrap.py",        # write-path: populates user_profile.yaml during guided setup
        "profile_loader.py",        # has write-path toggle_domain() that reads+writes user_profile.yaml
        "setup_todo_lists.py",      # write-path: reads then writes user_profile.yaml integrations
        "preflight.py",             # pre-flight health checker — reads config before system init;
                                    # diagnostic read path; pre-dates WS-2 config_loader migration
    }
    _LOOKBACK = 12  # lines before yaml.safe_load to search for config file name

    violations: list[str] = []
    for py_file in SCRIPTS_DIR.rglob("*.py"):
        if py_file.name in _EXEMPT:
            continue
        src = py_file.read_text(encoding="utf-8", errors="ignore")
        if "yaml.safe_load" not in src:
            continue
        lines = src.splitlines()
        flagged = False
        for i, line in enumerate(lines):
            if "yaml.safe_load" not in line:
                continue
            # Look back up to _LOOKBACK lines (inclusive) to find config file name.
            # Require the name to appear *quoted* (as a file-path string arg) to
            # avoid false positives from comments and error-message strings.
            window = lines[max(0, i - _LOOKBACK) : i + 1]
            for cfg_name in _CONFIG_NAMES:
                if any(
                    f'"{cfg_name}"' in w or f"'{cfg_name}'" in w
                    for w in window
                ):
                    rel = py_file.relative_to(SCRIPTS_DIR)
                    violations.append(f"{rel}: direct yaml.safe_load of {cfg_name!r}")
                    flagged = True
                    break
            if flagged:
                break  # one violation per file is enough

    assert violations == [], (
        "Direct config YAML reads detected — use lib.config_loader.load_config() instead "
        "(specs/pay-debt-reloaded.md §6.2 Rule 4):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# WS-4 Rule 5: narrative subpackage boundary
# ---------------------------------------------------------------------------

def test_narrative_no_cross_module_imports():
    """Narrative submodules import only from _base and work.helpers."""
    narrative_dir = SCRIPTS_DIR / "narrative"
    if not narrative_dir.exists():
        pytest.skip("scripts/narrative/ not found")
    ALLOWED = {"narrative._base", "narrative", "work.helpers"}
    violations: list[str] = []
    for py_file in narrative_dir.rglob("*.py"):
        if py_file.name in ("__init__.py", "_base.py"):
            continue
        imports = _get_imports(py_file)
        for imp in imports:
            if imp.startswith("narrative.") and imp not in ALLOWED:
                violations.append(f"{py_file.name} imports {imp!r}")
    assert violations == [], (
        "Narrative submodule cross-imports detected — submodules may only import "
        "from narrative._base (specs/pay-debt-reloaded.md §6.3 Rule 5):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# WS-4 Rule 6: No threading.Lock inside async functions with await (post-WS9)
# ---------------------------------------------------------------------------

def _contains_lock_acquire(func_node: ast.AsyncFunctionDef) -> bool:
    """Return True if the async function contains a threading.Lock acquisition.

    Detects both explicit `.acquire()` calls and `with lock:` context managers
    where the context expression is a Name or Attribute (i.e., a variable that
    could be a threading.Lock or RLock).
    """
    for node in ast.walk(func_node):
        # Detect lock.acquire() or _lock.acquire()
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "acquire"
        ):
            return True
        # Detect `with lock:` / `with self._lock:` (context manager — bare name or attr,
        # NOT a function call like `with open(...)` or `with contextlib.suppress(...)`)
        if isinstance(node, ast.With):
            for item in node.items:
                ctx = item.context_expr
                if isinstance(ctx, (ast.Name, ast.Attribute)):
                    return True
    return False


def _contains_await(func_node: ast.AsyncFunctionDef) -> bool:
    """Return True if the async function contains at least one await expression."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Await):
            return True
    return False


def test_no_threading_lock_in_async_with_await():
    """Detect threading.Lock acquisition inside async def functions that also
    contain await expressions.  This is the most common asyncio deadlock pattern
    (holding a thread lock across a yield point freezes the event loop for all
    other coroutines).  Enforces the constraint from WS-9-B step 4 via static
    analysis rather than prose.

    Target files: channel_listener.py and channels/discord.py — the two files
    where the asyncio/threading boundary exists (see WS-9-A audit in
    tmp/ws9-audit.md for full classification).

    Ref: specs/pay-debt-reloaded.md §6.2 Rule 6
    """
    TARGET_FILES = [
        SCRIPTS_DIR / "channel_listener.py",
        SCRIPTS_DIR / "channels" / "discord.py",
    ]
    violations: list[str] = []
    for py_file in TARGET_FILES:
        if not py_file.exists():
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                has_lock = _contains_lock_acquire(node)
                has_await = _contains_await(node)
                if has_lock and has_await:
                    violations.append(
                        f"{py_file.name}:{node.name} holds threading.Lock across await"
                    )
    assert violations == [], (
        "threading.Lock inside async-with-await detected — deadlock risk "
        "(specs/pay-debt-reloaded.md §6.2 Rule 6):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
