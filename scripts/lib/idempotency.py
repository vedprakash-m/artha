#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/lib/idempotency.py — Composite key idempotency store for Artha.

Prevents duplicate actions (e.g. scheduling the same appointment twice or
sending the same email twice) by maintaining a persistent store of
in-flight and completed action keys in ``state/idempotency_keys.json``.

## Composite Key Formula

    SHA-256(recipient + intent + date_window)[:32] (hex string)

    Where:
      recipient:   normalised lowercase recipient/domain label
      intent:      normalised action type / purpose
      date_window: truncated to day (YYYY-MM-DD) for most action types;
                   or to week (YYYY-WNN) for scheduling; or to month
                   (YYYY-MM) for financial.

## Key Lifecycle

    RESERVED → COMPLETED  (normal happy path)
    RESERVED → FAILED      (executor reported failure)
    RESERVED → EXPIRED     (TTL elapsed; key garbage-collected on PREFLIGHT)

## Store schema (state/idempotency_keys.json)

    {
      "<composite_key>": {
        "action_type": "communication",
        "status":      "RESERVED" | "COMPLETED" | "FAILED" | "EXPIRED",
        "created_at":  "<ISO-8601 UTC>",
        "expires_at":  "<ISO-8601 UTC>",
        "resolved_at": "<ISO-8601 UTC>"   // optional
      },
      ...
    }

## YAML-configurable windows (config/guardrails.yaml → idempotency_windows)

    idempotency_windows:
      scheduling:    7d
      financial:     30d
      communication: 48h
      default:       24h

All writes are atomic (tempfile + os.replace).  The store file is NOT
age-encrypted — it contains only opaque hashed keys and timestamps, no PII.

Ref: specs/harden.md §2.3 Idempotency Layer, §2.3.2 Config
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_STORE_PATH = _REPO_ROOT / "state" / "idempotency_keys.json"
_GUARDRAILS_PATH = _REPO_ROOT / "config" / "guardrails.yaml"

# ---------------------------------------------------------------------------
# Window configuration (loaded lazily from guardrails.yaml)
# ---------------------------------------------------------------------------

_WINDOW_CACHE: dict[str, timedelta] | None = None


def _parse_duration(s: str) -> timedelta:
    """Parse a duration string like '7d', '48h', '30d' into timedelta."""
    s = s.strip().lower()
    m = re.match(r"^(\d+)(d|h|m)$", s)
    if not m:
        raise ValueError(f"Cannot parse duration: {s!r}")
    n, unit = int(m.group(1)), m.group(2)
    if unit == "d":
        return timedelta(days=n)
    if unit == "h":
        return timedelta(hours=n)
    return timedelta(minutes=n)


def _load_windows() -> dict[str, timedelta]:
    """Load idempotency window configuration from guardrails.yaml."""
    global _WINDOW_CACHE
    if _WINDOW_CACHE is not None:
        return _WINDOW_CACHE

    defaults = {
        "scheduling": timedelta(days=7),
        "financial": timedelta(days=30),
        "communication": timedelta(hours=48),
        "default": timedelta(hours=24),
    }

    try:
        # Minimal YAML-ish parse (avoid PyYAML dependency)
        text = _GUARDRAILS_PATH.read_text(encoding="utf-8")
        # Find the idempotency_windows block
        block_match = re.search(
            r"idempotency_windows:\s*\n((?:[ \t]+\S[^\n]*\n?)*)",
            text,
        )
        if block_match:
            block = block_match.group(1)
            for line in block.splitlines():
                kv = re.match(r"^\s+(\w+):\s*(.+)", line)
                if kv:
                    key = kv.group(1).strip()
                    # Strip inline YAML comments before parsing duration
                    raw_val = kv.group(2).split("#")[0].strip()
                    try:
                        defaults[key] = _parse_duration(raw_val)
                    except ValueError:
                        pass
    except (OSError, Exception):  # noqa: BLE001
        pass  # use defaults

    _WINDOW_CACHE = defaults
    return _WINDOW_CACHE


def get_window(action_type: str, domain: str = "") -> timedelta:
    """Return the idempotency window for an action type, with optional domain refinement.

    DEBT-013: Domain-qualified lookup allows different dedup windows for the
    same action_type depending on the originating domain.  For example,
    ``instruction_sheet`` actions for ``immigration`` use a 30-day window
    (annual-cadence events) while ``instruction_sheet`` for ``iot`` uses 4h.

    Lookup priority:
    1. ``{action_type}_{domain}`` qualified key (e.g. ``instruction_sheet_immigration``)
    2. ``{action_type}`` unqualified key (e.g. ``instruction_sheet``)
    3. ``default`` (24 hours)

    Args:
        action_type: One of ``scheduling``, ``financial``, ``communication``,
                     ``instruction_sheet``, or any custom type in guardrails.yaml.
        domain:      Optional originating Artha domain (e.g. ``immigration``, ``iot``).
                     When provided, a domain-qualified key is tried first.

    Returns:
        timedelta representing the deduplication window.
    """
    windows = _load_windows()
    if domain:
        qualified = f"{action_type}_{domain}"
        if qualified in windows:
            return windows[qualified]
    return windows.get(action_type, windows["default"])


# ---------------------------------------------------------------------------
# Composite key
# ---------------------------------------------------------------------------

class CompositeKey:
    """Computes composite idempotency keys for Artha action proposals."""

    @staticmethod
    def _truncate_date(action_type: str) -> str:
        """Truncate current date to the appropriate window granularity.

        - scheduling:    YYYY-WNN (ISO week number)
        - financial:     YYYY-MM  (month)
        - communication: YYYY-MM-DD (day)
        - default:       YYYY-MM-DD (day)
        """
        now = datetime.now(timezone.utc)
        if action_type == "scheduling":
            return now.strftime("%Y-W%V")
        if action_type == "financial":
            return now.strftime("%Y-%m")
        return now.strftime("%Y-%m-%d")

    @classmethod
    def compute(
        cls,
        recipient: str,
        intent: str,
        action_type: str = "default",
        *,
        date_window: str | None = None,
        signal_type: str = "",
    ) -> str:
        """Compute a composite idempotency key.

        Args:
            recipient:    Target recipient/domain (e.g. "dr.smith@clinic.com",
                          "brokerage_account", "employer"). Normalised to lowercase.
            intent:       Action purpose (e.g. "schedule_appointment",
                          "transfer_funds", "send_reminder").  Normalised.
            action_type:  Action type bucket: ``scheduling``, ``financial``,
                          ``communication``, or ``default``.
            date_window:  Override date window string (default: auto-derived
                          from action_type + current date).
            signal_type:  RD-07: For ``instruction_sheet`` actions, include
                          the originating signal type in the key to prevent
                          cross-signal suppression (e.g. subscription_renewal
                          and form_deadline for the same entity must not share
                          a key and suppress each other).

        Returns:
            64-character lowercase hex SHA-256 digest.

        Migration note (RD-07): Keys previously computed without signal_type
        for instruction_sheet actions will not match keys computed with it.
        This causes one-cycle deduplication loss on first deployment — the
        worst case is one extra action proposal per affected signal. The user
        can decline it; it will be keyed correctly going forward.
        """
        # RD-07: Qualify instruction_sheet keys by signal_type to prevent
        # cross-signal collisions (subscription_renewal ≠ form_deadline).
        qualifier = (
            f"{signal_type}|" if (signal_type and action_type == "instruction_sheet")
            else ""
        )
        normalized = (
            qualifier
            + recipient.strip().lower()
            + "|"
            + intent.strip().lower()
            + "|"
            + (date_window or cls._truncate_date(action_type))
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Idempotency store
# ---------------------------------------------------------------------------

class IdempotencyStore:
    """Thread-unsafe persistent store for in-flight and completed action keys.

    Single-writer design — Artha runs as one CLI session at a time.
    All mutations are atomic (tempfile + os.replace).

    Args:
        path: Path to the JSON store file (default: state/idempotency_keys.json).
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _STORE_PATH

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_or_reserve(
        self,
        key: str,
        action_type: str = "default",
    ) -> Literal["ok", "duplicate", "pending"]:
        """Check for and optionally reserve an idempotency key.

        States returned:
          - ``"ok"``: key not seen before; entry created with RESERVED status.
          - ``"duplicate"``: key exists and is COMPLETED or RESERVED (dedup).
          - ``"pending"``: key exists with RESERVED status from a prior session
            (crash recovery — PREFLIGHT should surface this to the user).

        Args:
            key:         Composite key from :meth:`CompositeKey.compute`.
            action_type: Used to compute ``expires_at`` window.

        Returns:
            ``"ok"``, ``"duplicate"``, or ``"pending"``.
        """
        data = self._load()
        entry = data.get(key)

        if entry:
            status = entry.get("status", "")
            if status in ("COMPLETED", "RESERVED"):
                # Check if expired
                expires_at_str = entry.get("expires_at", "")
                if expires_at_str:
                    try:
                        expires_at = datetime.fromisoformat(expires_at_str)
                        if datetime.now(timezone.utc) > expires_at:
                            # Expired — treat as if not seen; overwrite below
                            pass
                        else:
                            if status == "RESERVED":
                                return "pending"
                            return "duplicate"
                    except ValueError:
                        pass

        # Reserve the key
        now = datetime.now(timezone.utc)
        window = get_window(action_type)
        data[key] = {
            "action_type": action_type,
            "status": "RESERVED",
            "created_at": now.isoformat(),
            "expires_at": (now + window).isoformat(),
        }
        self._save(data)
        return "ok"

    def mark_completed(self, key: str) -> None:
        """Mark a RESERVED key as COMPLETED.

        Args:
            key: Composite key previously reserved via :meth:`check_or_reserve`.
        """
        data = self._load()
        if key in data:
            data[key]["status"] = "COMPLETED"
            data[key]["resolved_at"] = datetime.now(timezone.utc).isoformat()
            self._save(data)

    def mark_failed(self, key: str) -> None:
        """Mark a RESERVED key as FAILED.

        FAILED keys are NOT blocked on future reservation — they allow retry.

        Args:
            key: Composite key previously reserved via :meth:`check_or_reserve`.
        """
        data = self._load()
        if key in data:
            data[key]["status"] = "FAILED"
            data[key]["resolved_at"] = datetime.now(timezone.utc).isoformat()
            self._save(data)

    def prune_expired(self) -> int:
        """Remove all RESERVED/FAILED/EXPIRED keys past their expiry window.

        Called at PREFLIGHT to prevent unbounded key accumulation.
        Also marks any stale RESERVED keys as EXPIRED before pruning.

        Returns:
            Number of keys removed.
        """
        data = self._load()
        now = datetime.now(timezone.utc)
        to_delete: list[str] = []
        pending_crashes: list[str] = []

        for key, entry in data.items():
            expires_str = entry.get("expires_at", "")
            status = entry.get("status", "")
            if not expires_str:
                continue
            try:
                expires_at = datetime.fromisoformat(expires_str)
            except ValueError:
                continue

            if now > expires_at:
                if status == "RESERVED":
                    # Stale RESERVED = crash during execution — surface to PREFLIGHT
                    pending_crashes.append(key)
                    data[key]["status"] = "EXPIRED"
                    # We'll keep expired entries for one more cycle for audit visibility
                elif status in ("FAILED", "EXPIRED", "COMPLETED"):
                    to_delete.append(key)

        for key in to_delete:
            del data[key]

        if data != self._load():  # only write if changed
            self._save(data)

        # Emit telemetry for pending crashes
        if pending_crashes:
            try:
                from telemetry import emit  # noqa: PLC0415
                for key in pending_crashes:
                    emit(
                        "idempotency.pending_crash",
                        extra={"key_prefix": key[:12]},
                    )
            except Exception:  # noqa: BLE001
                pass

        return len(to_delete) + len(pending_crashes)

    def list_pending(self) -> list[dict]:
        """Return all RESERVED entries (in-flight or crash victims).

        Returns:
            List of dicts with fields: key, action_type, created_at, expires_at.
        """
        data = self._load()
        return [
            {
                "key": k,
                "action_type": v.get("action_type"),
                "status": v.get("status"),
                "created_at": v.get("created_at"),
                "expires_at": v.get("expires_at"),
            }
            for k, v in data.items()
            if v.get("status") == "RESERVED"
        ]

    # ------------------------------------------------------------------
    # Private I/O
    # ------------------------------------------------------------------

    def get_entry(self, key: str) -> dict:
        """Return the store entry for *key*, or {} if not found. (DEBT-EXEC-001)

        Public alternative to the private _load() pattern:
            store._load().get(key, {})  ← breaks if _load() is renamed

        Use this method anywhere you need to inspect a single entry's state
        without owning the full store lifecycle.
        """
        return self._load().get(key, {})

    def _load(self) -> dict:
        """Load the store from disk.  Returns empty dict if file is missing/corrupt."""
        try:
            text = self._path.read_text(encoding="utf-8")
            return json.loads(text)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        """Atomically write the store to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=f".{self._path.name}-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, self._path)
        except Exception:  # noqa: BLE001
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_DEFAULT_STORE: IdempotencyStore | None = None


def get_default_store() -> IdempotencyStore:
    """Return the module-level default store instance (singleton)."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = IdempotencyStore()
    return _DEFAULT_STORE


def check_or_reserve(
    recipient: str,
    intent: str,
    action_type: str = "default",
    *,
    date_window: str | None = None,
) -> tuple[Literal["ok", "duplicate", "pending"], str]:
    """Convenience: compute key and check/reserve in one call.

    Returns:
        Tuple of (status, composite_key).
    """
    key = CompositeKey.compute(recipient, intent, action_type, date_window=date_window)
    status = get_default_store().check_or_reserve(key, action_type)
    return status, key


def mark_completed(key: str) -> None:
    """Convenience: mark a previously reserved key as completed. DEBT-036."""
    get_default_store().mark_completed(key)
