# Pay Down Technical Debt — Artha Structural Integrity Plan

**Spec ID:** PAY-DEBT v1.0
**Author:** Artha Enhancement Pipeline
**Date:** 2026-03-26
**Status:** Proposed
**Depends on:** None (all phases are self-contained with explicit gates)
**Blocked by:** None
**Estimated LOC:** ~430 LOC changed/new (restructuring + observability); ~470 new test LOC
**Codebase baseline:** 71,284 LOC production | 36,410 LOC test | 2,886 tests | 184 modules

---

## 0. Non-Negotiable Principles

Every change in this spec is anchored to four laws. If any refactoring
decision conflicts with these, the principle wins.

| # | Law | Implication for this spec |
|---|-----|---------------------------|
| N1 | **LLM/CLI is the engine** — prefer Markdown for prompts and state over hardcoded processes. Better AI models = better Artha automatically. | No new Python scripts for work that an improved LLM handles natively. State schemas stay as YAML-frontmatter Markdown. Registries stay declarative. |
| N2 | **PULL > PUSH** — running catch-up triggers everything. No cron, no daemons, no background services as core dependencies. | Observability is file-based (queryable at catch-up). No monitoring servers, no push alerting infra. |
| N3 | **Privacy paramount; non-PII is plaintext** — encrypt what's sensitive, don't encrypt what isn't. | Logs and metrics are plaintext JSONL (no PII in log events by design). State file encryption scope unchanged. |
| N4 | **Cloud-folder portable** — Artha runs from OneDrive/iCloud/Dropbox on Windows, Mac, and Linux simultaneously. | No platform-specific paths in production code. Machine-local storage (actions.db, logs) uses the existing `~/.artha-local/` convention. |

### Design Axiom

> Suboptimal and working beats optimal and broken.
> Every phase must leave Artha functional. If a refactor cannot be completed
> safely, stop at the facade layer — partial decomposition with a working
> shim is explicitly acceptable.

---

## 1. Problem Statements

### TD-1: God Files in the Critical Path

Three files constitute 8,537 LOC (12% of the codebase) with 5+ mixed
responsibilities each:

| File | LOC | Functions | Responsibilities |
|------|-----|-----------|------------------|
| `channel_listener.py` | 3,543 | 44 (34 top-level + 10 class methods) | Command routing, state readers, LLM orchestration, 9-layer security, bridge integration, Telegram formatting, action approval, PII redaction |
| `work_reader.py` | 3,146 | 54 | 22 CLI commands, meeting parsing, readiness scoring, decision tracking, Narrative Engine delegation, connector health, degraded-mode rendering, org topology |
| `preflight.py` | 1,848 | 30 | 40+ health checks spanning vault, OAuth, API connectivity, state templates, profile completeness, channels, actions, integrations, WorkIQ |

**Impact:** These files are difficult to test in isolation (channel_listener
has only 24 unit tests for 3,543 lines), create merge-conflict hotspots, and
resist incremental improvement because a change to any function risks
regression in unrelated functions sharing the same file. work_reader.py is the
most critical — the user's work performance depends on its reliability.

### TD-2: Work OS Structural Fragility

`work_reader.py` is a single 3,146-line module that implements all 22
`/work *` commands. It handles:
- CLI argument parsing (`main()`, `argparse`)
- State file reading (frontmatter + body parsing)
- Meeting parsing and readiness scoring (`_parse_today_meetings`, `_readiness_score`)
- Decision drift detection (`_detect_decision_drift`)
- Narrative Engine delegation (`cmd_memo`, `cmd_deck`, `cmd_newsletter`, `cmd_promo_case`, etc.)
- Connector health assessment (`cmd_health`, `_assess_provider_tier`)
- Org topology rendering (`cmd_graph`, `_build_influence_map`)
- File mutation (`_append_to_file`, `_ensure_decisions_header`, `cmd_sources_add`)

284 tests exist across `test_work_reader.py` (225) and `test_phase4_phase5.py`
(59). These import directly from `work_reader`, making safe decomposition
possible only via a backward-compatible facade.

### TD-3: Hardcoded Registry Anti-Pattern

Four parallel in-code registries must be manually synchronized:

| Registry | Location | Entries | Parallel config |
|----------|----------|---------|-----------------|
| `_HANDLER_MAP` | `pipeline.py` | 18 | config/connectors.yaml |
| `_HANDLER_MAP` | `action_executor.py` | 11 | config/actions.yaml |
| `_SIGNAL_ROUTING` | `action_composer.py` | 47 | (none) |
| `_COMMAND_ALIASES` | `channel_listener.py` | 68 | config/commands.md |

Every new capability requires edits in 2-3 files. The connector and action
handler maps duplicate information already present in YAML config files.

### TD-4: Observability is Retrospective-Only

The system writes to:
- `state/audit.md` — text-based, not machine-parseable at scale
- `tmp/*.json` — ephemeral metrics cleared by Step 18
- `state/health-check.md` — YAML frontmatter, semi-structured

There is no structured logging, no proactive failure detection, and no
time-series analysis capability. A connector can fail silently for days.
`AuditMiddleware` swallows `OSError` — the audit trail itself can disappear
without warning.

### TD-5: Foundation Module-Level Aliases

`foundation.py` exposes `ARTHA_DIR`, `STATE_DIR`, etc. as module-level
constants frozen at import time. The mutable `_config` dict is the actual
source of truth for tests. The docstring says aliases "MUST NOT be used
inside function bodies" but this is documentation-only — no enforcement.
Test authors who accidentally use the aliases get stale values.

`lib/common.py` independently derives `ARTHA_DIR` from `__file__`, creating
a second independent source of the same constants. 27 modules import from
`lib/common.py`.

### TD-6: Mixed Concurrency Models

Four different concurrency primitives with no unified abstraction:
- `ThreadPoolExecutor` (pipeline.py — connector fetches)
- `asyncio` (channel_listener.py — command handling)
- `threading.Thread` (action_executor.py — execution timeout)
- `subprocess.Popen` (preflight.py, pii_guard, LLM CLI calls)

Not a blocking issue today (single-user, PULL-based), but increases
cognitive overhead for any contributor and creates subtle deadlock risks.

### TD-7: State File Schema Drift

State files (`.md` with YAML frontmatter) have no formal schema validation.
Fields are added by different modules without migration. SQLite has
`_migrate_schema_if_needed()`; Markdown state files have nothing.
`WriteGuardMiddleware` heuristically catches >20% field loss, but cannot
detect a renamed or moved field.

### TD-8: Subprocess PII Guard Overhead

`pii_guard.py` is invoked via `subprocess.Popen` in the action executor
(twice per action: pre-enqueue + pre-execute). The same module has a
Python API (`filter_text()`) — the subprocess call adds ~100ms
per invocation with no security benefit when running in the same process.

### TD-9: E2E Test Coverage Gap

4 integration tests for 71K+ LOC. Multi-step workflows (catch-up → briefing
→ email delivery → state update) have no automated regression coverage.

### TD-10: Domain Logic in lib/common.py

`update_channel_health_md()` — 55 lines of domain-specific state mutation
(regex replacements on health-check.md YAML blocks) — lives in the shared
infrastructure library. This violates the layering contract: `lib/` should
contain only stateless utilities.

### TD-11: Silent Audit Middleware Failure

`AuditMiddleware._append()` catches `OSError` with `pass`. If `audit.md`
becomes unwritable (disk full, permission error, cloud-sync lock), the
audit trail silently stops. No stderr warning, no metric, no user
notification.

### TD-12: Cost Tracking Estimation Gap

Cost tracking in `cost_tracker.py` estimates token usage at ±50% accuracy.
It reads `health-check.md` and `pipeline_metrics.json` rather than
instrumenting actual API calls. Not a functional issue but creates budget
blind spots.

---

## 2. Assumptions (Tested)

| # | Assumption | Test | Result | Impact |
|---|-----------|------|--------|--------|
| D1 | `work_reader.py` is CLI-only (no library imports from production code) | `grep -rn "from work_reader import\|import work_reader" scripts/*.py` | **Confirmed: zero production importers** (only 2 test files) | Decomposition can restructure freely; no production callers break |
| D2 | `channel_listener.py` is CLI-only with 2 test importers | `grep -rn "import channel_listener" scripts/*.py tests/**/*.py` | **Confirmed: only test_channel_listener.py + test_knowledge_capture.py** | Same as D1 — no production coupling |
| D3 | `preflight.py` is CLI-only | `grep -rn "import preflight" scripts/*.py` | **DISPROVED: `mcp_server.py:431` does `from preflight import run_preflight`** | Phase 5 must update `mcp_server.py` import in the same atomic commit as the rename |
| D4 | Work reader has 284 tests (225 + 59) across 2 files | `grep -c "def test_" tests/work/test_work_reader.py tests/work/test_phase4_phase5.py` | **Confirmed: 225 + 59 = 284** | Strangler-fig facade keeps all 284 tests passing during decomposition |
| D5 | Channel listener has 24 unit tests in 382 LOC | `wc -l tests/unit/test_channel_listener.py; grep -c "def test_"` | **Confirmed** | Modest test surface — rewrite is low-risk |
| D6 | `lib/common.py` has 27 importers | `grep -rn "from lib.common import" scripts/**/*.py \| wc -l` | **Confirmed** | Touching constants requires backward-compatible re-export |
| D7 | `foundation._config` is the test-friendly mutable store | Read `foundation.py` source | **Confirmed: _config dict, tests monkeypatch it** | Adding `get_config()` is additive (no breaking change) |
| D8 | `narrative_engine.py` (1,722 LOC) is a single NarrativeEngine class used by work_reader | Checked imports + class structure | **Confirmed: separate module, composed by work_reader** | NarrativeEngine is already separated — work_reader refactoring doesn't touch it |
| D9 | Connector YAML files already describe what `_HANDLER_MAP` hardcodes | Compared `connectors.yaml` entries with `pipeline._HANDLER_MAP` | **Confirmed: both enumerate the same 18 connectors** | Can derive Python map from YAML at startup |
| D10 | Action config YAML already describes what executor `_HANDLER_MAP` hardcodes | Compared `actions.yaml` with `action_executor._HANDLER_MAP` | **Confirmed: overlapping keys** | Same as D9 |
| D11 | `audit.md` is written by 14+ modules, each with local `_audit_log()` | `grep -rn "_audit_log\|audit\.md" scripts/*.py \| wc -l` | **Confirmed** | Centralized logger must not break any existing append pattern |
| D12 | work_reader duplicates helpers with narrative_engine | `_parse_dt`, `_age_str`, `_read_frontmatter`, `_read_body`, `_extract_section`, `_load_profile` exist in both | **Confirmed: 6 identical helper functions** | Factor into shared `scripts/work/helpers.py` |
| D13 | 183 hard exits (`sys.exit`/`die()`) across scripts/ | `grep -rn "sys\.exit\|die(" scripts/*.py \| wc -l` | **Confirmed** | Not refactoring these now — documenting as future TD |
| D14 | `~/.artha-local/` exists as machine-local convention (actions.db) | Checked `action_queue._resolve_db_path()` | **Confirmed: macOS → ~/.artha-local, Windows → %LOCALAPPDATA%\Artha** | Logs can use same convention |
| D15 | No production code imports from `test_*` files | `grep -rn "from tests\|import test_" scripts/*.py` | **Confirmed: zero results** | Test rewrites don't affect production |
| D16 | `pii_guard.filter_text()` is callable in-process | Read `pii_guard.py` source API | **Confirmed: exists as regular Python function** | Can replace subprocess call with direct import |

---

## 3. Phase Overview & Dependency Map

```
Phase 0 ─── Safety Baseline (test gate + backup)
  │
Phase 1 ─── Foundation Hardening (TD-5, TD-10, TD-11)     ~50 LOC changed
  │                                                         ~10 new tests
  │
Phase 2 ─── Observability Layer (TD-4)                     ~200 LOC new
  │                                                         ~12 new tests
  │
Phase 3 ─── Work OS Restructure (TD-2)        ★ HIGHEST    ~0 new LOC
  │         (strangler-fig facade)              PRIORITY     ~59 new tests
  │         284 existing tests preserved
  │
Phase 4 ─── Channel Listener Restructure (TD-1)            ~0 new LOC
  │         (clean decomposition + new tests)                ~80 new tests
  │
Phase 5 ─── Preflight Restructure (TD-1)                   ~0 new LOC
  │                                                         ~40 new tests
  │
Phase 6 ─── Registry Consolidation (TD-3)                  ~80 LOC changed
  │                                                         ~9 new tests
  │
Phase 7 ─── Hardening (TD-7, TD-8)                        ~100 LOC changed
                                                            ~10 new tests
```

**Each phase is independently valuable.** You can stop after any phase gate
and have a strictly better system than before. Phases 3-5 are the structural
core. Phase 6-7 are quality-of-life improvements.

**Section 16 is the authoritative source for per-phase counts.** This
overview is a summary and must stay numerically aligned with Section 16.

**TD-6 (Mixed concurrency) and TD-9 (E2E tests) are explicitly deferred.**
TD-6 is not causing production issues and a concurrency unification would
touch every major module — the risk-reward ratio is wrong for this spec.
TD-9 requires test infrastructure investment (mocked API servers, fixture
pipelines) that deserves its own spec.

### 3.1 Architectural Boundary Rules

After decomposition, the following import-dependency DAG is enforced.
Violations are caught by `test_no_cross_domain_imports()` (added in Phase 1).

```
Foundation layer:  foundation, lib/*
                   ↑ (anyone may import)
Domain layer:      work/*, channel/*, preflight/*
                   (NO cross-domain imports — work/ must NOT import channel/, etc.)
                   ↑ (facades and orchestrators may import)
Orchestration:     pipeline, action_executor, work_reader (facade),
                   channel_listener (facade), mcp_server
```

This DAG prevents the decomposed subpackages from re-coupling over time —
the exact problem this spec sets out to solve.

### 3.2 Import Convention (Dual-Import Guard)

`tests/conftest.py` adds both `PROJECT_ROOT` and `SCRIPTS_DIR` to
`sys.path`. This creates a dual-import hazard: `from work.helpers import X`
(via SCRIPTS_DIR) and `from scripts.work.helpers import X` (via PROJECT_ROOT)
load as **different module objects**. Monkeypatching one doesn't affect
the other; `isinstance` checks fail across boundaries.

> **Convention:** Always use bare imports via SCRIPTS_DIR:
> `from work.helpers import _parse_dt` — **NOT** `from scripts.work.helpers import _parse_dt`.
>
> New test files and production code must follow this convention.
> Add a comment to `tests/conftest.py` documenting this rule.

---

## 4. Phase 0 — Safety Baseline

**Purpose:** Establish the regression-detection foundation before any
structural change. Every subsequent phase gate includes "Phase 0 still
green" as its first check.

### 4.1 Actions

1. **Snapshot the current test baseline:**
   ```bash
   source .venv/bin/activate
   python -m pytest tests/ -q --tb=no 2>&1 | tail -5
   # Expected: "2886 tests collected" — record exact count
   ```
   Store the count in `tmp/debt_baseline.json`:
   ```json
   {"baseline_test_count": 2886, "baseline_date": "2026-03-26", "failures": 0}
   ```

2. **Create a pre-refactor Git tag:**
   ```bash
   git tag pre-pay-debt-v1 -m "Baseline before PAY-DEBT refactoring"
   git push origin pre-pay-debt-v1
   ```

3. **Verify backup infrastructure:**
   ```bash
   python scripts/backup.py --dry-run
   ```

4. **Record performance baseline:**
   ```bash
   time python -c "import work_reader" 2>&1
   time python -c "import channel_listener" 2>&1
   time python scripts/work_reader.py --command pulse 2>&1
   ```
   Add to `tmp/debt_baseline.json`:
   ```json
   {"baseline_test_count": 2886, "baseline_date": "2026-03-26", "failures": 0,
    "import_work_reader_ms": "<measured>", "import_channel_listener_ms": "<measured>",
    "pulse_wall_ms": "<measured>"}
   ```

### 4.2 Phase Gate

| Check | Command | Pass criteria |
|-------|---------|---------------|
| All tests pass | `python -m pytest tests/ -q --tb=short` | 0 failures, 0 errors |
| Baseline recorded | `cat tmp/debt_baseline.json` | File exists, count matches |
| Git tag pushed | `git tag -l pre-pay-debt-v1` | Tag exists |
| Performance baseline | `import_work_reader_ms` and `pulse_wall_ms` in baseline JSON | Values present and non-zero |

### 4.3 Rollback

Not applicable — Phase 0 makes no changes.

---

## 5. Phase 1 — Foundation Hardening

**Targets:** TD-5 (module aliases), TD-10 (domain logic in lib/), TD-11
(silent audit failure)

**Risk:** LOW — three surgical fixes, each under 20 LOC, zero behavioral
change.

### 5.1 Changes

#### 5.1.1 TD-5: Add `get_config()` accessor to `foundation.py`

Add a stable accessor that always returns the current `_config` dict:

```python
# foundation.py — add after _config definition

def get_config() -> dict[str, Any]:
    """Return the mutable config dict.  Always use this in function bodies.

    The module-level aliases (ARTHA_DIR, STATE_DIR, etc.) are frozen at
    import time and exist only for backward compatibility in module-level
    scope.  Inside functions, always call ``get_config()["STATE_DIR"]``
    to get the current (possibly test-patched) value.
    """
    return _config
```

**What this does NOT do:** Remove the existing aliases. They have 27+
consumers ($D6). Removing them would be a breaking change with zero upside.
The accessor provides a safe alternative; migration of callers is optional
and incremental.

#### 5.1.2 TD-10: Move `update_channel_health_md()` out of `lib/common.py`

Create `scripts/health_check_updater.py` containing the 55-line function.
Update the 2 call sites (`channel_push.py:421`, `preflight.py:1321`) to
import from the new location. Keep a deprecated re-export in `lib/common.py`
for safety:

```python
# lib/common.py — at the end of file, replacing the function body
def update_channel_health_md(*args: Any, **kwargs: Any) -> None:
    """Deprecated — import from health_check_updater instead."""
    from health_check_updater import update_channel_health_md as _fn
    _fn(*args, **kwargs)
```

#### 5.1.3 TD-11: Log audit write failures to stderr

In `scripts/middleware/audit_middleware.py`, replace the bare `except OSError:
pass` with:

```python
except OSError as exc:
    print(f"[WARN] audit write failed: {exc}", file=sys.stderr)
```

One line. No behavior change for callers. The audit trail gap becomes visible.

### 5.2 Tests

| # | Test | Covers |
|---|------|--------|
| T1-1 | `get_config()` returns `_config`; monkeypatching `_config` is visible through `get_config()` | TD-5 accessor |
| T1-2 | `get_config()["STATE_DIR"]` matches `foundation._config["STATE_DIR"]` | TD-5 consistency |
| T1-3 | Module-level `ARTHA_DIR` is a `Path` and exists | TD-5 backward compat |
| T1-4 | `health_check_updater.update_channel_health_md()` creates section if missing | TD-10 move |
| T1-5 | `health_check_updater.update_channel_health_md()` updates existing channel entry | TD-10 move |
| T1-6 | `lib.common.update_channel_health_md` still works (deprecated re-export) | TD-10 backward compat |
| T1-7 | `AuditMiddleware.after_write()` prints to stderr on `OSError` (use `capsys` or `capfd`) | TD-11 |
| T1-8 | `AuditMiddleware.after_write()` does not raise on `OSError` (still swallows) | TD-11 no crash |
| T1-9 | `AuditMiddleware.after_write()` writes successfully when `audit.md` is writable | TD-11 happy path |
| T1-10 | `test_no_cross_domain_imports`: verify no imports between `work/`, `channel/`, `preflight/` subpackages (uses `ast` module to parse import statements) | Architectural boundary |

Files: `tests/unit/test_foundation_hardening.py` (~9 tests) + `tests/unit/test_architectural_boundaries.py` (1 test)

### 5.3 Phase Gate

| Check | Command | Pass criteria |
|-------|---------|---------------|
| Phase 0 baseline | `python -m pytest tests/ -q --tb=no` | ≥ 2886 tests, 0 failures |
| New tests pass | `python -m pytest tests/unit/test_foundation_hardening.py tests/unit/test_architectural_boundaries.py -v` | 10 pass |
| Deprecated re-export works | Import `from lib.common import update_channel_health_md` succeeds | No `ImportError` |
| No import cycles | `python -c "import foundation; import health_check_updater"` | No error |

### 5.4 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| A module uses `from lib.common import update_channel_health_md` that we missed | Low (grep is exhaustive) | Low (deprecated re-export catches it) | Deprecated re-export is the mitigation |
| `get_config()` creates a false sense of safety — callers cache the return value | Low | Low (dict is mutable ref, not copy) | Document: "returns mutable dict — do not cache keys" |

### 5.5 Rollback

```bash
git checkout pre-pay-debt-v1 -- scripts/foundation.py scripts/lib/common.py scripts/middleware/audit_middleware.py
# Delete new files:
rm scripts/health_check_updater.py tests/unit/test_foundation_hardening.py
```

---

## 6. Phase 2 — Observability Layer

**Target:** TD-4 (structured logging)

**Risk:** LOW-MEDIUM — additive (new module + integration points), no
existing behavior changes.

### 6.1 Design Decision: JSONL File Sink

**Rationale per N1-N4:**
- N2 (PULL > PUSH): File-based, queryable at catch-up time — no monitoring
  server, no push alerting.
- N3 (Privacy): Log events contain operation names and timing, never PII.
  Template: `{"ts": "...", "event": "connector.fetch", "connector": "gmail", "records": 142, "ms": 3200}`.
- N4 (Cloud-portable): Logs go to `~/.artha-local/logs/` (machine-local,
  per D14 convention). Not synced via cloud — prevents log bloat across
  machines and avoids cloud-sync contention on write-heavy files.

**Why not OpenTelemetry:** OTel adds `opentelemetry-api` + `opentelemetry-sdk`
dependencies (~15 packages), requires a collector/exporter, and is designed
for distributed systems. Artha is a single-user, single-process system.
The complexity-to-value ratio is wrong for N1/N2 philosophy.

**Minimal observability contract (required even without OTel):**
- Every top-level operation emits a `trace_id`.
- Child events emit `correlation_id` and/or `parent_span_id` when nested.
- Duration, count, records, and error totals are emitted as numeric fields so
  counters and histograms can be derived from JSONL at catch-up time.
- Artha does not run a tracing backend, but it does preserve trace semantics
  in the event schema so failures can be correlated across pipeline, channel,
  and action execution flows.

### 6.2 Changes

#### 6.2.1 New: `scripts/lib/logger.py` (~120 LOC)

```python
"""Structured JSONL logging for Artha.

Usage:
    from lib.logger import get_logger
    log = get_logger("pipeline")
    log.info("connector.fetch", connector="gmail", records=142, ms=3200)

Output: one JSON line per event to ~/.artha-local/logs/artha.YYYY-MM-DD.log.jsonl
Rotation: one file per day, auto-prune files older than 30 days on logger init.
"""
```

Implementation:
- Uses `logging.Logger` with a custom `logging.Handler` (no new deps)
- Handler writes to `~/.artha-local/logs/artha.YYYY-MM-DD.log.jsonl`
- `StructuredFormatter` outputs `{"ts", "level", "event", "module", "trace_id", "correlation_id?", "parent_span_id?", **kwargs}`
- `_prune_old_logs(logs_dir, max_age_days=30)` removes stale files on init
- PII guard: event names and numeric values only — no string content from
  user data. `log.info("email.classified", count=5)` is OK.
  `log.info("email.subject", subject="...")` is NOT OK and must never be
  coded. This is a code-review contract, not runtime enforcement (N3).
- Fallback: If `~/.artha-local/logs/` cannot be created (read-only FS),
  fall back to `stderr` with `[STRUCTURED]` prefix. Never crash.
- Numeric event fields (`ms`, `count`, `records`, `errors`) are part of the
  contract so connector latency, command latency, and failure-rate summaries
  can be derived without a dedicated metrics backend.

#### 6.2.2 Integration Points (3 hot paths)

| Module | Integration | Events logged |
|--------|------------|---------------|
| `pipeline.py` | After `run_pipeline()` completes | `connector.fetch` (per connector: name, records, ms, error) |
| `action_executor.py` | After `approve()` completes | `action.executed` (type, domain, result, ms) |
| `channel_listener.py` | In `process_message()` entry/exit | `command.received` (command, sender_hash, ms), `command.completed` |

Each integration is 3-5 lines: import + two log calls. No behavioral change
to existing code paths.

#### 6.2.3 Minimal Metrics + Trace Correlation

Derived at catch-up time from JSONL events:
- Connector metrics: fetch count, fetch error count, p50/p95 fetch latency
- Action metrics: execution count, failure count, execution latency
- Channel metrics: command count, command error count, end-to-end latency

This is intentionally not a push-metrics system. It satisfies the
observability requirement with local, queryable telemetry that matches N2.

#### 6.2.4 TD-11 Extension: Audit Middleware Structured Event

When `AuditMiddleware._append()` catches an `OSError`, also emit:
```python
log.warning("audit.write_failed", error=str(exc), domain=domain)
```

This creates a machine-queryable record of audit trail gaps.

### 6.3 Tests

| # | Test | Covers |
|---|------|--------|
| T2-1 | `get_logger("test")` returns a `logging.Logger` | Logger factory |
| T2-2 | Logging an event writes one JSON line to the correct file | JSONL output |
| T2-3 | JSON line contains `ts`, `level`, `event`, `module`, `trace_id` keys | Schema |
| T2-4 | `correlation_id` and extra numeric kwargs appear in JSON output | Extensibility + trace semantics |
| T2-5 | Log file path uses `YYYY-MM-DD` date suffix | Daily rotation |
| T2-6 | `_prune_old_logs()` removes files older than `max_age_days` | Cleanup |
| T2-7 | `_prune_old_logs()` preserves files within `max_age_days` | Cleanup safety |
| T2-8 | Logger falls back to stderr if logs dir unwritable | Resilience |
| T2-9 | Fallback to stderr does not raise an exception | No crash |
| T2-10 | Multiple `get_logger()` calls with same name return same logger | Singleton |
| T2-11 | `AuditMiddleware` emits structured log on `OSError` | TD-11 integration |
| T2-12 | Pipeline integration: `connector.fetch` event logged after fetch | Pipeline |

File: `tests/unit/test_structured_logger.py` (~12 tests)

### 6.4 Phase Gate

| Check | Command | Pass criteria |
|-------|---------|---------------|
| Phase 0 baseline | `python -m pytest tests/ -q --tb=no` | ≥ 2896 tests (baseline + Phase 1), 0 failures |
| New tests pass | `python -m pytest tests/unit/test_structured_logger.py -v` | 12 pass |
| Log file created | `python -c "from lib.logger import get_logger; get_logger('test').info('test.event')"` then check `~/.artha-local/logs/` | JSONL file exists |
| Trace semantics present | Representative JSONL line contains `trace_id` and numeric `ms` or `count` field | Observability contract satisfied |
| No new dependencies | `pip freeze \| wc -l` unchanged | Same count |

### 6.5 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| JSONL writes contend with cloud sync if user accidentally puts `~/.artha-local` in synced folder | Very low | Low (write contention on append-only file) | Document: `~/.artha-local` must not be in synced folder |
| Developers accidentally log PII in event kwargs | Medium | Medium (PII in plaintext log) | Code-review contract + grep-based CI check for log calls with content args |
| Log files accumulate beyond 30 days if Artha isn't run | Low | Low (stale files, no functional impact) | Acceptable — prune runs on next startup |

### 6.6 Rollback

```bash
rm scripts/lib/logger.py tests/unit/test_structured_logger.py
git checkout HEAD -- scripts/pipeline.py scripts/action_executor.py scripts/channel_listener.py scripts/middleware/audit_middleware.py
```

---

## 7. Phase 3 — Work OS Restructure ★ HIGHEST PRIORITY

**Target:** TD-2 (work_reader.py god file — 3,146 LOC, 54 functions)

**Risk:** MEDIUM — 284 existing tests provide a strong regression net. The
strangler-fig facade pattern ensures all tests pass before any new code
is exercised.

### 7.1 Design Decision: Strangler-Fig Facade

**Why not a clean break:**
- 284 tests import directly from `work_reader` (D1, D4)
- User's work performance depends on this module (user-stated priority)
- A clean break requires rewriting 284 tests simultaneously — one mistake
  masks a real regression

**Strategy:**
1. Create `scripts/work/` subpackage with focused modules
2. Move function bodies into submodules (cut+paste, no logic changes)
3. `work_reader.py` becomes a thin facade re-exporting from submodules
4. All 284 existing tests pass immediately through the facade
5. Write new focused tests for each submodule
6. Over time, update test imports to point to submodules directly
   (optional — facade can persist indefinitely)

### 7.2 Decomposition Map

Based on analysis of all 54 functions in `work_reader.py`:

| Submodule | Functions (moved from work_reader) | LOC Est. | Responsibility |
|-----------|------------------------------------|----------|----------------|
| `scripts/work/helpers.py` | `_parse_dt`, `_age_str`, `_age_hours`, `_read_frontmatter`, `_read_body`, `_extract_section`, `_staleness_header`, `_freshness_footer`, `_load_profile`, `_dfs_label`, `_boundary_label` | ~150 | Shared reading + formatting utilities (also used by `narrative_engine.py` — D12 dedup) |
| `scripts/work/briefing.py` | `WorkBriefingConfig`, `_build_briefing_config`, `_build_influence_map`, `_read_org_calendar_milestones`, `_validate_work_state_schema`, `_collect_eval_metrics`, `cmd_work`, `cmd_pulse`, `cmd_sprint`, `cmd_day` | ~700 | Daily briefing, pulse snapshot, sprint health, composite day view |
| `scripts/work/meetings.py` | `_MeetingEntry`, `_parse_meeting_start_dt`, `_parse_today_meetings`, `_extract_carry_forward_items`, `_readiness_score`, `_detect_decision_drift`, `_load_preread_markers`, `cmd_prep`, `cmd_live`, `cmd_mark_preread` | ~650 | Meeting parsing, readiness scoring, live assist, pre-read tracking |
| `scripts/work/health.py` | `_seniority_tier`, `_assess_provider_tier`, `_build_degraded_mode_report`, `_extract_exec_visibility_signals`, `cmd_health` | ~250 | Connector diagnostics, degraded-mode reporting |
| `scripts/work/decisions.py` | `_ensure_decisions_header`, `_append_to_file`, `cmd_decide` | ~200 | Structured decision capture and file ops |
| `scripts/work/discovery.py` | `cmd_people`, `cmd_docs`, `cmd_sources`, `cmd_sources_add`, `cmd_graph`, `cmd_incidents`, `cmd_repos` | ~500 | People lookup, doc search, source registry, org graph, incident/repo views |
| `scripts/work/career.py` | `cmd_connect`, `cmd_connect_prep`, `cmd_return`, `cmd_promo_case`, `cmd_journey` | ~500 | Review cycle, absence recovery, promotion, project timelines |
| `scripts/work/narrative.py` | `cmd_memo`, `cmd_newsletter`, `cmd_deck`, `cmd_talking_points` | ~200 | Thin delegation wrappers to `narrative_engine.py` |

Note: `main()` (CLI dispatch, argparse) stays in `work_reader.py` as the
entry point.

### 7.3 Facade Pattern

After decomposition, `work_reader.py` becomes:

```python
"""Work Reader — backward-compatible facade.

All implementation has moved to scripts/work/ submodules.
This file re-exports all public names for backward compatibility.
Import new code directly from scripts/work/ subpackages.
"""
from work.helpers import (  # noqa: F401
    _parse_dt, _age_str, _age_hours, _read_frontmatter, _read_body,
    _extract_section, _staleness_header, _freshness_footer,
    _load_profile, _dfs_label, _boundary_label,
)
from work.briefing import (  # noqa: F401
    WorkBriefingConfig, cmd_work, cmd_pulse, cmd_sprint, cmd_day,
    # ... etc
)
# ... re-exports for every submodule

def main(argv=None): ...  # stays here — CLI dispatch
```

### 7.4 The `narrative_engine.py` Dedup (D12)

Six helper functions are duplicated between `work_reader.py` and
`narrative_engine.py`: `_parse_dt`, `_age_str`, `_read_frontmatter`,
`_read_body`, `_extract_section`, `_load_profile`.

After Phase 3, both modules import from `scripts/work/helpers.py`:
- `work_reader.py` facade re-exports them
- `narrative_engine.py` imports directly: `from work.helpers import _read_frontmatter, ...`
- Delete the 6 duplicate function bodies from `narrative_engine.py`

### 7.5 Implementation Sequence (within Phase 3)

This sequence minimizes risk by ensuring tests pass at every checkpoint:

```
Step 3a: Create scripts/work/__init__.py (empty)
         → Test: all 284 tests still pass (no behavior change)

Step 3b: Create scripts/work/helpers.py — copy the 11 helper functions
         → Test: import work.helpers succeeds; all 284 tests still pass

Step 3b½: Generate public API snapshot of work_reader.py (R6 — contract test)
          Record all public names + callable signatures using `inspect.signature()`
          in tests/work/test_work_reader_contract.py
          → Test: snapshot test passes against current work_reader
          This catches signature drift (positional→keyword-only, return type
          changes) that behavioral tests might miss.

Step 3c: In work_reader.py, replace helper function bodies with
         imports from work.helpers (add re-exports)
         → Test: all 284 tests still pass through facade

Step 3d: Create scripts/work/briefing.py — move ~4 cmd functions + supporting code
         → Test: all 284 tests still pass (facade re-exports)

Step 3e–3i: Repeat for meetings, health, decisions, discovery, career, narrative
         → Test after EVERY step: all 284 tests still pass

Step 3j: Update narrative_engine.py to import from work.helpers
         → Test: all existing narrative_engine tests still pass

Step 3k: Write new focused tests for each submodule
         → Test: new submodule tests all pass
```

**Critical rule:** After each step (3a through 3j), run the full test suite.
If ANY test fails, stop and fix before proceeding. Do not batch steps.

### 7.6 Tests

**Existing tests (preserved):** 284 tests in `test_work_reader.py` (225) +
`test_phase4_phase5.py` (59) — must pass after every step.

**New focused tests:**

| # | Test file | Tests | Covers |
|---|-----------|-------|---------|
| T3-1..5 | `tests/work/test_work_helpers.py` | 5 | `_parse_dt` edge cases, `_read_frontmatter` missing file, `_freshness_footer` multi-domain |
| T3-6..12 | `tests/work/test_work_briefing.py` | 7 | `cmd_work` output structure, `cmd_pulse` brevity, `cmd_sprint` with missing state, `_build_briefing_config` defaults, `_validate_work_state_schema` detects issues |
| T3-13..22 | `tests/work/test_work_meetings.py` | 10 | `_parse_today_meetings` various formats, `_readiness_score` boundary cases, `_detect_decision_drift` detection + false positive, `cmd_prep` missing calendar, `_MeetingEntry` dataclass |
| T3-23..28 | `tests/work/test_work_health.py` | 6 | `_assess_provider_tier` all tiers, `cmd_health` degraded output, `_build_degraded_mode_report` formatting |
| T3-29..34 | `tests/work/test_work_decisions.py` | 6 | `cmd_decide` file creation, `_ensure_decisions_header` idempotency, `_append_to_file` encoding, `cmd_decide` with context |
| T3-35..44 | `tests/work/test_work_discovery.py` | 10 | `cmd_people` search, `cmd_sources` query, `cmd_sources_add` validation, `cmd_graph` rendering, `cmd_docs` with empty state, `cmd_repos` and `cmd_incidents` edge cases |
| T3-45..52 | `tests/work/test_work_career.py` | 8 | `cmd_connect` evidence assembly, `cmd_return` window parsing, `cmd_promo_case` output, `cmd_connect_prep` calibration mode, `cmd_journey` project filtering |
| T3-53..58 | `tests/work/test_work_narrative.py` | 6 | Delegation wrappers call NarrativeEngine correctly (mock verify) |
| T3-59 | `tests/work/test_work_reader_contract.py` | 1 | Public API snapshot: all re-exported names + signatures match pre-decomposition baseline |

Total new: ~59 tests | Existing preserved: 284 | Post-phase total: 343 work tests

### 7.7 Phase Gate

| # | Check | Command | Pass criteria |
|---|-------|---------|---------------|
| G3-1 | All existing tests pass | `python -m pytest tests/ -q --tb=short` | ≥ 2908 tests (baseline + Phase 1-2), 0 failures |
| G3-2 | All 284 work_reader tests pass through facade | `python -m pytest tests/work/ -q --tb=short` | 284 pass, 0 failures |
| G3-3 | New submodule + contract tests pass | `python -m pytest tests/work/test_work_helpers.py tests/work/test_work_briefing.py tests/work/test_work_meetings.py tests/work/test_work_health.py tests/work/test_work_decisions.py tests/work/test_work_discovery.py tests/work/test_work_career.py tests/work/test_work_narrative.py tests/work/test_work_reader_contract.py -v` | 59 pass |
| G3-4 | CLI still works | `python scripts/work_reader.py --command pulse` | Output produced, exit 0 |
| G3-5 | No import cycles | `python -c "import work_reader; import work.helpers; import work.briefing"` | No error |
| G3-6 | narrative_engine.py dedup | `grep -E '^def (_parse_dt|_age_str|_read_frontmatter|_read_body|_extract_section|_load_profile)' scripts/narrative_engine.py` | No matches (local helper defs removed; import lines don't start with `def`) |
| G3-7 | Functional smoke test | Run `/work pulse` via Telegram or local CLI | Output matches expected format |
| G3-8 | Performance within bounds | `time python -c "import work_reader"` | Import time ≤ 200% of Phase 0 baseline |
| G3-9 | Cross-machine sync works | On second machine after OneDrive sync: `python scripts/work_reader.py --command pulse` | Output produced, exit 0 |
| G3-10 | Contract test passes | `python -m pytest tests/work/test_work_reader_contract.py -v` | All signature assertions pass |

### 7.8 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Re-export facade has subtle import-order issue | Low | High (test failures) | Step-by-step with test run after each step; `conftest.py` already handles sys.path |
| Private functions (`_parse_dt` etc.) change behavior when moved | Very low | Medium | These are pure functions — no global state dependency. Diff the function bodies exactly. |
| `narrative_engine.py` import path change breaks its tests | Low | Medium | Run NE tests explicitly after Step 3j. If any fail, the fix is a single import line. |
| Someone has `from work_reader import cmd_work` in a custom script outside the repo | Very low | Low | Facade re-exports everything — external imports still work |
| `work.helpers` name conflicts with existing `scripts/work/` directory (if any) | — | — | **Verified: No existing `scripts/work/` directory.** There's `tests/work/` (tests) and `state/work/` (data) but no `scripts/work/`. Safe to create. |

### 7.9 Rollback

```bash
git checkout pre-pay-debt-v1 -- scripts/work_reader.py scripts/narrative_engine.py
rm -rf scripts/work/
# Remove new test files but keep existing test files intact
rm tests/work/test_work_helpers.py tests/work/test_work_briefing.py \
   tests/work/test_work_meetings.py tests/work/test_work_health.py \
   tests/work/test_work_decisions.py tests/work/test_work_discovery.py \
   tests/work/test_work_career.py tests/work/test_work_narrative.py \
   tests/work/test_work_reader_contract.py
```

---

## 8. Phase 4 — Channel Listener Restructure

**Target:** TD-1 (channel_listener.py — 3,543 LOC, 44 functions)

**Risk:** MEDIUM — 24 existing tests are retained in parallel until
behavioral parity is confirmed with the new test suites. The decomposition
creates focused, testable modules where before there was a monolith.

### 8.1 Design Decision: Clean Decomposition + New Tests

The user explicitly directed: "rewrite the test suites focused on the
decomposed implementation rather than trying to adapt the existing ones."

**Strategy:**
1. Create `scripts/channel/` subpackage with focused modules
2. `channel_listener.py` becomes a thin entry point: `main()`,
   `run_listener()`, `process_message()` — each delegating to submodules
3. Write new test suites for each submodule (~80 tests)
4. **Retain** old `test_channel_listener.py` (24 tests) and run in parallel
   until behavioral parity is confirmed via manual review checklist (G4-10).
   Only archive after parity is proven.

### 8.2 Decomposition Map

| Submodule | Functions (moved from channel_listener) | LOC Est. | Responsibility |
|-----------|-----------------------------------------|----------|----------------|
| `scripts/channel/router.py` | `_normalise_command`, `_COMMAND_ALIASES`, `_HANDLERS` dict, command dispatch logic from `process_message()` | ~200 | Command parsing, alias resolution, handler lookup |
| `scripts/channel/handlers.py` | `cmd_status`, `cmd_alerts`, `cmd_tasks`, `cmd_quick`, `cmd_domain`, `cmd_goals`, `cmd_diff`, `cmd_items_add`, `cmd_items_done`, `cmd_remember`, `cmd_cost`, `cmd_power`, `cmd_relationships`, `cmd_dashboard`, `cmd_help`, `cmd_unlock`, `cmd_queue`, `cmd_approve`, `cmd_reject`, `cmd_undo` | ~1100 | All command implementations |
| `scripts/channel/catchup.py` | `cmd_catchup`, `_run_pipeline`, `_get_last_catchup_iso`, `_gather_all_context`, `_read_briefing_template`, `_save_briefing` | ~250 | Full catch-up orchestration via channel |
| `scripts/channel/llm_bridge.py` | `_detect_llm_cli`, `_detect_all_llm_clis`, `_call_single_llm`, `_ask_llm`, `_ask_llm_ensemble`, `cmd_ask`, `_vault_relock_if_needed`, `_detect_domains`, `_gather_context` | ~400 | LLM CLI abstraction, failover, ensemble |
| `scripts/channel/security.py` | `_MessageDeduplicator`, `_RateLimiter`, `_SessionTokenStore`, `_requires_session`, sender whitelist logic, scope filter, PII redaction gate | ~250 | 9-layer security pipeline |
| `scripts/channel/formatters.py` | `_clean_for_telegram`, `_trim_to_cap`, `_split_message`, `_truncate`, `_extract_section_summaries`, `_strip_frontmatter`, `_filter_noise_bullets`, `_is_noise_section` | ~250 | Telegram-specific cleanup, budget trimming |
| `scripts/channel/state_readers.py` | `_read_state_file`, `_format_age`, `_get_latest_briefing_path`, `_apply_scope_filter`, `_get_domain_open_items`, `_domain_freshness`, `_READABLE_STATE_FILES`, `_DOMAIN_TO_STATE_FILE` | ~300 | Read-only state file access (whitelist-enforced) |
| `scripts/channel/stage.py` | `cmd_stage`, `cmd_radar`, `cmd_radar_try`, `cmd_radar_skip` | ~300 | Content stage and AI radar commands |

`channel_listener.py` retains: `process_message()` (delegating to router →
security → handler), `run_listener()`, `poll_with_resilience()`,
`health_check_all()`, `main()`, `_acquire_singleton_lock()`,
`_release_singleton_lock()`, `_audit_log()`, `_handle_callback_query()`,
`_parse_age_to_hours()`, `verify_listener_host()` — ~500 LOC.

### 8.3 Implementation Sequence

```
Step 4a: Create scripts/channel/__init__.py (empty)
         → Existing tests still importable from channel_listener

Step 4b: Extract formatters.py (pure functions, zero deps on other channel code)
         → Test: import channel.formatters succeeds

Step 4c: Extract state_readers.py (pure read functions, depends only on pathlib)
         → Test: import channel.state_readers succeeds

Step 4d: Extract security.py (3 classes + helpers, no external deps)
         → Test: import channel.security succeeds

Step 4e: Extract llm_bridge.py (subprocess wrappers, depends on security for vault relock)
         → Test: import channel.llm_bridge succeeds

Step 4f: Extract router.py (command aliases + dispatch)
         → Test: import channel.router succeeds

Step 4g: Extract handlers.py (command implementations, depends on state_readers + formatters)
         → Test: import channel.handlers succeeds

Step 4h: Extract catchup.py and stage.py (specialized command groups)
         → Test: import succeeds

Step 4i: Slim channel_listener.py to entry-point facade (~500 LOC)
         Import from submodules in process_message() and run_listener()
         → Test: all channel operations work via Telegram

Step 4j: Write new test suites for each submodule

Step 4k: Run old + new tests in parallel. Manually verify every scenario
         in test_channel_listener.py has an equivalent in the new suites.
         Only after parity is confirmed (G4-10): archive to tests/archive/
```

### 8.4 Tests

| # | Test file | Tests | Covers |
|---|-----------|-------|--------|
| T4-1..10 | `tests/unit/test_channel_formatters.py` | 10 | `_clean_for_telegram` edge cases, `_trim_to_cap` boundary, `_split_message` at exact limit, `_extract_section_summaries` budget, `_strip_frontmatter` with/without frontmatter, `_filter_noise_bullets` |
| T4-11..20 | `tests/unit/test_channel_state_readers.py` | 10 | `_read_state_file` for each whitelisted file, missing file, encrypted file rejection, `_format_age` formatting, scope filter, `_get_domain_open_items` |
| T4-21..32 | `tests/unit/test_channel_security.py` | 12 | `_MessageDeduplicator` dedup within window + accept after window, `_RateLimiter` under/over limit + cooldown, `_SessionTokenStore` create/verify/expire/revoke, `_requires_session` for critical domains |
| T4-33..42 | `tests/unit/test_channel_llm_bridge.py` | 10 | `_detect_llm_cli` fallback chain (mock `shutil.which`), `_detect_domains` keyword matching, `_gather_context` budget cap, `_ask_llm` with mock subprocess, ensemble voting logic, vault relock |
| T4-43..52 | `tests/unit/test_channel_router.py` | 10 | `_normalise_command` alias resolution, unknown command, command with args, case insensitivity, handler lookup for all command families |
| T4-53..67 | `tests/unit/test_channel_handlers.py` | 15 | `cmd_status` happy path, `cmd_items_add` with valid input, `cmd_items_done` existing item, `cmd_remember` rate limit, `cmd_approve` valid ID, `cmd_reject`, `cmd_undo`, `cmd_cost`, `cmd_help` output structure |
| T4-68..75 | `tests/unit/test_channel_catchup.py` | 8 | `cmd_catchup` delegates to pipeline, `_save_briefing` writes file, `_get_last_catchup_iso` parsing, `_gather_all_context` budget |
| T4-76..80 | `tests/unit/test_channel_stage.py` | 5 | `cmd_stage` list/preview/approve, `cmd_radar` output format, `cmd_radar_try` marks signal |

Total new: ~80 tests

### 8.5 Phase Gate

| # | Check | Command | Pass criteria |
|---|-------|---------|---------------|
| G4-1 | Full suite passes | `python -m pytest tests/ -q --tb=short` | ≥ 2967 tests (baseline + Phases 1-3), 0 failures |
| G4-2 | New channel tests pass | `python -m pytest tests/unit/test_channel_*.py -v` | 80 pass |
| G4-3 | `channel_listener.py` is ≤ 600 LOC | `wc -l scripts/channel_listener.py` | ≤ 600 |
| G4-4 | CLI entry point works | `python scripts/channel_listener.py --help` | Exit 0 |
| G4-5 | Telegram smoke test | Send `/status` via Telegram | Response received |
| G4-6 | No import cycles | `python -c "from channel import router, handlers, llm_bridge, security, formatters, state_readers"` | No error |
| G4-7 | Performance within bounds | `time python -c "import channel_listener"` | Import time ≤ 200% of Phase 0 baseline |
| G4-8 | Cross-machine sync works | On second machine after sync: `python scripts/channel_listener.py --help` | Exit 0 |
| G4-9 | Old tests still pass | `python -m pytest tests/unit/test_channel_listener.py -v` | 24 pass (retained until G4-10 confirmed) |
| G4-10 | Behavioral parity | Every scenario in old 24 tests has equivalent in new 80 tests | Manual review checklist completed |

### 8.6 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Circular imports between channel submodules | Medium | High (import crash) | Extract pure functions first (formatters, state_readers), then stateful modules. Follow DAG: formatters ← state_readers ← security ← handlers ← router |
| `process_message()` delegates incorrectly after restructure | Low | High (commands fail silently) | Telegram smoke test in gate G4-5; comprehensive handler tests |
| `_audit_log()` calls from submodules fail if audit path is wrong | Low | Medium | Pass `_audit_log` as a callback or import from channel_listener entrypoint |
| Old `test_channel_listener.py` tests covered behavior not replicated in new tests | Medium | Medium | **Run old + new in parallel until parity review (G4-10) is complete.** Only archive after every old scenario has a new equivalent. |

### 8.7 Rollback

```bash
git checkout pre-pay-debt-v1 -- scripts/channel_listener.py
rm -rf scripts/channel/
rm tests/unit/test_channel_formatters.py tests/unit/test_channel_state_readers.py \
   tests/unit/test_channel_security.py tests/unit/test_channel_llm_bridge.py \
   tests/unit/test_channel_router.py tests/unit/test_channel_handlers.py \
   tests/unit/test_channel_catchup.py tests/unit/test_channel_stage.py
# Old test_channel_listener.py was retained in-place — no restore needed.
```

---

## 9. Phase 5 — Preflight Restructure

**Target:** TD-1 (preflight.py — 1,848 LOC, 30 check functions)

**Risk:** LOW-MEDIUM — preflight is a pure check-and-report module with
well-defined contracts (each check returns a `CheckResult` dataclass).

### 9.1 Decomposition Map

| Submodule | Functions | LOC Est. | Responsibility |
|-----------|-----------|----------|----------------|
| `scripts/preflight/__init__.py` | `CheckResult`, `run_preflight`, `format_results`, `main` | ~300 | Orchestration: discover checks, run, format report |
| `scripts/preflight/vault_checks.py` | `check_keyring_backend`, `check_vault_health`, `check_vault_lock` | ~200 | Encryption infrastructure |
| `scripts/preflight/oauth_checks.py` | `check_oauth_token`, `check_token_freshness`, `check_msgraph_token` | ~250 | OAuth token validation + proactive refresh |
| `scripts/preflight/api_checks.py` | `check_script_health`, `check_pii_guard` | ~150 | API connectivity + PII guard |
| `scripts/preflight/state_checks.py` | `check_state_directory`, `check_state_templates`, `check_open_items`, `check_briefings_directory`, `check_profile_completeness`, `_is_bootstrap_stub` | ~250 | State file population and templates |
| `scripts/preflight/integration_checks.py` | `check_bridge_health`, `check_workiq`, `check_ado_auth`, `check_ha_connectivity`, `check_dep_freshness`, `check_channel_config`, `check_channel_health`, `check_action_handlers` | ~550 | Third-party and integration health |

### 9.2 Implementation Sequence

```
Step 5a: Create scripts/preflight/ package with __init__.py
         Move CheckResult dataclass + run_preflight + format_results + main
         → Test: python -m preflight (CLI still works)

         CRITICAL: This must be an ATOMIC single commit:
         1. Create scripts/preflight/ package with __init__.py exporting
            run_preflight, CheckResult, format_results, main
         2. Delete scripts/preflight.py (cannot coexist with preflight/ dir)
         3. Update config/Artha.core.md Step 0: python3 scripts/preflight.py
            → python3 -m preflight
         4. Update scripts/mcp_server.py:431 — the lazy import
            `from preflight import run_preflight` will resolve to the
            package automatically, but VERIFY with:
            python -c "from preflight import run_preflight; print('ok')"
         All four changes in ONE commit. No intermediate state.

Step 5b–5f: Extract check functions into category modules
         Each step: move functions, update imports in __init__.py
         → Test after each: all preflight-related tests pass
         → Test after each: mcp_server preflight tool still works

Step 5g: Write new focused tests per category

Step 5h: (No shim removal needed — handled atomically in 5a)
```

**Note on Python import mechanics (R5-1):** A `preflight.py` file and a
`preflight/` directory CANNOT coexist as importable modules — Python will
prefer one over the other unpredictably. That is why Step 5a is atomic:
delete the file and create the package in the same commit.

### 9.3 Tests

**Existing tests preserved:** 31 tests in `test_preflight_advisory.py` — will
need import path updates.

**New focused tests:**

| # | Test file | Tests | Covers |
|---|-----------|-------|--------|
| T5-1..6 | `tests/unit/test_preflight_vault.py` | 6 | Keyring backend detection, vault health pass/fail, stale lock auto-clear, active lock detection |
| T5-7..14 | `tests/unit/test_preflight_oauth.py` | 8 | Token presence, token freshness, msgraph 90-day cliff, proactive refresh mock, expired token detection |
| T5-15..20 | `tests/unit/test_preflight_api.py` | 6 | Script health with passing/failing script, PII guard py variant, PII guard sh fallback, state dir writable/read-only |
| T5-21..28 | `tests/unit/test_preflight_state.py` | 8 | Bootstrap stub detection, template population with --fix, open_items auto-create, profile completeness heuristic, briefings dir creation |
| T5-29..40 | `tests/unit/test_preflight_integration.py` | 12 | Bridge health (key present/missing), WorkIQ cache hit/miss, ADO auth valid/expired, HA connectivity LAN gate, channel config validation, action handler health |

Total new: ~40 tests

### 9.4 Phase Gate

| Check | Command | Pass criteria |
|-------|---------|---------------|
| Full suite passes | `python -m pytest tests/ -q --tb=short` | ≥ 3047 tests (baseline + Phases 1-4), 0 failures |
| New preflight tests pass | `python -m pytest tests/unit/test_preflight_*.py -v` | 40 pass |
| CLI works | `python -m preflight` or `python scripts/preflight/ --help` | Same output as before |
| Existing advisory tests pass (updated imports) | `python -m pytest tests/unit/test_preflight_advisory.py -v` | 31 pass |
| MCP server preflight tool works | `python -c "from preflight import run_preflight; print('ok')"` | Prints 'ok', no ImportError |
| Cold-start exit code | Run preflight without user_profile.yaml | Exit 3 |
| Cross-machine sync works | On second machine after sync: `python -m preflight` | Same output |

### 9.5 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| R5-1: `preflight.py` vs `preflight/` import conflict | Certain (Python limitation) | High (import fails) | Atomic rename in Step 5a — delete file and create package in one commit; never have both simultaneously |
| R5-2: `Artha.core.md` Step 0 references `python3 scripts/preflight.py` | Certain | Medium (workflow breaks) | Update Artha.core.md command in same atomic commit (Step 5a) |
| R5-3: Workshop/fork users have custom preflight calls | Very low | Low | Document breaking change in CHANGELOG |
| R5-4: `mcp_server.py:431` does `from preflight import run_preflight` | Certain | High (MCP server tool breaks) | Verified: lazy import resolves to package `__init__.py` automatically. Explicit verification in Step 5a. |

### 9.6 Rollback

```bash
git checkout pre-pay-debt-v1 -- scripts/preflight.py
rm -rf scripts/preflight/
# Restore Artha.core.md and mcp_server.py if modified:
git checkout pre-pay-debt-v1 -- config/Artha.core.md scripts/mcp_server.py
```

---

## 10. Phase 6 — Registry Consolidation

**Target:** TD-3 (hardcoded registries in 4 modules)

**Risk:** LOW — the registries are static data. No behavioral change.

### 10.1 Design Decision: Derive from Existing YAML + Security Allowlist

Artha already has YAML registries (D9, D10):
- `config/connectors.yaml` — connector definitions (used by pipeline.py)
- `config/actions.yaml` — action handler definitions (used by action_executor.py)
- `config/skills.yaml` — skill registration (used by skill_runner.py)

The `_HANDLER_MAP` dicts in Python are **redundant copies** of what's in YAML.
The fix: derive the Python maps from YAML at startup, validated against a
security allowlist.

**Why not move everything to YAML:** `_SIGNAL_ROUTING` (47 entries) and
`_COMMAND_ALIASES` (68 entries) are tightly coupled to Python function
references and code logic. Moving them to YAML would require a mapping layer
that adds complexity without adding value for a single-user system. These
stay in Python but get consolidated.

### 10.2 Changes

#### 10.2.1 Pipeline Handler Map → Derived from connectors.yaml

```python
# pipeline.py — replace _HANDLER_MAP with:

# Frozen fallback — used if connectors.yaml is unreadable.
# Kept in sync with connectors.yaml; last updated 2026-03-26.
_FALLBACK_HANDLER_MAP: Final[dict[str, str]] = {
    "connectors.google_email": "connectors.google_email",
    "connectors.google_calendar": "connectors.google_calendar",
    # ... (current 18 entries frozen as safety net)
}

_ALLOWED_MODULES: frozenset[str] = frozenset({
    "connectors.google_email", "connectors.google_calendar",
    # ... (security allowlist — exhaustive, maintained in code)
})

def _derive_handler_map(config: dict) -> dict[str, str]:
    """Build handler map from connectors.yaml, validated against allowlist.

    Each connector entry may specify a 'module' field; if absent, defaults
    to 'connectors.<connector_name>'.

    Falls back to _FALLBACK_HANDLER_MAP on YAML parse failure —
    fail-degraded, not fail-dead.
    """
    result = {}
    for name, cfg in config.items():
        if not cfg.get("enabled", True):
            continue
        module = cfg.get("module", f"connectors.{name}")
        if module not in _ALLOWED_MODULES:
            print(f"[SECURITY] module {module} not in allowlist, skipping {name}",
                  file=sys.stderr)
            continue
        result[name] = module
    return result
```

**Fallback strategy (R3):** If `connectors.yaml` is malformed or unreadable,
`_derive_handler_map` catches the error and falls back to
`_FALLBACK_HANDLER_MAP` with a loud stderr warning:
`"[CRITICAL] connectors.yaml unreadable — using frozen fallback. Fix YAML and re-run."`
This is **fail-degraded** (all known connectors work via the frozen snapshot)
not **fail-dead** (empty dict = zero connectors = no catch-up). The fallback
map must be updated whenever a connector is added or removed.

**Adding a new connector post-Phase 6:** Edit `connectors.yaml` (primary) +
add module to `_ALLOWED_MODULES` (security) + optionally update
`_FALLBACK_HANDLER_MAP` (safety). Two mandatory edits instead of three.

#### 10.2.2 Action Executor Handler Map → Derived from actions.yaml

Same pattern as 10.2.1 — derive from `config/actions.yaml`, validate
against `_ALLOWED_ACTION_MODULES` frozenset.

#### 10.2.3 Signal Routing + Command Aliases → Consolidate Within Modules

`_SIGNAL_ROUTING` stays in `action_composer.py` — it's domain logic, not
configuration. But add a `_validate_routing_table()` function called once
at import time that checks every `action_type` in the routing table exists
in `_ALLOWED_ACTION_MODULES`. This catches typos at startup rather than
at signal-fire time.

`_COMMAND_ALIASES` stays in the channel router (after Phase 4 decomposition,
it lives in `scripts/channel/router.py`). No change beyond the move.

### 10.3 Tests

| # | Test | Covers |
|---|------|--------|
| T6-1 | `_derive_handler_map` with full connectors.yaml — returns expected set | Derivation |
| T6-2 | `_derive_handler_map` skips disabled connectors | Filtering |
| T6-3 | `_derive_handler_map` rejects module not in allowlist | Security |
| T6-4 | `_derive_handler_map` defaults module to `connectors.<name>` | Convention |
| T6-5 | `_validate_routing_table` raises on unknown action_type | Early detection |
| T6-6 | `_validate_routing_table` passes with valid table | Happy path |
| T6-7 | Action executor handler map derived from actions.yaml | Derivation |
| T6-8 | Malformed YAML falls back to `_FALLBACK_HANDLER_MAP` (not empty dict) | Resilience |
| T6-9 | Fallback emits `[CRITICAL]` warning to stderr | Visibility |

File: `tests/unit/test_registry_consolidation.py` (~9 tests)

### 10.4 Phase Gate

| Check | Command | Pass criteria |
|-------|---------|---------------|
| Full suite passes | `python -m pytest tests/ -q --tb=short` | ≥ 3087 tests (baseline + Phases 1-5), 0 failures |
| Registry tests pass | `python -m pytest tests/unit/test_registry_consolidation.py -v` | 9 pass |
| Pipeline health check | `python scripts/pipeline.py --health` | All connectors discovered |
| No legacy `_HANDLER_MAP` declarations remain | `grep -n '^_HANDLER_MAP:' scripts/pipeline.py scripts/action_executor.py` | Zero results (`_FALLBACK_HANDLER_MAP` allowed) |
| Allowlist still enforced | Add a fake module to connectors.yaml, run pipeline | "[SECURITY]" message on stderr, fake module not loaded |

### 10.5 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Malformed connectors.yaml at startup blocks catch-up | Low (file is version-controlled) | High | `_derive_handler_map` catches YAML errors and **falls back to `_FALLBACK_HANDLER_MAP`** (frozen snapshot of current 18 connectors) with `[CRITICAL]` stderr warning. This is fail-degraded, not fail-dead. Preflight check validates YAML structure. |
| YAML module field injected with malicious path | Very low (single-user system, file is local) | High | `_ALLOWED_MODULES` frozenset is the security gate — YAML cannot override it |

---

## 11. Phase 7 — Hardening: State Schemas, PII Guard, Concurrency

**Targets:** TD-7 (state schema drift), TD-8 (subprocess PII guard)

**Risk:** LOW — additive schema validation; direct function call replaces
subprocess call.

### 11.1 TD-7: Lightweight State File Schema Validation

**Design decision:** Not a full migration framework. Artha's state files are
LLM-readable Markdown with YAML frontmatter (N1). A heavy schema validation
system would fight the format. Instead: a `_validate_frontmatter()` utility
that checks required fields exist.

#### New: `scripts/lib/state_schema.py` (~80 LOC)

```python
"""Lightweight YAML frontmatter validator for state files.

Does NOT enforce types or formats — the LLM handles flexible parsing.
Only checks that required fields exist, preventing silent data loss
when a state writer accidentally omits a critical field.
"""

# Schema registry: {filename: {required_fields: [...], version: N}}
_SCHEMAS: dict[str, dict] = {
    "health-check.md": {
        "required": ["schema_version", "domain", "last_updated"],
        "version": 1,
    },
    "goals.md": {
        "required": ["schema_version", "domain", "last_updated"],
        "version": 1,
    },
    "open_items.md": {
        "required": ["schema_version", "domain"],
        "version": 1,
    },
    # Additional schemas added incrementally
}

def validate_frontmatter(filename: str, frontmatter: dict) -> list[str]:
    """Return list of missing required fields. Empty list = valid."""
    schema = _SCHEMAS.get(filename)
    if schema is None:
        return []  # No schema = no validation (conservative)
    return [f for f in schema["required"] if f not in frontmatter]
```

Integration: Call from `WriteGuardMiddleware.before_write()` as an additional
check — if required fields are missing from the proposed content, block the
write (return `None`).

### 11.2 TD-8: PII Guard In-Process Call

Replace the subprocess invocation in `action_executor.py` with a direct
Python call:

```python
# Before (subprocess):
result = subprocess.run(
    [sys.executable, "scripts/pii_guard.py", "--check", text],
    capture_output=True, text=True, timeout=10
)

# After (in-process):
from pii_guard import filter_text
cleaned = filter_text(text)
```

**Constraint:** The subprocess call in `preflight.py` (which tests that
`pii_guard.py` is executable as a CLI tool) stays as-is — that check
validates the CLI entry point itself.

**Expected impact:** ~100ms saved per action proposal (×2 calls per action).

### 11.3 Tests

| # | Test | Covers |
|---|------|--------|
| T7-1 | `validate_frontmatter("health-check.md", valid_fm)` returns empty list | Happy path |
| T7-2 | `validate_frontmatter("health-check.md", {})` returns all required fields | Missing fields |
| T7-3 | `validate_frontmatter("unknown.md", any_fm)` returns empty list | Unknown schema = no validation |
| T7-4 | `validate_frontmatter` with partial fields returns only missing ones | Partial compliance |
| T7-5 | WriteGuardMiddleware blocks write when required fields missing | Integration |
| T7-6 | WriteGuardMiddleware allows write when all required fields present | Integration happy path |
| T7-7 | `pii_guard.filter_text()` called in-process from action_executor | In-process PII |
| T7-8 | In-process PII check catches PII in action parameters | Detection |
| T7-9 | In-process PII check passes clean text | False positive check |
| T7-10 | `filter_text()` handles empty string without error | Edge case |

File: `tests/unit/test_state_schema.py` + `tests/unit/test_pii_inprocess.py` (~10 tests)

### 11.4 Phase Gate

| Check | Command | Pass criteria |
|-------|---------|---------------|
| Full suite passes | `python -m pytest tests/ -q --tb=short` | ≥ 3096 tests (baseline + Phases 1-6), 0 failures |
| Schema tests pass | `python -m pytest tests/unit/test_state_schema.py -v` | 6 pass |
| PII tests pass | `python -m pytest tests/unit/test_pii_inprocess.py -v` | 4 pass |
| Action execution still works | Propose + approve a test action | Succeeds without subprocess PII call |

### 11.5 Rollback

```bash
rm scripts/lib/state_schema.py tests/unit/test_state_schema.py tests/unit/test_pii_inprocess.py
git checkout HEAD -- scripts/action_executor.py scripts/middleware/write_guard.py
```

---

## 12. Cross-Phase Risks & Mitigations

### 12.1 Cascading Regression

| Risk | Phase | Mitigation |
|------|-------|------------|
| Phase 3 facade breaks imports for Phase 4 | 3→4 | Phase 3 re-exports preserve all public names; Phase 4 doesn't import from work_reader |
| Phase 4 channel restructure breaks Phase 5 preflight | 4→5 | Preflight doesn't import from channel_listener. No coupling. |
| Phase 5 preflight rename breaks Artha.core.md workflow | 5 | Update Artha.core.md in same atomic commit as rename (Step 5a) |
| Phase 5 preflight rename breaks MCP server | 5 | `mcp_server.py:431` does `from preflight import run_preflight` — lazy import resolves to package `__init__.py` automatically. Verified in Step 5a gate. |

### 12.2 Cloud Sync Contention

| Risk | Applies to | Mitigation |
|------|-----------|------------|
| OneDrive locks a file during rename | Phase 3, 4, 5 | Perform file moves with `git mv` which is atomic. If sync delays, pause and retry. |
| New subdirectory not synced | Phase 3, 4, 5 | OneDrive syncs all non-ignored files. `scripts/work/`, `scripts/channel/`, `scripts/preflight/` will sync automatically. |
| Cross-machine import failure during sync window | Phase 3, 4, 5 | OneDrive syncs files individually, not atomically. There is a window where `work_reader.py` is the slim facade but `work/helpers.py` hasn't synced yet. **Mitigation:** Each phase gate includes a cross-machine verification step. Do not consider a phase complete until the second machine passes the smoke test. |

### 12.3 Test Infrastructure Brittleness

| Risk | Applies to | Mitigation |
|------|-----------|------------|
| `conftest.py` sys.path setup doesn't find new subpackages | Phase 3, 4, 5 | `conftest.py` adds `scripts/` to sys.path — subpackages under `scripts/` are importable automatically |
| Test fixtures assume single-file module layout | Phase 3 | Strangler facade means fixtures don't change. New tests create own fixtures. |
| Dual-import trap (`from work.X` vs `from scripts.work.X`) | Phase 3, 4, 5 | Convention established in §3.2: always use bare imports via SCRIPTS_DIR. New test files must follow this. Add comment to `tests/conftest.py`. |

### 12.4 Performance Regression

| Risk | Applies to | Mitigation |
|------|-----------|------------|
| Import time increases due to more module files | Phase 3, 4, 5 | Python caches imports after first load. Re-exports are near-zero cost. Verify with `time python -c "import work_reader"` before and after. |
| Structured logging adds I/O per catch-up | Phase 2 | JSONL append is ~0.1ms per event. Even 100 events = 10ms. Negligible vs. 30s+ catch-up. |

---

## 13. Deferred Items (Out of Scope)

| Item | Reason for deferral | Future spec? |
|------|-------------------|--------------|
| TD-6: Mixed concurrency unification | Not causing production issues; risk-reward wrong for a system-wide concurrency refactor | Yes — when Artha moves to async-first pipeline |
| TD-9: E2E test expansion | Requires test infrastructure (mocked APIs, fixture pipelines) that deserves its own spec | Yes — separate testing spec |
| TD-13: 183 `sys.exit()`/`die()` calls | Pervasive; no single fix. Would require exception-based error propagation redesign. | No — acceptable for CLI-first system |
| `narrative_engine.py` decomposition | 1,722 LOC single class, but well-structured (one class, clear methods). Not causing pain. | Only if it grows further |
| LLM Provider abstraction | Replacing subprocess CLI calls with a `LLMProvider` protocol is architecturally clean but premature — CLI tools change faster than Python APIs | When Anthropic/Google provide stable Python SDKs |
| TD-12: Cost tracking accuracy | Adding a `confidence: low` label is cosmetic; real fix requires parsing provider-specific CLI stdout for token counts. Deferred until LLM Provider abstraction exists. | With LLM Provider abstraction |

---

## 14. Rollback Strategy (Global)

Every phase has a specific rollback section. The global rollback for any
phase is:

```bash
# 1. Identify which phases were completed
# 2. For the failed phase, run its rollback commands
# 3. Run full test suite to verify:
python -m pytest tests/ -q --tb=short
# Must show ≥ baseline count from tmp/debt_baseline.json
```

**The Git tag `pre-pay-debt-v1` is the ultimate safety net.** If everything
goes wrong:

```bash
git checkout pre-pay-debt-v1
# Artha is exactly where it was before this spec
```

---

## 15. Success Criteria

| Criterion | Measurement | Target |
|-----------|------------|--------|
| Zero regression | All baseline tests pass after every phase | 2886+ tests, 0 failures |
| God files eliminated | `wc -l` on channel_listener.py, work_reader.py, preflight.py | Each ≤ 600 LOC |
| Work OS robust | All 284 existing work tests pass | 0 failures |
| New test coverage | New focused tests across decomposed modules | ≥ 220 new tests |
| Observability | Structured JSONL telemetry operational | log file created per catch-up with `trace_id` and numeric metric fields |
| Registry duplication eliminated | No legacy `_HANDLER_MAP` declarations in pipeline.py or action_executor.py | `grep '^_HANDLER_MAP:'` returns no results; `_FALLBACK_HANDLER_MAP` is allowed |
| Audit trail reliable | `AuditMiddleware` logs failures to stderr | Verified by test T1-7 |
| PII guard faster | In-process call replaces subprocess | ~200ms saved per action |
| State schema guard | WriteGuardMiddleware validates required fields | Verified by test T7-5 |

---

## 16. Implementation Order Summary

```
Phase 0: Safety baseline       →  Tag + backup + test count
Phase 1: Foundation hardening  → ~50 LOC changed, 10 new tests (9 + 1 boundary)
Phase 2: Observability layer   → ~200 LOC new, 12 new tests
Phase 3: Work OS restructure   →  0 new LOC, 59 new tests, 284 preserved  ★
Phase 4: Channel listener      →  0 new LOC, 80 new tests
Phase 5: Preflight             →  0 new LOC, 40 new tests
Phase 6: Registry consolidation → ~80 LOC changed, 9 new tests
Phase 7: State schemas + PII   → ~100 LOC changed, 10 new tests
                                 ─────────────────────────
                                 ~430 LOC changed/new
                                 ~220 new tests
                                 284 preserved work tests
                                 All 2886 baseline tests passing
```

**Estimated total test count after all phases:** ~3,106+ (2,886 baseline +
220 new)

---

## Appendix A: File Impact Matrix

| File | Phase | Change type |
|------|-------|-------------|
| `scripts/foundation.py` | 1 | Add `get_config()` (additive) |
| `scripts/lib/common.py` | 1 | Replace function body with deprecated re-export |
| `scripts/middleware/audit_middleware.py` | 1, 2 | stderr warning + structured log |
| `scripts/health_check_updater.py` | 1 | **New** — moved from lib/common.py |
| `scripts/channel_push.py` | 1 | Update import path (1 line) |
| `scripts/preflight.py` → `scripts/preflight/` | 1, 5 | Import update (Phase 1), restructure (Phase 5) |
| `scripts/mcp_server.py` | 5 | Verify lazy import resolves to preflight package |
| `scripts/lib/logger.py` | 2 | **New** — structured JSONL logger |
| `scripts/pipeline.py` | 2, 6 | Add log calls (Phase 2), derive handler map (Phase 6) |
| `scripts/action_executor.py` | 2, 6, 7 | Add log calls (P2), derive handler map (P6), in-process PII (P7) |
| `scripts/cost_tracker.py` | — | No changes (TD-12 deferred) |
| `scripts/work_reader.py` | 3 | Slim to ~200 LOC facade + main() |
| `scripts/work/__init__.py` | 3 | **New** — package init |
| `scripts/work/helpers.py` | 3 | **New** — shared utilities |
| `scripts/work/briefing.py` | 3 | **New** — briefing commands |
| `scripts/work/meetings.py` | 3 | **New** — meeting parsing |
| `scripts/work/health.py` | 3 | **New** — connector health |
| `scripts/work/decisions.py` | 3 | **New** — decision capture |
| `scripts/work/discovery.py` | 3 | **New** — people/docs/sources |
| `scripts/work/career.py` | 3 | **New** — review cycle/promo |
| `scripts/work/narrative.py` | 3 | **New** — NE delegation wrappers |
| `scripts/narrative_engine.py` | 3 | Remove 6 duplicate helpers, import from work.helpers |
| `scripts/channel_listener.py` | 4 | Slim to ~500 LOC entry point |
| `scripts/channel/__init__.py` | 4 | **New** — package init |
| `scripts/channel/router.py` | 4 | **New** — command dispatch |
| `scripts/channel/handlers.py` | 4 | **New** — command implementations |
| `scripts/channel/catchup.py` | 4 | **New** — catch-up orchestration |
| `scripts/channel/llm_bridge.py` | 4 | **New** — LLM CLI abstraction |
| `scripts/channel/security.py` | 4 | **New** — 9-layer security |
| `scripts/channel/formatters.py` | 4 | **New** — Telegram cleanup |
| `scripts/channel/state_readers.py` | 4 | **New** — read-only state access |
| `scripts/channel/stage.py` | 4 | **New** — content stage + radar |
| `scripts/preflight/__init__.py` | 5 | **New** — orchestrator |
| `scripts/preflight/vault_checks.py` | 5 | **New** |
| `scripts/preflight/oauth_checks.py` | 5 | **New** |
| `scripts/preflight/api_checks.py` | 5 | **New** |
| `scripts/preflight/state_checks.py` | 5 | **New** |
| `scripts/preflight/integration_checks.py` | 5 | **New** |
| `scripts/action_composer.py` | 6 | Add `_validate_routing_table()` |
| `scripts/lib/state_schema.py` | 7 | **New** — frontmatter validator |
| `scripts/middleware/write_guard.py` | 7 | Integrate schema validation |
| `scripts/pii_guard.py` | 7 | No change (already has `filter_text()`) |
| `config/Artha.core.md` | 5 | Update Step 0 command path |
| `pyproject.toml` | 3, 4, 5 | Add new packages to `[tool.setuptools]` |

## Appendix B: Test File Matrix

| Test file | Phase | Tests | Type |
|-----------|-------|-------|------|
| `tests/unit/test_foundation_hardening.py` | 1 | 9 | New |
| `tests/unit/test_architectural_boundaries.py` | 1 | 1 | New |
| `tests/unit/test_structured_logger.py` | 2 | 12 | New |
| `tests/work/test_work_helpers.py` | 3 | 5 | New |
| `tests/work/test_work_briefing.py` | 3 | 7 | New |
| `tests/work/test_work_meetings.py` | 3 | 10 | New |
| `tests/work/test_work_health.py` | 3 | 6 | New |
| `tests/work/test_work_decisions.py` | 3 | 6 | New |
| `tests/work/test_work_discovery.py` | 3 | 10 | New |
| `tests/work/test_work_career.py` | 3 | 8 | New |
| `tests/work/test_work_narrative.py` | 3 | 6 | New |
| `tests/work/test_work_reader_contract.py` | 3 | 1 | New |
| `tests/work/test_work_reader.py` | 3 | 225 | Preserved (facade) |
| `tests/work/test_phase4_phase5.py` | 3 | 59 | Preserved (facade) |
| `tests/unit/test_channel_formatters.py` | 4 | 10 | New |
| `tests/unit/test_channel_state_readers.py` | 4 | 10 | New |
| `tests/unit/test_channel_security.py` | 4 | 12 | New |
| `tests/unit/test_channel_llm_bridge.py` | 4 | 10 | New |
| `tests/unit/test_channel_router.py` | 4 | 10 | New |
| `tests/unit/test_channel_handlers.py` | 4 | 15 | New |
| `tests/unit/test_channel_catchup.py` | 4 | 8 | New |
| `tests/unit/test_channel_stage.py` | 4 | 5 | New |
| `tests/unit/test_channel_listener.py` | 4 | 24 | Retained (parallel until parity G4-10) |
| `tests/unit/test_preflight_vault.py` | 5 | 6 | New |
| `tests/unit/test_preflight_oauth.py` | 5 | 8 | New |
| `tests/unit/test_preflight_api.py` | 5 | 6 | New |
| `tests/unit/test_preflight_state.py` | 5 | 8 | New |
| `tests/unit/test_preflight_integration.py` | 5 | 12 | New |
| `tests/unit/test_preflight_advisory.py` | 5 | 31 | Updated imports |
| `tests/unit/test_registry_consolidation.py` | 6 | 9 | New |
| `tests/unit/test_state_schema.py` | 7 | 6 | New |
| `tests/unit/test_pii_inprocess.py` | 7 | 4 | New |
