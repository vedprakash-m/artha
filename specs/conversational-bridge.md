# Artha — Channel Bridge (ACB)
**Version**: 2.0.0 | **Status**: Approved Design | **Supersedes**: TCB v1.0.0
**Classification**: Distributable (no PII in this file)

---

## Table of Contents

- [§1. Executive Summary](#1-executive-summary)
- [§2. Design Principles](#2-design-principles)
- [§3. Architecture](#3-architecture)
- [§4. Channel Adapter Protocol](#4-channel-adapter-protocol)
- [§5. Channel Registry](#5-channel-registry)
- [§6. Security Architecture](#6-security-architecture)
- [§7. Layer 1 — Post-Catch-Up Push](#7-layer-1--post-catch-up-push)
- [§8. Layer 2 — Interactive Listener](#8-layer-2--interactive-listener)
- [§9. Layer 3 — Ingest & Gated Actions](#9-layer-3--ingest--gated-actions)
- [§10. Cross-Platform Service Management](#10-cross-platform-service-management)
- [§11. Observability & Audit](#11-observability--audit)
- [§12. Distribution Checklist](#12-distribution-checklist)
- [§13. Implementation Plan](#13-implementation-plan)
- [§14. Codebase Integration Map](#14-codebase-integration-map)
- [§15. Decisions & Alternatives](#15-decisions--alternatives)

---

## §1. Executive Summary

### Problem
Artha's pull-only CLI model requires a terminal session to access life intelligence. A family of four will not SSH into a terminal at 7 AM. The phone is the cockpit of modern life. The intelligence must find the user — not the other way around.

### Goal
Build a **platform-agnostic channel bridge** that delivers Artha's intelligence to any messaging platform (Telegram, Discord, Signal, Slack, Matrix, and future services) through a single adapter protocol — without breaking the existing 21-step catch-up pipeline, without introducing new security surfaces, and without platform lock-in.

### Scope
- Outbound push notifications after catch-up (Layer 1)
- Inbound read-only command responses (Layer 2)
- Ingest & human-gated write actions (Layer 3 — future)

### Design Philosophy Note

The original PRD (§ data sources) states: "All sources are pull-based. There are no push notifications, no webhooks, no event-driven triggers." The channel bridge is an intentional evolution of this principle. The *data collection* pipeline remains entirely pull-based — connectors fetch, they are never called. The channel bridge operates on the *output* side: it pushes **already-computed intelligence** to recipients after the pull-based pipeline completes. This preserves the architectural simplicity of pull-based ingest while extending the delivery surface.

### Non-Goals
- Building a second AI brain (the CLI session *is* the brain)
- Location-aware services (separate product surface)
- Platform-proprietary UI frameworks (Telegram Mini-Apps, etc.)
- Replacing the CLI workflow — the bridge is a *window*, not a *cockpit*

---

## §2. Design Principles

1. **Adapter, Not Application.** Each channel is a ~200-line adapter implementing a 4-method protocol. The bridge is infrastructure, not product logic. Adding a platform is a config change + one Python file.

2. **Push First, Interactive Second.** 80% of the value — the morning phone buzz with 3 alerts — requires zero daemon, zero new infrastructure. Ship it in days, not weeks.

3. **Read-Only by Default.** The interactive listener reads state files and sends formatted responses. It does not run the catch-up pipeline, does not write state, does not decrypt vault files. The security surface is minimal because the capability surface is minimal.

4. **No Free-Form AI Parse.** Chat messages are *commands*, not prompts. No LLM invocation from chat. This eliminates prompt injection as a threat class entirely.

5. **PII Redaction Is Not Optional.** Every outbound message — every byte — passes through `pii_guard.py`. There is no bypass path. There is no "trusted channel" exception.

6. **Cross-Platform or Nothing.** Every design decision must work on Windows (Task Scheduler), macOS (launchd), and Linux (systemd). No platform-specific assumptions in the protocol or registry.

7. **Existing Patterns, Not New Ones.** The channel adapter mirrors `ConnectorHandler`. The channel registry mirrors `connectors.yaml`. The push hook mirrors Step 15 (To Do sync). Familiarity is a feature.

---

## §3. Architecture

### High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    ARTHA CATCH-UP PIPELINE                   │
│  Steps 0–19b (unchanged)                                    │
│                                                             │
│  Step 20 (NEW): Channel Push                                │
│    ├─ Load channels.yaml                                    │
│    ├─ For each enabled channel with push: true              │
│    │   ├─ Format briefing (flash ≤500 chars)                │
│    │   ├─ Apply per-recipient access_scope filter           │
│    │   ├─ pii_guard.redact() — mandatory, no exceptions     │
│    │   ├─ channel_adapter.send_message()                    │
│    │   └─ Audit log: CHANNEL_PUSH | {channel} | {recipient}│
│    └─ Continue to "Catch-up complete."                      │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              INTERACTIVE LISTENER (Optional Daemon)          │
│  scripts/channel_listener.py --channel telegram             │
│                                                             │
│  On inbound message:                                        │
│    ├─ Verify sender in recipients whitelist                 │
│    ├─ Parse command from whitelist: /status /alerts /tasks  │
│    ├─ Reject unknown commands (no free-form text)           │
│    ├─ Read state files (READ-ONLY, no vault decrypt)        │
│    ├─ Format response with per-recipient sensitivity filter │
│    ├─ pii_guard.redact() — mandatory, no exceptions         │
│    ├─ channel_adapter.send_message()                        │
│    └─ Audit log: CHANNEL_IN + CHANNEL_OUT                   │
└─────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
scripts/
  channels/
    __init__.py              # Package init
    base.py                  # ChannelAdapter Protocol + ChannelMessage dataclass
    registry.py              # Load channels.yaml, instantiate adapters
    telegram.py              # Telegram adapter (reference implementation)
    discord.py               # Discord adapter (future)
    signal_cli.py            # Signal-CLI adapter (future)
    slack.py                 # Slack adapter (future)
  channel_push.py            # Post-catch-up push hook (Step 20)
  channel_listener.py        # Interactive daemon (Layer 2)
  setup_channel.py           # Interactive setup wizard
  service/
    artha-listener.xml       # Windows Task Scheduler config
    com.artha.channel-listener.plist  # macOS launchd config
    artha-listener.service   # Linux systemd unit
config/
  channels.example.yaml      # Distributable template (no PII)
  channels.yaml              # User's live config (gitignored)
docs/
  channels.md                # Setup guide for each supported platform
```

### Relationship to Existing Registries

| Registry | Purpose | Protocol Pattern |
|----------|---------|-----------------|
| `connectors.yaml` | **Inbound** data sources (Gmail, Canvas, etc.) | `fetch()` + `health_check()` |
| `actions.yaml` | **Outbound** write operations (email, calendar) | `handler` + `requires_approval` |
| `skills.yaml` | **Proactive** web intelligence (USCIS, weather) | `execute()` + `compare_fields` |
| **`channels.yaml`** (new) | **Outbound** messaging channels (Telegram, Discord) | `send_message()` + `health_check()` |

The channel registry is the fourth pillar. It completes the input→process→output→notify cycle.

---

## §4. Channel Adapter Protocol

### Protocol Definition

```python
# scripts/channels/base.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, runtime_checkable

@dataclass(frozen=True)
class ChannelMessage:
    """Immutable outbound message envelope."""
    text: str
    recipient_id: str
    buttons: list[dict[str, str]] = field(default_factory=list)
    # Each button: {"label": "Status", "command": "/status"}

@dataclass(frozen=True)
class InboundMessage:
    """Parsed inbound command from a channel."""
    sender_id: str
    sender_name: str
    command: str          # e.g., "/status", "/alerts"
    args: list[str]       # e.g., ["immigration"] for "/domain immigration"
    raw_text: str
    timestamp: str        # ISO 8601
    message_id: str       # Platform-specific dedup key

@runtime_checkable
class ChannelAdapter(Protocol):
    """Structural typing protocol for outbound messaging channels.

    Mirrors ConnectorHandler pattern from scripts/connectors/base.py.
    Adapters are duck-typed modules — no inheritance required.
    """

    def send_message(self, message: ChannelMessage) -> bool:
        """Send a text message (with optional buttons) to a recipient.
        Returns True on success, False on failure (never raises)."""
        ...

    def send_document(self, *, recipient_id: str,
                      file_path: str, caption: str = "") -> bool:
        """Send a file to a recipient. Returns True/False."""
        ...

    def health_check(self) -> bool:
        """Verify bot token validity and API reachability.
        Returns True if healthy. Called by preflight.py."""
        ...

    def poll(self, *, timeout: int = 30) -> list[InboundMessage]:
        """Long-poll for inbound messages. Returns empty list on timeout.
        Only required for Layer 2 (interactive mode).
        Adapters that only support push can raise NotImplementedError."""
        ...
```

### Registry Loader Contract

```python
# scripts/channels/registry.py

import importlib
from pathlib import Path

_CHANNEL_PKG = "scripts.channels"
_ALLOWED_PREFIX = "scripts/channels/"

def load_adapter(adapter_path: str):
    """Dynamically import a channel adapter module.
    Mirrors pipeline.py/_load_handler pattern exactly."""
    # Security: validate path prefix
    if not adapter_path.startswith(_ALLOWED_PREFIX):
        raise ValueError(
            f"Channel adapter must be under {_ALLOWED_PREFIX}: {adapter_path}"
        )
    module_name = adapter_path.replace("/", ".").removesuffix(".py")
    return importlib.import_module(module_name)

def load_channels_config() -> dict:
    """Load config/channels.yaml. Mirrors pipeline.py/load_connectors_config."""
    from scripts.lib.common import CONFIG_DIR
    channels_file = CONFIG_DIR / "channels.yaml"
    if not channels_file.exists():
        return {"defaults": {"push_enabled": False}, "channels": {}}
    import yaml
    with open(channels_file) as f:
        return yaml.safe_load(f) or {}
```

### Adapter Module Contract

Every adapter module (`scripts/channels/{platform}.py`) exports these top-level functions:

```python
def create_adapter(*, credential_key: str, **config) -> ChannelAdapter:
    """Factory function. Loads credentials from keyring via lib/auth.py.
    Called once at startup by registry.py."""

def platform_name() -> str:
    """Return human-readable platform name for audit logs."""
```

### Why Structural Typing (Not Inheritance)

Same rationale as `ConnectorHandler` — adapters are standalone modules loaded via `importlib`. No base class import required. Any module that quacks like a `ChannelAdapter` is one. This keeps adapters zero-dependency on Artha internals (they only need their platform SDK + the `keyring` library).

---

## §5. Channel Registry

### channels.example.yaml (Distributable)

```yaml
# Artha Channel Bridge Registry
# Copy to channels.yaml and configure your channels.
# channels.yaml is gitignored — it contains recipient IDs.
schema_version: "1.0"

# Global settings
defaults:
  redaction: full                  # Apply pii_guard to ALL outbound (full | partial | none)
  push_format: flash               # Briefing format for push (flash | digest)
  max_push_length: 500             # Characters per push message
  push_enabled: false              # Master kill switch — must be explicitly enabled
  listener_host: ""                # Hostname that runs the listener daemon (Layer 2).
                                   # Only the machine whose socket.gethostname() matches
                                   # will start the listener. Empty = any host allowed
                                   # (safe only for single-machine setups).
                                   # Set via: python scripts/setup_channel.py --install-service

channels:
  telegram:
    enabled: false
    adapter: "scripts/channels/telegram.py"
    auth:
      method: keyring
      credential_key: "artha-telegram-bot-token"
      setup_script: "scripts/setup_channel.py --channel telegram"
    recipients:
      primary:
        id: ""                     # Telegram chat_id (from setup wizard)
        access_scope: full         # full | family | standard
        push: true                 # Receives post-catch-up push
        interactive: true          # Can send commands
      spouse:
        id: ""
        access_scope: family       # Filtered: no immigration, finance PII
        push: true
        interactive: true
    features:
      push: true
      interactive: true
      documents: true
      buttons: true
    health_check: true
    retry:
      max_retries: 3
      base_delay: 2.0
      max_delay: 30.0

  discord:
    enabled: false
    adapter: "scripts/channels/discord.py"
    auth:
      method: keyring
      credential_key: "artha-discord-bot-token"
      setup_script: "scripts/setup_channel.py --channel discord"
    recipients:
      primary:
        id: ""
        access_scope: full
        push: true
        interactive: true
    features:
      push: true
      interactive: true
      documents: true
      buttons: true
    health_check: true
    retry:
      max_retries: 3
      base_delay: 2.0
      max_delay: 30.0

  slack:
    enabled: false
    adapter: "scripts/channels/slack.py"
    auth:
      method: keyring
      credential_key: "artha-slack-bot-token"
      setup_script: "scripts/setup_channel.py --channel slack"
    recipients:
      primary:
        id: ""
        access_scope: full
        push: true
        interactive: true
    features:
      push: true
      interactive: true
      documents: true
      buttons: true
    health_check: true
    retry:
      max_retries: 3
      base_delay: 2.0
      max_delay: 30.0

  # Template for community-contributed channels:
  # my_channel:
  #   enabled: false
  #   adapter: "scripts/channels/my_channel.py"
  #   auth:
  #     method: keyring
  #     credential_key: "artha-my-channel-token"
  #   recipients:
  #     primary:
  #       id: ""
  #       access_scope: full
  #       push: true
  #       interactive: false
  #   features:
  #     push: true
  #     interactive: false
  #     documents: false
  #     buttons: false
  #   health_check: true
```

### Access Scope Filtering

| Scope | Sees | Does NOT See |
|-------|------|-------------|
| `full` | All domains, all state, all alerts | — |
| `family` | Kids, calendar, home, shopping, social, goals, health (non-PII) | Immigration, finance (detailed), estate, insurance |
| `standard` | Calendar, tasks, general alerts only | All domain-specific state |

Scope filtering is applied **before** PII redaction — it's a content gate, not a redaction pass. A `family` recipient never sees immigration data at all, not even redacted.

### Scope-to-Domain Mapping

```yaml
# Hardcoded in channel_push.py and channel_listener.py
ACCESS_SCOPES:
  full:
    include: all
  family:
    include: [kids, calendar, home, shopping, social, goals, learning, vehicle, comms]
    exclude: [immigration, finance, estate, insurance, employment, digital, boundary]
  standard:
    include: [calendar, goals]
    exclude: all_others
```

---

## §6. Security Architecture

### Threat Model

| # | Threat | Impact | Mitigation | Layer |
|---|--------|--------|-----------|-------|
| T1 | Bot token compromise | Attacker impersonates Artha; sends phishing to family | Token in keyring only; `health_check()` verifies on startup + daily; alert on unexpected recipient count | All |
| T2 | Unknown sender messages bot | Information disclosure | Sender ID whitelist from `channels.yaml → recipients`; reject + log `CHANNEL_REJECT`; silent rejection (no response to unknowns) | L2 |
| T3 | Replay attack | Old command re-executed | Message ID dedup (LRU cache, last 1000 IDs); reject messages >5 min old via timestamp validation | L2 |
| T4 | Prompt injection via chat | Malicious text triggers unintended AI actions | **No LLM invocation from chat.** Commands are a hardcoded whitelist. Free-form text rejected. | L2 |
| T5 | PII leak to cloud chat service | SSN, A-number, account number visible to platform operator | `pii_guard.filter_text()` on every outbound byte. Access scope pre-filters sensitive domains entirely. | All |
| T6 | State file race condition | Pipeline + listener both write state simultaneously | **Listener is read-only.** Reads `open_items.md`, `health-check.md`, `dashboard.md`, `goals.md`, `calendar.md`. Never writes. No race condition by design. | L2 |
| T7 | Credential in synced config | Bot token synced to OneDrive/cloud/Git | `channels.yaml` stores keyring key names, never raw tokens. File gitignored. | Config |
| T8 | Privilege escalation — spouse reads immigration data | Family member sees PII-heavy domains | Per-recipient `access_scope` filter. `family` scope excludes PII-heavy domains entirely. | All |
| T9 | Critical domain access without verification | Sensitive queries without identity confirmation | PIN-based session tokens; 15 min expiry; PIN in keyring (`artha-channel-pin`) | L2 |
| T10 | Compromised phone sends commands | Lost/stolen device issues channel commands | Commands are read-only; no destructive capability. Session tokens for critical domains expire in 15 min. | L2 |
| T11 | Authenticated user floods commands | DoS via state file reads; audit log bloat | Per-sender rate limit: 10 commands/minute. Exceeding → 60s cooldown + `CHANNEL_RATE_LIMIT` audit event. | L2 |
| T12 | Recipient IDs as PII in channels.yaml | Telegram `chat_id`, Discord user IDs are user-identifiable; cloud sync risk | `channels.yaml` is gitignored. Recipient IDs classified as **PII** — documented in privacy surface. Future: vault-encrypt `channels.yaml` alongside other PII-bearing configs. | Config |
| T13 | Multiple listener instances on different machines | Message loss (Telegram long-poll offset consumed by random instance); inconsistent responses; audit log conflicts via OneDrive sync | **Explicit host designation** via `defaults.listener_host` in `channels.yaml`. Listener refuses to start unless `socket.gethostname()` matches. Telegram API pre-check (`deleteWebhook` + offset flush) on startup as defense-in-depth. See §8 "Multi-Machine Coordination". | L2 |
| T14 | Duplicate push from catch-up on multiple machines | Same briefing delivered twice in one day | Daily push marker file `state/.channel_push_marker_{date}.json` checked before sending. If marker exists and is <12h old, push is skipped with `CHANNEL_PUSH_SKIPPED` audit event. | L1 |

### Security Controls

| Control | Implementation | Where |
|---------|---------------|-------|
| Sender whitelist | `channels.yaml → recipients` map; reject unknown sender IDs silently | `channel_listener.py` |
| Command whitelist | Hardcoded set: `/status`, `/alerts`, `/tasks`, `/quick`, `/domain`, `/help`, `/unlock` | `channel_listener.py` |
| No free-form AI parse | Commands only. No LLM. Eliminates prompt injection entirely. | `channel_listener.py` |
| Per-recipient scope | Recipient → `access_scope` (`full`/`family`/`standard`); domain filter before format | `channel_push.py` + `channel_listener.py` |
| PII redaction | `pii_guard.filter_text()` on every outbound message. No exceptions. | `channel_push.py` + `channel_listener.py` |
| Session tokens | `/unlock <PIN>` → 15-min token for critical domain queries; PIN in keyring | `channel_listener.py` |
| Token health | `health_check()` verifies bot token on startup + daily (via preflight) | `preflight.py` P1 check |
| Audit trail | Every send/receive logged with event type + content hash | `state/audit.md` |
| Credential isolation | Tokens in OS keyring only; YAML stores key names, never secrets | `setup_channel.py` |
| Message dedup | LRU cache (1000 message IDs per channel); rejects duplicates | `channel_listener.py` |
| Timestamp validation | Reject inbound messages older than 5 minutes | `channel_listener.py` |
| Adapter path validation | Adapter path must be under `scripts/channels/` (mirrors connector path validation) | `registry.py` |
| Per-sender rate limit | 10 commands/minute per sender; 60s cooldown on breach; `CHANNEL_RATE_LIMIT` audit event | `channel_listener.py` |
| Recipient ID classification | Recipient IDs (chat_id, user IDs) classified as PII; `channels.yaml` gitignored; documented in `/privacy` surface | `channels.yaml` + `docs/security.md` |
| Listener host designation | `defaults.listener_host` in `channels.yaml`; listener refuses to start on non-designated host; prevents multi-instance message loss | `channel_listener.py` |
| Push deduplication | Daily marker file prevents duplicate pushes when catch-up runs on multiple machines | `channel_push.py` |

### Why "Biometric Unlock" Was Removed

The v1.0 spec proposed "biometric unlock" tied to iPhone Face ID. No concrete mechanism exists for a Telegram bot to verify an iPhone biometric scan. The replacement — **PIN + 15-min expiry in keyring** — is implementable today, works on all platforms, and is equally effective for the actual threat model.

---

## §7. Layer 1 — Post-Catch-Up Push

**Complexity**: ~200 lines | **Dependencies**: Platform SDK only | **New infrastructure**: Zero

### How It Works

```
Pipeline Step 19b (coaching nudge)
    ↓
Step 20 (NEW): Channel Push — scripts/channel_push.py
    ↓
Load config/channels.yaml
    ↓
if defaults.push_enabled == false → skip (master kill switch)
    ↓
For each enabled channel where features.push: true
    ↓
    For each recipient where push: true
        ↓
        1. Format briefing → flash format (≤max_push_length chars)
           Sources: latest briefing file, state/health-check.md (alerts),
                    state/open_items.md (tasks)
        ↓
        2. Apply access_scope filter
           full     → all content
           family   → strip immigration, finance, estate, insurance sections
           standard → calendar + tasks only
        ↓
        3. pii_guard.filter_text(formatted_message)
           Mandatory. Returns (filtered_text, pii_types_found).
           If PII found: send filtered version, log PII_FOUND types.
        ↓
        4. channel_adapter.send_message(ChannelMessage(
               text=filtered_text,
               recipient_id=recipient.id,
               buttons=[
                   {"label": "Status", "command": "/status"},
                   {"label": "Tasks",  "command": "/tasks"},
                   {"label": "Alerts", "command": "/alerts"},
               ] if channel.features.buttons else []
           ))
        ↓
        5. Audit → state/audit.md:
           [ISO] CHANNEL_PUSH | channel: telegram | recipient: primary
                 | chars: 342 | pii_filtered: false | scope: full
    ↓
Continue to "Catch-up complete."
```

### Push Message Format

Uses the existing **flash briefing** format, truncated to `max_push_length`:

**Full scope (`full`):**
```
ARTHA · Friday, Mar 13

3 alerts today.
🔴 EAD renewal deadline in 28 days — start I-765 prep
🟡 Arjun: 2 missing Canvas assignments (AP Physics, AP CS)
🟢 PSE bill paid ✓ | Fidelity 401k rebalance window open

/status for full pulse · /tasks for action items
```

**Family scope (`family`) — same system, filtered audience:**
```
ARTHA · Friday, Mar 13

Family update
📅 Ananya's orchestra concert Thursday 6 PM — added to calendar
📚 Arjun: 2 assignments due this week
🏠 PSE bill paid ✓

/tasks for action items
```

No immigration data. No financial details. No A-numbers. The same infrastructure, adapted to the audience.

### Integration with Catch-Up Pipeline

The push hook is invoked by the AI CLI session as **Step 20** of the catch-up workflow. It runs inline, after Step 19b, before the "Catch-up complete" message:

- No new process management
- No daemon lifecycle
- Runs in the existing Python venv
- Uses existing `lib/auth.py` for credential loading
- Uses existing `lib/retry.py` for send retries
- Uses existing `pii_guard.py` for redaction
- Logs to existing `state/audit.md`
- Failures are non-blocking (P1 — logged, catch-up continues)

### Push Deduplication Across Machines

Artha's workspace is synced via OneDrive. If a user runs catch-up on machine A at 7 AM and again on machine B at 7:15 AM, both runs invoke Step 20 and would deliver duplicate pushes.

**Design**: Before sending, `channel_push.py` checks for a daily marker file:

```
state/.channel_push_marker_{YYYY-MM-DD}.json
```

Contents: `{"host": "DESKTOP-HOME", "pushed_at": "2026-03-13T07:12:04Z", "channels": ["telegram"]}`

If the marker exists and `pushed_at` is less than 12 hours old, the push is skipped with a `CHANNEL_PUSH_SKIPPED` audit event. If the marker is stale (>12h, e.g., a second catch-up in the evening), the push proceeds normally and overwrites the marker.

Marker files older than 7 days are cleaned up automatically. The 12-hour window balances dedup safety with the ability to push again after an evening catch-up.

### Degraded Mode: Pending Push Queue

If the channel API is unreachable during push (e.g., Telegram down at 6 AM), the retry loop will exhaust its 3 attempts. Without a queue, the push is permanently lost.

**Design**: On final retry failure, write a pending push file:

```
state/.pending_pushes/{channel}_{recipient}_{ISO_timestamp}.json
```

Contents: `{"channel": "telegram", "recipient_id": "...", "text": "...", "scope": "full", "created": "ISO"}`

The next catch-up run checks `state/.pending_pushes/` before generating new pushes. If pending files exist and the channel is now healthy, send them first (oldest first), then delete the file. Pending pushes older than 24 hours are discarded (stale intelligence is worse than no intelligence).

This adds ~30 lines to `channel_push.py` and requires no new dependencies.

### Push Format Adaptation

The existing flash briefing format is designed for terminal display. Channel platforms have their own formatting constraints:

| Platform | Format | Constraints |
|----------|--------|-------------|
| Telegram | Markdown V2 (escaped) | 4096 char limit; specific escape rules for `.`, `-`, `(`, `)` |
| Discord | Discord Markdown | 2000 char limit; embed objects for rich formatting |
| Slack | Block Kit / mrkdwn | 3000 char limit per block; different bold/italic syntax |
| Signal | Plain text | No rich formatting |

Each adapter's `send_message()` is responsible for translating the Artha-internal format (plain text with emoji indicators) into platform-native formatting. The `ChannelMessage.text` field contains platform-agnostic text; the adapter renders it. This keeps the push hook format-unaware and the adapter format-responsible — same pattern as `ConnectorHandler` owning its own parse logic.

The `max_push_length` in `channels.yaml` should be set per-channel to respect platform limits (default 500 is safe for all platforms).

### Exact Insertion Point in Artha.md

Insert **before** the "Catch-up complete" line (currently at line ~900 of `config/Artha.md`), after Step 19b:

```markdown
### Step 20 — Channel Push (optional)
If `config/channels.yaml` exists and `defaults.push_enabled` is `true`:

```bash
python scripts/channel_push.py
```

- Sends flash briefing summary to each enabled channel recipient
- Per-recipient `access_scope` filtering applied before send
- `pii_guard.filter_text()` runs on every outbound message
- Failures are non-blocking — log warning, continue to catch-up completion
- On final retry failure, writes pending push to `state/.pending_pushes/` for next run
- Audit: `CHANNEL_PUSH` events logged to `state/audit.md`
- If `channels.yaml` is missing or `push_enabled: false`, this step is silently skipped
```

**Do NOT renumber existing Steps 0–19b.** Step 20 is an additive extension.

### Feature Flag

```yaml
# In channels.yaml → defaults
push_enabled: false    # Master kill switch — explicitly opt-in
```

Additionally, in `config/implementation_status.yaml`:
```yaml
channel_push:
  status: implemented
  confidence: high
  feature_flag: channels.yaml → defaults.push_enabled
```

---

## §8. Layer 2 — Interactive Listener

**Complexity**: ~500 lines | **Dependencies**: Platform SDK + asyncio | **New infrastructure**: Background service

### When to Build

Ship Layer 2 **only after** Layer 1 is stable and tested for at least 2 weeks. Layer 1 validates the adapter protocol, the PII pipeline, the scope filtering, and the audit trail. Layer 2 builds on proven foundation.

### Design

```python
# scripts/channel_listener.py --channel telegram [--channel discord]
import asyncio
import signal
import threading

async def main(channels: list[str]):
    registry = load_channel_registry()
    adapters = {ch: registry.create_adapter(ch) for ch in channels}

    # Command dispatch — hardcoded, not user-configurable
    COMMANDS = {
        "/status":  cmd_status,     # → state/health-check.md
        "/alerts":  cmd_alerts,     # → critical/urgent from latest briefing
        "/tasks":   cmd_tasks,      # → state/open_items.md
        "/quick":   cmd_quick,      # → ≤5 min tasks from open_items.md
        "/domain":  cmd_domain,     # → single domain deep-read (requires arg)
        "/help":    cmd_help,       # → list available commands
        "/unlock":  cmd_unlock,     # → PIN-based session token
    }

    # Graceful shutdown — cross-platform (Windows does not support
    # asyncio.add_signal_handler; use signal.signal + threading.Event)
    shutdown = threading.Event()
    def _request_shutdown(*_args):
        shutdown.set()
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    # Poll all channels concurrently via asyncio.gather to prevent
    # one slow/hung adapter from blocking the others.
    while not shutdown.is_set():
        poll_tasks = [
            poll_with_resilience(adapter, name)
            for name, adapter in adapters.items()
        ]
        results = await asyncio.gather(*poll_tasks, return_exceptions=True)
        for (name, adapter), result in zip(adapters.items(), results):
            if isinstance(result, Exception):
                log.error(f"[{name}] Poll error: {result}")
                continue
            for msg in result:
                await process_message(msg, adapter, name, COMMANDS)
```

### Command Processing Flow

```python
async def process_message(msg, adapter, channel_name, commands):
    config = get_channel_config(channel_name)
    recipient = find_recipient(config, msg.sender_id)

    # 1. Sender whitelist — silent rejection
    if recipient is None:
        audit_log("CHANNEL_REJECT", channel=channel_name,
                  sender=msg.sender_id, reason="unknown_sender")
        return  # Do NOT respond to unknown senders

    # 2. Dedup + timestamp validation
    if is_duplicate(msg.message_id) or is_stale(msg.timestamp, max_age_sec=300):
        return

    # 2a. Per-sender rate limiting (10 commands/minute)
    if is_rate_limited(msg.sender_id, max_per_minute=10):
        audit_log("CHANNEL_RATE_LIMIT", channel=channel_name,
                  sender=recipient.name, cooldown_sec=60)
        return  # Silent drop — do not respond during cooldown

    audit_log("CHANNEL_IN", channel=channel_name,
              sender=recipient.name, command=msg.command)

    # 3. Command whitelist
    handler = commands.get(msg.command)
    if handler is None:
        response = "Unknown command. Send /help for available commands."
    else:
        # 4. Scope + sensitivity gate for critical domains
        if requires_critical_access(msg.command, msg.args):
            if not has_valid_session_token(msg.sender_id):
                response = "This requires /unlock <PIN> first."
            else:
                response = await handler(msg.args, recipient.access_scope)
        else:
            response = await handler(msg.args, recipient.access_scope)

    # 5. Staleness indicator — every response includes data freshness
    state_age = get_state_file_age(msg.command)  # returns human-readable age
    response = f"{response}\n\n_Last updated: {state_age} ago_"

    # 6. PII redaction — mandatory, no exceptions
    filtered, pii_types = pii_guard.filter_text(response)

    # 7. Send
    adapter.send_message(ChannelMessage(
        text=filtered, recipient_id=msg.sender_id))

    # 8. Audit
    audit_log("CHANNEL_OUT", channel=channel_name,
              recipient=recipient.name, chars=len(filtered),
              pii_filtered=bool(pii_types))
```

### Critical Constraint: Read-Only

The interactive listener **never**:
- Runs the catch-up pipeline
- Writes to state files
- Decrypts vault files (reads only non-encrypted state)
- Invokes the AI CLI or any LLM
- Executes actions from `actions.yaml`

It reads: `open_items.md`, `health-check.md`, `dashboard.md`, `goals.md`, `calendar.md` — all non-encrypted, non-sensitive structural files.

This is the security architecture. The listener is a **formatted state reader**, not a second brain.

### Staleness Indicators

State files are only updated during catch-up. Between catch-ups, a `/status` response could be 12+ hours stale. **Every listener response includes a staleness indicator** showing when the underlying data was last modified:

```
ARTHA Status · Friday, Mar 13
[...active alerts and status...]

_Last updated: 2h 14m ago_
```

The `get_state_file_age()` helper reads `os.path.getmtime()` on the relevant state file and formats the delta as human-readable text. If the data is >12 hours old, the staleness line is prefixed with ⚠️ to visually flag it.

### State Files Readable by Listener (Whitelist)

| File | Commands | Encrypted? |
|------|----------|-----------|
| `state/health-check.md` | `/status` | No |
| `state/open_items.md` | `/tasks`, `/quick` | No |
| `state/dashboard.md` | `/status` | No |
| `state/goals.md` | `/status` | No |
| `state/calendar.md` | `/status`, `/alerts` | No |
| `state/comms.md` | `/alerts` | No |
| `state/home.md` | `/domain home` | No |
| `state/kids.md` | `/domain kids` | No |
| `briefings/{latest}.md` | `/alerts` | No |

Vault-encrypted files (`finance.md.age`, `immigration.md.age`, etc.) are **never** accessed by the listener. If a `full`-scope user sends `/domain immigration`, the response is: "Immigration details are available in your CLI session. Channel access is limited to non-encrypted domains."

### Reconnection & Resilience

```python
# Uses existing lib/retry.py exponential backoff pattern

async def poll_with_resilience(adapter, channel_name):
    backoff = ExponentialBackoff(base=2.0, max=300.0, multiplier=2.0)
    while True:
        try:
            messages = await adapter.poll(timeout=30)
            backoff.reset()
            return messages
        except ConnectionError:
            delay = backoff.next()
            log.warning(f"[{channel_name}] Connection lost. Retry in {delay:.0f}s")
            await asyncio.sleep(delay)
```

### Multi-Machine Coordination

Artha's workspace lives on OneDrive and is accessible from multiple machines (e.g., desktop, laptop, work PC). The interactive listener is a **singleton process** — only one instance should poll a given bot token at any time. Running multiple listeners against the same bot causes:

1. **Message loss** — Telegram's `getUpdates` long-polling uses an offset. When instance A acknowledges an offset, instance B never sees those messages. Commands are randomly consumed by whichever instance polls first.
2. **Inconsistent state reads** — OneDrive sync lag (10s–2min) means different machines may read different versions of state files.
3. **Audit log conflicts** — Two processes appending to `state/audit.md` via OneDrive sync will create merge conflicts or silent data loss.

#### Solution: Explicit Host Designation

The battle-tested pattern for personal multi-machine setups is **explicit single-leader designation** — the same approach used by database primary/replica configs, cron job scheduling, and home automation hubs. No distributed consensus protocol is needed for a 1–3 machine personal system.

**Primary guard — `listener_host` in channels.yaml:**

```yaml
defaults:
  listener_host: "DESKTOP-HOME"   # Only this machine runs the listener
```

On startup, `channel_listener.py` executes:

```python
import socket

def verify_listener_host(config: dict) -> bool:
    """Refuse to start if this machine is not the designated listener host."""
    designated = config.get("defaults", {}).get("listener_host", "")
    if not designated:
        # Empty = single-machine mode, allow any host (with warning)
        log.warning("listener_host not set — assuming single-machine setup. "
                    "Set defaults.listener_host in channels.yaml for multi-machine safety.")
        return True
    current = socket.gethostname()
    if current.lower() != designated.lower():
        log.info(f"Listener skipped: this host ({current}) is not the "
                 f"designated listener ({designated}). Exiting cleanly.")
        return False
    return True
```

This check runs before any adapter initialization, before any API call, before any polling loop. A non-designated host exits with code 0 (not an error — expected behavior). The OS service (Task Scheduler / launchd / systemd) can be installed on all machines; only the designated host actually runs.

**Secondary guard — Telegram API pre-check (defense-in-depth):**

Even with `listener_host`, defend against config drift or manual overrides:

```python
async def claim_polling_session(adapter):
    """Ensure clean polling state before entering the main loop.
    Mirrors the standard Telegram bot deployment practice of
    flushing stale offsets on startup."""
    # 1. Clear any webhook (prevents webhook/polling conflict)
    await adapter.delete_webhook()
    # 2. Flush pending updates (consume and discard stale messages)
    await adapter.flush_pending_updates()
    # 3. Log claim
    audit_log("CHANNEL_LISTENER_START", host=socket.gethostname())
```

This is the same startup sequence used by `python-telegram-bot`'s `Application.run_polling(drop_pending_updates=True)` and is the standard practice for Telegram bot deployments.

#### Multi-Machine UX: How It Works In Practice

**Scenario 1: Desktop at home is the always-on listener**
```
# On DESKTOP-HOME (always on):
$ python scripts/setup_channel.py --install-service
  → Detects hostname: DESKTOP-HOME
  → Sets defaults.listener_host: "DESKTOP-HOME" in channels.yaml
  → Installs system service
  ✓ Listener runs 24/7 on this machine

# On LAPTOP-TRAVEL:
$ python scripts/setup_channel.py --install-service
  ⚠ Listener host is set to DESKTOP-HOME.
  → Install service anyway? Service will remain idle on this host.
  → [Y] Install as standby  [N] Skip  [C] Change listener host
```

**Scenario 2: Switching the designated listener to laptop for travel**
```
$ python scripts/setup_channel.py --set-listener-host
  Current listener host: DESKTOP-HOME
  New listener host [LAPTOP-TRAVEL]: ▌
  ✓ Updated channels.yaml: listener_host → LAPTOP-TRAVEL
  ✓ Change will take effect on next listener restart
  ⚠ Remember to change back when you return home
```

**Scenario 3: Push from any machine (Layer 1 — no issue)**
```
# Catch-up can run from ANY machine.
# Push dedup marker prevents duplicate briefings (see "Push Deduplication").
# No listener_host check needed for push — it's a one-shot inline operation.
```

#### Why NOT Lock Files Over OneDrive

The obvious alternative — a lock file in `state/` with heartbeat — is **unreliable over cloud-synced filesystems**. OneDrive sync latency ranges from 10 seconds to 2+ minutes depending on network conditions and file activity. A lock file acquired on machine A may not be visible on machine B for 90 seconds — long enough for both machines to believe they hold the lock. This is a well-known failure mode in distributed systems literature ("[Chubby lock service](https://research.google/pubs/pub27897/)" exists precisely because file-based locks don't work across network boundaries).

Explicit host designation avoids the coordination problem entirely: there is no lock to acquire, no heartbeat to maintain, no race condition to resolve. The config file is the source of truth, and hostname comparison is instantaneous and deterministic.

---

## §9. Layer 3 — Ingest & Gated Actions

**Status**: Design sketch only — will be promoted to a **separate spec** before implementation.

> **Scope note**: Layer 3 introduces significant new dependencies (Whisper.cpp, pytesseract, audio processing) and operational surfaces that warrant their own spec document with independent threat modeling, dependency analysis, and implementation plan. The sketch below captures design intent to inform Layers 1–2 architecture, but is **not an implementation commitment** in this spec.

### Capabilities (Design Intent)

1. **Voice memo → transcription → state update proposal (human-gated)**
   - User sends voice note to Artha channel
   - Adapter downloads audio, runs local transcription (Whisper.cpp or platform STT)
   - Transcribed text formatted as action proposal: "Update finance.md: fuel expense $40"
   - User must `/approve` or `/reject` via channel — no auto-write

2. **Document upload → OCR → extraction → state update proposal (human-gated)**
   - User uploads PDF/photo
   - Local OCR (pytesseract or platform OCR) — no cloud processing
   - Extracted text formatted as action proposal
   - Same `/approve` / `/reject` gate

3. **Action buttons → `/approve OI-042` → execute approved action**
   - Only for pre-proposed actions from the catch-up pipeline
   - Action must already exist in `state/open_items.md` with `proposed_action` field
   - Requires valid session token for critical domains
   - PII-checked before execution (same gate as CLI actions)

### Why Human-Gated

Any operation that writes state must go through the same approval pipeline as CLI actions (§13 of Artha.md). The channel bridge does not get a special bypass. The friction is the feature — it prevents a lost phone from corrupting life data.

### Secure File Request (Constrained)

"Send me Arjun's immunization record" — only allowed for files with `standard` sensitivity. Files from `critical` or `high` sensitivity domains (immigration, finance, estate, insurance) are **never** sent to a cloud chat platform, regardless of recipient scope.

---

## §10. Cross-Platform Service Management

### Service Installer

```bash
# Interactive service setup (Layer 2 only)
python scripts/setup_channel.py --install-service

# Detects OS automatically:
#   Windows → creates Scheduled Task (runs at login, restarts on failure)
#   macOS   → creates launchd plist (KeepAlive: true)
#   Linux   → creates systemd unit (Restart=on-failure)

# Multi-machine: sets listener_host to current hostname if not already set.
# If listener_host is set to a different machine, prompts user:
#   [Y] Install as standby  [N] Skip  [C] Change listener host

# Switch designated listener to another machine:
python scripts/setup_channel.py --set-listener-host
```

### Windows (Primary Development Target)

```xml
<!-- scripts/service/artha-listener.xml (template) -->
<Task>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
  </Triggers>
  <Settings>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <AllowStartOnDemand>true</AllowStartOnDemand>
  </Settings>
  <Actions>
    <Exec>
      <Command>{venv_python}</Command>
      <Arguments>scripts/channel_listener.py --channel telegram</Arguments>
      <WorkingDirectory>{artha_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
```

### macOS

```xml
<!-- scripts/service/com.artha.channel-listener.plist -->
<plist version="1.0">
<dict>
  <key>Label</key><string>com.artha.channel-listener</string>
  <key>ProgramArguments</key>
  <array>
    <string>{venv_python}</string>
    <string>scripts/channel_listener.py</string>
    <string>--channel</string>
    <string>telegram</string>
  </array>
  <key>WorkingDirectory</key><string>{artha_dir}</string>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{artha_dir}/tmp/channel-listener.log</string>
  <key>StandardErrorPath</key><string>{artha_dir}/tmp/channel-listener.err</string>
</dict>
</plist>
```

### Linux

```ini
# scripts/service/artha-listener.service
[Unit]
Description=Artha Channel Listener
After=network-online.target

[Service]
ExecStart={venv_python} scripts/channel_listener.py --channel telegram
WorkingDirectory={artha_dir}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

### Credential Storage (All Platforms)

Uses `keyring` library — same infrastructure as Gmail OAuth, Outlook OAuth, iCloud auth:

| Credential | Keyring Service | Keyring Key |
|-----------|----------------|-------------|
| Telegram bot token | `artha` | `artha-telegram-bot-token` |
| Discord bot token | `artha` | `artha-discord-bot-token` |
| Slack bot token | `artha` | `artha-slack-bot-token` |
| Channel PIN | `artha` | `artha-channel-pin` |

Stored via: `python scripts/setup_channel.py --channel telegram`

### Setup Wizard Design: `setup_channel.py`

The setup wizard is referenced throughout this spec but is non-trivial UX. Here is the Telegram flow (reference implementation — other adapters follow the same pattern):

```
$ python scripts/setup_channel.py --channel telegram

╔══════════════════════════════════════════════╗
║  Artha Channel Setup — Telegram              ║
╚══════════════════════════════════════════════╝

Step 1/4: Create a Telegram Bot
  → Open Telegram and message @BotFather
  → Send: /newbot
  → Choose a name (e.g., "Artha Family")
  → Choose a username (e.g., "artha_family_bot")
  → Copy the bot token (format: 123456:ABC-DEF...)

Paste bot token: ▌
  ✓ Token stored in keyring (artha-telegram-bot-token)
  ✓ Bot API verified: "Artha Family" (@artha_family_bot)

Step 2/4: Discover Your Chat ID
  → Open Telegram and send any message to @artha_family_bot
  → Press Enter when done...

  ✓ Found 1 chat: "Vivek" (chat_id: 123456789)
  Use this as your primary recipient? [Y/n]: ▌

Step 3/4: Add Family Recipients (optional)
  Add another recipient? [y/N]: ▌
  → Have them message @artha_family_bot, then press Enter
  ✓ Found: "Spouse" (chat_id: 987654321)
  Access scope for "Spouse"? [full/family/standard] (default: family): ▌

Step 4/4: Set Channel PIN
  → This PIN is used for /unlock in sensitive domain queries.
  Enter 4-6 digit PIN: ▌
  ✓ PIN stored in keyring (artha-channel-pin)

✓ channels.yaml written to config/channels.yaml
✓ Telegram channel enabled with 2 recipients
✓ Run a catch-up to test push delivery
```

The wizard:
- Uses `getUpdates` API to discover chat IDs (user must message bot first)
- Stores token and PIN in keyring (never in YAML)
- Writes `channels.yaml` with discovered recipient IDs
- Validates bot token via `health_check()` before proceeding
- Supports `--install-service` flag (Phase 2) for OS service setup

---

## §11. Observability & Audit

### Audit Events

| Event | Trigger | Fields |
|-------|---------|--------|
| `CHANNEL_PUSH` | Post-catch-up push sent | channel, recipient, chars, pii_filtered, scope |
| `CHANNEL_IN` | Inbound command received | channel, sender, command |
| `CHANNEL_OUT` | Response sent | channel, recipient, chars, pii_filtered |
| `CHANNEL_REJECT` | Unknown sender blocked | channel, sender_id, reason |
| `CHANNEL_HEALTH` | Health check result | channel, healthy, latency_ms |
| `CHANNEL_ERROR` | Adapter error (send failure, timeout) | channel, error_type, message |
| `CHANNEL_SESSION` | Session token issued or expired | channel, recipient, action (unlock/expire) |
| `CHANNEL_RATE_LIMIT` | Sender exceeded 10 commands/minute | channel, sender, cooldown_sec |
| `CHANNEL_PUSH_PENDING` | Push failed, queued for retry | channel, recipient, pending_file |
| `CHANNEL_PUSH_SKIPPED` | Push skipped (already sent today) | channel, marker_host, marker_time |
| `CHANNEL_LISTENER_START` | Listener daemon started on designated host | host, channels |
| `CHANNEL_LISTENER_SKIP` | Listener startup skipped (non-designated host) | host, designated_host |

All events follow existing `state/audit.md` format:
```
[2026-03-13T07:12:04Z] CHANNEL_PUSH | channel: telegram | recipient: primary | chars: 342 | pii_filtered: false | scope: full
[2026-03-13T07:12:05Z] CHANNEL_PUSH | channel: telegram | recipient: spouse | chars: 198 | pii_filtered: false | scope: family
[2026-03-13T09:30:12Z] CHANNEL_IN | channel: telegram | sender: primary | command: /status
[2026-03-13T09:30:13Z] CHANNEL_OUT | channel: telegram | recipient: primary | chars: 856 | pii_filtered: false
```

### Preflight Extension

Add to `preflight.py` as **P1 checks** (warnings only, non-blocking):

```python
def check_channel_health():
    """Verify enabled channels are reachable. P1 — warning only."""
    channels_cfg = load_channels_config()
    for name, cfg in channels_cfg.get("channels", {}).items():
        if cfg.get("enabled") and cfg.get("health_check"):
            adapter = create_adapter_from_config(name, cfg)
            result = adapter.health_check()
            yield CheckResult(
                name=f"channel.{name}",
                severity="P1",
                passed=result,
                message=f"Channel {name}: {'healthy' if result else 'unreachable'}",
                fix_hint=f"Run: python scripts/setup_channel.py --channel {name}"
            )
```

### Health Check State

Append to `state/health-check.md`:
```yaml
channel_health:
  telegram:
    last_check: "2026-03-13T07:00:00Z"
    healthy: true
    last_push: "2026-03-13T07:12:04Z"
    push_count_today: 1
  discord:
    last_check: "2026-03-13T07:00:00Z"
    healthy: false
    error: "Bot token expired"
```

---

## §12. Distribution Checklist

### Must Be True Before v2.0 Release

**Protocol & Infrastructure**
- [ ] `ChannelAdapter` protocol in `scripts/channels/base.py`
- [ ] `ChannelMessage` + `InboundMessage` frozen dataclasses in `base.py`
- [ ] Channel registry loader in `scripts/channels/registry.py`
- [ ] Adapter path validation: must be under `scripts/channels/`

**Configuration**
- [ ] `channels.example.yaml` template with Telegram, Discord, Slack (no PII)
- [ ] `channels.yaml` added to `.gitignore`
- [ ] Feature flag: `push_enabled: false` (opt-in default)
- [ ] Access scope definitions: `full`, `family`, `standard`

**Reference Implementation**
- [ ] Telegram adapter (`scripts/channels/telegram.py`) — push + interactive + health_check
- [ ] Full test coverage for adapter (mock Telegram API)

**Push (Layer 1)**
- [ ] `channel_push.py` with scope filter + PII redaction + audit
- [ ] Step 20 documented in `config/Artha.md`
- [ ] Flash briefing format for push (≤500 chars)
- [ ] Non-blocking: push failures log warning, don't halt catch-up

**Security**
- [ ] `pii_guard.filter_text()` on every outbound message — no bypass path
- [ ] Per-recipient `access_scope` tested: `full`, `family`, `standard`
- [ ] Sender whitelist with silent rejection + audit
- [ ] Command whitelist (hardcoded, not configurable)
- [ ] PIN session tokens for critical domain access
- [ ] Message dedup + timestamp validation
- [ ] No vault-encrypted files accessible from listener
- [ ] Per-sender rate limiting (10 commands/minute) with audit
- [ ] Recipient IDs documented as PII in privacy surface
- [ ] Staleness indicator appended to every listener response
- [ ] `listener_host` designation prevents multi-instance listener
- [ ] Push dedup marker prevents duplicate briefings across machines

**Cross-Platform**
- [ ] Windows Task Scheduler template
- [ ] macOS launchd plist template
- [ ] Linux systemd unit template
- [ ] `setup_channel.py --install-service` detects OS

**Integration**
- [ ] `preflight.py` P1 channel health checks
- [ ] `implementation_status.yaml` updated: replace `whatsapp_bridge` with `channel_bridge` + add `channel_push`, `channel_listener`
- [ ] `actions.yaml`: update commented `send_telegram` template to reference `scripts/channels/telegram.py`
- [ ] `pyproject.toml`: add `channels` optional dependency group + include `scripts.channels` in packages
- [ ] 0 new mandatory dependencies (adapter SDKs installed on demand per `pip install artha[channels]`)

**Documentation**
- [ ] `docs/channels.md` setup guide per platform
- [ ] `config/observability.md` updated with channel audit events
- [ ] Pending push queue (`state/.pending_pushes/`) with 24h expiry
- [ ] Format adaptation documented per platform (Telegram MD, Discord, Slack, plain text)
- [ ] Integration test: mock adapter → push → verify redaction + scope

---

## §13. Implementation Plan

### Phase 1: Push Foundation (Layer 1)

**Exit Criteria**: Post-catch-up push delivers flash briefing to one Telegram recipient with PII redaction and audit logging.

| Step | Task | Depends On | Deliverable |
|------|------|-----------|------------|
| 1.1 | Define `ChannelAdapter` protocol + dataclasses | — | `scripts/channels/base.py` |
| 1.2 | Create `channels.example.yaml` template | — | `config/channels.example.yaml` |
| 1.3 | Build channel registry loader | 1.1, 1.2 | `scripts/channels/registry.py` |
| 1.4 | Implement Telegram adapter (`send_message` + `health_check`) | 1.1 | `scripts/channels/telegram.py` |
| 1.5 | Build `channel_push.py` with scope filter + PII gate | 1.3, 1.4 | `scripts/channel_push.py` |
| 1.6 | Build `setup_channel.py` interactive wizard | 1.2 | `scripts/setup_channel.py` |
| 1.7 | Add `config/channels.yaml` to `.gitignore` | — | `.gitignore` |
| 1.8 | Document Step 20 in `Artha.md` (insert before line ~900) | 1.5 | `config/Artha.md` |
| 1.9 | Extend `preflight.py` with P1 channel health checks | 1.3 | `scripts/preflight.py` |
| 1.10 | Add `channels` optional dep group to `pyproject.toml` | — | `pyproject.toml` |
| 1.11 | Add `scripts.channels` to `[tool.setuptools] packages` | — | `pyproject.toml` |
| 1.12 | Update `actions.yaml` — point `send_telegram` to `scripts/channels/telegram.py` | 1.4 | `config/actions.yaml` |
| 1.13 | Update `implementation_status.yaml` with `channel_bridge`, `channel_push` | 1.5 | `config/implementation_status.yaml` |
| 1.14 | Integration test with mock adapter | 1.5 | `tests/test_channel_push.py` |
| 1.15 | Write `docs/channels.md` | All above | `docs/channels.md` |

### Phase 2: Interactive Listener (Layer 2)

**Prerequisite**: Phase 1 stable for ≥2 weeks.

**Exit Criteria**: `/status`, `/alerts`, `/tasks` commands return formatted, redacted, scope-filtered responses via Telegram.

| Step | Task | Depends On | Deliverable |
|------|------|-----------|------------|
| 2.1 | Add `poll()` to Telegram adapter | Phase 1 | `scripts/channels/telegram.py` |
| 2.2 | Build `channel_listener.py` with asyncio event loop | 2.1 | `scripts/channel_listener.py` |
| 2.3 | Implement command handlers (status, alerts, tasks, quick, domain, help) | 2.2 | In `channel_listener.py` |
| 2.4 | Implement PIN-based session tokens + `/unlock` command | 2.2 | In `channel_listener.py` |
| 2.5 | Add sender whitelist + message dedup + timestamp validation + rate limiting | 2.2 | In `channel_listener.py` |
| 2.6 | Create OS service templates (Windows, macOS, Linux) | — | `scripts/service/` |
| 2.7 | Extend `setup_channel.py` with `--install-service` | 2.6 | `scripts/setup_channel.py` |
| 2.8 | Reconnection with exponential backoff | 2.2 | In `channel_listener.py` |
| 2.9 | Add staleness indicators to all command responses | 2.3 | In `channel_listener.py` |
| 2.10 | Implement `listener_host` check + Telegram API pre-check on startup | 2.2 | In `channel_listener.py` |
| 2.11 | Add `--set-listener-host` to `setup_channel.py` | 2.7 | `scripts/setup_channel.py` |
| 2.12 | Integration test: mock inbound → formatted response with redaction + staleness | 2.3 | `tests/test_channel_listener.py` |

### Phase 3: Multi-Channel + Ingest (Layer 3)

**Prerequisite**: Phase 2 stable. Telegram adapter battle-tested.

| Step | Task | Depends On | Deliverable |
|------|------|-----------|------------|
| 3.1 | Discord adapter | Phase 1 protocol | `scripts/channels/discord.py` |
| 3.2 | Slack adapter | Phase 1 protocol | `scripts/channels/slack.py` |
| 3.3 | Voice memo transcription (local Whisper.cpp) | Phase 2 | `scripts/channels/ingest.py` |
| 3.4 | Document OCR pipeline (local pytesseract) | Phase 2 | `scripts/channels/ingest.py` |
| 3.5 | `/approve` and `/reject` command handlers | Phase 2 | In `channel_listener.py` |
| 3.6 | 2-recipient integration test (full + family scope) | Phase 1 | `tests/test_scope_filter.py` |

---

## §14. Codebase Integration Map

Exact files that must be touched for Phase 1, with the specific change required:

| File | Change | Risk |
|------|--------|------|
| `.gitignore` (line ~7) | Add `config/channels.yaml` after `config/Artha.identity.md` | None |
| `config/Artha.md` (line ~900) | Insert Step 20 block before "Catch-up complete" | Low — additive only |
| `config/implementation_status.yaml` (line ~150) | Replace `whatsapp_bridge: not_started` with `channel_bridge: not_started` + add `channel_push: not_started` and `channel_listener: not_started` | Low |
| `config/actions.yaml` (line ~125) | Update commented `send_telegram` handler path: `scripts/actions/telegram_send.py` → `scripts/channels/telegram.py` | None — still commented |
| `config/observability.md` | Add `CHANNEL_PUSH`, `CHANNEL_IN`, `CHANNEL_OUT`, `CHANNEL_REJECT`, `CHANNEL_HEALTH`, `CHANNEL_ERROR`, `CHANNEL_SESSION` event type definitions | Low |
| `pyproject.toml` (line ~37) | Add `channels = ["python-telegram-bot>=21.0"]` optional dep group; update `all` group; add `"scripts.channels"` to packages | Low |
| `scripts/preflight.py` | Add `check_channel_health()` P1 function + register in main check loop | Low — new P1 check |
| `scripts/requirements.txt` | Add `python-telegram-bot>=21.0` as optional/commented entry | None |

**Files NOT modified** (clarification):
- `scripts/generate_identity.py` — NO changes needed. Channel config lives in `channels.yaml` (separate from profile). Channel data should NOT appear in §1 Identity (it would leak recipient IDs into a potentially committed file).
- `config/user_profile.yaml` — NO `integrations.channels` section needed. Channel config is a separate registry (`channels.yaml`), paralleling `connectors.yaml`.
- `scripts/pipeline.py` — NOT modified. Step 20 is invoked by the AI CLI session, not by pipeline.py.

---

## §15. Decisions & Alternatives

### D1: Adapter Protocol vs. Monolithic Hub

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **Adapter protocol** | One YAML line to add platform; mirrors `ConnectorHandler`; community can contribute adapters | Each adapter is a separate module | ✅ **Chosen** — aligns with existing extensibility |
| Monolithic `telegram_hub.py` (v1.0) | Single file | Every new platform = full rewrite; zero code reuse; contradicts distribution goal | ❌ Rejected |

### D2: Push Hook vs. Standing Daemon

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **Post-pipeline push hook** | Zero new infrastructure; runs inline; no process management | Only fires after catch-up completes | ✅ **Chosen for Layer 1** — 80% value, 0% complexity |
| Standing daemon (v1.0) | Real-time | Laptop sleep, network drops, zombie processes, credential refresh | Deferred to Layer 2 (interactive only) |

### D3: Command Whitelist vs. Free-Form AI Parse

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **Command whitelist** | Zero injection risk; predictable; testable; no LLM cost | Less "magical" | ✅ **Chosen** — security is non-negotiable |
| AI parse of free-form text | Natural language | Prompt injection; unbounded latency; unpredictable; LLM cost per message | ❌ Rejected |

### D4: PIN Session vs. Biometric Unlock

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **PIN + 15-min expiry** | Implementable today; keyring-stored; cross-platform | Requires memorizing a PIN | ✅ **Chosen** — concrete beats aspirational |
| Biometric via iPhone Shortcut (v1.0) | Premium feel | No Telegram↔FaceID bridge exists; iOS-only; no design | ❌ Rejected — vaporware |

### D5: Read-Only Listener vs. Read-Write Daemon

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **Read-only listener** | No race conditions; minimal security surface; no vault access | Can't update state from phone | ✅ **Chosen for Layer 2** — safety by subtraction |
| Read-write daemon (v1.0 implied) | Full power from phone | Race conditions with pipeline; vault key exposed to daemon; doubled AI invoking | ❌ Rejected for Layers 1–2; gated ingest in Layer 3 |

### D6: Platform Comparison (Informational)

The adapter pattern makes platform choice a user preference, not an architectural constraint:

| Factor | Telegram | Discord | Slack | Signal |
|--------|----------|---------|-------|--------|
| Cost | $0 | $0 | $0 (free tier) | $0 |
| Bot Framework | Excellent | Excellent | Excellent | CLI-based |
| Buttons | Yes | Yes (components) | Yes (Block Kit) | No |
| File Upload | Yes | Yes | Yes | Yes |
| E2E Encryption | Optional | No | No | Always |
| Self-Hosted | No | No | No | Yes |
| Family Adoption | High | Medium | Low | Medium |

---

## Appendix: Migration from TCB v1.0

| v1.0 Concept | v2.0 Equivalent | What Changed |
|-------------|-----------------|-------------|
| `telegram_hub.py` | `scripts/channels/telegram.py` adapter + `channel_push.py` + `channel_listener.py` | Monolith → protocol + adapters |
| macOS launchd daemon | Cross-platform service templates (Windows/macOS/Linux) | Platform-locked → platform-agnostic |
| Biometric unlock | PIN + 15-min session tokens via keyring | Vaporware → implementable |
| `@ArthaOS_Bot` hardcoded | `channels.yaml` registry with any bot name | Hardcoded → configurable |
| Telegram-only InlineMenu | `ChannelMessage.buttons` rendered per-platform | Platform-specific → abstract |
| macOS Keychain | `keyring` library (Windows Credential Manager, macOS Keychain, Linux secretservice) | Single-OS → cross-platform |
| Location awareness | Removed (out of scope) | Feature creep eliminated |
| Telegram Mini-App | Removed (platform-proprietary) | Anti-distributable eliminated |
| 4-step implementation plan | 3-phase plan with 31 tracked steps + exit criteria | Vague → accountable |
