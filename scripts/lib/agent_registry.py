# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/agent_registry.py — External agent registry for AR-9.

Loads, validates, persists, and queries the external agent registry stored
at config/agents/external-registry.yaml.

The registry is the single source of truth for external agent configuration.
It is loaded at most once per process (LRU-cached) and can be invalidated
for testing.

Thread/process safety: atomic YAML write via temp-file+rename (POSIX-safe).
Cloud-folder safe: no SQLite; YAML only.

Registry file: config/agents/external-registry.yaml
Drop folder:   config/agents/external/<agent>.agent.md

Ref: specs/subagent-ext-agent.md §4.2, §6.1, EA-1a
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger("artha.agent_registry")

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0"

VALID_TRUST_TIERS = frozenset({
    "owned", "trusted", "verified", "external", "untrusted"
})
VALID_STATUSES = frozenset({
    "active", "degraded", "suspended", "retired"
})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AgentRouting:
    keywords: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    min_confidence: float = 0.3
    min_keyword_hits: int = 2
    priority: int = 100
    exclude_keywords: list[str] = field(default_factory=list)


@dataclass
class AgentInvocation:
    timeout_seconds: int = 60
    max_budget: int = 10
    max_response_chars: int = 5000
    max_context_chars: int = 2000


@dataclass
class AgentHealth:
    status: str = "active"
    total_invocations: int = 0
    successful_invocations: int = 0
    failed_invocations: int = 0
    consecutive_failures: int = 0
    last_invocation: str | None = None
    last_success: str | None = None
    last_failure: str | None = None
    last_failure_reason: str | None = None
    mean_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    mean_quality_score: float = 0.0
    injection_detections: int = 0
    user_rejections: int = 0
    trust_promotions: list[str] = field(default_factory=list)
    trust_demotions: list[str] = field(default_factory=list)
    cache_hit_rate: float = 0.0
    # EA-12a: per-keyword quality correlation {keyword: [quality_score, ...]}
    keyword_quality: dict[str, list[float]] = field(default_factory=dict)
    # EA-13a: known weak query patterns (recorded when quality < threshold)
    weak_queries: list[str] = field(default_factory=list)
    # EA-14a: per-cluster cache hit count {cluster_keyword: hit_count}
    cache_hits_by_cluster: dict[str, int] = field(default_factory=dict)

    def last_success_within(self, hours: int) -> bool:
        """Return True if the agent had a successful invocation within N hours."""
        if not self.last_success:
            return False
        try:
            ts = datetime.fromisoformat(self.last_success.replace("Z", "+00:00"))
            elapsed = datetime.now(timezone.utc) - ts
            return elapsed.total_seconds() < hours * 3600
        except (ValueError, TypeError):
            return False


@dataclass
class FallbackEntry:
    type: str       # "kb" | "investigation" | "cowork"
    path: str | None = None
    url: str | None = None
    tool: str | None = None


@dataclass
class PiiProfile:
    allow: list[str] = field(default_factory=list)
    block: list[str] = field(default_factory=list)


@dataclass
class ExternalAgent:
    name: str
    label: str
    description: str
    source: str
    enabled: bool
    status: str
    content_hash: str
    trust_tier: str
    auto_dispatch: bool
    auto_dispatch_after: int
    routing: AgentRouting
    invocation: AgentInvocation
    health: AgentHealth
    pii_profile: PiiProfile
    fallback: str | None = None
    fallback_cascade: list[FallbackEntry] = field(default_factory=list)
    cache_responses: bool = True
    cache_ttl_days: int = 7
    max_cache_size_chars: int = 50000
    # Appendix A: registration timestamp (ISO, auto-populated on registration)
    registered_at: str | None = None
    # DD-15 / Appendix A: invoke in background, don't surface result to user
    shadow_mode: bool = False

    def is_active(self) -> bool:
        return self.enabled and self.status in ("active", "degraded")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class AgentRegistry:
    """Loads and manages the external agent registry.

    Usage:
        registry = AgentRegistry.load(config_dir)
        agents = registry.active_agents()
        registry.save()
    """

    def __init__(self, registry_path: Path, agents: dict[str, ExternalAgent],
                 schema_version: str = SCHEMA_VERSION) -> None:
        self._path = registry_path
        self._agents: dict[str, ExternalAgent] = agents
        self.schema_version = schema_version

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, config_dir: Path) -> "AgentRegistry":
        """Load the registry from config/agents/external-registry.yaml.

        If the file doesn't exist, returns an empty registry (not an error).
        """
        registry_path = config_dir / "agents" / "external-registry.yaml"
        if not registry_path.exists():
            return cls(registry_path=registry_path, agents={})

        raw = _read_yaml(registry_path)
        if not isinstance(raw, dict):
            return cls(registry_path=registry_path, agents={})

        agents: dict[str, ExternalAgent] = {}
        for name, entry in (raw.get("agents") or {}).items():
            if not isinstance(entry, dict):
                continue
            try:
                agent = _parse_agent(name, entry)
                agents[name] = agent
            except (KeyError, ValueError, TypeError):
                _log.warning("Skipping malformed registry entry: %s", name,
                             exc_info=True)

        return cls(
            registry_path=registry_path,
            agents=agents,
            schema_version=raw.get("schema_version", SCHEMA_VERSION),
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, name: str) -> ExternalAgent | None:
        return self._agents.get(name)

    def all_agents(self) -> list[ExternalAgent]:
        return list(self._agents.values())

    def active_agents(self) -> list[ExternalAgent]:
        """Return agents that are enabled and in an invocable status."""
        return [a for a in self._agents.values() if a.is_active()]

    def has_agent(self, name: str) -> bool:
        return name in self._agents

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def register(self, agent: ExternalAgent) -> None:
        """Add or replace an agent entry."""
        self._agents[agent.name] = agent

    def retire(self, name: str) -> bool:
        """Mark an agent as retired. Returns False if agent not found."""
        agent = self._agents.get(name)
        if not agent:
            return False
        agent.status = "retired"
        agent.enabled = False
        return True

    def reinstate(self, name: str) -> bool:
        """Reinstate a retired/suspended agent to active. Returns False if not found."""
        agent = self._agents.get(name)
        if not agent:
            return False
        agent.status = "active"
        agent.enabled = True
        return True

    def update_health(self, name: str, health: AgentHealth) -> None:
        """Update the health record for an agent."""
        agent = self._agents.get(name)
        if agent:
            agent.health = health

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate registry contents. Returns list of warning strings."""
        warnings: list[str] = []

        # Track keyword sets for overlap detection
        all_keywords: dict[str, set[str]] = {
            name: set(a.routing.keywords)
            for name, a in self._agents.items()
        }

        for name, agent in self._agents.items():
            # Trust tier
            if agent.trust_tier not in VALID_TRUST_TIERS:
                warnings.append(
                    f"{name}: invalid trust_tier '{agent.trust_tier}'"
                )

            # Status
            if agent.status not in VALID_STATUSES:
                warnings.append(
                    f"{name}: invalid status '{agent.status}'"
                )

            # Keywords required
            if not agent.routing.keywords:
                warnings.append(f"{name}: no routing keywords defined")

            # Source path existence check (warn, don't error)
            if agent.source and not Path(agent.source).is_absolute():
                # Resolve relative to repo root (parent of config/)
                repo_root = self._path.parent.parent.parent
                resolved = repo_root / agent.source
                if not resolved.exists():
                    warnings.append(
                        f"{name}: source file not found: {agent.source}"
                    )

            # Keyword overlap check
            my_kws = all_keywords[name]
            for other_name, other_kws in all_keywords.items():
                if other_name == name or not other_kws:
                    continue
                if len(my_kws) == 0:
                    continue
                overlap = my_kws & other_kws
                overlap_pct = len(overlap) / len(my_kws) * 100
                if overlap_pct > 50:
                    warnings.append(
                        f"{name}: >{overlap_pct:.0f}% keyword overlap "
                        f"with {other_name}: {', '.join(sorted(overlap))}"
                    )

        return warnings

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Atomically write the registry back to external-registry.yaml.

        Uses temp-file + rename for crash safety.
        """
        if yaml is None:
            raise RuntimeError("PyYAML is required to save the agent registry")

        data = {
            "schema_version": SCHEMA_VERSION,
            "agents": {
                name: _agent_to_dict(agent)
                for name, agent in self._agents.items()
            },
        }
        yaml_text = yaml.dump(data, default_flow_style=False, allow_unicode=True,
                              sort_keys=False)

        # Ensure directory exists
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: temp + rename
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=".registry-", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(yaml_text)
            # Atomic on POSIX; non-atomic on Windows but safe because
            # temp file is on same filesystem (same rename call)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Drop-folder discovery
    # ------------------------------------------------------------------

    @classmethod
    def discover_drop_folder(cls, config_dir: Path) -> list[Path]:
        """Scan config/agents/external/ for .agent.md files not yet registered.

        Returns paths to agent files that are new (not in any registry by name).
        """
        drop_folder = config_dir / "agents" / "external"
        if not drop_folder.is_dir():
            return []

        registry = cls.load(config_dir)
        new_files: list[Path] = []

        for p in drop_folder.glob("*.agent.md"):
            agent_name = p.stem.replace(".agent", "")
            if not registry.has_agent(agent_name):
                new_files.append(p)

        return new_files

    @classmethod
    def compute_content_hash(cls, path: Path) -> str:
        """Compute SHA-256 hash of an agent file for change detection."""
        try:
            content = path.read_bytes()
            return "sha256:" + hashlib.sha256(content).hexdigest()
        except OSError:
            return "sha256:" + "0" * 64


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _read_yaml(path: Path) -> Any:
    if yaml is None:
        # Fallback: try safe load via subprocess isn't practical here —
        # return empty dict so registry works without crashing
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _parse_agent(name: str, entry: dict) -> ExternalAgent:
    routing_raw = entry.get("routing") or {}
    routing = AgentRouting(
        keywords=routing_raw.get("keywords") or [],
        domains=routing_raw.get("domains") or [],
        min_confidence=float(routing_raw.get("min_confidence", 0.3)),
        min_keyword_hits=int(routing_raw.get("min_keyword_hits", 2)),
        priority=int(routing_raw.get("priority", 100)),
        exclude_keywords=routing_raw.get("exclude_keywords") or [],
    )

    inv_raw = entry.get("invocation") or {}
    invocation = AgentInvocation(
        timeout_seconds=int(inv_raw.get("timeout_seconds", 60)),
        max_budget=int(inv_raw.get("max_budget", 10)),
        max_response_chars=int(inv_raw.get("max_response_chars", 5000)),
        max_context_chars=int(inv_raw.get("max_context_chars", 2000)),
    )

    health_raw = entry.get("health") or {}
    health = AgentHealth(
        status=health_raw.get("status", "active"),
        total_invocations=int(health_raw.get("total_invocations", 0)),
        successful_invocations=int(health_raw.get("successful_invocations", 0)),
        failed_invocations=int(health_raw.get("failed_invocations", 0)),
        consecutive_failures=int(health_raw.get("consecutive_failures", 0)),
        last_invocation=health_raw.get("last_invocation"),
        last_success=health_raw.get("last_success"),
        last_failure=health_raw.get("last_failure"),
        last_failure_reason=health_raw.get("last_failure_reason"),
        mean_latency_ms=float(health_raw.get("mean_latency_ms", 0.0)),
        p95_latency_ms=float(health_raw.get("p95_latency_ms", 0.0)),
        mean_quality_score=float(health_raw.get("mean_quality_score", 0.0)),
        injection_detections=int(health_raw.get("injection_detections", 0)),
        user_rejections=int(health_raw.get("user_rejections", 0)),
        trust_promotions=list(health_raw.get("trust_promotions") or []),
        trust_demotions=list(health_raw.get("trust_demotions") or []),
        cache_hit_rate=float(health_raw.get("cache_hit_rate", 0.0)),
        keyword_quality=dict(health_raw.get("keyword_quality") or {}),
        weak_queries=list(health_raw.get("weak_queries") or []),
        cache_hits_by_cluster=dict(health_raw.get("cache_hits_by_cluster") or {}),
    )

    pii_raw = entry.get("pii_profile") or {}
    pii_profile = PiiProfile(
        allow=list(pii_raw.get("allow") or []),
        block=list(pii_raw.get("block") or []),
    )

    cascade_raw = entry.get("fallback_cascade") or []
    fallback_cascade = [
        FallbackEntry(
            type=item.get("type", "kb"),
            path=item.get("path"),
            url=item.get("url"),
            tool=item.get("tool"),
        )
        for item in cascade_raw
        if isinstance(item, dict)
    ]

    return ExternalAgent(
        name=name,
        label=entry.get("label", name),
        description=entry.get("description", ""),
        source=entry.get("source", ""),
        enabled=bool(entry.get("enabled", True)),
        status=entry.get("status", "active"),
        content_hash=entry.get("content_hash", ""),
        trust_tier=entry.get("trust_tier", "external"),
        auto_dispatch=bool(entry.get("auto_dispatch", False)),
        auto_dispatch_after=int(entry.get("auto_dispatch_after", 10)),
        routing=routing,
        invocation=invocation,
        health=health,
        pii_profile=pii_profile,
        fallback=entry.get("fallback"),
        fallback_cascade=fallback_cascade,
        cache_responses=bool(entry.get("cache_responses", True)),
        cache_ttl_days=int(entry.get("cache_ttl_days", 7)),
        max_cache_size_chars=int(entry.get("max_cache_size_chars", 50000)),
        registered_at=entry.get("registered_at"),
        shadow_mode=bool(entry.get("shadow_mode", False)),
    )


def _agent_to_dict(agent: ExternalAgent) -> dict:
    """Convert an ExternalAgent to a serializable dict for YAML output."""
    return {
        "label": agent.label,
        "description": agent.description,
        "source": agent.source,
        "enabled": agent.enabled,
        "status": agent.status,
        "content_hash": agent.content_hash,
        "trust_tier": agent.trust_tier,
        "auto_dispatch": agent.auto_dispatch,
        "auto_dispatch_after": agent.auto_dispatch_after,
        "pii_profile": {
            "allow": agent.pii_profile.allow,
            "block": agent.pii_profile.block,
        },
        "routing": {
            "keywords": agent.routing.keywords,
            "domains": agent.routing.domains,
            "min_confidence": agent.routing.min_confidence,
            "min_keyword_hits": agent.routing.min_keyword_hits,
            "priority": agent.routing.priority,
            "exclude_keywords": agent.routing.exclude_keywords,
        },
        "invocation": {
            "timeout_seconds": agent.invocation.timeout_seconds,
            "max_budget": agent.invocation.max_budget,
            "max_response_chars": agent.invocation.max_response_chars,
            "max_context_chars": agent.invocation.max_context_chars,
        },
        "fallback": agent.fallback,
        "fallback_cascade": [
            {k: v for k, v in vars(fc).items() if v is not None}
            for fc in agent.fallback_cascade
        ],
        "cache_responses": agent.cache_responses,
        "cache_ttl_days": agent.cache_ttl_days,
        "max_cache_size_chars": agent.max_cache_size_chars,
        "registered_at": agent.registered_at,
        "shadow_mode": agent.shadow_mode,
        "health": {
            "status": agent.health.status,
            "total_invocations": agent.health.total_invocations,
            "successful_invocations": agent.health.successful_invocations,
            "failed_invocations": agent.health.failed_invocations,
            "consecutive_failures": agent.health.consecutive_failures,
            "last_invocation": agent.health.last_invocation,
            "last_success": agent.health.last_success,
            "last_failure": agent.health.last_failure,
            "last_failure_reason": agent.health.last_failure_reason,
            "mean_latency_ms": agent.health.mean_latency_ms,
            "p95_latency_ms": agent.health.p95_latency_ms,
            "mean_quality_score": agent.health.mean_quality_score,
            "injection_detections": agent.health.injection_detections,
            "user_rejections": agent.health.user_rejections,
            "trust_promotions": agent.health.trust_promotions,
            "trust_demotions": agent.health.trust_demotions,
            "cache_hit_rate": agent.health.cache_hit_rate,
            "keyword_quality": agent.health.keyword_quality,
            "weak_queries": agent.health.weak_queries,
            "cache_hits_by_cluster": agent.health.cache_hits_by_cluster,
        },
    }
