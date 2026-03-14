---
schema_version: "1.0"
title: "Artha Supercharge Reloaded — Execution Playbook"
status: approved
author: "Principal Engineering Review"
created: 2026-03-13
last_updated: 2026-03-13
target_version: "6.0–7.0"
prerequisite: "supercharge.md (review + plan)"
---

# Artha Supercharge Reloaded — Execution Playbook

> This is the companion execution document to `supercharge.md`. Where supercharge.md
> analyzes and plans, this document executes — with detailed specifications for the
> unified pipeline, spec compaction, codebase cleanup, and README update.

---

## Table of Contents

1. [Unified Data Pipeline — The Ingestion Backbone](#1-unified-data-pipeline)
2. [Declarative Action Extension](#2-declarative-action-extension)
3. [MCP Integration — Adapter Pattern](#3-mcp-integration)
4. [Spec Compaction — Surgical Reduction](#4-spec-compaction)
5. [Codebase Cleanup — Archive Redundancy](#5-codebase-cleanup)
6. [README.md Overhaul](#6-readme-overhaul)
7. [Execution Order & Dependencies](#7-execution-order)
8. [Validation Gates](#8-validation-gates)

---

## 1. Unified Data Pipeline — The Ingestion Backbone

### 1.1 The Problem

Artha has 8 fetch scripts totaling ~4,500 lines. Despite a shared `lib/` existing,
each script still implements its own argparse, auth loading, health check, retry,
HTML stripping, footer removal, JSONL output, and main() boilerplate. The pipeline
(308 lines) and 8 connector handlers already exist alongside these scripts, operating
in a strangler-fig pattern.

**What this costs today:**
- ~1,400 lines of duplicated code across 8 scripts
- Adding a new email source requires writing a full Python script (~400 lines)
- Bug fixes must be applied to 8 files independently
- Inconsistent error codes, output formats, and health check patterns

### 1.2 Vision: One Pipeline, Many Connectors, Zero Boilerplate

```
┌────────────────────────────────────────────────────────────────────┐
│                    THE INGESTION PIPELINE                          │
│                                                                    │
│  config/connectors.yaml       config/user_profile.yaml            │
│  (what sources to use)        (auth credentials, calendars)       │
│         │                              │                          │
│         ▼                              ▼                          │
│  ┌──────────────────────────────────────────────────────┐        │
│  │              scripts/pipeline.py (308 lines)          │        │
│  │                                                       │        │
│  │  1. Parse connectors.yaml                            │        │
│  │  2. For each enabled connector:                       │        │
│  │     ├─ Load auth via lib/auth.py                     │        │
│  │     ├─ Run health_check() — skip if unhealthy        │        │
│  │     ├─ Call handler.fetch() with lib/retry wrapper    │        │
│  │     ├─ Pipe through lib/html_processing              │        │
│  │     └─ Emit via lib/output (standardized JSONL)      │        │
│  │  3. Log stats to health-check.md                     │        │
│  └──────────────┬────────────────────────────────────────┘        │
│                 │                                                  │
│  ┌──────────────▼────────────────────────────────────────┐        │
│  │           scripts/connectors/ (handler modules)        │        │
│  │                                                        │        │
│  │  google_email.py     (72 lines — Gmail API)           │        │
│  │  msgraph_email.py    (73 lines — MS Graph email)      │        │
│  │  imap_email.py       (79 lines — any IMAP server)     │        │
│  │  google_calendar.py  (82 lines — Google Calendar)     │        │
│  │  msgraph_calendar.py (85 lines — Outlook Calendar)    │        │
│  │  caldav_calendar.py  (101 lines — any CalDAV server)  │        │
│  │  canvas_lms.py       (via canvas_fetch — Canvas API)  │        │
│  │  onenote.py          (103 lines — OneNote API)        │        │
│  │                                                        │        │
│  │  Each: fetch(**kwargs) → Iterator[dict]               │        │
│  │        health_check(auth_context) → bool              │        │
│  └────────────────────────────────────────────────────────┘        │
│                                                                    │
│  ┌────────────────────────────────────────────────────────┐        │
│  │           scripts/lib/ (shared infrastructure)         │        │
│  │                                                        │        │
│  │  auth.py         — Unified token loader (237 lines)   │        │
│  │  retry.py        — Exponential backoff (170 lines)    │        │
│  │  html_processing — HTML strip + footers (177 lines)   │        │
│  │  output.py       — JSONL emitter (196 lines)          │        │
│  │  cli_base.py     — Canonical argparse (248 lines)     │        │
│  │  common.py       — Path constants (19 lines)          │        │
│  └────────────────────────────────────────────────────────┘        │
└────────────────────────────────────────────────────────────────────┘
```

### 1.3 connectors.yaml — The Declarative Spec

`connectors.yaml` is already implemented (234 lines). Each connector block declares:

```yaml
connector_name:
  type: email | calendar | lms | notes    # semantic type
  provider: google | microsoft | imap | caldav | canvas | onenote
  enabled: true | false
  auth:
    method: oauth2 | app_password | api_key
    token_file: ".tokens/..."             # for OAuth
    credential_key: "keyring-key-name"    # for app passwords
    setup_script: "scripts/setup_*.py"    # interactive auth setup
    scopes: ["scope1", "scope2"]          # OAuth scopes
  fetch:
    handler: "scripts/connectors/<name>.py"
    default_max_results: 200
    default_lookback: "7d"
    # handler-specific params:
    calendars_from_profile: "integrations.google_calendar.calendar_ids"
  output:
    format: jsonl
    fields: [id, subject, from, to, date_iso, body, ...]
    source_tag: "outlook"                 # empty = default
  health_check: true
  retry:
    max_retries: 3
    base_delay: 1.0
    max_delay: 30.0
```

### 1.4 Handler Protocol (Already Defined)

```python
# scripts/connectors/base.py
class ConnectorHandler(Protocol):
    def fetch(self, *, since: str, max_results: int,
              auth_context: dict, **kwargs) -> Iterator[dict]:
        """Yield standardized records."""
        ...
    def health_check(self, auth_context: dict) -> bool:
        """Return True if auth is valid and API is reachable."""
        ...
```

### 1.5 Verification Plan — Pipeline vs. Legacy Scripts

Before archiving any legacy script, output equivalence must be verified:

**Step 1 — Capture baseline:**
```bash
# Run legacy scripts
python scripts/gmail_fetch.py --since "2026-03-10" > /tmp/legacy-gmail.jsonl
python scripts/gcal_fetch.py --since "2026-03-10" > /tmp/legacy-gcal.jsonl
python scripts/msgraph_fetch.py --since "2026-03-10" > /tmp/legacy-outlook.jsonl
```

**Step 2 — Capture pipeline:**
```bash
python scripts/pipeline.py --since "2026-03-10" --source gmail > /tmp/pipe-gmail.jsonl
python scripts/pipeline.py --since "2026-03-10" --source google_calendar > /tmp/pipe-gcal.jsonl
python scripts/pipeline.py --since "2026-03-10" --source outlook_email > /tmp/pipe-outlook.jsonl
```

**Step 3 — Diff:**
```bash
# Sort both by ID field, then diff
jq -S '.' /tmp/legacy-gmail.jsonl | sort > /tmp/a.jsonl
jq -S '.' /tmp/pipe-gmail.jsonl | sort > /tmp/b.jsonl
diff /tmp/a.jsonl /tmp/b.jsonl
```

**Acceptance criteria:** Zero diff on content fields. Acceptable differences:
timestamp formatting (ISO-8601 variants), field ordering (pipeline normalizes),
source_tag presence.

**Step 4 — Parallel operation (2+ weeks):**
Run both pipeline AND legacy scripts during daily catch-ups. Compare briefing
quality. If briefings are equivalent or improved, proceed to archival.

### 1.6 Legacy Script Archival

After verification, move 8 scripts to `.archive/scripts/`:

| Script | Lines | Replacement |
|--------|-------|-------------|
| `gmail_fetch.py` | 436 | `connectors/google_email.py` (72) |
| `msgraph_fetch.py` | 702 | `connectors/msgraph_email.py` (73) |
| `icloud_mail_fetch.py` | 512 | `connectors/imap_email.py` (79) |
| `gcal_fetch.py` | 391 | `connectors/google_calendar.py` (82) |
| `msgraph_calendar_fetch.py` | 729 | `connectors/msgraph_calendar.py` (85) |
| `icloud_calendar_fetch.py` | 723 | `connectors/caldav_calendar.py` (101) |
| `canvas_fetch.py` | 364 | `connectors/canvas_lms.py` (via fetch) |
| `msgraph_onenote_fetch.py` | 802 | `connectors/onenote.py` (103) |
| **Total archived** | **4,659** | **Replaced by 595 lines in handlers** |

**Net reduction: ~4,064 lines of Python.**

### 1.7 Adding New Connectors — User Guide

#### Scenario A: New Email Source (e.g., Fastmail)
Just add YAML — reuse existing `imap_email.py` handler:
```yaml
fastmail:
  type: email
  provider: imap
  enabled: true
  auth:
    method: app_password
    credential_key: "artha-fastmail-password"
    server: "imap.fastmail.com"
    port: 993
  fetch:
    handler: "scripts/connectors/imap_email.py"
    default_max_results: 200
  output:
    format: jsonl
    source_tag: "fastmail"
```
Then: `python -c "import keyring; keyring.set_password('artha-fastmail-password', 'artha', 'your-app-password')"`

**Zero Python code required.** Works because IMAP is a standard protocol.

#### Scenario B: New Calendar (e.g., Nextcloud)
Reuse `caldav_calendar.py`:
```yaml
nextcloud_calendar:
  type: calendar
  provider: caldav
  enabled: true
  auth:
    method: app_password
    credential_key: "artha-nextcloud-password"
    server: "https://cloud.example.com/remote.php/dav"
  fetch:
    handler: "scripts/connectors/caldav_calendar.py"
```

#### Scenario C: Genuinely New Protocol (e.g., Todoist API)
Write a handler (~80-100 lines):
```python
# scripts/connectors/todoist.py
from typing import Iterator

def fetch(*, since: str, max_results: int, auth_context: dict, **kw) -> Iterator[dict]:
    import requests
    token = auth_context["api_key"]
    resp = requests.get("https://api.todoist.com/rest/v2/tasks",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"since": since})
    resp.raise_for_status()
    for task in resp.json()[:max_results]:
        yield {
            "id": task["id"],
            "subject": task["content"],
            "date_iso": task["due"]["date"] if task.get("due") else None,
            "body": task.get("description", ""),
            "source": "todoist",
        }

def health_check(auth_context: dict) -> bool:
    import requests
    try:
        resp = requests.get("https://api.todoist.com/rest/v2/projects",
                            headers={"Authorization": f"Bearer {auth_context['api_key']}"},
                            timeout=10)
        return resp.status_code == 200
    except Exception:
        return False
```

Then add YAML block and credential to keyring. Done.

### 1.8 Trade-Offs: Pipeline vs. Alternatives

| Approach | Lines of Code | New Source Effort | Error Isolation | Backward Compat | Risk |
|----------|---------------|-------------------|-----------------|-----------------|------|
| **Pipeline + declarative connectors** | 308 + 595 handlers | YAML block (6 lines) | ✅ Per-connector | ✅ Fallback | Low |
| Keep 8 separate scripts | ~4,659 | New script (~400 lines) | ✅ Inherent | ✅ Current | None |
| Abstract base class inheritance | ~3,000 | Subclass (~200 lines) | ✅ Per-class | ⚠️ Breaks API | Med |
| **MCP adapter over pipeline** | **+300 adapter** | **None (wraps existing)** | **✅ Per-connector** | **✅ Script fallback** | **Low** |
| MCP-native rewrite (no scripts) | ~2,000 | MCP tool definition | ❌ MCP-coupled | ❌ CLI-specific | High |
| Plugin entry points (pkg) | ~1,500 | pip-installable | ✅ Per-plugin | ✅ Isolated | Med |

**Decision:** Pipeline + declarative connectors as the implementation layer,
with an MCP adapter (§3) exposing the same pipeline as structured tools.
This gives MCP's discoverability and structured data exchange when available,
with script fallback when MCP is not connected. See §3 for full design.

**Why not MCP-native rewrite?** MCP is the invocation layer, not the implementation
layer. Rewriting connectors as pure MCP tools loses cron/launchd scheduling,
terminal debugging, and CLI-agnostic fallback. The adapter pattern gets the same
benefits with zero rewrite risk.

**Why not plugin entry points?** Premature for current scale. Plugin systems add
packaging complexity (setup.py, entry_points, discovery logic) for a project with
one codebase and a small community. If Artha reaches 50+ community connectors,
reconsider. YAML config achieves equivalent extensibility without the overhead.

### 1.9 Forward-Looking: Pipeline Extensions

| Extension | Effort | When |
|-----------|--------|------|
| RSS/Atom feed connector | 1 handler (~80 lines) | v6.2 |
| Slack workspace connector | 1 handler (~100 lines) + OAuth | v6.2 |
| Discord connector | 1 handler (~80 lines) + bot token | v6.2 |
| Plaid financial data | 1 handler (~150 lines) + Plaid API key | v7.0 |
| Local mbox import | 1 handler (~60 lines) | v6.1 |
| Apple Health XML | 1 handler (~100 lines) | v7.0 |
| Parallel fetch (asyncio) | Pipeline refactor | v7.0 |

Each is additive — handler + YAML block. No pipeline changes needed.

---

## 2. Declarative Action Extension

### 2.1 Current State

`config/actions.yaml` (136 lines) defines 4 actions. The framework works.

### 2.2 Extension Architecture

Actions follow the same declarative pattern as connectors:

```yaml
# config/actions.yaml — adding a new action
send_telegram:
  type: api_call
  enabled: true
  handler: "scripts/actions/telegram_send.py"
  requires_approval: true        # P2: human-gated by default
  friction: low                  # UI hint for display
  pii_check: true                # Layer 3 PII filter before send
  params:
    bot_token_key: "artha-telegram-bot-token"    # keyring key
    chat_id: "{telegram_chat_id}"                # from contacts
  audited: true                  # log to state/audit.md
```

### 2.3 Action Handler Contract

```python
# scripts/actions/base_action.py (future)
from typing import Protocol

class ActionHandler(Protocol):
    def execute(self, *, params: dict, context: dict) -> dict:
        """Execute the action. Return {success: bool, message: str}."""
        ...
    def validate(self, params: dict) -> list[str]:
        """Return list of validation errors, empty if valid."""
        ...
```

### 2.4 Security Invariants for Actions

No matter how extensible actions become, these rules are non-negotiable:

1. **`requires_approval: true` is the default.** Actions marked `false` must be
   explicitly justified (e.g., todo_sync is read-heavy, low-risk)
2. **`pii_check: true` for any outbound action.** Data leaving the machine goes
   through `safe_cli.py`
3. **Handler path validation.** Must be under `scripts/actions/`. No arbitrary paths
4. **Credential access via keyring only.** No credentials in YAML, env vars, or files
5. **Audit logging.** Every action execution logged to `state/audit.md`

### 2.5 Trade-Off: Declarative Actions vs. Alternatives

| Approach | Extensibility | Security | Complexity |
|----------|---------------|----------|------------|
| **Declarative YAML + handlers** | ★★★★★ | ★★★★★ (approval + PII check) | Low |
| Hardcode in instruction file | ★☆☆☆☆ | ★★★★★ | None |
| LangChain-style tool registry | ★★★★★ | ★★★☆☆ (less control) | High |
| **MCP adapter over YAML actions** | **★★★★★** | **★★★★★ (same checks)** | **Low** |
| MCP-native actions (no YAML) | ★★★★☆ | ★★★☆☆ (loses approval gating) | Medium |

**Decision:** Declarative YAML as the definition layer, with MCP adapter (§3)
exposing actions as MCP tools. The YAML file remains the source of truth for
what actions exist, their approval requirements, and their PII check rules.
The MCP adapter enforces the same invariants — it wraps, not replaces.

---

## 3. MCP Integration — Adapter Pattern

### 3.1 Why MCP Matters for Artha

Artha's current architecture uses `run_in_terminal` → Python scripts → stdout JSONL.
This works, but it means the AI CLI must: parse JSONL from stdout, interpret exit
codes for errors, and construct CLI flags from context. MCP (Model Context Protocol)
eliminates this friction — the AI calls structured tools directly, receives typed
data, and gets structured errors.

As MCP becomes the universal standard across Claude, Gemini, Copilot, and VS Code,
Artha should be MCP-ready without abandoning its battle-tested Python pipeline.

### 3.2 Architecture: Adapter, Not Rewrite

The MCP server is a **thin adapter** (~300 lines) that wraps existing Python
functions as MCP tools. Connectors, skills, and pipeline code are untouched.

```
┌─────────────────────────────────────────────────────────────────┐
│                     AI CLI (Claude, Gemini, Copilot)            │
│                                                                 │
│  ┌──── MCP Channel ────┐    ┌──── Terminal Channel ────┐       │
│  │ artha-mcp-server     │    │ python scripts/pipeline  │       │
│  │  (structured tools)  │    │  (stdout JSONL fallback) │       │
│  └──────────┬───────────┘    └──────────┬───────────────┘       │
└─────────────┼───────────────────────────┼───────────────────────┘
              │ calls                     │ calls
              ▼                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              SHARED PYTHON LAYER (unchanged)                    │
│                                                                 │
│  pipeline.py ──→ connectors/{google_email, msgraph, ...}.py    │
│  skill_runner.py ──→ skills/{uscis, visa_bulletin, ...}.py     │
│  actions/ ──→ gmail_send.py, todo_sync.py                      │
│  vault.py, pii_guard.py, preflight.py                          │
└─────────────────────────────────────────────────────────────────┘
```

**Key invariant:** The MCP server imports and calls the same functions that
`pipeline.py` and `skill_runner.py` call. One implementation, two invocation paths.

### 3.3 MCP vs. Scripts — Capability Comparison

| Capability | Scripts (terminal) | MCP (structured) |
|---|---|---|
| AI receives data | Captures stdout, parses JSONL | Gets structured data in-context |
| AI passes params | CLI flags (`--since`, `--source`) | Typed tool parameters |
| Error handling | Exit codes + stderr text | Structured error objects |
| Tool discovery | Must be documented in Artha.md | Auto-discovered by AI runtime |
| Multi-turn interaction | Not possible (script exits) | Tool maintains context |
| Cross-CLI portability | Each CLI invokes differently | MCP is universal standard |
| Cron/launchd scheduling | Direct scheduling | Needs host process (use scripts) |
| Terminal debugging | `python script.py --verbose` | Harder (runs inside AI runtime) |
| Offline operation | Works without AI CLI | Requires AI CLI as MCP host |

**Conclusion:** MCP is superior for AI-interactive use; scripts are superior for
automation and debugging. The adapter pattern gets both.

### 3.4 MCP Tools to Expose

| MCP Tool | Wraps | Category | Notes |
|---|---|---|---|
| `artha_fetch_data` | `pipeline.py` | Read | Fetch from all/specific connectors |
| `artha_health_check` | `pipeline.py --health` | Read | Connector health status |
| `artha_list_connectors` | `pipeline.py --list` | Read | Show enabled connectors |
| `artha_run_skills` | `skill_runner.py` | Read | Run data fidelity skills |
| `artha_read_state` | `vault.py` decrypt + read | Read | Read a state file (auto-decrypt) |
| `artha_write_state` | `vault.py` write + encrypt | Write | Write state (integrity guard + encrypt) |
| `artha_send_email` | `gmail_send.py` | Write | Requires `approved: true` param |
| `artha_pii_scan` | `pii_guard.py` | Read | Scan text for PII before action |
| `artha_preflight` | `preflight.py` | Read | Pre-catch-up health gate |
| `artha_todo_sync` | `todo_sync.py` | Write | Microsoft To Do bidirectional sync |

**Not exposed as MCP tools** (stay script-only):
- `setup_google_oauth.py`, `setup_msgraph_oauth.py`, `setup_icloud_auth.py` — interactive, one-time
- `generate_identity.py` — one-time config assembly
- `migrate.py`, `upgrade.py` — administrative utilities
- `vault.py` raw decrypt — security risk; wrapped inside `artha_read_state` instead

### 3.5 MCP Server Implementation

```python
# scripts/mcp_server.py — Artha MCP Server (~300 lines)
# Transport: stdio (launched by AI CLI, never HTTP)
# Protocol: MCP SDK (pip install mcp)

from mcp.server import Server
from mcp.types import Tool, TextContent
import json

server = Server("artha")

@server.tool()
async def artha_fetch_data(since: str, source: str | None = None) -> str:
    """Fetch email/calendar/LMS data from enabled connectors.
    Returns JSONL records. Equivalent to: python scripts/pipeline.py"""
    # Imports and calls pipeline.run_pipeline() directly
    # Returns structured JSON, not raw JSONL
    ...

@server.tool()
async def artha_read_state(domain: str) -> str:
    """Read a domain state file. Auto-decrypts .age files.
    PII guard applied to output before returning."""
    # Validates domain against known list
    # Calls vault.decrypt if .age exists
    # Runs pii_guard on content before returning
    ...

@server.tool()
async def artha_write_state(domain: str, content: str, approved: bool = False) -> str:
    """Write a domain state file with integrity guard.
    Requires approved=true. Enforces net-negative write guard."""
    if not approved:
        return json.dumps({"error": "Write requires approved=true"})
    # Net-negative guard check
    # Write + vault.encrypt if sensitive domain
    # Audit log
    ...
```

### 3.6 connectors.yaml Extension

Add optional `mcp` block to each connector (backward-compatible):

```yaml
gmail:
  type: email
  provider: google
  enabled: true
  # ... existing fields ...
  mcp:
    prefer_mcp: true                # AI should use MCP tool when connected
    fallback: "scripts/pipeline.py --source gmail"  # terminal fallback
```

This lets the AI runtime know: "if artha-mcp-server is connected, call
`artha_fetch_data(source='gmail')`; if not, fall back to the pipeline script."

### 3.7 Artha.core.md Catch-Up Workflow Update

Step 4 (Fetch) gains a three-tier invocation preference:

```markdown
### Step 4 — Fetch

**Tier 1 — MCP tools (when artha-mcp-server is connected):**
Call `artha_fetch_data` with `since: $LAST_CATCH_UP`. Returns structured
records directly — no JSONL parsing needed.

**Tier 2 — Unified pipeline (when MCP unavailable, connectors.yaml exists):**
python scripts/pipeline.py --since "$LAST_CATCH_UP" --verbose

**Tier 3 — Individual scripts (legacy fallback):**
[existing individual script commands]
```

Graceful degradation: MCP → pipeline → individual scripts.

### 3.8 Per-CLI MCP Configuration

**Claude Desktop / Claude CLI (`.claude.json` or `claude_desktop_config.json`):**
```json
{
  "mcpServers": {
    "artha": {
      "command": "python3",
      "args": ["scripts/mcp_server.py"],
      "cwd": "/path/to/artha"
    }
  }
}
```

**Gemini CLI (`.gemini/settings.json`):**
```json
{
  "mcpServers": {
    "artha": {
      "command": "python3",
      "args": ["scripts/mcp_server.py"]
    }
  }
}
```

**VS Code / GitHub Copilot (`.vscode/mcp.json`):**
```json
{
  "servers": {
    "artha": {
      "command": "python3",
      "args": ["scripts/mcp_server.py"]
    }
  }
}
```

### 3.9 Security Invariants for MCP

Non-negotiable rules that apply regardless of invocation path:

1. **Local-only transport.** stdio only — never HTTP/SSE to remote hosts.
   The MCP server runs as a child process of the AI CLI on the user's machine.
2. **PII guard on all read responses.** `artha_read_state` runs `pii_guard.py`
   before returning content to the AI. Same protection as terminal output.
3. **Write tools require explicit approval.** `artha_write_state(approved=true)`
   and `artha_send_email(approved=true)` enforced at the tool level.
   The MCP server rejects writes where `approved` is false/missing.
4. **No raw vault key exposure.** MCP tools decrypt/encrypt internally.
   Keys never appear in tool parameters or responses.
5. **Audit logging.** Every MCP tool invocation logged to `state/audit.md`
   with timestamp, tool name, parameters (PII-scrubbed), and result status.
6. **Handler path validation.** MCP server only loads modules from `scripts/`.
   No arbitrary code execution via tool parameters.
7. **actions.yaml as source of truth.** MCP action tools enforce the same
   `requires_approval`, `pii_check`, and `friction` rules from `actions.yaml`.

### 3.10 MCP-Native Opportunities (Future)

Capabilities that become possible only with MCP, not achievable via scripts:

| Opportunity | What It Enables | Version |
|---|---|---|
| **Streaming briefing** | AI generates sections as data arrives (not batch) | v7.0 |
| **Interactive bootstrap** | MCP tool guides onboarding with multi-turn Q&A in-protocol | v7.0 |
| **Real-time skill alerts** | MCP notifications push USCIS status changes immediately | v7.0+ |
| **Cross-agent orchestration** | One MCP server serves Claude, Gemini, Copilot simultaneously | v7.0 |
| **MCP Resources** | State files as browsable resources (AI explores without explicit read) | v7.0 |
| **MCP Sampling** | MCP server asks the AI to reason about data mid-pipeline | v8.0 |
| **MCP Roots** | Workspace-aware context for multi-repo setups | v7.0 |

These are additive — they extend the adapter, not replace it.

### 3.11 Implementation Phases

| Phase | Scope | Effort | Depends On |
|---|---|---|---|
| **Phase F1** (v6.1) | Read-only MCP tools: `artha_fetch_data`, `artha_health_check`, `artha_read_state`, `artha_pii_scan`, `artha_preflight`, `artha_list_connectors` | ~200 lines, 1 day | Pipeline working (§1) |
| **Phase F2** (v6.2) | Write MCP tools: `artha_write_state`, `artha_send_email`, `artha_todo_sync` | ~100 lines, 0.5 day | F1 stable |
| **Phase F3** (v6.2) | Update Artha.core.md Step 4 to prefer MCP. Add `mcp:` blocks to connectors.yaml | Config only | F1 stable |
| **Phase F4** (v7.0) | MCP-native features: streaming, resources, notifications | ~300 lines | F2 stable + MCP SDK maturity |

### 3.12 Trade-Off Summary

| Approach | Effort | Risk | Benefit |
|---|---|---|---|
| **Adapter** (MCP wraps existing Python) | ~300 lines | Zero — existing code unchanged | Full MCP benefits + script fallback |
| **Rewrite** (connectors as native MCP tools) | ~2,000 lines | High — new bugs, lose cron | Marginally cleaner MCP |
| **Dual maintain** (separate MCP + scripts) | ~1,500 lines ongoing | Medium — inevitable divergence | None over adapter |
| **Ignore MCP** | 0 lines | Medium — miss industry shift | None |

**Decision:** Adapter pattern. Zero risk to deployed system, incremental adoption,
single source of truth for connector logic, full backward compatibility.

---

## 4. Spec Compaction — Surgical Reduction

### 4.1 Strategy

**Preserve:** Vision, design principles, functional requirements, security model,
architecture decisions, alert thresholds, autonomy framework.

**Remove:** Embedded state-file examples with real data (→ registry.md),
multi-version changelog tables (→ CHANGELOG.md), Phase 2B feature prose already
in `implementation_status.yaml`, verbose repetition.

**Tighten:** Long paragraphs → concise bullets. Multi-paragraph explanations →
single sentences. Inline examples → tables.

**De-PII:** Replace all personal names with the Patel family convention.

### 4.2 PRD Compaction (artha-prd.md: 2,507 → ≤1,800)

| Section | Current | Action | Target |
|---------|---------|--------|--------|
| Header + changelog table | ~20 | Replace with "See CHANGELOG.md" | 5 |
| §1 Vision | ~15 | Keep (core) | 15 |
| §2 Problem | ~30 | Keep; de-PII school/child names | 25 |
| §3 Design Principles (P1-P9) | ~60 | Keep (core) | 60 |
| §4 [removed — Vega unreleased] | ~20 | Section removed | — |
| §5 Life Data Map | ~25 | De-PII all family/institution names | 20 |
| §6 Interaction Modes | ~140 | Tighten — remove verbose repetition | 80 |
| §7 Functional Req (FR-1 to FR-18) | ~600 | De-PII; tighten descriptions | 450 |
| §8 Goal Intelligence | ~200 | De-PII; tighten | 130 |
| §9 Architecture | ~190 | Remove inline state-file examples (→ registry) | 110 |
| §10 Autonomy Framework | ~90 | Keep (core) | 90 |
| §11 Data Sources | ~105 | De-PII institutions | 70 |
| §12 Privacy Model | ~75 | Keep (security-critical) | 75 |
| §13 Roadmap | ~320 | Keep v5+; collapse completed phases | 100 |
| §14 Success Criteria | ~95 | Keep | 80 |
| §15 NFRs | ~95 | Keep | 80 |
| §16 Open Questions | ~40 | Keep relevant; mark resolved | 30 |
| Inline state-file examples | ~200 | **DELETE** — belong in registry.md | 0 |

**What NOT to cut:**
- FR definitions (even brief) — they define scope
- Alert thresholds — they're the product's intelligence
- Privacy model — fundamental constraint
- Autonomy framework — key differentiator

### 4.3 Tech Spec Compaction (artha-tech-spec.md: 4,833 → ≤3,000)

| Section | Action |
|---------|--------|
| Header + changelog | Collapse to "See CHANGELOG.md" |
| §1 Architecture overview | Keep; de-PII diagrams |
| §2 Instruction file spec | **Reference only** — content lives in Artha.core.md |
| §3 MCP tool config | Keep (implementation-critical) |
| §4 Routing rules | **Reference only** — already in Artha.core.md §3 |
| §5-7 Domain state schemas | **Remove inline examples** — schemas in registry.md |
| §8 Security model | Keep (critical) |
| §9 Operational | Tighten |
| Inline state-file examples (~400 lines) | **DELETE** — contain real data, belong in registry |

**Key preservation areas:**
- Catch-up workflow steps (the core algorithm)
- Tiered context loading (creative solution to context window limits)
- Data integrity guard (three-layer backup/verify/guard)
- Encryption model (which files, which keys, which keychain)

### 4.4 UX Spec Compaction (artha-ux-spec.md: 2,667 → ≤1,800)

| Section | Action |
|---------|--------|
| UX principles (§1) | Keep (core design philosophy) |
| Interaction model (§2) | De-PII; tighten session flow diagrams |
| Information arch (§3) | Keep (tier structure is key) |
| Catch-up output (§4) | Keep format; genericize examples |
| Alert system (§6) | Keep |
| Commands (§10) | **Reference only** — already in commands.md |
| Family access (§13) | De-PII |
| Error & recovery (§14) | Tighten |
| Visual generation (§18) | Keep (Gemini integration) |
| Autonomy UX (§19) | Keep (trust progression) |

### 4.5 Files to Archive (Not Compact)

| File | Lines | Reason | Action |
|------|-------|--------|--------|
| `standardization.md` | 2,566 | ~60% done; superseded by supercharge.md | → `.archive/specs/` |
| `remediation.md` | 1,513 | ~65% done; superseded | → `.archive/specs/` |
| `hardening.md` | 1,483 | ~40% done; superseded | → `.archive/specs/` |
| `artha-tasks.md` | 4,055 | Stale task tracker | → `.archive/specs/` |
| **Total archived** | **9,617** | | |

### 4.6 PII Replacement Convention

| Original | Replacement |
|----------|-------------|
| Vedprakash / Ved | Raj |
| Archana | Priya |
| Parth | Arjun |
| Trisha | Ananya |
| Mishra (family) | Patel |
| Tesla STEM High School | Lincoln High School |
| Issaquah Middle School | Jefferson Middle School |
| Issaquah School District | Portland Public Schools |
| Sammamish, WA | Redmond, WA |
| mi.vedprakash@gmail.com | raj.patel@example.com |
| vedprakash.m@outlook.com | rpatel@outlook.com |
| Microsoft (employer) | [employer] |
| Fragomen (law firm) | [immigration attorney] |

### 4.7 Verification

After compaction:
```bash
# Automated PII scan
python scripts/pii_guard.py scan specs/*.md

# Semantic name scan
grep -rn "Vedprakash\|Archana\|Parth\|Trisha\|Mishra\|Tesla STEM\|Issaquah\|Fragomen\|mi\.vedprakash" specs/

# Both must return zero results
```

---

## 5. Codebase Cleanup — Archive Redundancy

### 5.1 Cleanup Philosophy

Move to `.archive/`, don't delete. History is preserved. Distribution tree is clean.
The `.archive/` directory is already gitignored.

### 5.2 Files to Archive Immediately

Do not touch specs folder in this cleanup

**Note:** `parse_contacts.py`, `parse_apple_health.py`, `local_mail_bridge.py`,
and `king_county_tax.py` have already been archived in prior cleanup rounds.

### 5.3 Files to Archive After Pipeline Verification

After the pipeline passes the A/B verification (§1.5):

| Script | Lines | Replacement |
|--------|-------|-------------|
| `scripts/gmail_fetch.py` | 436 | `connectors/google_email.py` |
| `scripts/msgraph_fetch.py` | 702 | `connectors/msgraph_email.py` |
| `scripts/icloud_mail_fetch.py` | 512 | `connectors/imap_email.py` |
| `scripts/gcal_fetch.py` | 391 | `connectors/google_calendar.py` |
| `scripts/msgraph_calendar_fetch.py` | 729 | `connectors/msgraph_calendar.py` |
| `scripts/icloud_calendar_fetch.py` | 723 | `connectors/caldav_calendar.py` |
| `scripts/canvas_fetch.py` | 364 | `connectors/canvas_lms.py` |
| `scripts/msgraph_onenote_fetch.py` | 802 | `connectors/onenote.py` |
| **Total** | **4,659** | **→ 595 lines in handlers** |

### 5.4 Archive Execution Commands

```bash
cd ~/OneDrive/Artha



# Phase 2: Legacy scripts (after pipeline verification)
# DO NOT run until §1.5 verification passes
mv scripts/gmail_fetch.py .archive/scripts/
mv scripts/msgraph_fetch.py .archive/scripts/
mv scripts/icloud_mail_fetch.py .archive/scripts/
mv scripts/gcal_fetch.py .archive/scripts/
mv scripts/msgraph_calendar_fetch.py .archive/scripts/
mv scripts/icloud_calendar_fetch.py .archive/scripts/
mv scripts/canvas_fetch.py .archive/scripts/
mv scripts/msgraph_onenote_fetch.py .archive/scripts/
```

### 5.5 Post-Cleanup Distribution Tree

```
artha/
├── README.md                      # Updated quick start
├── CHANGELOG.md                   # Version history
├── LICENSE                        # AGPL v3
├── pyproject.toml                 # Package definition
├── artha.py                       # Entry point
├── AGENTS.md / CLAUDE.md / GEMINI.md  # CLI loaders
│
├── config/
│   ├── Artha.core.md              # Distributable instruction template
│   ├── user_profile.example.yaml  # New user template
│   ├── connectors.yaml            # Data source registry
│   ├── actions.yaml               # Action registry
│   ├── skills.yaml                # Skill scheduler
│   ├── implementation_status.yaml # Feature flags
│   ├── commands.md                # Slash command reference
│   ├── briefing-formats.md        # Output templates
│   ├── bootstrap-interview.md     # Onboarding flow
│   ├── registry.md                # File inventory + schemas
│   ├── observability.md           # Calibration + /diff
│   ├── routing.example.yaml       # Email routing template
│   ├── settings.example.md        # Deprecated settings template
│   └── presets/cultural/          # 6 cultural presets
│
├── scripts/
│   ├── pipeline.py                # Unified ingestion engine
│   ├── mcp_server.py              # MCP adapter over pipeline (Phase F)
│   ├── connectors/                # 8 handler modules + base.py
│   ├── actions/                   # Action handlers (future)
│   ├── lib/                       # 6 shared modules
│   ├── skills/                    # 5 data skills + base + README
│   ├── artha.py → ../artha.py     # (root entry point)
│   ├── generate_identity.py       # Profile → Artha.md
│   ├── pii_guard.py               # Layer 1 PII defense
│   ├── preflight.py               # Health gate
│   ├── vault.py                   # Encryption
│   ├── safe_cli.py                # Layer 3 outbound filter
│   ├── profile_loader.py          # Config access
│   ├── gmail_send.py              # Email send action
│   ├── google_auth.py             # OAuth helper
│   ├── setup_google_oauth.py      # Auth setup
│   ├── setup_icloud_auth.py       # Auth setup
│   ├── setup_msgraph_oauth.py     # Auth setup
│   ├── setup_todo_lists.py        # To Do setup
│   ├── skill_runner.py            # Skill scheduler
│   ├── todo_sync.py               # To Do sync
│   ├── migrate.py                 # Legacy migration
│   ├── upgrade.py                 # Version upgrade
│   ├── _bootstrap.py              # Venv helper
│   ├── demo_catchup.py            # Demo mode
│   └── vault_hook.py              # macOS watchdog
│
├── prompts/                       # 17 domain prompts + README
├── state/templates/               # 25 blank domain templates
├── docs/                          # 8 documentation files
├── specs/                         # supercharge.md + supercharge-reloaded.md + 3 compacted specs
├── tests/                         # Unit + extraction tests
├── briefings/                     # (gitignored) User briefing archive
├── summaries/                     # (gitignored) Summary archive
└── tmp/                           # (gitignored) Working files
```

**Result:** Clean, navigable, documented tree. No duplicate scripts. No stale specs.

### 5.6 Impact Assessment

| Metric | Before | After |
|--------|--------|-------|
| Python lines (scripts/) | 11,910 + 2,297 = 14,207 | ~9,548 (after legacy archival) |
| Spec lines | 21,088 | ~8,200 (after compaction + archival) |
| Files in scripts/ | 30 | 22 (after legacy archival) |
| Files in specs/ | 9 | 5 (after archival) |
| Total distribution files | ~140 | ~100 |

---

## 6. README.md Overhaul

### 6.1 What to Update

The current README (180 lines) is solid but needs to reflect v6.0 improvements:

| Section | Current | Update |
|---------|---------|--------|
| Quick Start | 7 manual steps | Single-command start with artha.py |
| Project Structure | Lists old scripts | Updated tree (pipeline + connectors) |
| Features | Missing pipeline, connectors, actions | Add declarative architecture |
| Test docs | "130+ test cases" | Updated count |
| badges | License + Python version | Add CI status badge |

### 6.2 Key Messages to Add

1. **Declarative connectors** — add any email/calendar source via YAML
2. **Unified pipeline** — one command fetches from all sources
3. **Action framework** — extensible actions with PII safety
4. **5 extension points** — domain, connector, skill, action, cultural preset
5. **Cultural awareness** — presets for diverse family structures

### 6.3 Updated Quick Start

```markdown
## Quick Start

### 1. Clone and Launch
\`\`\`bash
git clone https://github.com/vedprakash-m/artha.git
cd artha
python artha.py
\`\`\`

This auto-detects first run, shows a demo briefing, and guides you through setup.

### 2. Or Set Up Manually
\`\`\`bash
cp config/user_profile.example.yaml config/user_profile.yaml
# Edit with your details
python scripts/generate_identity.py
python scripts/setup_google_oauth.py    # Connect Gmail
python scripts/preflight.py             # Verify setup
\`\`\`

### 3. Run Your First Catch-Up
Open your AI CLI and say: "catch me up"
```

---

## 7. Execution Order & Dependencies

```
Phase A: Archive stale specs (no dependencies)                     ✅ DONE
    │
    ├──→ Phase B: Compact PRD/Tech/UX specs (after archive)        ✅ DONE
    │
    ├──→ Phase C: Archive stale scripts (prior cleanups)           ✅ DONE
    │
    ├──→ Phase D: Update README.md (after all changes)             ✅ DONE
    │
    ├──→ Phase E: Verify pipeline → archive legacy scripts         ✅ DONE
    │
    ├──→ Phase F1: MCP adapter — read-only tools                   ✅ DONE
    │
    ├──→ Phase F2: MCP adapter — write tools                       ✅ DONE
    │
    ├──→ Phase F3: Update Artha.core.md for MCP-preferred flow     ✅ DONE
    │
    └──→ Phase F4: MCP-native features (streaming, resources)      ⬜ (v7.0)
```

**Status:** Phases A–F3 completed. Phase F4 (streaming, resources) deferred to v7.0.

### 7.1 Completed

1. ✅ Archive 5 stale specs → `.archive/specs/`
2. ✅ Compact PRD (2,507 → 1,791), Tech Spec (4,833 → 2,885), UX Spec (2,667 → 1,149)
3. ✅ De-PII all specs (zero matches on PII scan)
4. ✅ Update README.md (architecture diagram, pipeline/connectors, updated test count)
5. ✅ Rewrite all 8 connectors as standalone modules (no legacy imports) — 108 tests pass
6. ✅ Archive 8 legacy fetch scripts → `.archive/scripts/`
7. ✅ Build `scripts/mcp_server.py` — F1 read-only + F2 write tools with approval enforcement
8. ✅ Update Artha.core.md Step 4 to prefer MCP invocation (three-tier graceful degradation)
9. ✅ Add `mcp:` blocks to all 8 connectors in `config/connectors.yaml`

### 7.2 Next: MCP-native features (Phase F4 — v7.0)

10. Streaming briefings via MCP resources (Server-Sent Events)
11. Live notifications for USCIS case status changes
12. MCP resource endpoints for state files

### 7.3 Deferred: Phase F4

---

## 8. Validation Gates

### Gate 1: PII-Free Distribution ✅
```bash
grep -rniE "Vedprakash|Archana|Parth|Trisha|Mishra|Tesla.STEM|Issaquah|Fragomen" \
  specs/ prompts/ config/Artha.core.md docs/ README.md
# Expected zero actionable results.
# Known documented exceptions (not data exposure):
#   specs/supercharge-reloaded.md §4.6 — PII replacement table documents
#     the original→replacement mapping used for de-PII of other files.
#   README.md:39 — GitHub repo URL (vedprakash-m/artha.git) is structural.
# All other files: zero results — VERIFIED 2026-03-13 (updated 2026-03-13)
```

### Gate 2: Tests Pass ✅
```bash
python -m pytest tests/unit/ -q
# 108 passed, 20 xfailed — VERIFIED 2026-03-13
```

### Gate 3: Clean Distribution Tree ✅
```bash
# No duplicate legacy fetch scripts (Phase E complete)
ls scripts/*_fetch.py scripts/canvas_fetch.py 2>/dev/null | wc -l  # should be 0
# Pipeline --list works (connectors.yaml loads correctly)
python scripts/pipeline.py --list   # should show 8 enabled connectors
```

### Gate 4: Pipeline Output Equivalence (Phase E)
```bash
# Compare pipeline vs. legacy output
diff <(python scripts/pipeline.py --since "..." --source gmail | jq -S .) \
     <(python scripts/gmail_fetch.py --since "..." | jq -S .)
# Zero content diff
```

### Gate 5: README Accuracy ✅
```bash
python artha.py --help
python scripts/preflight.py --help
python scripts/pipeline.py --list
```

### Gate 6: MCP Server Health (Phase F1) ✅
```bash
# MCP server starts without error (stdio transport)
python scripts/mcp_server.py  # exits cleanly or starts listening on stdin

# Read-only tools return valid data
# (tested via AI CLI or MCP inspector)
artha_health_check → returns JSON with per-connector status
artha_list_connectors → returns enabled connector list matching connectors.yaml
artha_fetch_data(since="2026-03-12", source="gmail") → returns records
artha_read_state(domain="calendar") → returns state content (PII-scrubbed)
artha_pii_scan(text="SSN: 123-45-6789") → returns {"found": true, "types": {...}}
```

### Gate 7: MCP Write Safety (Phase F2) ✅
```bash
# Write without approval is rejected
artha_write_state(domain="goals", content="...", approved=false) → error
artha_send_email(to="...", approved=false) → error

# Write with approval succeeds and logs to audit.md
artha_write_state(domain="goals", content="...", approved=true) → success
grep "artha_write_state" state/audit.md  # entry exists

# Empty content is rejected
artha_write_state(domain="kids", content="", approved=true) → blocked

# Net-negative guard triggers on data loss (new < 50% of existing)
artha_write_state(domain="kids", content="# stub", approved=true) → blocked
```

### Gate 8: MCP ↔ Script Equivalence (Phase F3)
```bash
# MCP fetch returns same data as pipeline script
diff <(artha_fetch_data via MCP | jq -S .) \
     <(python scripts/pipeline.py --since "..." | jq -S .)
# Zero content diff (structural format may differ: JSON vs JSONL)
```

---

*supercharge-reloaded.md v2.0 — Execution Playbook for Artha v6.0–7.0*
*Parent plan: [supercharge.md](supercharge.md)*
