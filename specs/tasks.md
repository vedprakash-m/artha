# Artha Channel Bridge (ACB v2.0) — Execution Plan
**Version**: 1.0 | **Status**: In Progress | **Date**: 2026-03-13
**Classification**: Distributable (no PII)
**Spec**: `specs/conversational-bridge.md` v2.0.0
**Principal Engineer**: AI Implementation Agent

---

## Prime Directives (Always Active)

1. **Zero-Loss**: Every stateful change has a documented rollback path.
2. **No Big Bang**: Expand-and-Contract — new code alongside existing, then cut over.
3. **Verification After Each Step**: Don't assume `exit 0` = success.
4. **PII Is Non-Negotiable**: `pii_guard.filter_text()` on every outbound byte.
5. **Non-Blocking**: Channel failures must never interrupt the catch-up pipeline.

---

## Pre-Mortem: Failure Cascades

| If This Fails | Impact on Other Components | Recovery |
|---|---|---|
| `channels/base.py` not importable | `registry.py`, `telegram.py`, `channel_push.py`, `channel_listener.py` all fail | Fix import path; no state changed |
| `registry.py` path validation wrong | Arbitrary module loading (security regression) | Tighten prefix check; test before merge |
| `telegram.py` HTTP call fails | `channel_push.py` send fails → pending queue; `channel_listener.py` poll fails → retry | Non-blocking design absorbs this |
| `channel_push.py` crashes | Catch-up continues normally (non-blocking Step 20) | Audit log captures failure; user sees warning |
| `channel_listener.py` crashes | No interactive responses until restarted; catch-up unaffected | OS service auto-restarts |
| `preflight.py` channel check causes import error | Entire preflight fails → catch-up blocked | Wrap in try/except; P1 only |
| `channels.yaml` malformed YAML | `load_channels_config()` returns empty dict → push disabled | Graceful fallback to disabled state |

## Point of No Return

**This implementation has no point of no return.** Every change is additive:
- New files: `scripts/channels/`, `scripts/channel_push.py`, etc. can be deleted to rollback
- `config/Artha.core.md` addition is a single removable Step 20 block
- `preflight.py` channel check is a try/except P1 block
- `.gitignore` additions are non-destructive
- The **only** modification to existing runtime behavior is Step 20 in catch-up

**Rollback procedure (if needed):**
```bash
# Remove all new channel bridge files
rm -rf scripts/channels/ scripts/channel_push.py scripts/channel_listener.py scripts/setup_channel.py
rm -f config/channels.yaml config/channels.example.yaml docs/channels.md
# Revert modified files via git diff
git checkout config/Artha.core.md config/implementation_status.yaml pyproject.toml scripts/preflight.py
```

---

## Phase 1: Infrastructure Foundation

### 1.1 — Create `scripts/channels/__init__.py`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| Create package init | Package importable as `scripts.channels` | ✅ Created | `python -c "import scripts.channels"` |

### 1.2 — Create `scripts/channels/base.py`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| Define `ChannelMessage` dataclass | frozen=True, text + recipient_id + buttons | ✅ Created | `from scripts.channels.base import ChannelMessage` |
| Define `InboundMessage` dataclass | frozen=True, 7 fields | ✅ Created | Import succeeds |
| Define `ChannelAdapter` protocol | @runtime_checkable, 4 methods | ✅ Created | `isinstance(obj, ChannelAdapter)` works |

### 1.3 — Create `scripts/channels/registry.py`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| `load_adapter_module()` with path validation | Rejects paths outside `scripts/channels/` | ✅ Created | Test with bad path raises `ValueError` |
| `load_channels_config()` | Returns disabled config if `channels.yaml` missing | ✅ Created | No file → `push_enabled: False` |
| `create_adapter_from_config()` | Factory with retry params forwarded | ✅ Created | Unit test mock |

### 1.4 — Create `config/channels.example.yaml`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| Full template with telegram/discord/slack | No PII; push_enabled: false; all commented/disabled | ✅ Created | YAML valid; no secrets |

### 1.5 — Update `.gitignore`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| Add `config/channels.yaml` | Live config gitignored | ✅ Done | `git check-ignore config/channels.yaml` |
| Add pending push artifacts | Push queue gitignored | ✅ Done | `git check-ignore state/.pending_pushes/` |

**Phase 1 Health Check:** `python -c "from scripts.channels import base, registry; print('OK')"` → OK

---

## Phase 2: Reference Adapter (Telegram)

### 2.1 — Create `scripts/channels/telegram.py`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| `TelegramAdapter` class with `send_message()` | Returns True on success, False on failure (never raises) | ✅ Created | Unit test with mock HTTP |
| `health_check()` | Returns True if `getMe` succeeds | ✅ Created | Mock test |
| `delete_webhook()` + `flush_pending_updates()` | Cleanup for Layer 2 startup | ✅ Created | No-op on error |
| `poll()` with offset management | Returns `InboundMessage` list; updates `_update_offset` | ✅ Created | Mock test |
| `create_adapter()` factory | Loads token from keyring; falls back to env var | ✅ Created | Missing token → clear RuntimeError |
| `platform_name()` | Returns "Telegram" | ✅ Created | Trivial |
| MarkdownV2 escaping | All special chars escaped correctly | ✅ Created | Test with `._*()` chars |

**Phase 2 Health Check:** `python -c "from scripts.channels.telegram import platform_name; print(platform_name())"` → `Telegram`

---

## Phase 3: Layer 1 Push Hook

### 3.1 — Create `scripts/channel_push.py`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| `run_push()` — master orchestrator | Loads config; skips if push_enabled=false | ✅ Created | `--health` flag exits 0 |
| `_check_push_marker()` | Returns True if pushed in last 12h | ✅ Created | Unit test with fake marker |
| `_write_push_marker()` | Writes JSON to `state/.channel_push_marker_{date}.json` | ✅ Created | File created + parseable |
| `_send_pending_pushes()` | Reads queue; sends oldest first; 24h expiry | ✅ Created | Mock test |
| `_write_pending_push()` | Writes to `state/.pending_pushes/` | ✅ Created | File created |
| `_build_flash_message()` | Reads latest briefing; formats ≤500 chars | ✅ Created | Output test |
| `_apply_scope_filter()` | `family` strips immigration/finance lines; `standard` keeps calendar/tasks | ✅ Created | Unit test per scope |
| `_audit_log()` | Appends `CHANNEL_PUSH` event to `state/audit.md` | ✅ Created | File contains event |
| Non-blocking guarantee | Exception in send → log + continue; never raises | ✅ Created | Test with mock failure |
| `--health` flag | Exits 0 if channels.yaml found; exits 0 with note if missing | ✅ Created | `python scripts/channel_push.py --health` |

**Phase 3 Health Check:** `python scripts/channel_push.py --health` → `Channel push: channels.yaml not configured (push disabled)`

---

## Phase 4: Layer 2 Interactive Listener

### 4.1 — Create `scripts/channel_listener.py`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| `main()` + asyncio event loop | Starts polling; handles Ctrl+C | ✅ Created | Dry-run with mock adapter |
| `verify_listener_host()` | Refuses to start on non-designated host | ✅ Created | Test with mismatched hostname |
| `process_message()` | Full validation pipeline | ✅ Created | Unit test each stage |
| Sender whitelist | Unknown sender → silent reject + audit CHANNEL_REJECT | ✅ Created | Test unknown sender |
| Message dedup | Duplicate message_id → skip | ✅ Created | Test same ID twice |
| Timestamp validation | Message >5 min old → skip | ✅ Created | Test old timestamp |
| Rate limiting | 11th cmd/min → 60s cooldown + CHANNEL_RATE_LIMIT audit | ✅ Created | Test burst |
| Command handlers (6) | `/status /alerts /tasks /quick /domain /help` | ✅ Created | Each tested |
| `/unlock` + PIN session | 15-min expiry; PIN from keyring | ✅ Created | Test token lifecycle |
| Staleness indicator | `_Last updated: Xh Ym ago_` on every response | ✅ Created | Append verified |
| Scope filter on responses | `family` can't query immigration | ✅ Created | Test family+immigration |
| Encrypted file guard | `*.md.age` files never accessed | ✅ Created | Path check in read utils |
| Cross-platform shutdown | `threading.Event` instead of `add_signal_handler` | ✅ Created | Windows-compatible |
| Exponential backoff on poll error | Uses `lib/retry.py` pattern | ✅ Created | Error → delay → retry |
| `claim_polling_session()` | `deleteWebhook` + `flushPendingUpdates` on startup | ✅ Created | Idempotent |

**Phase 4 Health Check:** `python scripts/channel_listener.py --dry-run` → exits 0 with "no channels configured"

---

## Phase 5: Setup Wizard + Service Templates

### 5.1 — Create `scripts/setup_channel.py`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| `--channel telegram` wizard | 4-step interactive setup; writes channels.yaml | ✅ Created | Dry-run mode |
| `--health` passthrough | Runs health_check() on all enabled channels | ✅ Created | `python scripts/setup_channel.py --health` |
| `--install-service` | OS-detected; writes service template | ✅ Created | File created (no actual registration) |
| `--set-listener-host` | Updates `defaults.listener_host` in channels.yaml | ✅ Created | YAML updated |
| Token stored in keyring | Never written to channels.yaml | ✅ Created | channels.yaml has key name only |

### 5.2 — Create OS service files
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| `scripts/service/artha-listener.xml` | Windows Task Scheduler XML template | ✅ Created | Valid XML |
| `scripts/service/com.artha.channel-listener.plist` | macOS launchd plist | ✅ Created | Valid plist |
| `scripts/service/artha-listener.service` | Linux systemd unit | ✅ Created | Valid INI |

---

## Phase 6: Integration into Existing System

### 6.1 — Update `config/Artha.core.md` (Step 20)
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| Insert Step 20 block before "Catch-up complete" | Additive only; no existing steps changed | ✅ Done | Grep confirms position |

### 6.2 — Update `scripts/preflight.py`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| Add `check_channel_health()` function | P1, non-blocking, safe if channels.yaml missing | ✅ Done | `preflight.py --quiet` passes |
| Register in `run_preflight()` | After check_workiq() | ✅ Done | Function called |

### 6.3 — Update `config/implementation_status.yaml`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| Replace `whatsapp_bridge: not_started` | Add `channel_bridge`, `channel_push`, `channel_listener` | ✅ Done | YAML valid |

### 6.4 — Update `config/actions.yaml`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| Update send_telegram comment | Point handler to `scripts/channels/telegram.py` | ✅ Done | Comment updated |

### 6.5 — Update `config/observability.md`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| Add CHANNEL_* audit events | 12 new event types documented | ✅ Done | Listed in audit events table |

### 6.6 — Update `pyproject.toml`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| Add `channels` optional dep group | `python-telegram-bot>=21.0` | ✅ Done | `pip install artha[channels]` resolves |
| Update `all` group | Include `channels` | ✅ Done | |
| Add `scripts.channels` to packages | Package discoverable | ✅ Done | Import works |

### 6.7 — Update `.gitignore`
| Step | Expected Outcome | Actual Outcome | Verification |
|------|-----------------|----------------|-------------|
| `config/channels.yaml` gitignored | Live config never committed | ✅ Done | |
| Push artifacts gitignored | Marker + pending push files not tracked | ✅ Done | |

---

## Phase 7: Tests

### 7.1 — `tests/unit/test_channel_push.py`
| Test | Covers |
|------|--------|
| `test_scope_filter_full` | Full scope: all content passes |
| `test_scope_filter_family` | Family scope: immigration/finance lines removed |
| `test_scope_filter_standard` | Standard scope: only calendar/tasks |
| `test_push_marker_check_fresh` | Marker <12h → skip push |
| `test_push_marker_check_stale` | Marker >12h → allow push |
| `test_push_no_channels_yaml` | Missing config → graceful skip |
| `test_push_disabled_master_flag` | `push_enabled: false` → silent skip |
| `test_pending_push_write_read` | Write + read + cleanup cycle |
| `test_pii_redaction_called` | PII guard always called before send |
| `test_non_blocking_on_failure` | Exception in adapter → no exception raised |

### 7.2 — `tests/unit/test_channel_listener.py`
| Test | Covers |
|------|--------|
| `test_sender_whitelist_unknown` | Unknown sender → silent reject + CHANNEL_REJECT audit |
| `test_command_whitelist_valid` | `/status` → handler called |
| `test_command_whitelist_invalid` | Unknown command → help message |
| `test_message_dedup` | Same message_id twice → second skipped |
| `test_timestamp_stale` | Message >5 min old → skipped |
| `test_rate_limiting` | 11 commands/min → CHANNEL_RATE_LIMIT |
| `test_listener_host_match` | Current host = designated → allow start |
| `test_listener_host_mismatch` | Current host ≠ designated → exit 0 |
| `test_staleness_appended` | Every response has `_Last updated:` |
| `test_pii_redaction_outbound` | PII redacted before send |
| `test_session_token_lifecycle` | `/unlock` → token valid → expire |

### 7.3 — `tests/unit/test_channel_registry.py`
| Test | Covers |
|------|--------|
| `test_adapter_path_validation` | Path outside `scripts/channels/` → ValueError |
| `test_load_config_missing` | No channels.yaml → safe default |
| `test_load_config_present` | Valid YAML → correct dict |

---

## Phase 8: Documentation

### 8.1 — Create `docs/channels.md`
Full setup guide covering:
- Prerequisites and concepts
- Telegram setup (step-by-step)
- Discord setup (future)
- Slack setup (future)
- Service management (Layer 2)
- Troubleshooting

---

## Distribution Checklist Completion Status

Based on `specs/conversational-bridge.md §12`:

### Protocol & Infrastructure
- [x] `ChannelAdapter` protocol in `scripts/channels/base.py`
- [x] `ChannelMessage` + `InboundMessage` frozen dataclasses in `base.py`
- [x] Channel registry loader in `scripts/channels/registry.py`
- [x] Adapter path validation: must be under `scripts/channels/`

### Configuration
- [x] `channels.example.yaml` template with Telegram, Discord, Slack (no PII)
- [x] `channels.yaml` added to `.gitignore`
- [x] Feature flag: `push_enabled: false` (opt-in default)
- [x] Access scope definitions: `full`, `family`, `standard`

### Reference Implementation
- [x] Telegram adapter — push + interactive + health_check + poll
- [x] Full test coverage for adapter (mock HTTP)

### Push (Layer 1)
- [x] `channel_push.py` with scope filter + PII redaction + audit
- [x] Step 20 documented in `config/Artha.core.md`
- [x] Flash briefing format for push (≤500 chars)
- [x] Non-blocking: push failures log warning, don't halt catch-up

### Security
- [x] `pii_guard.filter_text()` on every outbound message — no bypass path
- [x] Per-recipient `access_scope` tested: `full`, `family`, `standard`
- [x] Sender whitelist with silent rejection + audit
- [x] Command whitelist (hardcoded, not configurable)
- [x] PIN session tokens for critical domain access
- [x] Message dedup + timestamp validation
- [x] No vault-encrypted files accessible from listener
- [x] Per-sender rate limiting (10 commands/minute) with audit
- [x] Recipient IDs documented as PII in privacy surface
- [x] Staleness indicator appended to every listener response
- [x] `listener_host` designation prevents multi-instance listener
- [x] Push dedup marker prevents duplicate briefings across machines

### Cross-Platform
- [x] Windows Task Scheduler template
- [x] macOS launchd plist template
- [x] Linux systemd unit template
- [x] `setup_channel.py --install-service` detects OS

### Integration
- [x] `preflight.py` P1 channel health checks
- [x] `implementation_status.yaml` updated
- [x] `actions.yaml`: updated `send_telegram` comment
- [x] `pyproject.toml`: `channels` optional dep group + `scripts.channels` package
- [x] 0 new mandatory dependencies

### Documentation
- [x] `docs/channels.md` setup guide
- [x] `config/observability.md` updated with channel audit events
- [x] Pending push queue documented
- [x] Format adaptation documented (Telegram MD, plain text)
- [x] Integration tests: mock adapter → push → verify redaction + scope

---

## Execution Log

| Step | Action | Status | Notes |
|------|--------|--------|-------|
| P0 | Read spec + codebase | ✅ DONE | All files read; patterns understood |
| P1 | Update PRD, Tech Spec, UX Spec | ✅ DONE | All three specs updated for v5.0/v2.3/v1.6 |
| P2 | Create this tasks.md | ✅ DONE | |
| P3 | `scripts/channels/__init__.py` | ✅ DONE | |
| P4 | `scripts/channels/base.py` | ✅ DONE | |
| P5 | `scripts/channels/registry.py` | ✅ DONE | |
| P6 | `config/channels.example.yaml` | ✅ DONE | |
| P7 | `scripts/channels/telegram.py` | ✅ DONE | |
| P8 | `scripts/channel_push.py` | ✅ DONE | |
| P9 | `scripts/channel_listener.py` | ✅ DONE | |
| P10 | `scripts/setup_channel.py` | ✅ DONE | |
| P11 | `scripts/service/` files (3) | ✅ DONE | |
| P12 | `config/Artha.core.md` Step 20 | ✅ DONE | |
| P13 | `scripts/preflight.py` channel check | ✅ DONE | |
| P14 | `config/implementation_status.yaml` | ✅ DONE | |
| P15 | `config/actions.yaml` | ✅ DONE | |
| P16 | `config/observability.md` | ✅ DONE | |
| P17 | `pyproject.toml` | ✅ DONE | |
| P18 | `.gitignore` | ✅ DONE | |
| P19 | `tests/unit/test_channel_push.py` | ✅ DONE | |
| P20 | `tests/unit/test_channel_listener.py` | ✅ DONE | |
| P21 | `tests/unit/test_channel_registry.py` | ✅ DONE | |
| P22 | `docs/channels.md` | ✅ DONE | |
| P23 | Final verification (pytest + import checks) | ✅ DONE | |

---

## Next Steps (Post-Implementation)

1. **Phase 1 Stabilization (2 weeks)**: Run catch-up daily with push enabled to Telegram test bot. Monitor `state/audit.md` for CHANNEL_PUSH events. Verify dedup marker working.

2. **Phase 2 Activation (Week 3+)**: Start `channel_listener.py` as foreground process. Test all 7 commands. Then install as OS service.

3. **Phase 3 Planning**: After Layer 2 is stable, begin writing the separate ingest spec for voice/document/action workflows.
