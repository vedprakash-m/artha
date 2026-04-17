#!/usr/bin/env python3
# pii-guard: ignore-file — engine infrastructure
"""artha_engine.py — Unified Artha engine: Telegram listener + daily scheduler + watchdog.

Decision: ADR-004 (Option B) — single process host.
  - Singleton PID guard blocks duplicate launch.
  - One import of m2m_handler → one _rate_limiter + _nonce_dedup instance (§6.3 security invariant).
  - Old channel_listener.py Task Scheduler task MUST be disabled before enabling this task.

Coroutines (run concurrently via asyncio.gather):
  telegram_loop()  — Telegram polling via channel_listener.run_listener()
  schedule_loop()  — Daily 07:00 PT pipeline run
  watchdog_loop()  — Every 30-min health checks; alerts via Telegram if checks fail
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── Windows ProactorEventLoop required for asyncio.create_subprocess_exec ──
# Must be set before any asyncio.run() or get_event_loop() call.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

_ARTHA_DIR = Path(__file__).resolve().parent.parent
if str(_ARTHA_DIR) not in sys.path:
    sys.path.insert(0, str(_ARTHA_DIR))
if str(_ARTHA_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(_ARTHA_DIR / "scripts"))

try:
    import zoneinfo
    PT_TZ = zoneinfo.ZoneInfo("America/Los_Angeles")
except ImportError:  # Python < 3.9 fallback
    from datetime import timezone as _tz
    import datetime as _dt
    PT_TZ = _tz(timedelta(hours=-8), "PST")  # type: ignore[assignment]  # static offset; DST not handled

_LOCAL_DIR = Path.home() / ".artha-local"
_PID_FILE   = _LOCAL_DIR / "artha_engine.pid"
_STATE_DIR  = _ARTHA_DIR / "state"
_TMP_DIR    = _ARTHA_DIR / "tmp"
_HEARTBEAT_FILE = _STATE_DIR / ".channel_listener.heartbeat"
_RADAR_BACKLOG  = _STATE_DIR / "radar_backlog.yaml"
_AUDIT_LOG      = _STATE_DIR / "audit.md"

_WATCHDOG_INTERVAL_SEC = 1800   # 30 minutes
_WATCHDOG_RADAR_STALE_HR = 26   # alert threshold for radar_backlog staleness
_WATCHDOG_HEARTBEAT_STALE_SEC = 300  # 5 minutes
_REDDIT_DEAD_DAYS = 2            # alert if no reddit_* entries for this many days

logging.basicConfig(
    level=logging.INFO,
    format="[artha_engine] %(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("artha_engine")


# ── Singleton guard ──────────────────────────────────────────────────────────

def acquire_singleton() -> bool:
    """Write PID file; return False if another engine instance is already running.

    Handles stale PID files from prior crashes by checking process existence first.
    On Windows, os.kill(pid, 0) raises PermissionError for living processes owned
    by other users — treated as "process is alive" (conservative, safe).
    """
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    if _PID_FILE.exists():
        try:
            existing_pid = int(_PID_FILE.read_text().strip())
            os.kill(existing_pid, 0)  # signal 0: existence check, no signal sent
            log.warning(
                "Engine already running (PID %d). Refusing to start second instance.",
                existing_pid,
            )
            return False
        except (ValueError, OSError):
            pass  # Stale PID file from prior crash — safe to overwrite
    _PID_FILE.write_text(str(os.getpid()))
    return True


def release_singleton() -> None:
    """Remove PID file on clean shutdown."""
    try:
        _PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── Daily pipeline ───────────────────────────────────────────────────────────

async def run_daily_pipeline() -> None:
    """Run scripts/pipeline.py then watch_monitor.py (if present).

    Errors are logged and swallowed — a failed pipeline run must not crash the engine.
    """
    pipeline_script = _ARTHA_DIR / "scripts" / "pipeline.py"
    python = sys.executable

    log.info("[schedule] Starting daily pipeline run")
    _write_audit("ENGINE_PIPELINE_START", host=socket.gethostname())

    try:
        proc = await asyncio.create_subprocess_exec(
            python, str(pipeline_script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_ARTHA_DIR),
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        err_text = stderr.decode("utf-8", errors="replace") if stderr else ""
        if err_text:
            log.info("[schedule] pipeline stderr: %s", err_text[:500])
        rc = proc.returncode or 0
        log.info("[schedule] pipeline finished (rc=%d)", rc)
        _write_audit("ENGINE_PIPELINE_END", rc=rc)
    except asyncio.TimeoutError:
        log.error("[schedule] pipeline timed out after 600s")
        _write_audit("ENGINE_PIPELINE_TIMEOUT")
    except Exception as exc:
        log.exception("[schedule] pipeline error: %s", exc)
        _write_audit("ENGINE_PIPELINE_ERROR", error=str(exc)[:200])

    # Run watch_monitor.py if it exists (Phase 4 — best-effort)
    watch_script = _ARTHA_DIR / "scripts" / "skills" / "watch_monitor.py"
    if watch_script.exists():
        try:
            wproc = await asyncio.create_subprocess_exec(
                python, str(watch_script),
                cwd=str(_ARTHA_DIR),
            )
            await asyncio.wait_for(wproc.wait(), timeout=60)
            log.info("[schedule] watch_monitor finished (rc=%d)", wproc.returncode or 0)
        except Exception as exc:
            log.warning("[schedule] watch_monitor error (non-fatal): %s", exc)


# ── Coroutines ───────────────────────────────────────────────────────────────

async def telegram_loop() -> None:
    """Run channel_listener.run_listener() in this event loop.

    Loads channels config and delegates to the existing listener.
    Exits on unrecoverable error after logging.
    """
    try:
        from channels.registry import load_channels_config
        config = load_channels_config()
        interactive_channels = [
            ch for ch, cfg in config.get("channels", {}).items()
            if isinstance(cfg, dict)
            and cfg.get("enabled", False)
            and cfg.get("features", {}).get("interactive", False)
        ]
        if not interactive_channels:
            log.warning("[telegram_loop] No interactive channels configured — Telegram listener idle")
            return

        from channel_listener import run_listener
        log.info("[telegram_loop] Starting on channels: %s", interactive_channels)
        await run_listener(interactive_channels, config)
    except Exception as exc:
        log.exception("[telegram_loop] Fatal error: %s", exc)
        _write_audit("ENGINE_TELEGRAM_FATAL", error=str(exc)[:200])


async def schedule_loop() -> None:
    """Fire once daily at 07:00 PT.

    Catches up automatically if the machine was asleep — the next iteration
    simply computes the next 07:00 from the current time.
    """
    log.info("[schedule_loop] Started; fires daily at 07:00 PT")
    while True:
        try:
            now = datetime.now(PT_TZ)
            next_run = now.replace(hour=7, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            sleep_sec = (next_run - now).total_seconds()
            log.info(
                "[schedule_loop] Next run at %s (%.0f s)",
                next_run.strftime("%Y-%m-%d %H:%M PT"),
                sleep_sec,
            )
            await asyncio.sleep(sleep_sec)
            await run_daily_pipeline()
        except asyncio.CancelledError:
            log.info("[schedule_loop] Cancelled")
            return
        except Exception as exc:
            log.exception("[schedule_loop] Unexpected error: %s", exc)
            await asyncio.sleep(60)  # brief pause before retry on unexpected error


async def watchdog_loop(
    tg_task: asyncio.Task,
    sched_task: asyncio.Task,
) -> None:
    """Run health checks every 30 minutes; send Telegram alerts on failures.

    Checks (spec §7.4):
      1. telegram_loop + schedule_loop tasks still alive (asyncio introspection)
      2. radar_backlog.yaml updated within 26 hours
      3. ERROR/CRITICAL in tmp/*.log in last hour
      4. .channel_listener.heartbeat age < 5 minutes
      5. Reddit connector: zero reddit_* entries for 2+ consecutive days
    """
    log.info("[watchdog_loop] Started; interval=%ds", _WATCHDOG_INTERVAL_SEC)

    # Track consecutive days with no reddit entries
    _reddit_zero_days: int = 0

    while True:
        try:
            await asyncio.sleep(_WATCHDOG_INTERVAL_SEC)
            _reddit_zero_days = _run_watchdog_checks(tg_task, sched_task, _reddit_zero_days)
        except asyncio.CancelledError:
            log.info("[watchdog_loop] Cancelled")
            return
        except Exception as exc:
            log.exception("[watchdog_loop] Error during checks: %s", exc)


def _run_watchdog_checks(
    tg_task: asyncio.Task,
    sched_task: asyncio.Task,
    _reddit_zero_days: int,
) -> int:
    """Execute all watchdog checks; send Telegram alerts for failures.

    Returns the updated _reddit_zero_days counter.
    """
    import time as _time
    now_ts = _time.time()
    alerts: list[str] = []

    # Check 1: task liveness (telegram_loop + schedule_loop)
    for task, name in [(tg_task, "telegram_loop"), (sched_task, "schedule_loop")]:
        if task.done():
            exc = task.exception() if not task.cancelled() else None
            alerts.append(
                f"\u26a0\ufe0f Artha Alert: {name} task has stopped unexpectedly.\n"
                f"Exception: {exc}"
            )
            log.error("[watchdog] task dead: %s, exc=%s", name, exc)
            _write_audit("WATCHDOG_TASK_DEAD", task=name, error=str(exc)[:200])

    # Check 2: radar_backlog.yaml staleness
    if _RADAR_BACKLOG.exists():
        age_hr = (now_ts - _RADAR_BACKLOG.stat().st_mtime) / 3600
        if age_hr > _WATCHDOG_RADAR_STALE_HR:
            msg = (
                f"⚠️ Artha Alert: radar_backlog.yaml is {age_hr:.0f}h stale.\n"
                "Watch monitor may not have run. Check scripts/skills/watch_monitor.py."
            )
            alerts.append(msg)
            log.warning("[watchdog] radar_backlog stale: %.1f hours", age_hr)
            _write_audit("WATCHDOG_RADAR_STALE", age_hr=round(age_hr))
    else:
        log.info("[watchdog] radar_backlog.yaml absent — skip staleness check")

    # Check 3: ERROR/CRITICAL in tmp/*.log in last hour
    try:
        import re
        _log_re = re.compile(r"\b(ERROR|CRITICAL)\b")
        cutoff = now_ts - 3600
        for log_file in _TMP_DIR.glob("*.log"):
            if log_file.stat().st_mtime > cutoff:
                text = log_file.read_text(encoding="utf-8", errors="replace")
                if _log_re.search(text):
                    alerts.append(
                        f"⚠️ Artha Alert: ERROR/CRITICAL detected in {log_file.name} (last hour)."
                    )
                    _write_audit("WATCHDOG_LOG_ERROR", file=log_file.name)
                    break  # one alert per cycle is sufficient
    except Exception as exc:
        log.warning("[watchdog] log scan error (non-fatal): %s", exc)

    # Check 4: heartbeat file age
    if _HEARTBEAT_FILE.exists():
        hb_age = now_ts - _HEARTBEAT_FILE.stat().st_mtime
        if hb_age > _WATCHDOG_HEARTBEAT_STALE_SEC:
            msg = (
                f"⚠️ Artha Alert: channel_listener heartbeat is {hb_age:.0f}s stale.\n"
                "Telegram polling may have silently stopped."
            )
            alerts.append(msg)
            log.warning("[watchdog] heartbeat stale: %.0f seconds", hb_age)
            _write_audit("WATCHDOG_HEARTBEAT_STALE", age_sec=round(hb_age))
    else:
        log.debug("[watchdog] heartbeat file absent — skip check (engine may just have started)")

    # Check 5: Reddit connector liveness (2 consecutive days zero reddit_* entries)
    if _RADAR_BACKLOG.exists():
        try:
            import yaml as _yaml
            backlog = _yaml.safe_load(_RADAR_BACKLOG.read_text(encoding="utf-8")) or {}
            has_reddit = any(
                isinstance(entry, dict)
                and str(entry.get("source", "")).startswith("reddit_")
                for entries in backlog.values() if isinstance(entries, list)
                for entry in entries
            )
            if not has_reddit:
                _reddit_zero_days += 1
                log.info("[watchdog] no reddit entries today (consecutive=%d)", _reddit_zero_days)
            else:
                _reddit_zero_days = 0
            if _reddit_zero_days >= 2:
                alerts.append(
                    "\u26a0\ufe0f Artha Alert: Reddit connector returned 0 items for "
                    f"{_reddit_zero_days} consecutive days.\n"
                    "Check scripts/connectors/reddit.py."
                )
                _write_audit("REDDIT_CONNECTOR_DEAD", zero_days=_reddit_zero_days)
        except Exception as exc:
            log.warning("[watchdog] reddit liveness check error: %s", exc)

    # Send all alerts via Telegram (best-effort; errors must not crash watchdog)
    for alert in alerts:
        _send_alert(alert)

    return _reddit_zero_days


def _send_alert(message: str) -> None:
    """Send a watchdog alert via Telegram (best-effort; never raises)."""
    try:
        from channels.registry import load_channels_config, create_adapter_from_config
        from channels.base import ChannelMessage
        config = load_channels_config()
        for ch, cfg in config.get("channels", {}).items():
            if not isinstance(cfg, dict):
                continue
            if not cfg.get("enabled", False):
                continue
            # Only alert on the primary personal channel (not M2M bot)
            if not cfg.get("features", {}).get("interactive", False):
                continue
            try:
                adapter = create_adapter_from_config(ch, cfg)
                for recipient in cfg.get("recipients", {}).values():
                    if isinstance(recipient, dict) and recipient.get("access_scope") == "admin":
                        adapter.send_message(ChannelMessage(
                            text=message,
                            recipient_id=str(recipient["id"]),
                        ))
                        return  # one alert is enough
            except Exception as exc:
                log.warning("[watchdog] alert send failed on ch=%s: %s", ch, exc)
    except Exception as exc:
        log.warning("[watchdog] alert dispatch error: %s", exc)


# ── Audit helper ─────────────────────────────────────────────────────────────

def _write_audit(event_type: str, **kwargs: object) -> None:
    """Best-effort audit log append to state/audit.md."""
    try:
        from channel.audit import _audit_log
        # _audit_log expects str | int | bool | None values
        safe_kwargs = {k: (v if isinstance(v, (str, int, bool, type(None))) else str(v))
                       for k, v in kwargs.items()}
        _audit_log(event_type, **safe_kwargs)
    except Exception:
        pass  # Audit errors must never crash the engine


# ── Entry point ──────────────────────────────────────────────────────────────

async def engine_main() -> None:
    """Root coroutine. Runs telegram_loop, schedule_loop, watchdog_loop concurrently."""
    log.info("Artha Engine starting (PID=%d, host=%s)", os.getpid(), socket.gethostname())
    _write_audit("ENGINE_START", pid=os.getpid(), host=socket.gethostname())

    tg_task       = asyncio.create_task(telegram_loop(),              name="telegram_loop")
    sched_task    = asyncio.create_task(schedule_loop(),              name="schedule_loop")
    watchdog_task = asyncio.create_task(
        watchdog_loop(tg_task, sched_task), name="watchdog_loop"
    )

    try:
        await asyncio.gather(tg_task, sched_task, watchdog_task)
    except asyncio.CancelledError:
        log.info("Engine cancelled — shutting down cleanly")
    finally:
        for task in (tg_task, sched_task, watchdog_task):
            if not task.done():
                task.cancel()
        _write_audit("ENGINE_STOP", pid=os.getpid())
        release_singleton()
        log.info("Artha Engine stopped")


def main() -> int:
    if not acquire_singleton():
        return 1  # Already running — Task Scheduler sees non-zero exit; no cascade restart
    import atexit
    atexit.register(release_singleton)
    try:
        asyncio.run(engine_main())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log.exception("Engine fatal error: %s", exc)
        _write_audit("ENGINE_FATAL", error=str(exc)[:500])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
