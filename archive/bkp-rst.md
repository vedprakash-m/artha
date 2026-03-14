# Backup/Restore Module Extraction — Implementation Plan

**Date:** 2026-03-13 (rev 3.1: 2026-03-13)
**Status:** APPROVED v3.1 — ready for execution
**Scope:** Extract GFS backup/restore logic from `scripts/vault.py` into `scripts/backup.py`, introduce `scripts/foundation.py` as shared foundation, and deliver `backup.py` as a fully standalone CLI

---

## 0. Review Changelog

### v3 → v3.1 (execution-readiness fixes)

| # | Change | Rationale |
|---|--------|-----------|
| RC-14 | `main()` accepts `argv` parameter | `main()` used `parse_args()` (reads `sys.argv`), but test called non-existent `main_dispatch()`. Now `main(argv=None)` with `parse_args(argv)` — standard argparse testable pattern. |
| RC-15 | `BACKUP_DIR` frozen-alias enforcement note | Module-level `BACKUP_DIR = _config["BACKUP_DIR"]` freezes at import time. Added explicit rule + grep check: all function bodies must use `_config["BACKUP_DIR"]`. |
| RC-16 | `_do_preflight()` test scope clarified | Subprocess delegates to real `ARTHA_DIR`, ignoring `_config` patches. Documented as tested only via `--help` check; functional testing out of scope. |
| RC-17 | R3 expanded: full mock target mapping | `TestEncryptTriggersBackup` patches 4 mock targets; only `_backup_snapshot` actually changes module. Other 3 (`age_encrypt`, `get_public_key`, `check_age_installed`) stay valid because vault re-imports them from foundation. |
| RC-18 | `TestEncryptTriggersBackup` stays in `test_vault.py` | It tests `vault.do_encrypt()`, not backup functions. Moved out of the "11 classes to copy" list. Updated all test counts: vault=17, backup=77, total=94+. |
| RC-19 | Auto-validation keyring interaction note | Tests calling `backup_snapshot()` with stale/missing `last_validate` trigger auto-validation → `keyring.get_password()`. Added guidance: seed `last_validate` to today to suppress, or mock keyring. |

### v2 → v3

| # | Change | Rationale |
|---|--------|-----------|
| RC-1 | Renamed `crypto.py` → `foundation.py` | Of 18 exports, only 5 are cryptographic. The other 13 are directory constants, a logger, and a process terminator. "foundation" describes what the module actually is. |
| RC-2 | Moved `_load_backup_registry()` from vault.py → backup.py | It reads `user_profile.yaml → backup` and produces a data structure consumed exclusively by `backup_snapshot()`. Keeping it in vault.py means vault retains backup-aware code and backup.py can't offer a `snapshot` CLI. |
| RC-3 | Added `backup.py snapshot` subcommand | Without it, backup.py can read and restore archives but not create them. A "standalone CLI" that can't produce backups is incomplete. |
| RC-4 | Introduced `_config` dict in foundation.py | Eliminates the 3x monkeypatch fixture tax. Functions read from a shared dict; tests patch ONE object. |
| RC-5 | Strengthened backup failure handling in `do_encrypt()` | Print-to-stdout alone scrolls off-screen. Now also persists failure to `state/health-check.md` for next preflight gate. |
| RC-6 | Removed `backup.py preflight` — replaced with thin delegation | Duplicated checks from `vault.py health` and `preflight.py`. Now shells out to `vault.py health` to avoid logic drift. |
| RC-7 | Added automatic weekly backup validation | `backup_snapshot()` triggers `do_validate_backup()` when last validation is >7d old. ~3s cost, prevents months of silent corruption. |
| RC-8 | Added `backup.py export-key` / `backup.py import-key` | The age private key is the single point of failure. Without export/import, one hardware failure makes every backup ZIP worthless. |
| RC-9 | Fixed test count arithmetic | `TestGFSTierLogic` has 11 tests (was listed as 10). `TestBackupRegistry` has 6 tests (spec said 6, earlier analysis said 7). Verified from `pytest --co`. |
| RC-10 | Added `skills_cache` inconsistency resolution | `SENSITIVE_FILES` lists 10 entries including `skills_cache`; backup registry lists 9 sensitive entries. Must reconcile. |
| RC-11 | Added §3.6 Behavioral Contracts | Edge cases: restore during active session, duplicate same-date snapshots, unknown domains in ZIP. |
| RC-12 | Split into two commits instead of one atomic | Step 1 (foundation.py) is independently testable. Separate commit gives a safe rollback point. |
| RC-13 | Replaced line-number references with function names | Line numbers shift with every edit; function names are stable. |

---

## 1. Motivation

### 1.1 Why Split Now

`vault.py` is 1,442 lines serving two unrelated operational concerns:

| Concern | Responsibility | Approx. Lines | Test coverage |
|---------|---------------|---------------|--------------|
| **Vault** | Session lock/unlock, age encrypt/decrypt, key management, integrity guards | ~550 | 13 standalone tests |
| **Backup** | GFS snapshots, pruning, restore, install, validation, status reporting, registry loading | ~500 | 69 tests (12 test classes) |
| **Shared** | Constants, `log()`, `die()`, age primitives, key management | ~200 | used by both |

The backup code has its own data structures (ZIP archives, manifests), its own test matrix (69 of 82 vault tests), and a distinct operational cadence (archive management vs session lifecycle). These concerns belong in separate modules.

### 1.2 Why Three Modules, Not Two

A naive two-module split creates a circular import:
```
backup.py  ──imports──►  vault.py   (needs log, die, age_encrypt, etc.)
vault.py   ──imports──►  backup.py  (needs backup_snapshot, get_health_summary)
```

Resolving this with lazy imports works but creates **invisible coupling** — someone adding a top-level import to `vault.py` months from now could break the lazy import chain without any compile-time warning.

The clean cut: extract shared primitives into `scripts/foundation.py`:
```
vault.py      ──imports──►  foundation.py   (session lifecycle)
backup.py     ──imports──►  foundation.py   (archive management)
vault.py      ──imports──►  backup.py       (lazy, 2 thin call sites)
```

95% of the coupling becomes a one-way dependency through `foundation.py`. The remaining vault→backup link (2 lazy imports in function bodies) is genuinely thin and easily understood.

### 1.3 Why `foundation.py`, Not `crypto.py`

The shared module contains 18 exports:

| Category | Count | Examples |
|----------|-------|---------|
| Directory/path constants | 6 | `ARTHA_DIR`, `STATE_DIR`, `CONFIG_DIR`, `AUDIT_LOG`, `LOCK_FILE`, `SENSITIVE_FILES` |
| Credential constants | 2 | `KC_SERVICE`, `KC_ACCOUNT` |
| Timing constants | 2 | `STALE_THRESHOLD`, `LOCK_TTL` |
| Logging/exit utilities | 2 | `log()`, `die()` |
| Configuration accessor | 1 | `_config` dict |
| Cryptographic operations | 5 | `get_private_key()`, `get_public_key()`, `check_age_installed()`, `age_decrypt()`, `age_encrypt()` |

Only 5 of 18 exports are cryptographic. Naming this `crypto.py` would mislead anyone reading the codebase — they'd expect pure encryption logic, not a 70% non-crypto utility module. `foundation.py` honestly describes what it is: the shared foundation for all Artha scripts.

### 1.4 Distribution Imperative

The overarching goal is a standardized distribution package. A user setting up Artha on a new machine should be able to:

```bash
python scripts/backup.py install ~/Downloads/2026-03-14.zip
```

This requires `backup.py` to be a **standalone CLI** — not an optional add-on, but the primary interface for archive operations. `vault.py` continues dispatching these commands for backward compatibility, but `backup.py` is the canonical entry point.

A standalone CLI that can only restore but not create backups is incomplete. `backup.py` must also offer `snapshot` (create) and key management (`export-key`, `import-key`) to be truly self-sufficient.

---

## 2. Current State Analysis

### 2.1 Functions by Destination

**→ `scripts/foundation.py`** (shared foundation):

| Function | Purpose |
|----------|---------|
| Constants block | `ARTHA_DIR`, `STATE_DIR`, `CONFIG_DIR`, `AUDIT_LOG`, `LOCK_FILE`, `SENSITIVE_FILES`, `KC_SERVICE`, `KC_ACCOUNT`, `STALE_THRESHOLD`, `LOCK_TTL` |
| `_config` dict | Shared mutable config dict — all constants accessible as `_config["ARTHA_DIR"]` etc. for test patching |
| `log(msg)` | Audit log + stdout |
| `die(msg)` | Error + exit |
| `get_private_key()` | Keychain retrieval |
| `get_public_key()` | Profile/settings key read |
| `check_age_installed()` | `age` on PATH check |
| `age_decrypt(…)` | age CLI decrypt wrapper |
| `age_encrypt(…)` | age CLI encrypt wrapper |

**→ `scripts/backup.py`** (archive engine):

| Function | foundation.py imports it uses |
|----------|-------------------------------|
| `_load_backup_registry()` | `CONFIG_DIR`, `STATE_DIR`, `ARTHA_DIR`, `SENSITIVE_FILES` |
| `_file_sha256(path)` | — (pure `hashlib`) |
| `_load_manifest()` | — |
| `_save_manifest(manifest)` | `log()` |
| `_get_backup_tier(d)` | — |
| `_prune_backups(tier, keep_n)` | `log()` |
| `_zip_archive_path(entry)` | — |
| `backup_snapshot(registry, today)` | `get_public_key()`, `age_encrypt()`, `log()` |
| `_select_backup_zip(…)` | — |
| `_restore_from_zip(…)` | `check_age_installed()`, `get_private_key()`, `die()`, `age_decrypt()`, `log()` |
| `do_validate_backup(…)` | `check_age_installed()`, `get_private_key()`, `die()`, `age_decrypt()`, `log()` |
| `do_backup_status()` | — |
| `do_restore(…)` | `check_age_installed()`, `die()` |
| `do_install(…)` | — |
| `get_health_summary()` | — |

Constants defined in backup.py: `BACKUP_DIR`, `BACKUP_MANIFEST`, `GFS_RETENTION`

**→ stays in `scripts/vault.py`** (session lifecycle):

| Function | Purpose |
|----------|---------|
| `_read_lock_data()` | Lock file JSON |
| `_pid_running(pid)` | PID liveness check |
| `check_lock_state()` | Stale/active lock detection |
| `do_release_lock()` | Manual lock clear |
| `_restore_bak()` | Session-level .bak restore |
| `is_integrity_safe()` | Net-negative write guard |
| `do_decrypt()` | Decrypt all sensitive files |
| `do_encrypt()` | Encrypt + trigger backup |
| `do_status()` | Show encryption state |
| `do_health()` | Vault health check |
| `main()` | CLI dispatcher |

### 2.2 `_load_backup_registry()` — Moved to `backup.py`

**v2 decision:** Keep in vault.py as "configuration loading, not backup logic."

**v3 decision:** Move to backup.py. Rationale:

1. It reads the `backup:` section of `user_profile.yaml` — that's backup domain configuration.
2. Its only consumer is `backup_snapshot()`. After extraction, vault.py's single usage would be `registry = _load_backup_registry()` right before calling `backup_snapshot(registry)` — a function defined in a different module calling a function also defined in that same module, roundtripped through vault.py for no reason.
3. Moving it to backup.py enables the `backup.py snapshot` CLI subcommand without creating a backup.py→vault.py dependency.
4. `TestBackupRegistry` (6 tests) moves to `test_backup.py` with the function it tests.

The `backup_snapshot()` public API keeps its `registry` parameter for testability — callers can pass a custom registry. But `backup.py` also exposes `load_backup_registry()` (renamed from `_load_backup_registry()`, now public) for internal use and the snapshot CLI:

```python
# backup.py — used by snapshot CLI and vault.py's do_encrypt()
def load_backup_registry() -> list[dict]:
    """Read config/user_profile.yaml → backup section. Falls back to SENSITIVE_FILES."""

def backup_snapshot(registry: list[dict], today: "_date_type | None" = None) -> int:
    """Create one ZIP snapshot from the given registry. Returns file count."""
```

```python
# vault.py do_encrypt() — lazy import, 2 names
from scripts.backup import backup_snapshot, load_backup_registry
registry = load_backup_registry()
count = backup_snapshot(registry)
```

### 2.3 `SENSITIVE_FILES` / `skills_cache` Inconsistency

**Current state:**
- `vault.py` `SENSITIVE_FILES` has **10 entries** including `skills_cache`
- `user_profile.yaml` backup registry has **9 sensitive** entries — no `skills_cache`
- `state/skills_cache.md.age` does **not exist** on disk

**Resolution:** Remove `skills_cache` from `SENSITIVE_FILES` in this refactor. It was added speculatively for the skills caching feature but the feature doesn't yet produce encrypted state. If/when it does, add both the `SENSITIVE_FILES` entry and the backup registry entry together. This keeps the two lists consistent.

**Impact:** `do_decrypt()` and `do_encrypt()` iterate over `SENSITIVE_FILES`. Removing `skills_cache` has no behavioral effect (the file doesn't exist, so both loops skip it with "no .age file" / "no .md file"). Test count unchanged.

### 2.4 Coupling Points

| # | Coupling | Direction | Resolution |
|---|----------|-----------|------------|
| C1 | `do_encrypt()` calls backup functions | vault → backup | Lazy import: `from scripts.backup import backup_snapshot, load_backup_registry` |
| C2 | `do_health()` reads backup manifest | vault → backup | Lazy import: `from scripts.backup import get_health_summary` |
| C3 | Backup functions use foundation primitives | backup → foundation | Top-level: `from scripts.foundation import log, die, get_public_key, …` |
| C4 | Vault functions use foundation primitives | vault → foundation | Top-level: `from scripts.foundation import …` |
| C5 | Shared constants | all three | Defined in `foundation.py _config` dict, re-exported as module-level names |
| C6 | `main()` dispatches backup commands | vault → backup | Lazy imports in `main()` dispatch branches |
| C7 | Test fixtures patch constants | tests | Patch `foundation._config` dict — propagates to all modules reading from it |
| C8 | `mcp_server.py` imports `vault` at runtime | external → vault | No change — vault re-exports foundation primitives. See §3.6. |

---

## 3. Target Architecture

### 3.1 Module Layout

```
scripts/
  foundation.py     ← constants (_config dict), log/die, age primitives, key management
  vault.py          ← encrypt/decrypt/lock/unlock/status/health
  backup.py         ← GFS backup/restore/validate/install/status, registry loading, ZIP handling
```

### 3.2 Dependency Graph

```
 ┌──────────┐     ┌──────────┐
 │ vault.py │     │backup.py │
 └────┬─────┘     └────┬─────┘
      │                 │
      │   top-level     │  top-level
      │   import        │  import
      ▼                 ▼
 ┌─────────────────────────┐
 │     foundation.py       │
 │  _config, constants,    │
 │  log, die, age_*,       │
 │  get_*_key              │
 └─────────────────────────┘

 vault.py ──lazy import──► backup.py  (2 call sites only: do_encrypt, do_health)
```

No circular imports at module level. The 2 lazy imports (inside `do_encrypt()` and `do_health()`) only execute at runtime, long after all modules are loaded.

### 3.3 `foundation.py` — The `_config` Dict Pattern

The root cause of the test patching tax is Python's `from X import Y` semantics: each module gets its own name binding. Patching `foundation.ARTHA_DIR` doesn't update `backup.ARTHA_DIR`.

**Solution:** `foundation.py` stores all constants in a mutable `_config` dict. All functions access values through `_config`. Module-level constant names are provided for backward-compatible read access, but all runtime behavior goes through `_config`:

```python
# scripts/foundation.py

# Mutable config dict — THE source of truth. Test fixtures patch this.
_config: dict[str, Any] = {}

def _init_config() -> None:
    """Populate _config with defaults. Called once at module load."""
    artha_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _config.update({
        "ARTHA_DIR":   artha_dir,
        "STATE_DIR":   artha_dir / "state",
        "CONFIG_DIR":  artha_dir / "config",
        "AUDIT_LOG":   artha_dir / "state" / "audit.md",
        "LOCK_FILE":   artha_dir / ".artha-decrypted",
        "SENSITIVE_FILES": [
            "immigration", "finance", "insurance", "estate", "health",
            "audit", "vehicle", "contacts", "occasions",
        ],  # 9 entries — skills_cache removed (see §2.3)
        "KC_SERVICE":       "age-key",
        "KC_ACCOUNT":       "artha",
        "STALE_THRESHOLD":  300,
        "LOCK_TTL":         1800,
    })

_init_config()

# Module-level aliases for backward compatibility (read-only convenience)
ARTHA_DIR        = _config["ARTHA_DIR"]
STATE_DIR        = _config["STATE_DIR"]
CONFIG_DIR       = _config["CONFIG_DIR"]
AUDIT_LOG        = _config["AUDIT_LOG"]
LOCK_FILE        = _config["LOCK_FILE"]
SENSITIVE_FILES  = _config["SENSITIVE_FILES"]
KC_SERVICE       = _config["KC_SERVICE"]
KC_ACCOUNT       = _config["KC_ACCOUNT"]
STALE_THRESHOLD  = _config["STALE_THRESHOLD"]
LOCK_TTL         = _config["LOCK_TTL"]
```

All functions that previously referenced `ARTHA_DIR` now reference `_config["ARTHA_DIR"]`. For example:

```python
def log(msg: str) -> None:
    audit_log = _config["AUDIT_LOG"]
    entry = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] VAULT | {msg}"
    if audit_log.exists():
        try:
            with open(audit_log, "a") as f:
                f.write(entry + "\n")
        except OSError:
            pass
    print(entry)
```

**Test impact — radical simplification:**

```python
# BEFORE: 18+ monkeypatch.setattr calls across 3 modules
monkeypatch.setattr(crypto, "ARTHA_DIR", temp_artha_dir)
monkeypatch.setattr(vault, "ARTHA_DIR", temp_artha_dir)
monkeypatch.setattr(backup, "ARTHA_DIR", temp_artha_dir)
# ...repeat for STATE_DIR, CONFIG_DIR, AUDIT_LOG, LOCK_FILE, ...

# AFTER: 1 fixture function, all modules see the same values
@pytest.fixture
def mock_artha_env(temp_artha_dir, monkeypatch):
    """Redirect all Artha paths to a temp directory."""
    monkeypatch.setitem(foundation._config, "ARTHA_DIR", temp_artha_dir)
    monkeypatch.setitem(foundation._config, "STATE_DIR", temp_artha_dir / "state")
    monkeypatch.setitem(foundation._config, "CONFIG_DIR", temp_artha_dir / "config")
    monkeypatch.setitem(foundation._config, "AUDIT_LOG", temp_artha_dir / "state" / "audit.md")
    monkeypatch.setitem(foundation._config, "LOCK_FILE", temp_artha_dir / ".artha-decrypted")
    # Seed settings.md for get_public_key()
    config_dir = temp_artha_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.md").write_text('age_recipient: "age1mockpublickey"\n')
```

This **one fixture** works for vault tests, backup tests, and any future module. No more per-module patching, no more forgotten bindings, no more `_config` drift. All functions read from `_config` at call time, not at import time.

**vault.py and backup.py usage:** These modules import foundation constants but their functions access `_config` through foundation:

```python
# vault.py
from scripts.foundation import (
    _config,  # for runtime path access
    log, die, get_private_key, get_public_key,
    check_age_installed, age_decrypt, age_encrypt,
    SENSITIVE_FILES,  # read at module level is fine for iteration lists
)

# In functions:
def do_decrypt() -> None:
    lock_file = _config["LOCK_FILE"]
    state_dir = _config["STATE_DIR"]
    ...
```

### 3.4 `foundation.py` Exports

```python
# scripts/foundation.py — shared foundation
__all__ = [
    # Config dict (for test fixtures and runtime access)
    "_config",
    # Constants (module-level aliases for convenience)
    "ARTHA_DIR", "STATE_DIR", "CONFIG_DIR", "AUDIT_LOG", "LOCK_FILE",
    "SENSITIVE_FILES", "KC_SERVICE", "KC_ACCOUNT",
    "STALE_THRESHOLD", "LOCK_TTL",
    # Utilities
    "log", "die",
    # Key management
    "get_private_key", "get_public_key",
    # Encryption
    "check_age_installed", "age_decrypt", "age_encrypt",
]
```

`foundation.py` has **no imports from vault.py or backup.py** — it is a leaf module. It imports only stdlib + `keyring`.

### 3.5 `backup.py` Public API

```python
__all__ = [
    "load_backup_registry",
    "backup_snapshot",
    "get_health_summary",
    "do_backup_status",
    "do_validate_backup",
    "do_restore",
    "do_install",
]

def load_backup_registry() -> list[dict]:
    """Read config/user_profile.yaml → backup. Falls back to SENSITIVE_FILES."""

def backup_snapshot(registry: list[dict], today: "_date_type | None" = None) -> int:
    """Create one ZIP snapshot from the given registry. Returns file count."""

def get_health_summary() -> tuple[int, str | None]:
    """Returns (snapshot_count, last_validate_iso)."""

def do_backup_status() -> None
def do_validate_backup(domain=None, date_str=None) -> None
def do_restore(date_str=None, domain=None, dry_run=False, data_only=False) -> None
def do_install(zip_path_str, dry_run=False, data_only=False) -> None
```

### 3.6 Behavioral Contracts

Edge cases that must be specified and tested:

| Scenario | Behavior |
|----------|----------|
| `backup.py restore` while vault session is active (lock file present) | **Warn and proceed.** Restore writes `.age` files and decrypts plain/config. An active session means plaintext already exists — the restore will overwrite it. Print warning: "⚠ Active vault session detected. Restored files may be overwritten when the session ends." Log to audit. |
| `backup.py snapshot` called twice on same date | **Overwrite.** The ZIP for that tier/date is replaced atomically. Outer manifest entry is updated. No duplicate ZIPs. |
| ZIP contains files for a domain not in current `user_profile.yaml` | **Restore anyway.** Restore reads from the ZIP's internal manifest, not the live config. The internal manifest has `restore_path` for every file. Unknown domains are restored to their declared paths. |
| `backup.py install` given a ZIP with version != "2" | **Warn and attempt.** Print: "⚠ ZIP backup version {v} — expected 2. Attempting restore." Future-proof: don't hard-fail on version mismatch. |
| `backup.py restore --date` matches multiple ZIPs across tiers | **Use the first match**, consistent with `_select_backup_zip()` existing behavior. Document that tier precedence is undefined for same-date matches. |
| `backup_snapshot()` returns 0 (failure) | **Non-fatal for encrypt** (encryption succeeded). Persist failure to `health-check.md` for next preflight gate. Print warning. Audit log. |
| Auto-validation triggered by `backup_snapshot()` fails | **Log and continue.** The snapshot itself was created successfully. The validation failure is informational — it means a previous snapshot (not this one) may be corrupt. |

### 3.7 `mcp_server.py` Compatibility

`mcp_server.py` does `import vault` (bare) inside functions at runtime, calling `vault.get_private_key()`, `vault.get_public_key()`, `vault.age_decrypt()`, `vault.age_encrypt()`. These functions will be defined in `foundation.py` and re-exported by `vault.py` via top-level import. The bare `import vault` will continue to see these as `vault.get_private_key()` etc.

Verification: after extraction, `vault.get_private_key` is actually `foundation.get_private_key` accessed through vault's namespace. This works because Python `from X import Y` creates a name binding in the importing module.

**Guard test:** `test_vault_exports_crypto_primitives()` asserts all 4 names mcp_server.py uses are present in vault's namespace.

---

## 4. Implementation Steps

Execute in this exact order. Each step ends with a green test suite.

### Step 0: Pre-flight — verify baseline

```bash
source ~/.artha-venvs/.venv/bin/activate
python -m pytest tests/ -q          # must show: 251 passed, 2 skipped, 20 xfailed
python -m pytest tests/unit/test_vault.py -q  # must show: 82 passed
```

Record exact counts. Any failure here is a pre-existing issue — fix before proceeding.

### Step 1: Create `scripts/foundation.py` — shared foundation

1. Create `scripts/foundation.py` with the `_bootstrap` preamble (same pattern as vault.py):
   ```python
   #!/usr/bin/env python3
   """foundation.py — Artha shared foundation: constants, logging, and cryptographic primitives."""
   from __future__ import annotations
   import sys, os as _os
   _scripts_dir = _os.path.dirname(_os.path.abspath(__file__))
   if _scripts_dir not in sys.path:
       sys.path.insert(0, _scripts_dir)
   from _bootstrap import reexec_in_venv
   reexec_in_venv()
   ```
2. Implement the `_config` dict pattern (see §3.3):
   - Create `_config: dict` and `_init_config()` function
   - Populate with all constants: `ARTHA_DIR`, `STATE_DIR`, `CONFIG_DIR`, `AUDIT_LOG`, `LOCK_FILE`, `SENSITIVE_FILES` (9 entries — `skills_cache` removed per §2.3), `KC_SERVICE`, `KC_ACCOUNT`, `STALE_THRESHOLD`, `LOCK_TTL`
   - Add module-level aliases for backward compatibility
3. Move (cut-paste, not copy) from `vault.py` into `foundation.py`:
   - All stdlib imports needed by the moved functions (`os`, `re`, `shutil`, `subprocess`, `sys`, `tempfile`, `json`, `time`, `from datetime import …`, `from pathlib import Path`)
   - `import keyring`
   - `_yaml` import block
   - `log()`, `die()` — **refactored to use `_config`** for path lookups
   - `get_private_key()`, `get_public_key()` — **refactored to use `_config`**
   - `check_age_installed()`
   - `age_decrypt()`, `age_encrypt()`
   - Windows UTF-8 reconfigure block
4. Add `__all__` listing all public names (§3.4).
5. Remove `skills_cache` from `SENSITIVE_FILES` (§2.3).
6. Verify: `python -c "import ast; ast.parse(open('scripts/foundation.py').read()); print('foundation OK')"`
7. Update `vault.py`:
   - Add `from scripts.foundation import _config, log, die, get_private_key, get_public_key, check_age_installed, age_decrypt, age_encrypt, SENSITIVE_FILES, KC_SERVICE, KC_ACCOUNT` at top
   - Remove the moved code (constants block, log, die, key functions, age functions)
   - Remove `skills_cache` from `SENSITIVE_FILES` (it's now in foundation.py without it)
   - **Refactor all vault.py functions** to use `_config["LOCK_FILE"]`, `_config["STATE_DIR"]`, etc. instead of module-level constants for paths
   - Keep module-level `from scripts.foundation import ARTHA_DIR, STATE_DIR, ...` for any read-only usage at module level
8. Run full test suite — **all 251 tests must pass**.
   - Tests import `scripts.vault as vault` and use `vault.log`, `vault.ARTHA_DIR` etc.
   - These still resolve because vault re-exports from foundation.

**Risk checkpoint:** If any test fails, check that:
- The `from scripts.foundation import …` in vault.py includes all needed names
- Functions that access paths use `_config["..."]` not the module-level alias
- The `mock_vault_env` fixture still patches the right attributes (it should still work since vault.py still has the name bindings; `_config` patching comes in Step 3)

**After Step 1 passes, this is a safe commit point.** Foundation extraction is independently reversible.

### Step 2: Create `scripts/backup.py` — archive engine

1. Create `scripts/backup.py` with:
   ```python
   #!/usr/bin/env python3
   """backup.py — Artha GFS backup/restore manager."""
   from __future__ import annotations
   import sys, os as _os
   _scripts_dir = _os.path.dirname(_os.path.abspath(__file__))
   if _scripts_dir not in sys.path:
       sys.path.insert(0, _scripts_dir)
   from _bootstrap import reexec_in_venv
   reexec_in_venv()

   import argparse
   import hashlib
   import json
   import os
   import shutil
   import subprocess
   import zipfile
   from datetime import date as _date_type, datetime, timedelta, timezone
   from pathlib import Path
   from typing import Any

   from scripts.foundation import (
       _config,
       SENSITIVE_FILES,
       log, die,
       get_public_key, get_private_key,
       check_age_installed, age_encrypt, age_decrypt,
       KC_SERVICE, KC_ACCOUNT,
   )
   ```
2. Define backup-specific config entries added to `_config` at module init:
   ```python
   # Backup-specific constants (extend foundation's _config)
   if "BACKUP_DIR" not in _config:
       _config["BACKUP_DIR"] = _config["ARTHA_DIR"] / "backups"
       _config["BACKUP_MANIFEST"] = _config["BACKUP_DIR"] / "manifest.json"
   BACKUP_DIR      = _config["BACKUP_DIR"]
   BACKUP_MANIFEST = _config["BACKUP_MANIFEST"]
   GFS_RETENTION: dict[str, int | None] = {"daily": 7, "weekly": 4, "monthly": 12, "yearly": None}
   ```
   **IMPORTANT — frozen alias rule:** `BACKUP_DIR` and `BACKUP_MANIFEST` above are frozen at import time.
   All backup.py **functions** must use `_config["BACKUP_DIR"]` and `_config["BACKUP_MANIFEST"]`, never the
   module-level aliases. The aliases exist ONLY for `__all__` export and external callers doing
   `from scripts.backup import BACKUP_DIR`. Any function body that references the module-level name
   will silently use stale values when tests patch `_config`.
   ```
   ```
3. Move (cut-paste, not copy) from `vault.py` into `backup.py`:
   - `_load_backup_registry()` — rename to `load_backup_registry()` (public). Refactor to use `_config` for paths.
   - `_file_sha256()`, `_load_manifest()`, `_save_manifest()`, `_get_backup_tier()`,
     `_prune_backups()`, `_zip_archive_path()`, `_backup_snapshot()`,
     `_select_backup_zip()`, `_restore_from_zip()`,
     `do_validate_backup()`, `do_backup_status()`, `do_restore()`, `do_install()`
4. Rename `_backup_snapshot` → `backup_snapshot` (public cross-module API).
5. Change `backup_snapshot` signature to accept `registry` parameter:
   ```python
   def backup_snapshot(registry: list[dict], today: "_date_type | None" = None) -> int:
   ```
   Remove the internal `registry = _load_backup_registry()` call. The caller provides it.
6. Add automatic weekly validation trigger at end of `backup_snapshot()`:
   ```python
   # Auto-validate if overdue (>7 days since last validation)
   manifest = _load_manifest()
   last_validate = manifest.get("last_validate")
   if last_validate and backed_up > 0:
       try:
           last_dt = datetime.fromisoformat(last_validate.replace("Z", "+00:00"))
           days_since = (datetime.now(timezone.utc) - last_dt).days
           if days_since > 7:
               log(f"AUTO_VALIDATE | reason: overdue | days_since: {days_since}")
               try:
                   do_validate_backup()
               except SystemExit:
                   log("AUTO_VALIDATE_FAILED | non_fatal | snapshot_was_created_successfully")
       except ValueError:
           pass
   elif not last_validate and backed_up > 0:
       # Never validated — trigger first validation
       log("AUTO_VALIDATE | reason: never_validated")
       try:
           do_validate_backup()
       except SystemExit:
           log("AUTO_VALIDATE_FAILED | non_fatal | snapshot_was_created_successfully")
   ```
7. Add `get_health_summary()`:
   ```python
   def get_health_summary() -> tuple[int, str | None]:
       """Returns (snapshot_count, last_validate_iso) for vault health check."""
       manifest = _load_manifest()
       return len(manifest.get("snapshots", {})), manifest.get("last_validate")
   ```
8. Add `__all__` with the public API (§3.5).
9. Add key management commands:
   ```python
   def do_export_key() -> None:
       """Print the age private key to stdout with security warnings."""
       import keyring
       key = keyring.get_password(KC_SERVICE, KC_ACCOUNT)
       if not key:
           die("No private key found in credential store.")
       print("=" * 60)
       print("⚠  AGE PRIVATE KEY — HANDLE WITH EXTREME CARE")
       print("=" * 60)
       print()
       print("Store this key securely (password manager, printed safe copy).")
       print("Anyone with this key can decrypt ALL your Artha state files.")
       print("Do NOT commit this to git, email it, or store it in cloud notes.")
       print()
       print(key)
       print()
       print("=" * 60)
       log("KEY_EXPORTED | action: private_key_displayed_to_stdout")

   def do_import_key() -> None:
       """Read an age private key from stdin and store in credential store."""
       import keyring
       print("Paste your age private key (starts with AGE-SECRET-KEY-1...):")
       print("Press Enter, then Ctrl-D (macOS/Linux) or Ctrl-Z (Windows) when done.")
       key = sys.stdin.read().strip()
       if not key.startswith("AGE-SECRET-KEY-"):
           die("Invalid key format. Age private keys start with 'AGE-SECRET-KEY-'.")
       keyring.set_password(KC_SERVICE, KC_ACCOUNT, key)
       print(f"✓ Private key stored in credential store (service={KC_SERVICE}, account={KC_ACCOUNT}).")
       log("KEY_IMPORTED | action: private_key_stored_in_credential_store")
   ```
10. Add `main()` with `argparse` (see §4.7 below).
11. **Do not remove functions from vault.py yet** — that happens in Step 4.
12. Verify: `python -c "import ast; ast.parse(open('scripts/backup.py').read()); print('backup OK')"`

### Step 3: Create `tests/unit/test_backup.py`

1. Create `tests/unit/test_backup.py`.
2. Add imports:
   ```python
   import hashlib
   import json
   import pytest
   import os
   import shutil
   import subprocess
   import sys
   import zipfile
   from datetime import date
   from pathlib import Path
   from unittest.mock import MagicMock, patch

   import scripts.backup as backup
   import scripts.foundation as foundation
   import scripts.vault as vault
   ```
3. Copy (not move yet) the `_create_test_zip()` helper from `test_vault.py`.
4. Create `mock_backup_env` fixture using the `_config` dict pattern:
   ```python
   @pytest.fixture
   def mock_backup_env(temp_artha_dir, monkeypatch):
       """Redirect all Artha paths to temp directory via foundation._config."""
       state_dir  = temp_artha_dir / "state"
       config_dir = temp_artha_dir / "config"
       backup_dir = temp_artha_dir / "backups"

       # Patch the single source of truth
       monkeypatch.setitem(foundation._config, "ARTHA_DIR",  temp_artha_dir)
       monkeypatch.setitem(foundation._config, "STATE_DIR",  state_dir)
       monkeypatch.setitem(foundation._config, "CONFIG_DIR", config_dir)
       monkeypatch.setitem(foundation._config, "AUDIT_LOG",  state_dir / "audit.md")
       monkeypatch.setitem(foundation._config, "LOCK_FILE",  temp_artha_dir / ".artha-decrypted")

       # Patch backup-specific config
       monkeypatch.setitem(foundation._config, "BACKUP_DIR",      backup_dir)
       monkeypatch.setitem(foundation._config, "BACKUP_MANIFEST", backup_dir / "manifest.json")

       # Seed config/settings.md for get_public_key()
       config_dir.mkdir(parents=True, exist_ok=True)
       (config_dir / "settings.md").write_text('age_recipient: "age1mockpublickey"\n')

       return temp_artha_dir
   ```
5. Add `_make_test_registry()` helper:
   ```python
   def _make_test_registry(state_dir: Path, config_dir: Path = None) -> list[dict]:
       """Build a minimal backup registry for tests."""
       entries = [
           {"name": "finance", "source_type": "state_encrypted",
            "source_path": state_dir / "finance.md.age",
            "restore_path": "state/finance.md.age"},
           {"name": "goals", "source_type": "state_plain",
            "source_path": state_dir / "goals.md",
            "restore_path": "state/goals.md"},
       ]
       if config_dir:
           entries.append({
               "name": "cfg__config__user_profile_yaml",
               "source_type": "config",
               "source_path": config_dir / "user_profile.yaml",
               "restore_path": "config/user_profile.yaml",
           })
       return entries
   ```
6. Copy (not move yet) **11** backup test classes from `test_vault.py`:
   - `TestGFSTierLogic` (11), `TestManifest` (4), `TestBackupSnapshot` (9),
     `TestPruning` (4), `TestValidateBackup` (9),
     `TestBackupStatus` (3), `TestBackupRegistry` (6),
     `TestBackupSnapshotComprehensive` (5), `TestRestore` (6),
     `TestInstall` (5), `TestDataOnlyRestore` (5)
   - **`TestEncryptTriggersBackup` stays in `test_vault.py`** — it tests `vault.do_encrypt()`, not backup functions. Update its mock targets per R3.
7. Update all test classes:
   - Replace `vault._backup_snapshot(…)` → `backup.backup_snapshot(registry, …)` with registry from `_make_test_registry()` or from `load_backup_registry()`
   - Replace `vault._load_manifest` → `backup._load_manifest`
   - Replace `vault._save_manifest` → `backup._save_manifest`
   - Replace `vault._get_backup_tier` → `backup._get_backup_tier`
   - Replace `vault._prune_backups` → `backup._prune_backups`
   - Replace `vault._file_sha256` → `backup._file_sha256`
   - Replace `vault._zip_archive_path` → `backup._zip_archive_path`
   - Replace `vault._select_backup_zip` → `backup._select_backup_zip`
   - Replace `vault._restore_from_zip` → `backup._restore_from_zip`
   - Replace `vault._load_backup_registry` → `backup.load_backup_registry`
   - Replace `vault.do_validate_backup` → `backup.do_validate_backup`
   - Replace `vault.do_backup_status` → `backup.do_backup_status`
   - Replace `vault.do_restore` → `backup.do_restore`
   - Replace `vault.do_install` → `backup.do_install`
   - Replace `vault.BACKUP_DIR` → `backup.BACKUP_DIR` (or `_config["BACKUP_DIR"]`)
   - Replace `vault.BACKUP_MANIFEST` → `backup.BACKUP_MANIFEST` (or `_config["BACKUP_MANIFEST"]`)
   - Replace `vault.GFS_RETENTION` → `backup.GFS_RETENTION`
   - Replace `mock_vault_env` → `mock_backup_env` in all test function signatures
   - Update `backup_snapshot()` calls to pass `registry` parameter
   - Update mock targets: `patch("scripts.vault._backup_snapshot")` → `patch("scripts.backup.backup_snapshot")`
8. `TestBackupRegistry` moves here (tests `load_backup_registry()` which now lives in backup.py).
   - Rename to `TestBackupRegistry` (name stays — it's testing backup registry loading).
   - Update `vault._load_backup_registry` → `backup.load_backup_registry`
9. Add structural guard tests:
   ```python
   def test_no_circular_import():
       """All three modules load in any order without ImportError."""
       import importlib
       importlib.reload(importlib.import_module("scripts.foundation"))
       importlib.reload(importlib.import_module("scripts.vault"))
       importlib.reload(importlib.import_module("scripts.backup"))

   def test_config_propagates_to_all_modules(mock_backup_env):
       """Fixture patches _config once, all modules see the same values."""
       assert foundation._config["ARTHA_DIR"] == backup._config["ARTHA_DIR"]
       # backup and vault share foundation._config — same object
       assert foundation._config is backup._config  # they import the same dict

   def test_vault_has_no_backup_functions():
       """Verify backup functions were fully extracted from vault."""
       assert not hasattr(vault, '_backup_snapshot')
       assert not hasattr(vault, '_load_manifest')
       assert not hasattr(vault, '_prune_backups')
       assert not hasattr(vault, '_load_backup_registry')
   ```
10. Add standalone CLI tests:
    ```python
    class TestBackupCLI:
        def test_status_subcommand(self, mock_backup_env, capsys):
            """backup.py status runs without error."""
            backup.do_backup_status()
            out = capsys.readouterr().out
            assert "VAULT BACKUP STATUS" in out

        def test_help_output(self):
            """backup.py --help produces usage text."""
            result = subprocess.run(
                [sys.executable, "scripts/backup.py", "--help"],
                capture_output=True, text=True,
                cwd=str(foundation._config["ARTHA_DIR"]),
                env={**os.environ, "ARTHA_NO_REEXEC": "1"},
            )
            assert "status" in result.stdout
            assert "restore" in result.stdout
            assert "snapshot" in result.stdout
            assert "export-key" in result.stdout

        def test_snapshot_subcommand(self, mock_backup_env):
            """backup.py snapshot creates a ZIP."""
            # Seed a minimal .age file
            state_dir = foundation._config["STATE_DIR"]
            (state_dir / "finance.md.age").write_bytes(b"encrypted-content")
            # Write a profile with just the one file
            import yaml
            profile = {"backup": {"state_files": [{"name": "finance", "sensitive": True}]}}
            (foundation._config["CONFIG_DIR"] / "user_profile.yaml").write_text(yaml.dump(profile))
            # Run snapshot via main(argv=...) — NOT sys.argv
            backup.main(["snapshot"])
            # Verify a ZIP was created
            backup_dir = foundation._config["BACKUP_DIR"]
            zips = list(backup_dir.rglob("*.zip"))
            assert len(zips) >= 1
    ```
11. Add key management tests:
    ```python
    class TestKeyManagement:
        def test_export_key_prints_key(self, capsys):
            with patch("keyring.get_password", return_value="AGE-SECRET-KEY-1FAKE"):
                backup.do_export_key()
            out = capsys.readouterr().out
            assert "AGE-SECRET-KEY-1FAKE" in out
            assert "EXTREME CARE" in out

        def test_export_key_fails_when_no_key(self):
            with patch("keyring.get_password", return_value=None):
                with pytest.raises(SystemExit):
                    backup.do_export_key()

        def test_import_key_stores_valid_key(self, monkeypatch):
            monkeypatch.setattr("sys.stdin", __import__("io").StringIO("AGE-SECRET-KEY-1FAKE\n"))
            with patch("keyring.set_password") as mock_set:
                backup.do_import_key()
            mock_set.assert_called_once_with("age-key", "artha", "AGE-SECRET-KEY-1FAKE")

        def test_import_key_rejects_invalid_format(self, monkeypatch):
            monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not-a-valid-key\n"))
            with pytest.raises(SystemExit):
                backup.do_import_key()
    ```
12. Run `test_backup.py` — **all backup tests must pass** (both backup.py and vault.py have the functions at this point — both valid).

### Step 4: Remove backup code from vault.py and test_vault.py

1. Remove from `vault.py`:
   - All backup functions moved to backup.py (14 functions — see §2.1)
   - `_load_backup_registry()` (now `load_backup_registry()` in backup.py)
   - `BACKUP_DIR`, `BACKUP_MANIFEST`, `GFS_RETENTION` constants
   - Backup-only imports no longer needed (`zipfile`, `hashlib` — check whether anything else uses them; if not, remove)
2. Update `do_encrypt()` — replace the `_backup_snapshot()` call:
   ```python
   # GFS backup snapshot (§8.5.2)
   from scripts.backup import backup_snapshot, load_backup_registry
   registry = load_backup_registry()
   count = backup_snapshot(registry)
   if count == 0:
       print("  ⚠ GFS backup FAILED — no files archived.")
       print("    Encryption was successful, but no backup was created.")
       print("    Fix: python scripts/backup.py status")
       log("BACKUP_FAILED | post_encrypt | file_count: 0")
       # Persist failure for next preflight gate
       _mark_backup_failure()
   ```
3. Add `_mark_backup_failure()` helper to vault.py:
   ```python
   def _mark_backup_failure() -> None:
       """Record backup failure in health-check.md for next preflight gate."""
       hc = _config["STATE_DIR"] / "health-check.md"
       if hc.exists():
           try:
               text = hc.read_text(encoding="utf-8")
               marker = "backup_last_failure:"
               ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
               if marker in text:
                   import re
                   text = re.sub(rf"{marker}.*", f"{marker} {ts}", text)
               else:
                   text = text.rstrip() + f"\n{marker} {ts}\n"
               hc.write_text(text, encoding="utf-8")
           except OSError:
               pass
   ```
4. Update `do_health()` — replace the manifest-reading GFS section:
   ```python
   # 7. GFS backup catalog
   from scripts.backup import get_health_summary
   snapshot_count, last_validate = get_health_summary()
   if snapshot_count == 0:
       print("  GFS backups:  ⚠ none — run vault.py encrypt to create first backup")
   else:
       if last_validate:
           try:
               last_dt    = datetime.fromisoformat(last_validate.replace("Z", "+00:00"))
               days_since = (datetime.now(timezone.utc) - last_dt).days
               if days_since > 35:
                   print(f"  GFS backups:  ⚠ {snapshot_count} snapshot(s) but validation overdue ({days_since}d)")
                   ok = False
               else:
                   print(f"  GFS backups:  ✓ {snapshot_count} snapshot(s), validated {days_since}d ago")
           except ValueError:
               print(f"  GFS backups:  ✓ {snapshot_count} snapshot(s) (validation: {last_validate})")
       else:
           print(f"  GFS backups:  ⚠ {snapshot_count} snapshot(s) but never validated — run vault.py validate-backup")
           ok = False
   ```
5. Update `main()` — dispatch backup commands via lazy imports:
   ```python
   elif cmd in ("backup-status", "backup_status"):
       from scripts.backup import do_backup_status
       do_backup_status()
   elif cmd in ("validate-backup", "validate_backup"):
       from scripts.backup import do_validate_backup
       # parse --domain, --date args (existing code)
       do_validate_backup(domain=domain, date_str=date_str)
   elif cmd == "restore":
       from scripts.backup import do_restore
       # parse --date, --domain, --dry-run, --data-only args (existing code)
       do_restore(date_str=date_str, domain=domain, dry_run=dry_run, data_only=data_only)
   elif cmd == "install":
       from scripts.backup import do_install
       # parse zip_arg, --dry-run, --data-only args (existing code)
       do_install(zip_arg, dry_run=dry_run, data_only=data_only)
   ```
6. Remove from `test_vault.py`:
   - All 11 backup test classes (now in `test_backup.py`). **Keep `TestEncryptTriggersBackup`** — it tests `vault.do_encrypt()`. Update its mock targets per R3.
   - The `_create_test_zip()` helper
   - Backup-specific imports (`zipfile`, `hashlib`) if unused by remaining tests
7. Update `mock_vault_env` fixture to use `_config` pattern:
   ```python
   @pytest.fixture
   def mock_vault_env(temp_artha_dir, monkeypatch):
       """Redirect all Artha paths to temp directory via foundation._config."""
       monkeypatch.setitem(foundation._config, "ARTHA_DIR",  temp_artha_dir)
       monkeypatch.setitem(foundation._config, "STATE_DIR",  temp_artha_dir / "state")
       monkeypatch.setitem(foundation._config, "CONFIG_DIR", temp_artha_dir / "config")
       monkeypatch.setitem(foundation._config, "AUDIT_LOG",  temp_artha_dir / "state" / "audit.md")
       monkeypatch.setitem(foundation._config, "LOCK_FILE",  temp_artha_dir / ".artha-decrypted")
       # Seed settings.md
       config_dir = temp_artha_dir / "config"
       config_dir.mkdir(parents=True, exist_ok=True)
       (config_dir / "settings.md").write_text('age_recipient: "age1mockpublickey"\n')
       return temp_artha_dir
   ```
8. Add sanity checks to `test_vault.py`:
   ```python
   def test_vault_exports_crypto_primitives():
       """vault re-exports foundation primitives for backward compat (mcp_server.py)."""
       assert callable(vault.log)
       assert callable(vault.die)
       assert callable(vault.get_private_key)
       assert callable(vault.get_public_key)
       assert callable(vault.age_encrypt)
       assert callable(vault.age_decrypt)

   def test_vault_has_no_backup_functions():
       """Confirm backup functions were fully extracted."""
       assert not hasattr(vault, '_backup_snapshot')
       assert not hasattr(vault, '_load_manifest')
       assert not hasattr(vault, '_prune_backups')
       assert not hasattr(vault, '_load_backup_registry')
       assert not hasattr(vault, 'BACKUP_DIR')
   ```
9. Run full test suite — **all tests must pass**.

### Step 5: Update vault.py usage and help text

1. Update `vault.py main()` usage string:
   ```
   Backup commands (also available via: python scripts/backup.py):
     backup-status    — show GFS backup catalog
     validate-backup  — validate backup integrity
     restore          — restore from GFS catalog
     install          — restore from explicit ZIP file

   Key management (also available via: python scripts/backup.py):
     export-key / import-key — see backup.py --help
   ```

### Step 6: Update docs and specs

1. **`README.md`**:
   - Add `backup.py` standalone CLI section with all subcommands including `snapshot`, `export-key`, `import-key`
   - Document `--data-only` flag for both `restore` and `install`
   - Add cold-start restore workflow (§4.9 below) with key management section
   - Update module architecture description
2. **`specs/artha-tech-spec.md`**:
   - Bump to v2.6
   - Update §8.5.2 to reference three-module architecture (`foundation.py`, `vault.py`, `backup.py`)
   - Document `backup_snapshot(registry)` parameter change
   - Document `_config` dict pattern
   - Document auto-validation trigger
3. **`specs/artha-prd.md`**:
   - Update F15.58 to note module separation
   - Add note about `scripts/foundation.py` as shared foundation
   - Add F15.59: Key export/import for cold-start disaster recovery

---

### 4.7 `backup.py` Standalone CLI Design

Use `argparse` with subcommands:

```python
def main(argv: "list[str] | None" = None) -> None:
    """CLI entry point. Pass argv for programmatic/test invocation; None reads sys.argv."""
    parser = argparse.ArgumentParser(
        prog="backup.py",
        description="Artha GFS backup manager — archive, restore, validate, and manage state snapshots.",
    )
    sub = parser.add_subparsers(dest="command")

    # ── Archive operations ──
    snap = sub.add_parser("snapshot", help="Create a GFS backup snapshot now")
    snap.add_argument("--tier", choices=["daily", "weekly", "monthly", "yearly"],
                      help="Force a specific tier (default: auto from today's date)")

    sub.add_parser("status", help="Show backup catalog and validation status")

    val = sub.add_parser("validate", help="Validate backup integrity")
    val.add_argument("--domain", help="Validate one domain only")
    val.add_argument("--date", help="Validate a specific date (YYYY-MM-DD)")

    # ── Restore operations ──
    rst = sub.add_parser("restore", help="Restore from GFS backup catalog")
    rst.add_argument("--domain", help="Restore one domain only")
    rst.add_argument("--date", help="Restore a specific date (YYYY-MM-DD)")
    rst.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    rst.add_argument("--data-only", action="store_true", help="Restore state files only (skip config)")

    inst = sub.add_parser("install", help="Restore from an explicit backup ZIP file")
    inst.add_argument("zipfile", help="Path to the backup ZIP file")
    inst.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    inst.add_argument("--data-only", action="store_true", help="Restore state files only (skip config)")

    # ── Key management ──
    sub.add_parser("export-key", help="Display the age private key (for secure backup)")
    sub.add_parser("import-key", help="Store an age private key in the credential store")

    # ── Diagnostics ──
    sub.add_parser("preflight", help="Check prerequisites (delegates to vault.py health)")

    args = parser.parse_args(argv)

    if args.command == "snapshot":
        registry = load_backup_registry()
        count = backup_snapshot(registry)
        if count == 0:
            die("Snapshot failed — no files archived.")
    elif args.command == "status":
        do_backup_status()
    elif args.command == "validate":
        do_validate_backup(domain=args.domain, date_str=args.date)
    elif args.command == "restore":
        do_restore(date_str=args.date, domain=args.domain,
                   dry_run=args.dry_run, data_only=args.data_only)
    elif args.command == "install":
        do_install(args.zipfile, dry_run=args.dry_run, data_only=args.data_only)
    elif args.command == "export-key":
        do_export_key()
    elif args.command == "import-key":
        do_import_key()
    elif args.command == "preflight":
        _do_preflight()
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
```

### 4.8 Cold-Start Preflight

`backup.py preflight` delegates to `vault.py health` to avoid duplicating check logic:

```python
def _do_preflight() -> None:
    """Check prerequisites for backup/restore by delegating to vault.py health."""
    artha_dir = _config["ARTHA_DIR"]
    result = subprocess.run(
        [sys.executable, str(artha_dir / "scripts" / "vault.py"), "health"],
        capture_output=True, text=True, cwd=str(artha_dir),
        env={**os.environ, "ARTHA_NO_REEXEC": "1"},
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    sys.exit(result.returncode)
```

This keeps check logic in exactly one place (`vault.py health`, which `preflight.py` also calls). No drift risk.

**Testing note:** `_do_preflight()` runs a subprocess against the real `ARTHA_DIR`, ignoring any `_config` patching. Functional testing of `preflight` is out of scope — it is covered only by the `test_help_output` check that the subcommand appears in `--help`. The actual health check has its own test coverage via `test_vault.py`.

### 4.9 Cold-Start Restore Workflow (for README)

```markdown
### Cold-Start Restore (new machine)

1. Clone or copy the Artha directory
2. Install age: `brew install age` (macOS) or `winget install FiloSottile.age` (Windows)
3. Create the venv: `python scripts/preflight.py` (auto-creates venv on first run)
4. Import your private key:
   ```bash
   python scripts/backup.py import-key
   # Paste your AGE-SECRET-KEY-... and press Ctrl-D
   ```
   (If you don't have your key backed up, see "Key Backup" below.)
5. Check readiness: `python scripts/backup.py preflight`
6. Restore: `python scripts/backup.py install ~/Downloads/2026-03-14.zip`

### Key Backup (do this NOW, before you need it)

Your age private key is the single point of failure. Without it, every backup
ZIP is unrecoverable. Export and store it securely:

```bash
python scripts/backup.py export-key
```

Store the output in:
- Your password manager (1Password, Bitwarden, etc.)
- A printed copy in a fire safe
- NOT in email, cloud notes, or any synced file
```

---

## 5. Risks and Mitigations

### R1: Three-Module Refactor Scope

**Risk:** Extracting `foundation.py` and the `_config` dict is additional work beyond a simple backup extraction.
**Likelihood:** Low — the functions being moved are pure utilities with no internal state. The `_config` dict is ~20 lines of additional code.
**Mitigation:**
- Step 1 (create `foundation.py`) is tested immediately — full test suite must pass before proceeding.
- `foundation.py` has no business logic — it's a config dict + constants + 7 small functions.
- If Step 1 fails tests, the fix is always "add missing name to the import list" or "use `_config["X"]` instead of `X`."
- Step 1 gets its own commit — safe rollback if anything goes wrong.

### R2: `_config` Dict — Runtime vs Import-Time Resolution

**Risk:** Module-level aliases (`ARTHA_DIR = _config["ARTHA_DIR"]`) are set once at import time. If code uses the alias instead of `_config["ARTHA_DIR"]`, test patching via `_config` won't propagate.
**Likelihood:** Certain if not enforced during refactoring.
**Mitigation:**
- **Rule:** All functions must access paths via `_config["X"]`, never via the module-level alias.
- Module-level aliases exist only for backward compatibility with external code that does `from scripts.foundation import ARTHA_DIR` for non-test usage (e.g., constants in comments, type annotations).
- Add a code review checklist item: "No function body references a module-level path constant directly. Use `_config[...]`."
- Structural guard test: `test_config_propagates_to_all_modules` verifies that patching `_config` is sufficient.

### R3: `TestEncryptTriggersBackup` Mock Target Changes

**Risk:** Multiple mock targets change after extraction. Currently patches `scripts.vault._backup_snapshot`, `scripts.vault.age_encrypt`, `scripts.vault.get_public_key`, `scripts.vault.check_age_installed`. After extraction, these live in different modules.
**Likelihood:** Certain — requires explicit update.
**Mitigation — full mock target mapping for `TestEncryptTriggersBackup`:**

| Current mock target | New mock target | Why |
|---|---|---|
| `scripts.vault._backup_snapshot` | `scripts.backup.backup_snapshot` | Function moved to backup.py |
| `scripts.vault.check_age_installed` | `scripts.vault.check_age_installed` | Still works — vault re-imports from foundation; patching vault's binding is fine |
| `scripts.vault.get_public_key` | `scripts.vault.get_public_key` | Same — vault re-imports; patching vault's binding works |
| `scripts.vault.age_encrypt` | `scripts.vault.age_encrypt` | Same — vault re-imports; patching vault's binding works |

**Key insight:** Because vault.py does `from scripts.foundation import age_encrypt, ...`, patching `scripts.vault.age_encrypt` patches vault's local binding, which is what `do_encrypt()` calls. This continues to work. Only `_backup_snapshot` changes because it moves entirely out of vault.py.

- Additionally mock `scripts.backup.load_backup_registry` since `do_encrypt()` now calls both `load_backup_registry()` and `backup_snapshot()`.
- `test_vault_has_no_backup_functions()` guard catches accidental re-addition.

**Note:** `TestEncryptTriggersBackup` stays in `test_vault.py` (not `test_backup.py`) because it tests `vault.do_encrypt()` behavior. See R10 for updated test counts.

### R4: `mcp_server.py` Runtime `import vault`

**Risk:** `mcp_server.py` does bare `import vault` inside functions. After extraction, `vault.get_private_key` is actually `foundation.get_private_key` accessed through vault's namespace. If the re-export is missing, mcp_server breaks at runtime.
**Likelihood:** Low — we re-export via `from scripts.foundation import get_private_key, ...` in vault.py.
**Mitigation:**
- `test_vault_exports_crypto_primitives()` asserts all 4 names: `get_private_key`, `get_public_key`, `age_decrypt`, `age_encrypt`.
- Additionally assert `callable(vault.log)` and `hasattr(vault, 'ARTHA_DIR')` for completeness.
- mcp_server.py only calls 4 functions — all re-exported.

### R5: `test_catch_up_e2e.py` and Other External Imports

**Risk:** Integration tests may import `from vault import is_integrity_safe` or `from vault import check_lock_state`. These stay in vault.py. No change needed.
**Likelihood:** None.
**Mitigation:** Verified — `is_integrity_safe` and `check_lock_state` are vault-only, not moving.

### R6: Silent Backup Failure After Encrypt

**Risk:** `do_encrypt()` doesn't check `_backup_snapshot()` return value. User sees "Encrypt complete" without knowing backup failed.
**Likelihood:** Historically rare but consequences are severe.
**Mitigation (strengthened from v2):**
- `do_encrypt()` checks return value and:
  1. Prints multi-line warning with fix action
  2. Writes audit log entry
  3. Persists failure timestamp to `health-check.md` via `_mark_backup_failure()`
- Next `preflight.py` run reads `health-check.md` and flags the failure
- No `try/except` around the lazy import — `ModuleNotFoundError` should crash hard
- Warning is non-fatal: encryption was successful, backup is supplementary

### R7: `backup_snapshot(registry)` Signature Change

**Risk:** Signature changes from `(today=None)` to `(registry, today=None)`. External callers break.
**Likelihood:** Low — `_backup_snapshot` was always private (`_` prefix). Only caller is `do_encrypt()`.
**Mitigation:**
- Function was always internal. Only call site (`do_encrypt()`) is updated in the same step.
- All test classes updated to use `_make_test_registry()` helper.
- `backup.py snapshot` CLI calls `load_backup_registry()` internally.

### R8: Accidental Public API Leak

**Risk:** Functions that were private (`_` prefix) in vault.py become importable from backup.py.
**Likelihood:** Medium.
**Mitigation:**
- Keep `_` prefix for internal functions: `_load_manifest`, `_save_manifest`, `_prune_backups`, `_get_backup_tier`, `_zip_archive_path`, `_select_backup_zip`, `_restore_from_zip`, `_file_sha256`.
- Only `load_backup_registry`, `backup_snapshot`, `get_health_summary`, `do_*` functions are public.
- Add `__all__` to both `foundation.py` and `backup.py` with explicit public API.

### R9: Git Blame History Loss

**Risk:** Creating new files loses `git blame` history for moved code.
**Likelihood:** Certain.
**Mitigation:**
- Use `git log --follow` which tracks renames.
- Note in commit messages: "Functions moved from vault.py — use `git log --follow`."
- Not a blocking concern.

### R10: Test Count Arithmetic

**Risk:** The test split must add up correctly or we've lost tests.
**Likelihood:** Medium — easy to miscount during moves.
**Mitigation:**
Verified counts from `pytest --collect-only`:

| Location | Tests | Composition |
|----------|-------|-------------|
| **test_vault.py (before)** | 82 | 13 standalone + 69 in 12 classes |
| **test_vault.py (after)** | 17 | 13 standalone + 2 from TestEncryptTriggersBackup + 2 new guard tests |
| **test_backup.py (new)** | 77+ | 67 from 11 classes + 10 new (structural + CLI + key mgmt) |
| **Total** | 94+ | Was 82, gained structural/CLI/key tests |

**Full suite before:** 251 passed, 2 skipped, 20 xfailed
**Full suite after:** 263+ passed (251 − 0 lost + 12+ new tests), 2 skipped, 20 xfailed

Run the arithmetic check: `python -m pytest tests/ -q` total should be ≥ 263.

Per-class breakdown for test_backup.py:

| Class | Methods | What it tests |
|-------|---------|--------------|
| `TestGFSTierLogic` | 11 | `_get_backup_tier()` |
| `TestManifest` | 4 | `_load_manifest()`, `_save_manifest()` |
| `TestBackupSnapshot` | 9 | `backup_snapshot()` |
| `TestPruning` | 4 | `_prune_backups()` |
| `TestValidateBackup` | 9 | `do_validate_backup()` |
| `TestBackupStatus` | 3 | `do_backup_status()` |
| `TestBackupRegistry` | 6 | `load_backup_registry()` |
| `TestBackupSnapshotComprehensive` | 5 | `backup_snapshot()` multi-source-type |
| `TestRestore` | 6 | `do_restore()` |
| `TestInstall` | 5 | `do_install()` |
| `TestDataOnlyRestore` | 5 | `--data-only` mode |
| Standalone structural guards | 3 | circular import, config propagation, function absence |
| `TestBackupCLI` | 3 | CLI subcommands |
| `TestKeyManagement` | 4 | export-key, import-key |
| **Total** | **77** | |

`TestEncryptTriggersBackup` (2 tests) stays in `test_vault.py` — see R3.

### R11: Auto-Validation Side Effects

**Risk:** `backup_snapshot()` now triggers `do_validate_backup()` when overdue. This adds ~3 seconds to encrypt operations weekly, and `do_validate_backup()` calls `sys.exit(1)` on validation failure.
**Likelihood:** The `sys.exit(1)` inside auto-validation would abort `do_encrypt()` — catastrophic.
**Mitigation:**
- Wrap auto-validation in `try/except SystemExit` (already shown in §4 Step 2 item 6).
- Log the failure as non-fatal.
- The snapshot itself was already created successfully before validation runs.
- Consider refactoring `do_validate_backup()` to return a bool instead of calling `sys.exit()`. **Out of scope for this PR** — the try/except is sufficient.

**Test interaction:** `do_validate_backup()` calls `get_private_key()` → `keyring.get_password()`. Tests that call `backup_snapshot()` with a manifest where `last_validate` is >7 days old (or absent) will trigger auto-validation, which will try to hit the real Keychain. **Prevention:** Tests that call `backup_snapshot()` should either (a) seed `last_validate` in the manifest to today's ISO timestamp to suppress auto-validation, or (b) mock `keyring.get_password` and `check_age_installed`. Option (a) is simpler and preferred for most tests.

### R12: Key Export Security

**Risk:** `backup.py export-key` prints the age private key to stdout. If the terminal is shared, logged, or screen-captured, the key is exposed.
**Likelihood:** Low in normal use but non-zero.
**Mitigation:**
- Print security warnings before and after the key.
- Log `KEY_EXPORTED` to audit trail.
- Do NOT add a `--quiet` flag that suppresses warnings — the warnings are the feature.
- Document: "Run this command in a private terminal. Clear your scrollback after."
- `import-key` reads from stdin to avoid the key appearing in command-line history.

### R13: `_config` Dict Breaks Direct Constant Access

**Risk:** Code that does `from scripts.foundation import ARTHA_DIR; my_path = ARTHA_DIR / "foo"` at module level works fine — but the value is frozen at import time. If a test later patches `_config["ARTHA_DIR"]`, this module-level `my_path` won't update.
**Likelihood:** Medium — someone will write this pattern.
**Mitigation:**
- **Convention:** Module-level path compositions are forbidden. Always compute paths inside functions:
  ```python
  # BAD — frozen at import time
  MY_PATH = ARTHA_DIR / "foo"

  # GOOD — resolved at call time
  def get_my_path():
      return _config["ARTHA_DIR"] / "foo"
  ```
- backup.py's `BACKUP_DIR` and `BACKUP_MANIFEST` are stored in `_config` and accessed via `_config["BACKUP_DIR"]` in functions. The module-level aliases exist only for `__all__` and external read access.

---

## 6. Verification Checklist

Run after each step. All must pass before proceeding to the next step.

```bash
# ── Syntax ──
python -c "import ast; ast.parse(open('scripts/foundation.py').read()); print('foundation OK')"
python -c "import ast; ast.parse(open('scripts/vault.py').read()); print('vault OK')"
python -c "import ast; ast.parse(open('scripts/backup.py').read()); print('backup OK')"

# ── Import order (no circular import) ──
python -c "import scripts.foundation; import scripts.vault; import scripts.backup; print('imports OK')"
python -c "import scripts.backup; import scripts.vault; import scripts.foundation; print('reverse OK')"

# ── Full test suite ──
python -m pytest tests/ -q
# Expected: 263+ passed, 2 skipped, 20 xfailed

# ── Module-specific tests ──
python -m pytest tests/unit/test_vault.py -q    # Expected: 17 tests (13 standalone + 2 TestEncryptTriggersBackup + 2 guard)
python -m pytest tests/unit/test_backup.py -q   # Expected: 77+ tests

# ── Standalone CLI ──
python scripts/backup.py status           # must produce output, exit 0
python scripts/backup.py --help           # must show all subcommands
python scripts/backup.py preflight        # delegates to vault.py health
python scripts/backup.py snapshot         # creates a ZIP (requires encrypted state)

# ── Key management ──
python scripts/backup.py export-key       # prints key with warnings
echo "AGE-SECRET-KEY-..." | python scripts/backup.py import-key  # stores key

# ── mcp_server.py unaffected ──
grep -n 'vault\.' scripts/mcp_server.py   # should show only crypto primitives via vault

# ── No leftover backup functions in vault.py ──
grep -n 'def _file_sha256\|def _load_manifest\|def _save_manifest\|def _get_backup_tier\|def _prune_backups\|def _zip_archive_path\|def _backup_snapshot\|def _select_backup_zip\|def _restore_from_zip\|def do_validate_backup\|def do_backup_status\|def do_restore\|def do_install\|def _load_backup_registry' scripts/vault.py
# Should return 0 matches

# ── No leftover backup constants in vault.py ──
grep -n '^BACKUP_DIR\|^BACKUP_MANIFEST\|^GFS_RETENTION' scripts/vault.py
# Should return 0 matches

# ── Vault still re-exports foundation primitives ──
python -c "import scripts.vault as v; assert callable(v.log); assert callable(v.get_private_key); assert callable(v.age_encrypt); print('re-exports OK')"

# ── _config dict works ──
python -c "
from scripts.foundation import _config
assert 'ARTHA_DIR' in _config
assert 'STATE_DIR' in _config
print(f'config keys: {len(_config)}')
print('_config OK')
"

# ── No frozen module-level path usage in function bodies ──
# backup.py functions must use _config["BACKUP_DIR"], not the module-level BACKUP_DIR
grep -n 'BACKUP_DIR\|BACKUP_MANIFEST' scripts/backup.py | grep -v '_config\|^[0-9]*:BACKUP\|^[0-9]*:#\|__all__'
# Should return 0 matches
```

---

## 7. File Changeset Summary

| File | Action | Approximate size |
|---|---|---|
| `scripts/foundation.py` | **CREATE** | ~220 lines (`_config` dict + constants + 7 functions + `__all__`) |
| `scripts/backup.py` | **CREATE** | ~700 lines (14 backup functions + `load_backup_registry` + `get_health_summary` + auto-validate + key mgmt + `main()` + `__all__`) |
| `scripts/vault.py` | **EDIT** | Remove ~900 lines (foundation constants/functions + backup functions + registry); add ~25 lines (foundation import + backup lazy imports + backup failure handler + guard) |
| `tests/unit/test_backup.py` | **CREATE** | ~870 lines (77 tests + fixtures + helpers + structural guards + CLI + key mgmt tests) |
| `tests/unit/test_vault.py` | **EDIT** | Remove ~730 lines of backup tests (keep `TestEncryptTriggersBackup`); update fixture to use `_config`; add 2 guard tests |
| `README.md` | **EDIT** | Add backup.py CLI docs, cold-start workflow with key management, `--data-only` flag |
| `specs/artha-tech-spec.md` | **EDIT** | Bump v2.6, update §8.5.2 architecture, document `_config` pattern |
| `specs/artha-prd.md` | **EDIT** | Update F15.58, add F15.59 (key export/import) |

**Net effect:** Same total logic plus key management and auto-validation, distributed across three modules with clean unidirectional dependencies. vault.py drops from ~1,442 lines to ~540.

---

## 8. Git Strategy

**Two commits** — foundation extraction is independently reversible:

**Commit 1** — after Step 1 passes all tests:

```bash
git add scripts/foundation.py scripts/vault.py tests/unit/test_vault.py
git commit -m "refactor(vault): extract foundation.py — shared constants and crypto primitives

New module: scripts/foundation.py (leaf, no internal imports)
  - _config dict pattern: mutable config for test fixture patching
  - Constants: ARTHA_DIR, STATE_DIR, CONFIG_DIR, AUDIT_LOG, LOCK_FILE, etc.
  - Utilities: log(), die()
  - Crypto: get_private_key(), get_public_key(), age_encrypt(), age_decrypt()
  - Removed skills_cache from SENSITIVE_FILES (never existed on disk)

vault.py: imports foundation top-level, all functions use _config dict.
test_vault.py: fixture uses monkeypatch.setitem(foundation._config, ...).
Re-exports all primitives for mcp_server.py backward compatibility.

Tests: 251 passed, 2 skipped, 20 xfailed (unchanged)
Functions moved from vault.py — use git log --follow for history."
```

**Commit 2** — after Step 4 passes all tests:

```bash
git add scripts/backup.py scripts/vault.py \
       tests/unit/test_backup.py tests/unit/test_vault.py
git commit -m "refactor(vault): extract backup.py — GFS archive engine

New module: scripts/backup.py — standalone CLI with argparse
  - All 14 backup functions + load_backup_registry() from vault.py
  - backup_snapshot(registry) takes registry as parameter
  - New: snapshot subcommand (create backups from CLI)
  - New: export-key / import-key (age key disaster recovery)
  - New: auto-validation trigger (weekly, inside backup_snapshot)
  - preflight delegates to vault.py health (no check duplication)

vault.py changes:
  - do_encrypt() warns on backup failure + persists to health-check.md
  - do_health() uses get_health_summary() from backup.py
  - main() dispatches backup commands via lazy imports
  - Zero backup logic remains (only 2 lazy import call sites)

Test split: 17 vault + 77 backup = 94 (was 82 + 12 new)
Full suite: 263+ passed, 2 skipped, 20 xfailed"
```

**Third commit** for docs/specs (Step 6):

```bash
git add README.md specs/artha-tech-spec.md specs/artha-prd.md specs/bkp-rst.md
git commit -m "docs: update specs and README for three-module architecture (v2.6)

- README: backup.py CLI section, cold-start workflow, key backup guide
- Tech spec: v2.6, foundation.py + _config pattern, auto-validation
- PRD: F15.58 module separation, F15.59 key export/import
- bkp-rst.md: final implementation record (v3)"
```

Push only after all three commits are green:

```bash
python -m pytest tests/ -q && git push origin main
```

---

## 9. Success Criteria

- [ ] `scripts/foundation.py` exists as shared foundation — no imports from vault/backup
- [ ] `scripts/foundation.py` provides `_config` dict — single patching point for tests
- [ ] `scripts/backup.py` exists with all archive functions + standalone CLI
- [ ] `scripts/backup.py` has `snapshot` subcommand — can create backups independently
- [ ] `scripts/backup.py` has `export-key` / `import-key` — key disaster recovery
- [ ] `scripts/vault.py` contains zero backup logic (only lazy imports to dispatch)
- [ ] `scripts/vault.py` re-exports all foundation primitives (backward compatibility)
- [ ] `skills_cache` removed from `SENSITIVE_FILES` (inconsistency resolved)
- [ ] `tests/unit/test_backup.py` has 77+ backup tests, all passing
- [ ] `tests/unit/test_vault.py` has 17 tests (13 standalone + 2 from TestEncryptTriggersBackup + 2 guards), all passing
- [ ] Full suite: 263+ tests passing, 0 failures
- [ ] Import order test passes (no circular imports)
- [ ] `_config` propagation test passes (single fixture patches all modules)
- [ ] mcp_server.py unaffected (vault primitives accessible via `vault.*`)
- [ ] `vault.py encrypt` triggers backup and **warns on failure** + persists to health-check.md
- [ ] `vault.py health` reports GFS status via `get_health_summary()`
- [ ] `vault.py restore/install/backup-status/validate-backup` dispatch to backup.py
- [ ] `backup.py snapshot/status/validate/restore/install/export-key/import-key/preflight` work standalone
- [ ] `backup.py --help` shows all subcommands with descriptions
- [ ] `backup.py preflight` delegates to `vault.py health` (no check duplication)
- [ ] Auto-validation triggers weekly inside `backup_snapshot()` (~3s, non-fatal on failure)
- [ ] Behavioral contracts tested: restore during active session, same-date overwrite, unknown domains
- [ ] `README.md` documents cold-start workflow with key backup instructions
- [ ] Two code commits + one docs commit — foundation extraction independently reversible

---

## 10. Future Work (Out of Scope)

These are natural follow-ons but explicitly not part of this change:

1. **`mcp_server.py` → import foundation directly** — currently uses bare `import vault` for crypto primitives. Could import `foundation` instead, removing the dependency on vault.py entirely. Low priority — the re-export pattern works.
2. **`do_validate_backup()` return bool** — currently calls `sys.exit(1)` on failure. Refactoring to return a bool would eliminate the `try/except SystemExit` in auto-validation. Medium complexity.
3. **Surface backup health in catch-up briefing** — add a single line to the briefing footer: `GFS backup: N snapshots, last validated Xd ago ✓/⚠`. Trigger a 🟡 alert if validation is overdue (>35d).
4. **`vault.py` argparse migration** — replace the manual `while i < len(args)` parser in vault.py `main()` with argparse, matching backup.py's pattern.
5. **Backup encryption key rotation** — currently tied to a single age keypair. Future: support key rotation with re-encryption of existing backups.
6. **Encrypted key export** — `export-key` currently prints plaintext to stdout. Future: export as age-encrypted file using a passphrase, so it can be stored in cloud storage safely.
