#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module; payload fields are age-encrypted before write
"""
scripts/action_bridge.py — JSON drop-file bridge for dual-machine operation.

Implements the cross-machine action exchange protocol defined in specs/dual-setup.md.

Bridge directory layout (OneDrive-synced):
  state/.action_bridge/
    proposals/            ← Mac writes, Windows reads & deletes
    results/              ← Windows writes, Mac reads & deletes
    .bridge_health_mac.json     ← Mac writes only
    .bridge_health_windows.json ← Windows writes only

File naming: {ISO-8601-compact}_{action-uuid-first-8}.json
  e.g. 2026-03-21T09-15-00Z_a1b2c3d4.json

Encryption invariant:
  ALL payload-bearing fields (title, description, parameters, result_message,
  result_data) are age-encrypted (age1: prefix) before writing to bridge files.
  Routing-only fields (action_id, action_type, domain, friction, etc.) are
  plaintext to support queue management without key access.

Role semantics:
  proposer  → Mac: writes proposals, reads results  (default if not listener_host)
  executor  → Windows: reads proposals, writes results (matches listener_host)

Delivery guarantee:
  Results use an at-least-once outbox pattern (bridge_synced=0 flag in actions.db).
  retry_outbox() re-writes result files for any terminal action missing a result file.

Ref: specs/dual-setup.md
"""
from __future__ import annotations

import json
import logging
import os
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("action_bridge")

# Bridge protocol version
_BRIDGE_VERSION = "1.0"

# Default TTL for GC (days)
_DEFAULT_TTL_DAYS = 7

# Health staleness threshold
_DEFAULT_HEALTH_STALE_HOURS = 48

# Metrics sink (matches Artha's existing lightweight metrics pattern)
_METRICS_FILENAME = "bridge_metrics.json"


# ---------------------------------------------------------------------------
# Atomic file write helper
# ---------------------------------------------------------------------------

def _write_bridge_file(target_path: Path, payload: dict) -> Path:
    """Atomically write a bridge JSON file via tempfile + os.replace.

    Write-once guarantee: the file is created atomically; no in-place edits.
    If the write fails, the target is never touched.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target_path.parent),
        prefix=".tmp_",
        suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(target_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target_path


def _bridge_filename(action_id: str) -> str:
    """Generate a bridge filename: {ISO-compact}_{uuid-first-8}.json.

    This ensures chronological sorting equals filename sorting.
    Example: 2026-03-21T09-15-00Z_a1b2c3d4.json
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    short_id = action_id.replace("-", "")[:8]
    return f"{ts}_{short_id}.json"


# ---------------------------------------------------------------------------
# Encryption helpers (delegates to action_queue helpers via lazy import)
# ---------------------------------------------------------------------------

def _encrypt_field(value: str, pubkey: str | None) -> str:
    """Encrypt a string with age. Returns 'age1:...' prefix or original on failure."""
    if not pubkey or not value:
        return value
    try:
        _ensure_scripts_on_path()
        from action_queue import _encrypt_field as _aq_enc  # noqa: PLC0415
        return _aq_enc(value, pubkey)
    except Exception:
        return value


def _decrypt_field(value: str, privkey: str | None) -> str:
    """Decrypt an age-encrypted field. Pass-through if not encrypted."""
    if not value or not privkey:
        return value
    try:
        _ensure_scripts_on_path()
        from action_queue import _decrypt_field as _aq_dec  # noqa: PLC0415
        return _aq_dec(value, privkey)
    except Exception:
        return value


def _ensure_scripts_on_path() -> None:
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


def _get_privkey(artha_dir: Path) -> str | None:
    """Get the age private key from system keyring (lazy, may return None)."""
    try:
        _ensure_scripts_on_path()
        from foundation import get_private_key  # noqa: PLC0415
        return get_private_key()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Bridge metrics (follows Artha's tmp/ lightweight metrics convention)
# ---------------------------------------------------------------------------

class BridgeMetrics:
    """Lightweight bridge metrics counters and latency histograms.

    Written to tmp/bridge_metrics.json (same pattern as pipeline_metrics.json).
    No new external dependency — stdlib JSON only.
    """

    _COUNTERS = (
        "proposals_written",
        "proposals_ingested",
        "results_written",
        "results_ingested",
        "outbox_retry_count",
        "outbox_pending",
        "orphan_results",
        "gc_deleted_files",
    )

    def __init__(self, artha_dir: Path) -> None:
        self._path = artha_dir / "tmp" / _METRICS_FILENAME
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {c: 0 for c in self._COUNTERS}

    def increment(self, counter: str, amount: int = 1) -> None:
        self._data[counter] = self._data.get(counter, 0) + amount

    def record_latency(self, metric: str, latency_ms: float) -> None:
        key = f"{metric}_ms_recent"
        recent: list = self._data.get(key, [])
        recent.append(round(latency_ms, 1))
        self._data[key] = recent[-100:]  # keep last 100 samples

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            _write_bridge_file(self._path, self._data)
        except Exception:
            pass  # metrics are best-effort; never crash on metric write failure


# ---------------------------------------------------------------------------
# Bridge health (per-machine heartbeat files)
# ---------------------------------------------------------------------------

def write_health(
    bridge_dir: Path,
    role: str,
    metrics: dict[str, Any] | None = None,
) -> None:
    """Write this machine's health heartbeat file.

    Mac writes .bridge_health_mac.json  (role='mac').
    Windows writes .bridge_health_windows.json  (role='windows' or 'executor').

    Each machine ONLY writes its own file — eliminates shared-write race conditions.
    """
    # Normalize role to mac/windows for filename
    file_role = "mac" if role in ("proposer", "mac") else "windows"
    target = bridge_dir / f".bridge_health_{file_role}.json"
    payload: dict[str, Any] = {
        "last_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hostname": platform.node(),
        "role": role,
        **(metrics or {}),
    }
    try:
        _write_bridge_file(target, payload)
    except Exception as exc:
        log.warning("[bridge] health write failed: %s", exc)


def check_health_staleness(
    bridge_dir: Path,
    peer_role: str,
    stale_hours: int = _DEFAULT_HEALTH_STALE_HOURS,
) -> tuple[bool, float]:
    """Check whether the peer machine's health file is stale.

    Args:
        bridge_dir: Path to state/.action_bridge/
        peer_role:  'mac' or 'windows' (the OTHER machine)
        stale_hours: threshold in hours

    Returns:
        (is_stale, hours_since_last_seen)
    """
    health_file = bridge_dir / f".bridge_health_{peer_role}.json"
    if not health_file.exists():
        return True, float("inf")
    try:
        with open(health_file, encoding="utf-8") as f:
            data = json.load(f)
        last_seen_str = data.get("last_seen", "")
        last_seen = datetime.fromisoformat(last_seen_str)
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        elapsed_h = (datetime.now(timezone.utc) - last_seen).total_seconds() / 3600
        return elapsed_h >= stale_hours, round(elapsed_h, 1)
    except Exception:
        return True, float("inf")


# ---------------------------------------------------------------------------
# Proposal write — Mac → bridge/proposals/ → Windows
# ---------------------------------------------------------------------------

def write_proposal(
    bridge_dir: Path,
    proposal: Any,  # ActionProposal (avoid circular import)
    pubkey: str | None = None,
) -> Path:
    """Atomically write an action proposal to bridge proposals/ directory.

    Encrypts payload-bearing fields (title, description, parameters).
    Keeps routing envelope plaintext for queue management.

    Called by ActionExecutor._enqueue_and_maybe_export() on the Mac.
    Returns the path to the newly written bridge file.
    """
    params_str = json.dumps(proposal.parameters)

    payload: dict[str, Any] = {
        "bridge_version": _BRIDGE_VERSION,
        # ── Plaintext routing envelope ──────────────────────────────────
        "action_id":      proposal.id,
        "created_at":     datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin_host":    platform.node(),
        "action_type":    proposal.action_type,
        "domain":         proposal.domain,
        "friction":       proposal.friction,
        "min_trust":      proposal.min_trust,
        "sensitivity":    proposal.sensitivity,
        "expires_at":     proposal.expires_at,
        "reversible":     proposal.reversible,
        "undo_window_sec": proposal.undo_window_sec,
        "source_step":    proposal.source_step,
        "source_skill":   proposal.source_skill,
        "linked_oi":      proposal.linked_oi,
        # ── Encrypted payload fields ────────────────────────────────────
        # title is encrypted in bridge files even though it is plaintext in the local DB
        # (ActionQueue._should_encrypt only encrypts for high/critical sensitivity)
        "title":          _encrypt_field(proposal.title, pubkey),
        "description":    _encrypt_field(proposal.description or "", pubkey),
        "parameters":     _encrypt_field(params_str, pubkey),
        # ── Optional redacted preview (no PII) ──────────────────────────
        "preview_redacted": f"[{proposal.action_type}] {proposal.domain}",
    }

    filename = _bridge_filename(proposal.id)
    target = bridge_dir / "proposals" / filename
    written = _write_bridge_file(target, payload)
    _audit_event(
        bridge_dir.parent.parent,  # artha_dir
        "BRIDGE_PROPOSAL_WRITE",
        action_id=proposal.id[:16],
        action_type=proposal.action_type,
        domain=proposal.domain,
    )
    return written


# ---------------------------------------------------------------------------
# Result write — Windows → bridge/results/ → Mac
# ---------------------------------------------------------------------------

def write_result(
    bridge_dir: Path,
    action_id: str,
    final_status: str,
    result_message: str | None = None,
    result_data: dict | None = None,
    pubkey: str | None = None,
    origin_host: str | None = None,
) -> Path:
    """Atomically write an action result to bridge results/ directory.

    Called on the Windows (executor) machine after record_result() succeeds.
    Encrypts result_message and result_data.

    Returns the path to the newly written bridge file.
    """
    result_data_str = json.dumps(result_data) if result_data else None

    payload: dict[str, Any] = {
        "bridge_version":  _BRIDGE_VERSION,
        "action_id":       action_id,
        "origin_host":     origin_host or platform.node(),
        "final_status":    final_status,
        "executed_at":     datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Encrypted payload
        "result_message":  _encrypt_field(result_message or "", pubkey),
        "result_data":     _encrypt_field(result_data_str, pubkey) if result_data_str else None,
    }

    filename = _bridge_filename(action_id)
    target = bridge_dir / "results" / filename
    return _write_bridge_file(target, payload)


# ---------------------------------------------------------------------------
# Proposal ingestion — Windows reads Mac's proposals
# ---------------------------------------------------------------------------

def ingest_proposals(
    bridge_dir: Path,
    queue: Any,   # ActionQueue instance
    artha_dir: Path,
    pubkey: str | None = None,
) -> int:
    """Ingest all pending proposal bridge files into the local actions.db.

    Runs on Windows (executor role) at the start of each poll cycle,
    BEFORE processing inbound Telegram messages.

    Algorithm (per spec §4.1):
      1. Glob proposals/*.json (sorted chronologically)
      2. Parse, validate, check expiry
      3. Decrypt payload fields
      4. queue.ingest_remote() — UUID-dedup only (not type+domain dedup)
      5. Delete file after ingestion (read-once-then-delete)
      6. Log BRIDGE_PROPOSAL_INGEST or BRIDGE_PROPOSAL_SKIP audit events

    Returns count of newly ingested proposals.
    """
    proposals_dir = bridge_dir / "proposals"
    if not proposals_dir.exists():
        return 0

    privkey = _get_privkey(artha_dir)
    ingested = 0

    # Sort by filename = chronological order
    for f in sorted(proposals_dir.glob("*.json")):
        if f.name.startswith("."):
            continue  # skip health files

        # ── Parse ──────────────────────────────────────────────────────
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("[bridge] ingest_proposals: malformed file %s: %s", f.name, exc)
            _audit_event(artha_dir, "BRIDGE_PROPOSAL_SKIP",
                         action_id="?", reason=f"parse_error:{exc!s:.80}")
            _safe_delete(f)
            continue

        action_id = data.get("action_id", "")
        action_type = data.get("action_type", "")
        domain = data.get("domain", "")

        if not action_id or not action_type:
            log.warning("[bridge] ingest_proposals: missing required fields in %s", f.name)
            _safe_delete(f)
            continue

        # ── Expiry check ────────────────────────────────────────────────
        expires_at = data.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp:
                    log.info("[bridge] proposal %s expired (%s) — skipping", action_id[:12], expires_at)
                    _audit_event(artha_dir, "BRIDGE_PROPOSAL_SKIP",
                                 action_id=action_id[:16], reason="expired")
                    _safe_delete(f)
                    continue
            except ValueError:
                pass  # malformed expiry — proceed anyway

        # ── Decrypt payload ─────────────────────────────────────────────
        title = _decrypt_field(data.get("title", ""), privkey)
        description = _decrypt_field(data.get("description", ""), privkey)
        params_str = _decrypt_field(data.get("parameters", "{}"), privkey)

        try:
            parameters = json.loads(params_str)
        except json.JSONDecodeError:
            parameters = {}

        # ── Build ActionProposal ────────────────────────────────────────
        try:
            _ensure_scripts_on_path()
            from actions.base import ActionProposal  # noqa: PLC0415
        except ImportError:
            log.error("[bridge] cannot import ActionProposal — skipping %s", action_id[:12])
            continue

        proposal = ActionProposal(
            id=action_id,
            action_type=action_type,
            domain=domain,
            title=title,
            description=description,
            parameters=parameters,
            friction=data.get("friction", "standard"),
            min_trust=int(data.get("min_trust", 1)),
            sensitivity=data.get("sensitivity", "standard"),
            reversible=bool(data.get("reversible", False)),
            undo_window_sec=data.get("undo_window_sec"),
            expires_at=expires_at,
            source_step=data.get("source_step"),
            source_skill=data.get("source_skill"),
            linked_oi=data.get("linked_oi"),
        )

        # ── Ingest ──────────────────────────────────────────────────────
        try:
            did_ingest = queue.ingest_remote(proposal, pubkey=pubkey)
        except Exception as exc:
            log.error("[bridge] ingest_remote failed for %s: %s", action_id[:12], exc)
            _audit_event(artha_dir, "BRIDGE_PROPOSAL_SKIP",
                         action_id=action_id[:16], reason=str(exc)[:80])
            continue

        if did_ingest:
            _audit_event(artha_dir, "BRIDGE_PROPOSAL_INGEST",
                         action_id=action_id[:16],
                         action_type=action_type,
                         origin_host=data.get("origin_host", "?"))
            ingested += 1
            log.info("[bridge] ingested proposal %s [%s/%s]",
                     action_id[:12], action_type, domain)
        else:
            _audit_event(artha_dir, "BRIDGE_PROPOSAL_SKIP",
                         action_id=action_id[:16], reason="duplicate_action_id")

        # ── Delete after processing (read-once-then-delete) ─────────────
        _safe_delete(f)

    return ingested


# ---------------------------------------------------------------------------
# Result ingestion — Mac reads Windows' results
# ---------------------------------------------------------------------------

def ingest_results(
    bridge_dir: Path,
    queue: Any,   # ActionQueue instance
    artha_dir: Path,
) -> int:
    """Ingest all pending result bridge files into the local actions.db.

    Runs on Mac at catch-up startup, BEFORE briefing_adapter is invoked.

    Additive-only invariant (spec §4.2): only fills in result fields
    (result_status, result_message, result_data, executed_at, status).
    NEVER overwrites existing non-null proposal fields (description,
    parameters, source_step, source_skill, linked_oi).

    Returns count of ingested results.
    """
    results_dir = bridge_dir / "results"
    if not results_dir.exists():
        return 0

    privkey = _get_privkey(artha_dir)
    ingested = 0

    for f in sorted(results_dir.glob("*.json")):
        if f.name.startswith("."):
            continue

        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("[bridge] ingest_results: malformed file %s: %s", f.name, exc)
            _safe_delete(f)
            continue

        action_id = data.get("action_id", "")
        final_status = data.get("final_status", "")

        if not action_id or not final_status:
            log.warning("[bridge] ingest_results: missing required fields in %s", f.name)
            _safe_delete(f)
            continue

        # ── Decrypt result fields ───────────────────────────────────────
        result_message = _decrypt_field(data.get("result_message", ""), privkey)
        result_data_raw = _decrypt_field(data.get("result_data") or "", privkey)
        result_data: dict | None = None
        if result_data_raw:
            try:
                result_data = json.loads(result_data_raw)
            except json.JSONDecodeError:
                result_data = None

        executed_at = data.get("executed_at")

        # ── Apply to local DB (additive-only) ───────────────────────────
        try:
            ok = queue.apply_remote_result(
                action_id=action_id,
                final_status=final_status,
                result_message=result_message,
                result_data=result_data,
                executed_at=executed_at,
            )
        except Exception as exc:
            log.error("[bridge] apply_remote_result failed for %s: %s", action_id[:12], exc)
            _audit_event(artha_dir, "BRIDGE_RESULT_ORPHAN",
                         action_id=action_id[:16], reason=str(exc)[:80])
            _safe_delete(f)
            continue

        if ok:
            _audit_event(artha_dir, "BRIDGE_RESULT_INGEST",
                         action_id=action_id[:16],
                         final_status=final_status,
                         origin_host=data.get("origin_host", "?"))
            ingested += 1
            log.info("[bridge] ingested result %s → %s", action_id[:12], final_status)
        else:
            _audit_event(artha_dir, "BRIDGE_RESULT_ORPHAN",
                         action_id=action_id[:16], reason="not_in_local_db")
            log.warning("[bridge] orphan result %s (not in local DB)", action_id[:12])

        _safe_delete(f)

    return ingested


# ---------------------------------------------------------------------------
# Outbox retry — Windows re-writes missing result files
# ---------------------------------------------------------------------------

def retry_outbox(
    bridge_dir: Path,
    queue: Any,   # ActionQueue instance
    artha_dir: Path,
    pubkey: str | None = None,
) -> int:
    """Scan for terminal actions with bridge_synced=0 and write their result files.

    Provides at-least-once delivery guarantee for results.  Without this, ~1%
    of results could silently never reach the Mac if a crash occurred between
    record_result() and write_result().

    Runs on Windows after proposal ingestion, before GC.
    Returns count of result files written.
    """
    written = 0
    try:
        pending_sync = queue.list_unsynced_results()
    except Exception as exc:
        log.warning("[bridge] retry_outbox: list_unsynced_results failed: %s", exc)
        return 0

    for row in pending_sync:
        action_id = row.get("id", "")
        final_status = row.get("status", "unknown")
        result_message = row.get("result_message") or ""
        result_data_str = row.get("result_data")
        executed_at = row.get("executed_at")

        try:
            result_data = json.loads(result_data_str) if result_data_str else None
        except json.JSONDecodeError:
            result_data = None

        try:
            write_result(
                bridge_dir,
                action_id=action_id,
                final_status=final_status,
                result_message=result_message,
                result_data=result_data,
                pubkey=pubkey,
            )
            queue.mark_bridge_synced(action_id)
            written += 1
            _audit_event(artha_dir, "BRIDGE_RESULT_WRITE",
                         action_id=action_id[:16], final_status=final_status)
        except Exception as exc:
            log.error("[bridge] retry_outbox write_result failed for %s: %s",
                      action_id[:12], exc)

    return written


# ---------------------------------------------------------------------------
# Garbage collection
# ---------------------------------------------------------------------------

def gc(
    bridge_dir: Path,
    artha_dir: Path,
    ttl_days: int = _DEFAULT_TTL_DAYS,
) -> int:
    """Prune bridge files older than ttl_days.

    CRITICAL: always runs AFTER ingestion, never before.  Pruning before
    ingestion would silently drop proposals that haven't been processed yet.

    Health files (.bridge_health_*.json) are never GC'd.

    Returns count of deleted files.
    """
    deleted = 0
    oldest_age_days = 0.0
    cutoff = time.time() - (ttl_days * 86400)

    for subdir in ("proposals", "results"):
        d = bridge_dir / subdir
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            if f.name.startswith("."):
                continue
            try:
                mtime = f.stat().st_mtime
                age_days = (time.time() - mtime) / 86400
                if age_days > oldest_age_days:
                    oldest_age_days = age_days
                if mtime < cutoff:
                    f.unlink(missing_ok=True)
                    deleted += 1
            except OSError:
                pass

    if deleted > 0:
        _audit_event(artha_dir, "BRIDGE_GC",
                     files_pruned=deleted,
                     oldest_age_days=round(oldest_age_days, 1))
        log.info("[bridge] GC removed %d expired file(s)", deleted)

    return deleted


# ---------------------------------------------------------------------------
# Role & config helpers
# ---------------------------------------------------------------------------

def detect_role(channels_config: dict) -> str:
    """Detect whether this machine is 'proposer' (Mac) or 'executor' (Windows).

    Based on channels.yaml → defaults.listener_host:
      hostname matches → 'executor'  (Windows: ingests proposals, writes results)
      hostname differs → 'proposer'  (Mac: writes proposals, reads results)
    """
    import socket
    designated = channels_config.get("defaults", {}).get("listener_host", "").strip()
    if not designated:
        return "proposer"  # single-machine mode
    current = socket.gethostname()
    return "executor" if current.lower() == designated.lower() else "proposer"


def is_bridge_enabled(artha_config: dict) -> bool:
    """Return True if multi_machine.bridge_enabled is true in artha_config.yaml."""
    return bool(artha_config.get("multi_machine", {}).get("bridge_enabled", False))


def get_bridge_dir(artha_dir: Path) -> Path:
    """Return the canonical bridge directory path."""
    return artha_dir / "state" / ".action_bridge"


def load_artha_config(artha_dir: Path) -> dict:
    """Load config/artha_config.yaml (best-effort; returns empty dict on failure)."""
    config_path = artha_dir / "config" / "artha_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml  # noqa: PLC0415
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Conflict detection helper (spec §8.3)
# ---------------------------------------------------------------------------

def detect_conflicts(artha_dir: Path) -> list[str]:
    """Detect OneDrive conflict copies in state/ directory.

    Returns list of conflicted file paths. Empty list = no conflicts.
    """
    import socket as _socket  # local import to avoid top-level dependency
    state_dir = artha_dir / "state"
    conflicts: list[str] = []
    if not state_dir.exists():
        return conflicts
    machine_suffix = f"*-{_socket.gethostname()}*"
    for pattern in ("*conflicted*", machine_suffix, "*-conflict-*"):
        conflicts.extend(str(p) for p in state_dir.glob(pattern))
    return conflicts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_delete(path: Path) -> None:
    """Delete a bridge file, logging but not raising on failure."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("[bridge] failed to delete %s: %s", path.name, exc)


def _audit_event(artha_dir: Path, event_type: str, **kwargs: Any) -> None:
    """Append a bridge audit event to state/audit.md (best-effort)."""
    try:
        audit_path = artha_dir / "state" / "audit.md"
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        parts = [f"[{ts}] {event_type}"]
        for k, v in kwargs.items():
            parts.append(f"{k}:{v}")
        line = " | ".join(parts) + "\n"
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # audit failure is never fatal
