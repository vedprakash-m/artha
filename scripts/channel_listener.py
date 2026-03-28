#!/usr/bin/env python3
# pii-guard: ignore-file — channel listener infrastructure
"""channel_listener.py — Artha interactive channel listener (Layer 2) — entry point.

All logic lives in scripts/channel/ subpackage.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import dataclasses
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_ARTHA_DIR = Path(__file__).resolve().parent.parent
if str(_ARTHA_DIR) not in sys.path:
    sys.path.insert(0, str(_ARTHA_DIR))

_STATE_DIR = _ARTHA_DIR / "state"
_BRIEFINGS_DIR = _ARTHA_DIR / "briefings"
_AUDIT_LOG = _STATE_DIR / "audit.md"

try:
    from lib.logger import get_logger as _get_logger
    _chlog = _get_logger("channel")
except Exception:  # pragma: no cover
    class _NoOpChannelLogger:  # type: ignore[no-redef]
        def info(self, *a, **k): pass
        def error(self, *a, **k): pass
    _chlog = _NoOpChannelLogger()  # type: ignore[assignment]

_MAX_MESSAGE_AGE_SECONDS = 5 * 60
_POLL_BACKOFF_BASE = 2.0
_POLL_BACKOFF_MAX = 300.0

logging.basicConfig(
    level=logging.INFO,
    format="[channel_listener] %(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("channel_listener")

# ── Subpackage imports ──────────────────────────────────────────────────────
from channel.audit import _audit_log
from channel.formatters import (
    _strip_frontmatter, _clean_for_telegram, _trim_to_cap, _split_message,
    _truncate, _extract_section_summaries, _filter_noise_bullets, _is_noise_section,
)
from channel.state_readers import (
    _read_state_file, _format_age, _apply_scope_filter, _get_domain_open_items,
    _get_latest_briefing_path, _domain_freshness, _parse_age_to_hours,
    _READABLE_STATE_FILES, _DOMAIN_TO_STATE_FILE,
)
from channel.security import (
    _MessageDeduplicator, _RateLimiter, _SessionTokenStore, _requires_session,
    verify_listener_host,
)
from channel.llm_bridge import (
    _detect_domains, _gather_context, _detect_llm_cli, _detect_all_llm_clis,
    _vault_relock_if_needed, _call_single_llm, _ask_llm, _ask_llm_ensemble, cmd_ask,
)
from channel.handlers import (
    cmd_status, cmd_alerts, cmd_tasks, cmd_quick, cmd_domain, cmd_dashboard,
    cmd_goals, cmd_diff, cmd_items_add, cmd_items_done, cmd_remember, cmd_cost,
    cmd_power, cmd_relationships, cmd_help, cmd_queue, cmd_approve, cmd_reject,
    cmd_undo, cmd_unlock, _handle_callback_query,
)
from channel.catchup import cmd_catchup
from channel.stage import cmd_stage, cmd_radar, cmd_radar_try, cmd_radar_skip
from channel.router import _normalise_command, _COMMAND_ALIASES, ALLOWED_COMMANDS, _HANDLERS
from channel._lock import _acquire_singleton_lock, _release_singleton_lock



# ── process_message ─────────────────────────────────────────────────────

async def process_message(
    msg,
    adapter,
    channel_name: str,
    config: dict[str, Any],
    deduplicator: _MessageDeduplicator,
    rate_limiter: _RateLimiter,
    token_store: _SessionTokenStore,
) -> None:
    """Full inbound message processing pipeline.

    Security gates (in order):
      1. Sender whitelist
      2. Message dedup
      3. Timestamp validation
      4. Rate limiting
      5. Command whitelist
      6. Session token check (critical domains)
      7. Scope filter
      8. PII redaction
      9. Staleness indicator
    """
    _t0_msg = time.monotonic()
    _chlog.info("command.received", channel=channel_name, command=getattr(msg, "command", "unknown"))
    channel_cfg = config.get("channels", {}).get(channel_name, {})
    recipients = channel_cfg.get("recipients", {})

    # 1. Sender whitelist — silent rejection for unknown senders
    recipient_cfg = next(
        (r for r in recipients.values()
         if isinstance(r, dict) and str(r.get("id", "")) == msg.sender_id),
        None,
    )
    if recipient_cfg is None:
        _audit_log(
            "CHANNEL_REJECT",
            channel=channel_name,
            sender=msg.sender_id,
            reason="unknown_sender",
        )
        return  # Silent — do NOT respond to unknown senders

    scope = recipient_cfg.get("access_scope", "standard")
    recipient_name = next(
        (name for name, r in recipients.items()
         if isinstance(r, dict) and str(r.get("id", "")) == msg.sender_id),
        "unknown",
    )

    # 2. Message dedup
    if deduplicator.is_duplicate(msg.message_id):
        log.debug("Duplicate message from %s: %s", msg.sender_id, msg.message_id)
        return

    # 3. Timestamp validation — reject messages older than 5 minutes
    try:
        msg_time = datetime.fromisoformat(msg.timestamp.replace("Z", "+00:00"))
        age_sec = (datetime.now(timezone.utc) - msg_time).total_seconds()
        if age_sec > _MAX_MESSAGE_AGE_SECONDS:
            log.debug("Stale message from %s (%.0fs old)", msg.sender_id, age_sec)
            return
    except (ValueError, TypeError) as exc:
        log.warning("timestamp_parse_failed sender=%s error=%s", msg.sender_id, exc)
        pass  # Can't parse timestamp — allow message through

    # 4. Rate limiting
    if rate_limiter.is_rate_limited(msg.sender_id):
        _audit_log(
            "CHANNEL_RATE_LIMIT",
            channel=channel_name,
            sender=recipient_name,
            cooldown_sec=_RATE_LIMIT_COOLDOWN_SEC,
        )
        return  # Silent drop during cooldown

    _audit_log(
        "CHANNEL_IN",
        channel=channel_name,
        sender=recipient_name,
        command=msg.command,
    )

    # ── Callback query intercept: act:VERB:action_id (§5.3) ──────────────
    # Inline keyboard button presses arrive with raw_text = "act:VERB:uuid"
    # They bypass the command whitelist but are still sender-whitelisted above.
    if msg.raw_text.startswith("act:"):
        await _handle_callback_query(
            callback_data=msg.raw_text,
            sender_id=msg.sender_id,
            msg=msg,
            adapter=adapter,
        )
        return

    # 5. Command normalisation — accept flexible input
    norm_cmd, norm_args = _normalise_command(msg.raw_text)
    if norm_cmd:
        msg = dataclasses.replace(msg, command=norm_cmd, args=norm_args)
    is_slash_command = msg.command.startswith("/")

    # Send ack for long-running commands (track message_id for later deletion)
    _ack_msg_id: int | None = None
    _long_running = {"/catchup", "/domain", "/diff"}  # encrypted domains can be slow
    _encrypted_domain_names = {"finance", "insurance", "immigration", "estate", "vehicle", "health"}
    _needs_ack = (
        msg.command in _long_running
        or (msg.command == "/goals")
        or not is_slash_command  # free-form questions
    )

    if _needs_ack:
        from channels.base import ChannelMessage as _CM
        if msg.command == "/catchup":
            ack_text = "⏳ Running catch-up… this may take a minute or two."
        elif not is_slash_command:
            ack_text = "💭 Thinking…"
        else:
            ack_text = "⏳ Loading…"
        if hasattr(adapter, 'send_message_get_id'):
            _ack_msg_id = adapter.send_message_get_id(_CM(
                text=ack_text,
                recipient_id=msg.sender_id,
            ))
        else:
            adapter.send_message(_CM(text=ack_text, recipient_id=msg.sender_id))


    if is_slash_command and msg.command not in ALLOWED_COMMANDS:
        response = "Unknown command. Send ? for commands."
        staleness = "N/A"
    elif not is_slash_command:
        # Free-form question → context-aware LLM Q&A
        response, staleness = await cmd_ask(msg.raw_text, scope)
    elif msg.command == "/unlock":
        response, staleness = await cmd_unlock(
            msg.args, scope, msg.sender_id, token_store
        )
    else:
        # 6. Session token check for critical domain access
        if _requires_session(msg.command, msg.args):
            if not token_store.has_valid_token(msg.sender_id):
                response = (
                    "_This query requires authentication._\n"
                    "Send /unlock <PIN> to start a 15-min session, then retry."
                )
                staleness = "N/A"
            else:
                response, staleness = await _HANDLERS[msg.command](msg.args, scope)
        else:
            response, staleness = await _HANDLERS[msg.command](msg.args, scope)

    # Delete the ack message now that we have the real response
    if _ack_msg_id and hasattr(adapter, 'delete_message'):
        adapter.delete_message(msg.sender_id, _ack_msg_id)

    # 7. Append staleness indicator (every response)
    if staleness not in ("N/A", "never"):
        stale_prefix = "⚠️ " if _parse_age_to_hours(staleness) > 12 else ""
        response = response + f"\n\n{stale_prefix}Last updated: {staleness} ago"
    elif staleness == "never":
        response = response + "\n\n⚠️ No catch-up has run yet"

    # 7b. Determine output format — HTML commands skip markdown cleanup
    _html_commands = {"/dashboard"}
    use_html = (norm_cmd in _html_commands
                or (norm_cmd == "/domain" and msg.args and msg.args[0].lower() == "dashboard"))

    if not use_html:
        response = _clean_for_telegram(response)

    # 8. PII redaction — mandatory
    try:
        sys.path.insert(0, str(_ARTHA_DIR / "scripts"))
        from pii_guard import filter_text as _pii_filter
        filtered, pii_found = _pii_filter(response)
    except ImportError:
        log.warning("pii_guard not importable — PII filter skipped (UNSAFE)")
        filtered = response
        pii_found = {}

    # 9. Send response — split into chunks if too long for Telegram (4096 limit)
    from channels.base import ChannelMessage
    _TG_MAX = 4000  # leave margin below 4096

    chunks = _split_message(filtered, _TG_MAX) if len(filtered) > _TG_MAX else [filtered]
    for chunk in chunks:
        adapter.send_message(ChannelMessage(
            text=chunk,
            recipient_id=msg.sender_id,
            parse_mode="HTML" if use_html else "",
        ))

    _audit_log(
        "CHANNEL_OUT",
        channel=channel_name,
        recipient=recipient_name,
        chars=len(filtered),
        chunks=len(chunks),
        pii_filtered=bool(pii_found),
        command=msg.command,
    )
    _chlog.info(
        "command.completed",
        channel=channel_name,
        command=getattr(msg, "command", "unknown"),
        ms=round((time.monotonic() - _t0_msg) * 1000),
    )


# ── run_listener ────────────────────────────────────────────────

async def run_listener(
    channel_names: list[str],
    config: dict[str, Any],
    dry_run: bool = False,
) -> None:
    """Main asyncio loop. Polls all channels concurrently."""
    from channels.registry import create_adapter_from_config

    # Initialize shared state
    deduplicator = _MessageDeduplicator()
    rate_limiter = _RateLimiter()
    token_store = _SessionTokenStore()

    # Instantiate adapters
    adapters: dict[str, Any] = {}
    for ch in channel_names:
        ch_cfg = config.get("channels", {}).get(ch, {})
        if not ch_cfg.get("enabled", False):
            log.warning("Channel '%s' is not enabled in channels.yaml — skipping", ch)
            continue
        if not ch_cfg.get("features", {}).get("interactive", False):
            log.warning("Channel '%s' has interactive: false — skipping", ch)
            continue
        try:
            adapter = create_adapter_from_config(ch, ch_cfg)
        except Exception as exc:
            log.error("Could not load adapter for '%s': %s", ch, exc)
            _audit_log(
                "CHANNEL_ERROR",
                channel=ch,
                error_type="adapter_load_failed",
                message=str(exc)[:200],
            )
            continue

        # Layer 2 startup: claim the polling session
        if not dry_run:
            log.info("[%s] Claiming polling session (deleteWebhook + flush)…", ch)
            try:
                adapter.delete_webhook()
                adapter.flush_pending_updates()
            except AttributeError as exc:
                log.warning("layer2_startup_helper_missing ch=%s error=%s", ch, exc)
                pass  # Adapter doesn't implement Layer 2 startup helpers
            except Exception as exc:
                log.warning("[%s] Session claim failed (non-fatal): %s", ch, exc)

        adapters[ch] = adapter
        _audit_log(
            "CHANNEL_LISTENER_START",
            channel=ch,
            host=socket.gethostname(),
        )
        log.info("[%s] Listener started on %s", ch, socket.gethostname())

    if not adapters:
        log.warning("No interactive adapters loaded — nothing to listen on")
        return

    if dry_run:
        log.info("[DRY-RUN] Adapters loaded: %s — not starting poll loop", list(adapters.keys()))
        return

    # Cross-platform shutdown: threading.Event works on Windows
    # (asyncio.add_signal_handler does NOT work on Windows)
    shutdown = threading.Event()

    def _request_shutdown(*_args: object) -> None:
        log.info("Shutdown requested")
        shutdown.set()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    log.info("Polling on channels: %s (press Ctrl+C to stop)", list(adapters.keys()))

    # Bridge setup — executor role only; no-op if bridge disabled
    _bridge_active = False
    _bridge_dir = None
    _bridge_queue = None
    _bridge_pubkey = None
    try:
        from lib.config_loader import load_config as _load_config  # noqa: PLC0415
        _artha_cfg = _load_config("artha_config")
        if _artha_cfg.get("multi_machine", {}).get("bridge_enabled", False):
            from action_bridge import detect_role, get_bridge_dir  # noqa: PLC0415
            _channels_cfg: dict = _load_config("channels")
            if detect_role(_channels_cfg) == "executor":
                    from action_queue import ActionQueue as _AQ  # noqa: PLC0415
                    _bridge_dir = get_bridge_dir(_ARTHA_DIR)
                    _bridge_dir.mkdir(parents=True, exist_ok=True)
                    (_bridge_dir / "proposals").mkdir(exist_ok=True)
                    (_bridge_dir / "results").mkdir(exist_ok=True)
                    _bridge_queue = _AQ(_ARTHA_DIR)
                    _bridge_active = True
                    log.info("[bridge] Executor mode active; polling %s", _bridge_dir)
    except Exception as _bridge_init_exc:
        log.warning("[bridge] Startup init failed (non-fatal): %s", _bridge_init_exc)

    while not shutdown.is_set():
        # Bridge ingestion cycle (executor role only; no-op if bridge disabled)
        if _bridge_active and _bridge_dir is not None and _bridge_queue is not None:
            try:
                from action_bridge import ingest_proposals, retry_outbox, gc  # noqa: PLC0415
                ingest_proposals(_bridge_dir, _bridge_queue, _ARTHA_DIR)
                retry_outbox(_bridge_dir, _bridge_queue, _ARTHA_DIR)
                gc(_bridge_dir, _ARTHA_DIR)
            except Exception as _bridge_exc:
                log.warning("[bridge] ingestion cycle error (non-fatal): %s", _bridge_exc)

        # Poll all channels concurrently
        poll_tasks = [
            poll_with_resilience(adapter, ch)
            for ch, adapter in adapters.items()
        ]
        results = await asyncio.gather(*poll_tasks, return_exceptions=True)

        for (ch, adapter), result in zip(adapters.items(), results):
            if isinstance(result, Exception):
                log.error("[%s] Gather error: %s", ch, result)
                continue
            for msg in (result or []):
                try:
                    await process_message(
                        msg, adapter, ch, config,
                        deduplicator, rate_limiter, token_store,
                    )
                except Exception as exc:
                    log.error(
                        "[%s] Error processing message from %s: %s",
                        ch, msg.sender_id, exc,
                    )
                    _audit_log(
                        "CHANNEL_ERROR",
                        channel=ch,
                        error_type="process_message_error",
                        message=str(exc)[:200],
                    )

        # Brief sleep to avoid busy-looping when adapters return instantly
        await asyncio.sleep(0.1)

    log.info("Listener stopped cleanly")


# ── poll_with_resilience ────────────────────────────────────────

async def poll_with_resilience(
    adapter,
    channel_name: str,
    poll_timeout: int = 30,
) -> list:
    """Poll with exponential backoff on connection errors.

    Returns empty list on timeout or persistent failure (never raises).
    """
    delay = _POLL_BACKOFF_BASE
    try:
        return adapter.poll(timeout=poll_timeout)
    except Exception as exc:
        log.warning(
            "[%s] Poll error: %s — backing off %.0fs", channel_name, exc, delay
        )
        await asyncio.sleep(min(delay, _POLL_BACKOFF_MAX))
        return []


# ── health_check_all ────────────────────────────────────────────

def health_check_all(channel_names: list[str], config: dict[str, Any]) -> bool:
    """Run health_check() on all specified channels. Returns True if all healthy."""
    from channels.registry import create_adapter_from_config

    all_healthy = True
    for ch in channel_names:
        ch_cfg = config.get("channels", {}).get(ch, {})
        if not ch_cfg.get("enabled", False):
            log.info("[%s] Not enabled — skipping health check", ch)
            continue
        try:
            import time as _time
            _t0 = _time.perf_counter()
            adapter = create_adapter_from_config(ch, ch_cfg)
            ok = adapter.health_check()
            latency_ms = int((_time.perf_counter() - _t0) * 1000)
        except Exception as exc:
            log.error("[%s] health_check error: %s", ch, exc)
            ok = False
            latency_ms = -1

        status = "healthy ✓" if ok else "UNHEALTHY ✗"
        log.info("[%s] %s", ch, status)
        _audit_log(
            "CHANNEL_HEALTH",
            channel=ch,
            healthy=ok,
            latency_ms=latency_ms,
        )
        if not ok:
            all_healthy = False

    return all_healthy


# ── main ────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Artha interactive channel listener (Layer 2)"
    )
    parser.add_argument(
        "--channel", "-c",
        action="append",
        dest="channels",
        metavar="NAME",
        help="Channel to listen on (can be specified multiple times). Required unless --dry-run or --health.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate setup and exit without starting the poll loop",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Run health checks on all enabled channels and exit",
    )
    args = parser.parse_args()

    from channels.registry import load_channels_config
    config = load_channels_config()

    # Health check mode
    if args.health:
        enabled = [
            ch for ch, cfg in config.get("channels", {}).items()
            if isinstance(cfg, dict) and cfg.get("enabled", False)
        ]
        if not enabled:
            print("channel_listener: no channels configured or enabled ✓")
            return 0
        ok = health_check_all(enabled, config)
        return 0 if ok else 1

    # Determine channels to listen on
    channels = args.channels or []
    if not channels:
        if args.dry_run:
            # In dry-run mode, default to all enabled interactive channels
            channels = [
                ch for ch, cfg in config.get("channels", {}).items()
                if isinstance(cfg, dict)
                and cfg.get("enabled", False)
                and cfg.get("features", {}).get("interactive", False)
            ]
            if not channels:
                print("channel_listener: no channels configured (dry-run OK)")
                return 0
        else:
            parser.error("--channel is required (unless --dry-run or --health)")

    # Listener host check
    if not verify_listener_host(config):
        # Not the designated host — exit 0 (expected behavior, not error)
        return 0

    # Singleton lock — refuse to start a second instance on the same machine
    if not args.dry_run:
        if not _acquire_singleton_lock():
            log.info("Another listener instance is already running — exiting")
            return 0
        import atexit as _atexit
        _atexit.register(_release_singleton_lock)

    # Run asyncio event loop
    try:
        asyncio.run(run_listener(channels, config, dry_run=args.dry_run))
    except KeyboardInterrupt:
        pass  # Handled by signal handler
    except Exception as exc:
        log.error("Listener fatal error: %s", exc)
        _audit_log(
            "CHANNEL_ERROR",
            error_type="listener_fatal",
            message=str(exc)[:500],
        )
        return 1

    return 0
