"""Typed contracts of the Catch-up core (re-artha §6.1, §6.5, §7.1, §12.4).

The vocabulary here is the only place stage names, exit codes, lifecycle values and
failure classes are defined; ledger, adapters and the command entry point import
them.  Everything is immutable and JSON round-trippable, and every constructor
validates so an invalid value cannot be built.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from catchup_core import CORE_CONTRACT_VERSION

__all__ = [
    "AlertStatus", "BriefingFormat", "CatchupRequest", "CatchupResult", "Confidentiality",
    "CoverageEntry", "CoverageStatus", "ExecutionMode", "ExitCode", "FactStatus", "FailureClass",
    "InvalidTransition", "ProcessingState", "Provenance", "RunStatus", "Severity", "Stage",
    "StageCheckpoint", "can_transition", "next_stage",
]


class ContractError(ValueError):
    """A contract value failed validation."""


class Stage(StrEnum):
    CREATED = "CREATED"
    PREFLIGHT = "PREFLIGHT"
    FETCH = "FETCH"
    NORMALIZE = "NORMALIZE"
    EXTRACT = "EXTRACT"
    RECONCILE = "RECONCILE"
    TRIAGE = "TRIAGE"
    COMPOSE = "COMPOSE"
    VALIDATE = "VALIDATE"
    COMMIT = "COMMIT"
    PRESENTED = "PRESENTED"
    FINALIZED = "FINALIZED"


_ORDER: tuple[Stage, ...] = tuple(Stage)


class InvalidTransition(ContractError):
    pass


def next_stage(stage: Stage) -> Stage | None:
    index = _ORDER.index(stage)
    return _ORDER[index + 1] if index + 1 < len(_ORDER) else None


def can_transition(src: Stage, dst: Stage) -> bool:
    """Forward one step, or straight to FINALIZED (which seals state even after failure)."""
    if src is Stage.FINALIZED:
        return False
    return dst is next_stage(src) or dst is Stage.FINALIZED


class ExitCode(IntEnum):
    COMPLETE = 0
    DEGRADED = 10
    BLOCKED = 20
    USAGE_ERROR = 30
    AWAITING_EXTRACTION = 40    # paused: answer the printed requests, then resume with --responses


class RunStatus(StrEnum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    BLOCKED = "blocked"

    def exit_code(self) -> ExitCode:
        return {
            RunStatus.COMPLETE: ExitCode.COMPLETE,
            RunStatus.DEGRADED: ExitCode.DEGRADED,
            RunStatus.BLOCKED: ExitCode.BLOCKED,
        }[self]


class ExecutionMode(StrEnum):
    AUTHORITATIVE = "authoritative"
    SHADOW = "shadow"


class BriefingFormat(StrEnum):
    FLASH = "flash"
    STANDARD = "standard"
    DEEP = "deep"
    DIGEST = "digest"


class FailureClass(StrEnum):
    """Bounded error taxonomy (§7.8)."""

    AUTH = "auth"
    PROVIDER = "provider"
    NETWORK = "network"
    SCHEMA_REJECTED = "schema_rejected"
    DECODE = "decode"
    EXTRACT = "extract"
    RECONCILE = "reconcile"
    POLICY = "policy"
    STORAGE = "storage"
    PERMISSION = "permission"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class Provenance(StrEnum):
    VERIFIED_SOURCE = "verified_source"
    USER_CONFIRMED = "user_confirmed"
    LEGACY_UNVERIFIED = "legacy_unverified"
    DISPUTED = "disputed"
    MODEL_HYPOTHESIS = "model_hypothesis"


class FactStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    UNKNOWN = "unknown"
    RETRACTED = "retracted"


class Confidentiality(StrEnum):
    ORDINARY = "ordinary"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class Severity(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"

    @property
    def rank(self) -> int:
        return int(self.value[1])


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    SNOOZED = "snoozed"
    RESOLVED = "resolved"


class ProcessingState(StrEnum):
    DISCOVERED = "discovered"
    FETCHED = "fetched"
    DECODED = "decoded"
    NORMALIZED = "normalized"
    EXTRACTED = "extracted"
    RECONCILED = "reconciled"
    EXCLUDED = "excluded"


class CoverageStatus(StrEnum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    ANOMALY = "anomaly"
    NOTIFIED_NOT_FETCHED = "notified_not_fetched"

    @property
    def quiet_claim_allowed(self) -> bool:
        return self is CoverageStatus.COMPLETE


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


@dataclass(frozen=True)
class CatchupRequest:
    """Input of the coordinator (§6.5)."""

    catchup_id: str
    actor: str
    channel: str
    mode: ExecutionMode = ExecutionMode.AUTHORITATIVE
    briefing_format: BriefingFormat = BriefingFormat.STANDARD
    connector_scopes: tuple[str, ...] = ()
    pilot_domains: tuple[str, ...] = ()
    host: str = ""
    policy_version: str = "0"
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require(bool(self.catchup_id.strip()), "catchup_id is required (idempotency key)")
        _require(bool(self.actor.strip()), "actor is required")
        _require(bool(self.channel.strip()), "channel is required")
        _require(self.schema_version >= 1, "schema_version must be >= 1")
        if self.mode is ExecutionMode.AUTHORITATIVE:
            _require(bool(self.host.strip()), "an authoritative Catch-up names its designated host")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CORE_CONTRACT_VERSION,
            "catchup_id": self.catchup_id, "actor": self.actor, "channel": self.channel,
            "mode": self.mode.value, "briefing_format": self.briefing_format.value,
            "connector_scopes": list(self.connector_scopes), "pilot_domains": list(self.pilot_domains),
            "host": self.host, "policy_version": self.policy_version, "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CatchupRequest:
        try:
            return cls(
                catchup_id=str(data["catchup_id"]), actor=str(data["actor"]), channel=str(data["channel"]),
                mode=ExecutionMode(data.get("mode", "authoritative")),
                briefing_format=BriefingFormat(data.get("briefing_format", "standard")),
                connector_scopes=tuple(data.get("connector_scopes", ())),
                pilot_domains=tuple(data.get("pilot_domains", ())),
                host=str(data.get("host", "")), policy_version=str(data.get("policy_version", "0")),
                schema_version=int(data.get("schema_version", 1)),
            )
        except KeyError as exc:
            raise ContractError(f"missing field {exc}") from exc
        except ValueError as exc:
            raise ContractError(str(exc)) from exc


@dataclass(frozen=True)
class StageCheckpoint:
    catchup_id: str
    stage: Stage
    status: str                       # "committed" | "failed" | "skipped"
    input_versions: Mapping[str, str] = field(default_factory=dict)
    artifact_ids: tuple[str, ...] = ()
    failure_class: FailureClass | None = None

    def __post_init__(self) -> None:
        _require(self.status in ("committed", "failed", "skipped"), f"bad checkpoint status {self.status!r}")
        _require((self.status == "failed") == (self.failure_class is not None),
                 "a failed checkpoint carries a failure class and only a failed one does")


@dataclass(frozen=True)
class CoverageEntry:
    connector: str
    account_scope: str
    status: CoverageStatus
    required: bool = True
    last_verified: str | None = None
    blind_spots: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.connector), "connector is required")
        _require(bool(self.account_scope), "account_scope is required")


@dataclass(frozen=True)
class CatchupResult:
    """Output of the coordinator (§6.5).  A shadow result is comparison data only."""

    catchup_id: str
    mode: ExecutionMode
    status: RunStatus
    coverage: tuple[CoverageEntry, ...] = ()
    briefing_id: str | None = None
    briefing_version: int | None = None
    reason_unpublished: str | None = None
    artifact_ids: tuple[str, ...] = ()
    failures: tuple[FailureClass, ...] = ()
    pending_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode is ExecutionMode.SHADOW:
            _require(self.briefing_id is None, "a shadow result never publishes a Briefing")
        if self.briefing_id is not None:
            _require(self.briefing_version is not None and self.briefing_version >= 1,
                     "a published Briefing has a version >= 1")
            _require(self.status is not RunStatus.BLOCKED, "a blocked Catch-up publishes no Briefing")
        elif self.mode is ExecutionMode.AUTHORITATIVE:
            _require(bool(self.reason_unpublished), "an unpublished authoritative result states why")
        if self.status is RunStatus.COMPLETE:
            required_gaps = [c for c in self.coverage if c.required and not c.status.quiet_claim_allowed]
            _require(not required_gaps, "complete is impossible while required coverage has gaps")
        if self.status is RunStatus.BLOCKED:
            _require(bool(self.failures) or bool(self.reason_unpublished),
                     "a blocked result names a failure or a reason")

    @classmethod
    def derive_status(cls, coverage: tuple[CoverageEntry, ...], failures: tuple[FailureClass, ...]) -> RunStatus:
        """Complete only when every required source is complete and nothing failed."""
        if failures:
            return RunStatus.DEGRADED
        if any(c.required and not c.status.quiet_claim_allowed for c in coverage):
            return RunStatus.DEGRADED
        return RunStatus.COMPLETE

    def exit_code(self) -> ExitCode:
        return self.status.exit_code()

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CORE_CONTRACT_VERSION,
            "catchup_id": self.catchup_id, "mode": self.mode.value, "status": self.status.value,
            "briefing_id": self.briefing_id, "briefing_version": self.briefing_version,
            "reason_unpublished": self.reason_unpublished,
            "coverage": [
                {"connector": c.connector, "account_scope": c.account_scope, "status": c.status.value,
                 "required": c.required, "last_verified": c.last_verified,
                 "blind_spots": list(c.blind_spots), "detail": c.detail}
                for c in self.coverage
            ],
            "artifact_ids": list(self.artifact_ids),
            "failures": [f.value for f in self.failures],
            "pending_refs": list(self.pending_refs),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CatchupResult:
        try:
            version = str(data["contract_version"])
            if version.split(".", maxsplit=1)[0] != CORE_CONTRACT_VERSION.split(".")[0]:
                raise ContractError(f"unsupported contract major version {version}")
            return cls(
                catchup_id=str(data["catchup_id"]),
                mode=ExecutionMode(data["mode"]),
                status=RunStatus(data["status"]),
                coverage=tuple(
                    CoverageEntry(
                        connector=c["connector"], account_scope=c["account_scope"],
                        status=CoverageStatus(c["status"]), required=bool(c.get("required", True)),
                        last_verified=c.get("last_verified"), blind_spots=tuple(c.get("blind_spots", ())),
                        detail=c.get("detail", ""),
                    )
                    for c in data.get("coverage", ())
                ),
                briefing_id=data.get("briefing_id"), briefing_version=data.get("briefing_version"),
                reason_unpublished=data.get("reason_unpublished"),
                artifact_ids=tuple(data.get("artifact_ids", ())),
                failures=tuple(FailureClass(f) for f in data.get("failures", ())),
                pending_refs=tuple(data.get("pending_refs", ())),
            )
        except KeyError as exc:
            raise ContractError(f"missing field {exc}") from exc
        except ValueError as exc:
            if isinstance(exc, ContractError):
                raise
            raise ContractError(str(exc)) from exc
