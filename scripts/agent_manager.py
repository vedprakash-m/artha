"""
scripts/agent_manager.py — AR-9 External Agent Management CLI.

Manage registered external agents: register, retire, promote, inspect.
Operates on config/agents/external-registry.yaml via AgentRegistry.

Commands:
  list                  — list all registered agents + health summary
  register [--file F]   — register new agents from drop folder (or specific file)
  retire <name>         — retire an agent (status=retired, enabled=false)
  reinstate <name>      — restore a suspended or degraded agent to active
  promote <name>        — manually promote trust tier one level
  demote <name>         — manually demote trust tier one level
  refresh-cache <name>  — invalidate knowledge cache for an agent
  discover              — scan drop folder, show unregistered agent files
  health [name]         — show health metrics (all agents or specific agent)
  validate              — validate registry YAML integrity
  delegate              — run post-invocation pipeline (verify + integrate + cache)

CLI:
  python scripts/agent_manager.py list
  python scripts/agent_manager.py register
  python scripts/agent_manager.py register --file config/agents/external/my-agent.agent.md
  python scripts/agent_manager.py retire storage-deployment-expert
  python scripts/agent_manager.py reinstate storage-deployment-expert
  python scripts/agent_manager.py promote storage-deployment-expert
  python scripts/agent_manager.py refresh-cache storage-deployment-expert
  python scripts/agent_manager.py discover
  python scripts/agent_manager.py health
  python scripts/agent_manager.py health storage-deployment-expert
  python scripts/agent_manager.py validate
  python scripts/agent_manager.py delegate --agent storage-deployment-expert --query "SDP stuck"
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_CONFIG_DIR = _REPO_ROOT / "config"
_DROP_DIR = _CONFIG_DIR / "agents" / "external"
_REGISTRY_FILE = _CONFIG_DIR / "agents" / "external-registry.yaml"
_CACHE_DIR = _REPO_ROOT / "tmp" / "ext-agent-cache"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def _load_registry():
    """Load AgentRegistry, returning None if unavailable."""
    try:
        from lib.agent_registry import AgentRegistry  # noqa: PLC0415
        return AgentRegistry.load(_CONFIG_DIR)
    except Exception as exc:
        print(f"⛔ Could not load agent registry: {exc}", file=sys.stderr)
        return None


def _save_registry(reg) -> bool:
    try:
        reg.save()
        return True
    except Exception as exc:
        print(f"⛔ Could not save registry: {exc}", file=sys.stderr)
        return False


def _parse_agent_md(path: Path) -> dict:
    """Parse a .agent.md file into a minimal agent definition dict.

    Expected frontmatter fields (YAML between --- markers):
      name, label, description, domains, trust_tier, keywords, exclude_keywords,
      routing.min_confidence, invocation.timeout_seconds, invocation.auto_dispatch,
      invocation.max_response_chars, pii_profile.allow, fallback_cascade
    """
    raw = path.read_text(encoding="utf-8")
    frontmatter: dict = {}

    # Extract YAML frontmatter
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            try:
                import yaml  # noqa: PLC0415
                frontmatter = yaml.safe_load(parts[1]) or {}
            except Exception:
                pass

    # Derive name from filename if not in frontmatter
    if "name" not in frontmatter:
        frontmatter["name"] = path.stem.replace(".agent", "")

    return frontmatter


def _make_agent_entry(frontmatter: dict, source_path: Path) -> dict:
    """Build a canonical registry entry from parsed frontmatter."""
    name = frontmatter.get("name", source_path.stem)

    # Compute content hash for change detection — full SHA-256 with prefix
    # (matches AgentRegistry.compute_content_hash() format)
    content_hash = "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest()

    # Build routing block
    routing = frontmatter.get("routing", {}) or {}
    keywords = frontmatter.get("keywords", []) or []
    exclude_keywords = frontmatter.get("exclude_keywords", []) or []

    # Build invocation block
    invocation = frontmatter.get("invocation", {}) or {}

    # Build pii_profile block
    pii_profile = frontmatter.get("pii_profile", {}) or {}

    # Fallback cascade — convert bare strings to type-dicts expected by _parse_agent
    raw_cascade = frontmatter.get("fallback_cascade", ["kb"]) or ["kb"]
    fallback_cascade = [
        item if isinstance(item, dict) else {"type": item}
        for item in raw_cascade
    ]

    return {
        "name": name,
        "label": frontmatter.get("label", name.replace("-", " ").title()),
        "description": frontmatter.get("description", ""),
        "trust_tier": frontmatter.get("trust_tier", "external"),
        "enabled": True,
        "status": "active",
        "source": source_path.relative_to(_REPO_ROOT).as_posix(),
        "content_hash": content_hash,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "shadow_mode": frontmatter.get("shadow_mode", False),
        "auto_dispatch": invocation.get("auto_dispatch", False),
        "auto_dispatch_after": invocation.get("auto_dispatch_after", 10),
        "routing": {
            "keywords": keywords,
            "domains": frontmatter.get("domains", []),
            "exclude_keywords": exclude_keywords,
            "min_confidence": routing.get("min_confidence", 0.6),
        },
        "invocation": {
            "timeout_seconds": invocation.get("timeout_seconds", 60),
            "max_response_chars": invocation.get("max_response_chars", 5000),
            "max_context_chars": invocation.get("max_context_chars", 2000),
        },
        "pii_profile": {
            "allow": pii_profile.get("allow", []),
            "block": pii_profile.get("block", []),
        },
        "fallback_cascade": fallback_cascade,
        "health": {
            "status": "active",
            "total_invocations": 0,
            "successful_invocations": 0,
            "failed_invocations": 0,
            "mean_quality_score": 0.0,
            "consecutive_failures": 0,
            "last_invocation": None,
            "last_success": None,
            "last_failure_reason": None,
        },
    }


# ---------------------------------------------------------------------------
# §4.9.1 Archive helpers
# ---------------------------------------------------------------------------

_ARCHIVE_DIR = _DROP_DIR / ".archive"
_ARCHIVE_KEEP = 5  # keep last N versions per agent


def _archive_agent_version(name: str, current_file: Path, stored_hash: Optional[str]) -> None:
    """Copy the *current* (pre-update) agent file to the .archive folder.

    Archive filename: <agent-name>-<hash[:8]>.md
    Keeps last _ARCHIVE_KEEP versions per agent; prunes oldest excess.
    Ref: specs/subagent-ext-agent.md §4.9.1 (Gemini feature).
    """
    if not current_file.exists():
        return
    # Derive a short hash for the archive filename
    short = (stored_hash or "").replace("sha256:", "")[:8] or "unknown"
    archive_name = f"{name}-{short}.md"
    try:
        _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        dest = _ARCHIVE_DIR / archive_name
        shutil.copy2(current_file, dest)
        # Prune: keep only the _ARCHIVE_KEEP most recent archives for this agent
        existing_archives = sorted(
            _ARCHIVE_DIR.glob(f"{name}-*.md"),
            key=lambda p: p.stat().st_mtime,
        )
        for old in existing_archives[:-_ARCHIVE_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError as exc:
        # Archive failure must never block the update
        print(f"    ⚠ Could not archive previous version of {name}: {exc}")


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def cmd_list() -> int:
    """List all registered agents with status summary."""
    reg = _load_registry()
    if reg is None:
        return 1

    try:
        agents = list(reg._agents.values()) if hasattr(reg, "_agents") else []
    except Exception:
        agents = []

    if not agents:
        print("No agents registered. Use: python scripts/agent_manager.py register")
        return 0

    print(f"\n{'Name':<35} {'Status':<12} {'Trust':<12} {'Calls':>7} {'Quality':>8}")
    print("─" * 80)
    for agent in agents:
        name = getattr(agent, "name", "?")
        status = "active" if getattr(agent, "enabled", False) else "disabled"
        health = getattr(agent, "health", None)
        state = getattr(health, "status", "unknown") if health else "unknown"
        if state != "active":
            status = state
        trust = getattr(agent, "trust_tier", "external")
        total = getattr(health, "total_invocations", 0) if health else 0
        quality = getattr(health, "mean_quality_score", 0.0) if health else 0.0
        q_str = f"{quality:.2f}" if total > 0 else "  n/a"
        icon = "✓" if status == "active" else ("⚠" if status in ("degraded", "suspended") else "✗")
        print(f"  {icon} {name:<33} {status:<12} {trust:<12} {total:>7} {q_str:>8}")

    print(f"\n{len(agents)} agent(s) registered. "
          f"{sum(1 for a in agents if getattr(a, 'enabled', False))} active.")
    return 0


def _check_deleted_agents(reg, interactive: bool = True) -> int:
    """§4.9.3: Detect registered agents whose source file has been deleted.

    For each registered agent with a ``source`` path that no longer exists on
    disk, warn the user and (when *interactive*) offer to retire the agent.

    Returns the count of agents with missing source files.
    """
    if reg is None:
        return 0
    missing_count = 0
    try:
        all_agents = list(reg._agents.values()) if hasattr(reg, "_agents") else []
    except Exception:
        return 0

    for agent in all_agents:
        source = getattr(agent, "source", None)
        if not source:
            continue
        source_path = _REPO_ROOT / source
        if source_path.exists():
            continue
        # Source file missing
        missing_count += 1
        name = getattr(agent, "name", "?")
        print(f"\n⚠ Agent file removed: {source}")
        print(f"  The agent '{name}' is still registered.")
        if not interactive:
            continue
        try:
            answer = input("  Retire it? (yes/no/keep) [keep]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "keep"
        if answer in ("yes", "y"):
            reg.retire(name)
            _save_registry(reg)
            # Clear knowledge cache
            cache_file = _CACHE_DIR / f"{name}.md"
            if cache_file.exists():
                cache_file.unlink(missing_ok=True)
            print(f"  ✓ Retired: {name}")
        elif answer in ("no", "n"):
            print(f"  ℹ️  '{name}' not retired (agent removed from disk but kept in registry).")
        else:
            print(f"  ○ Kept: {name} remains registered.")
    return missing_count


def cmd_discover() -> int:
    """Scan drop folder and show unregistered .agent.md files.

    Also detects registered agents whose source file has been removed
    from the drop folder (§4.9.3 deletion handling).
    """
    reg = _load_registry()

    # §4.9.3: surface deleted agents before showing new ones
    _check_deleted_agents(reg, interactive=True)

    if not _DROP_DIR.is_dir():
        print(f"⚠ Drop folder not found: {_DROP_DIR}")
        print("  Create it: mkdir config/agents/external/")
        return 0

    agent_files = sorted(_DROP_DIR.glob("*.agent.md"))
    if not agent_files:
        print(f"No .agent.md files found in {_DROP_DIR.relative_to(_REPO_ROOT)}")
        return 0

    registered: set[str] = set()
    if reg is not None:
        try:
            registered = {a.name for a in reg._agents.values()} if hasattr(reg, "_agents") else set()
        except Exception:
            pass

    unregistered = [f for f in agent_files if f.stem not in registered]

    print(f"\nDrop folder: {_DROP_DIR.relative_to(_REPO_ROOT)}")
    print(f"Found {len(agent_files)} .agent.md file(s):\n")
    for f in agent_files:
        name = f.stem
        registered_mark = "✓ registered" if name in registered else "○ unregistered"
        print(f"  {registered_mark:<18} {f.name}")

    if unregistered:
        print(f"\n{len(unregistered)} unregistered agent(s). Run: python scripts/agent_manager.py register")
    else:
        print("\nAll agents registered ✓")
    return 0


def cmd_register(file_path: Optional[str] = None, force: bool = False) -> int:
    """Register agents from the drop folder (or a specific file).

    When *force* is True, re-register agents whose source file has changed
    (detected via content hash comparison) and emit an EXT_AGENT_UPDATE audit
    event.
    """
    reg = _load_registry()
    if reg is None:
        return 1

    if file_path:
        targets = [Path(file_path)]
        if not targets[0].exists():
            print(f"⛔ File not found: {file_path}", file=sys.stderr)
            return 1
    else:
        if not _DROP_DIR.is_dir():
            print(f"⛔ Drop folder not found: {_DROP_DIR}")
            print("  Create it first: mkdir config/agents/external/")
            return 1
        targets = sorted(_DROP_DIR.glob("*.agent.md"))
        if not targets:
            print(f"No .agent.md files found in {_DROP_DIR.relative_to(_REPO_ROOT)}")
            return 0

    # Load existing agent names
    existing: set[str] = set()
    try:
        existing = {a.name for a in reg._agents.values()} if hasattr(reg, "_agents") else set()
    except Exception:
        pass

    registered_count = 0
    updated_count = 0
    skipped_count = 0
    errors: list[str] = []

    for agent_file in targets:
        try:
            frontmatter = _parse_agent_md(agent_file)
            name = frontmatter.get("name", agent_file.stem)

            if name in existing:
                # EA-3c: compare content hash to detect changes.
                # Use full sha256: prefix format. Also handle old 16-char stored hashes
                # (written by earlier versions of this tool) by normalising both sides.
                current_full = "sha256:" + hashlib.sha256(agent_file.read_bytes()).hexdigest()
                current_short = hashlib.sha256(agent_file.read_bytes()).hexdigest()[:16]
                stored_hash: Optional[str] = None
                try:
                    if hasattr(reg, "_agents") and name in reg._agents:
                        stored_hash = getattr(reg._agents[name], "content_hash", None)
                except Exception:
                    pass

                # Normalise for comparison: strip sha256: prefix for legacy compat
                def _norm(h: Optional[str]) -> str:
                    if not h:
                        return ""
                    return h[len("sha256:"):] if h.startswith("sha256:") else h

                hash_changed = (stored_hash is not None) and (
                    _norm(current_full) != _norm(stored_hash)
                )
                current_hash = current_full  # use full format going forward

                if not hash_changed:
                    print(f"  ○ Already registered: {name} — no changes detected")
                    skipped_count += 1
                    continue

                # Hash changed — report the diff
                print(
                    f"  △ Changed: {name} "
                    f"(hash: {stored_hash} → {current_hash})"
                )
                if not force:
                    print(
                        f"    ↳ Re-run with --force to apply, "
                        "or use: agent_manager.py update"
                    )
                    skipped_count += 1
                    continue

                # --force: §4.9.1 archive previous version before applying update
                _archive_agent_version(name, agent_file, stored_hash)

                # Apply the update
                entry = _make_agent_entry(frontmatter, agent_file)
                from lib.agent_registry import _parse_agent  # noqa: PLC0415
                agent_obj = _parse_agent(name, entry)
                # Preserve existing health metrics
                if hasattr(reg, "_agents") and name in reg._agents:
                    agent_obj.health = reg._agents[name].health
                reg._agents[name] = agent_obj
                updated_count += 1
                print(f"    ✓ Updated: {name}")

                # EA-11a: audit update event
                try:
                    from lib.ext_agent_audit import write_ext_agent_event  # noqa: PLC0415
                    write_ext_agent_event(
                        "EXT_AGENT_UPDATE",
                        name,
                        f"hash: {stored_hash}→{current_hash}",
                    )
                except Exception:
                    pass
                continue

            entry = _make_agent_entry(frontmatter, agent_file)

            # Build ExternalAgent from parsed entry dict and register
            from lib.agent_registry import _parse_agent  # noqa: PLC0415
            agent_obj = _parse_agent(name, entry)
            reg.register(agent_obj)

            existing.add(name)
            registered_count += 1
            print(f"  ✓ Registered: {name} (trust: {entry['trust_tier']})")

        except Exception as exc:
            errors.append(f"{agent_file.name}: {exc}")
            print(f"  ⛔ Error: {agent_file.name}: {exc}", file=sys.stderr)

    if registered_count > 0 or updated_count > 0:
        if not _save_registry(reg):
            return 1

    summary_parts = [f"Registered {registered_count}"]
    if updated_count:
        summary_parts.append(f"updated {updated_count}")
    summary_parts.append(f"skipped {skipped_count}")
    print(f"\n{', '.join(summary_parts)}", end="")
    if errors:
        print(f", {len(errors)} error(s)")
    else:
        print(" ✓")
    return 0 if not errors else 1


def cmd_update(file_path: Optional[str] = None) -> int:
    """Apply pending updates to already-registered agents (EA-3c).

    Equivalent to ``register --force`` but only processes agents that already
    exist in the registry and have a different content hash.
    Health data (counters, quality scores) is preserved across the update.
    """
    return cmd_register(file_path=file_path, force=True)


def cmd_retire(name: str) -> int:
    """Retire an agent (status=retired, enabled=false)."""
    reg = _load_registry()
    if reg is None:
        return 1

    if hasattr(reg, "retire"):
        try:
            reg.retire(name)
        except KeyError:
            print(f"⛔ Agent not found: {name}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"⛔ Could not retire {name}: {exc}", file=sys.stderr)
            return 1
    elif hasattr(reg, "_agents"):
        if name not in reg._agents:
            print(f"⛔ Agent not found: {name}", file=sys.stderr)
            return 1
        agent = reg._agents[name]
        agent.enabled = False
        agent.status = "retired"
    else:
        print("⛔ Registry does not support retire operation", file=sys.stderr)
        return 1

    if not _save_registry(reg):
        return 1

    # Invalidate knowledge cache
    cache_file = _CACHE_DIR / f"{name}.md"
    if cache_file.exists():
        cache_file.unlink(missing_ok=True)
        print(f"  ✓ Knowledge cache cleared for {name}")

    print(f"✓ Retired agent: {name}")
    return 0


def cmd_reinstate(name: str) -> int:
    """Restore a suspended or degraded agent to active status."""
    reg = _load_registry()
    if reg is None:
        return 1

    if hasattr(reg, "reinstate"):
        try:
            reg.reinstate(name)
        except KeyError:
            print(f"⛔ Agent not found: {name}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"⛔ Could not reinstate {name}: {exc}", file=sys.stderr)
            return 1
    elif hasattr(reg, "_agents"):
        if name not in reg._agents:
            print(f"⛔ Agent not found: {name}", file=sys.stderr)
            return 1
        agent = reg._agents[name]
        agent.enabled = True
        agent.status = "active"
        if hasattr(agent, "health") and agent.health:
            agent.health.status = "active"
            agent.health.consecutive_failures = 0
    else:
        print("⛔ Registry does not support reinstate operation", file=sys.stderr)
        return 1

    if not _save_registry(reg):
        return 1
    print(f"✓ Reinstated agent: {name} → active")
    return 0


def cmd_promote(name: str) -> int:
    """Manually promote an agent's trust tier one level."""
    _TRUST_ORDER = ["untrusted", "external", "verified", "trusted", "owned"]
    reg = _load_registry()
    if reg is None:
        return 1

    if not hasattr(reg, "_agents") or name not in reg._agents:
        print(f"⛔ Agent not found: {name}", file=sys.stderr)
        return 1

    agent = reg._agents[name]
    current = getattr(agent, "trust_tier", "external")
    idx = _TRUST_ORDER.index(current) if current in _TRUST_ORDER else 1
    if idx >= len(_TRUST_ORDER) - 1:
        print(f"ℹ️  {name} is already at the highest trust tier: {current}")
        return 0
    new_tier = _TRUST_ORDER[idx + 1]
    agent.trust_tier = new_tier
    if not _save_registry(reg):
        return 1
    print(f"✓ Promoted {name}: {current} → {new_tier}")
    return 0


def cmd_demote(name: str) -> int:
    """Manually demote an agent's trust tier one level."""
    _TRUST_ORDER = ["untrusted", "external", "verified", "trusted", "owned"]
    reg = _load_registry()
    if reg is None:
        return 1

    if not hasattr(reg, "_agents") or name not in reg._agents:
        print(f"⛔ Agent not found: {name}", file=sys.stderr)
        return 1

    agent = reg._agents[name]
    current = getattr(agent, "trust_tier", "external")
    idx = _TRUST_ORDER.index(current) if current in _TRUST_ORDER else 1
    if idx <= 0:
        print(f"ℹ️  {name} is already at the lowest trust tier: {current}")
        return 0
    new_tier = _TRUST_ORDER[idx - 1]
    agent.trust_tier = new_tier
    if not _save_registry(reg):
        return 1
    print(f"✓ Demoted {name}: {current} → {new_tier}")
    return 0


def cmd_refresh_cache(name: str) -> int:
    """Invalidate the knowledge cache for an agent."""
    try:
        from lib.knowledge_extractor import KnowledgeExtractor  # noqa: PLC0415
        extractor = KnowledgeExtractor(
            cache_dir=str(_CACHE_DIR),
            agent_name=name,
        )
        extractor.invalidate()
        print(f"✓ Knowledge cache cleared for: {name}")
        return 0
    except Exception:
        # Fallback: delete file directly
        cache_file = _CACHE_DIR / f"{name}.md"
        if cache_file.exists():
            cache_file.unlink(missing_ok=True)
            print(f"✓ Knowledge cache cleared for: {name}")
        else:
            print(f"ℹ️  No cache file found for: {name}")
        return 0


def cmd_info(name: str) -> int:
    """Show detailed info for a single agent (spec Appendix C: --info)."""
    reg = _load_registry()
    if reg is None:
        return 1

    agent = reg.get(name)
    if agent is None:
        print(f"⛔ Agent not found: {name}", file=sys.stderr)
        return 1

    label = getattr(agent, "label", name)
    desc = getattr(agent, "description", "")
    trust = getattr(agent, "trust_tier", "external")
    status = getattr(agent, "status", "unknown")
    enabled = getattr(agent, "enabled", False)
    source = getattr(agent, "source", "")
    auto_d = getattr(agent, "auto_dispatch", False)
    shadow = getattr(agent, "shadow_mode", False)

    print(f"\n{'Agent Info':^60}")
    print("═" * 60)
    print(f"  Name:         {name}")
    print(f"  Label:        {label}")
    print(f"  Status:       {status} {'(enabled)' if enabled else '(disabled)'}")
    print(f"  Trust tier:   {trust}")
    print(f"  Auto-dispatch:{auto_d}")
    print(f"  Shadow mode:  {shadow}")
    print(f"  Source:       {source}")
    if desc:
        print(f"  Description:  {desc.strip()[:200]}")

    # Routing
    routing = getattr(agent, "routing", None)
    if routing:
        kw = getattr(routing, "keywords", []) or []
        domains = getattr(routing, "domains", []) or []
        min_conf = getattr(routing, "min_confidence", 0.0)
        min_hits = getattr(routing, "min_keyword_hits", 1)
        exclude = getattr(routing, "exclude_keywords", []) or []
        print(f"\n  Routing:")
        print(f"    Keywords ({len(kw)}): {', '.join(kw[:8])}{'…' if len(kw) > 8 else ''}")
        print(f"    Domains:        {', '.join(domains)}")
        print(f"    Min confidence: {min_conf}")
        print(f"    Min keyword hits: {min_hits}")
        if exclude:
            print(f"    Exclude:        {', '.join(exclude)}")

    # Invocation
    inv = getattr(agent, "invocation", None)
    if inv:
        print(f"\n  Invocation:")
        print(f"    Timeout:        {getattr(inv, 'timeout_seconds', 60)}s")
        print(f"    Max budget:     {getattr(inv, 'max_budget', 10)}")
        print(f"    Max response:   {getattr(inv, 'max_response_chars', 4000)} chars")

    # PII profile
    pii = getattr(agent, "pii_profile", None)
    if pii:
        allow = getattr(pii, "allow", []) or []
        block = getattr(pii, "block", []) or []
        print(f"\n  PII profile:")
        print(f"    Allow: {', '.join(allow) if allow else '(none)'}")
        if block:
            print(f"    Block: {', '.join(block)}")

    # Fallback
    cascade = getattr(agent, "fallback_cascade", []) or []
    if cascade:
        fb_types = []
        for fb in cascade:
            fb_types.append(fb.get("type", "?") if isinstance(fb, dict) else getattr(fb, "type", "?"))
        print(f"\n  Fallback cascade: {' → '.join(fb_types)}")

    # Health summary
    health = getattr(agent, "health", None)
    if health:
        total = getattr(health, "total_invocations", 0)
        quality = getattr(health, "mean_quality_score", 0.0)
        h_status = getattr(health, "status", "unknown")
        print(f"\n  Health:       {h_status} | {total} invocations | quality {quality:.2f}")

    # Cache
    cache_file = _CACHE_DIR / f"{name}.md"
    if cache_file.exists():
        size = cache_file.stat().st_size
        print(f"  Cache:        {size:,} bytes")
    else:
        print(f"  Cache:        none")

    print()
    return 0


def cmd_health(name: Optional[str] = None) -> int:
    """Show health metrics for all agents or a specific agent.

    Also surfaces missing source files (§4.9.3 deletion handling) when
    reporting on all agents.
    """
    reg = _load_registry()
    if reg is None:
        return 1

    # §4.9.3: warn about deleted agent files (non-interactive in health report)
    if name is None:
        _check_deleted_agents(reg, interactive=False)

    try:
        agents = list(reg._agents.values()) if hasattr(reg, "_agents") else []
    except Exception:
        agents = []

    if name:
        agents = [a for a in agents if getattr(a, "name", "") == name]
        if not agents:
            print(f"⛔ Agent not found: {name}", file=sys.stderr)
            return 1

    if not agents:
        print("No agents registered.")
        return 0

    print(f"\n{'Agent Health Report':^60}")
    print("─" * 60)
    for agent in agents:
        aname = getattr(agent, "name", "?")
        health = getattr(agent, "health", None)
        if health is None:
            print(f"\n{aname}: no health data")
            continue

        state = getattr(health, "status", "unknown")
        total = getattr(health, "total_invocations", 0)
        success = getattr(health, "successful_invocations", 0)
        failed = getattr(health, "failed_invocations", 0)
        quality = getattr(health, "mean_quality_score", 0.0)
        consec = getattr(health, "consecutive_failures", 0)
        last_inv = getattr(health, "last_invocation", None)
        last_fail = getattr(health, "last_failure_reason", None)
        trust = getattr(agent, "trust_tier", "external")

        state_icon = {"active": "✓", "degraded": "⚠", "suspended": "⛔", "retired": "✗"}.get(state, "?")
        print(f"\n  {state_icon} {aname} [{trust}]")
        print(f"    State:       {state}")
        print(f"    Calls:       {total} total ({success} ok, {failed} failed)")
        if total > 0:
            print(f"    Success rate:{success/total*100:.1f}%")
        print(f"    Mean quality:{quality:.2f}")
        if consec > 0:
            print(f"    Consec fails:{consec}")
        if last_inv:
            print(f"    Last invoke: {last_inv}")
        if last_fail:
            print(f"    Last failure:{last_fail}")

        # Cache status
        cache_file = _CACHE_DIR / f"{aname}.md"
        if cache_file.exists():
            size = cache_file.stat().st_size
            print(f"    Cache:       {size:,} bytes ({cache_file.relative_to(_REPO_ROOT)})")
        else:
            print(f"    Cache:       none")

    print()
    return 0


def cmd_validate() -> int:
    """Validate registry YAML integrity."""
    reg = _load_registry()
    if reg is None:
        return 1

    if hasattr(reg, "validate"):
        try:
            errors = reg.validate()
            if errors:
                print(f"⛔ Registry validation failed ({len(errors)} error(s)):")
                for e in errors:
                    print(f"  • {e}")
                return 1
            print("✓ Registry valid")
            return 0
        except Exception as exc:
            print(f"⛔ Validation error: {exc}", file=sys.stderr)
            return 1

    # Basic fallback validation
    try:
        agents = list(reg._agents.values()) if hasattr(reg, "_agents") else []
        issues: list[str] = []
        for agent in agents:
            name = getattr(agent, "name", "")
            if not name:
                issues.append("Agent with empty name")
            if not getattr(agent, "routing", None):
                issues.append(f"{name}: missing routing config")
        if issues:
            print(f"⛔ {len(issues)} validation issue(s):")
            for i in issues:
                print(f"  • {i}")
            return 1
        print(f"✓ Registry valid ({len(agents)} agent(s))")
        return 0
    except Exception as exc:
        print(f"⛔ Validation error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# cmd_delegate — AR-9 Post-invocation pipeline
# ---------------------------------------------------------------------------

def cmd_delegate(
    agent_name: str,
    query: str,
    response_file: str | None = None,
) -> int:
    """Run the post-invocation pipeline: verify → score → integrate → cache → health.

    Reads the agent response from *response_file* (or stdin if None/"-").
    Outputs the final unified prose to stdout.

    Returns 0 on success, 1 on failure.
    """
    reg = _load_registry()
    if reg is None:
        return 1

    agent = reg.get(agent_name)
    if agent is None:
        print(f"⛔ Agent not found: {agent_name}", file=sys.stderr)
        return 1

    # Read agent response
    if response_file and response_file != "-":
        try:
            response_text = Path(response_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"⛔ Cannot read response file: {exc}", file=sys.stderr)
            return 1
    else:
        import io
        response_text = sys.stdin.read()

    if not response_text.strip():
        print("⛔ Empty response — nothing to verify.", file=sys.stderr)
        return 1

    try:
        from lib.response_verifier import ResponseVerifier  # noqa: PLC0415
        from lib.agent_scorer import score_agent_response   # noqa: PLC0415
        from lib.response_integrator import ResponseIntegrator  # noqa: PLC0415
        from lib.knowledge_extractor import KnowledgeExtractor  # noqa: PLC0415
        from lib.agent_health import AgentHealthTracker  # noqa: PLC0415
    except ImportError as exc:
        print(f"⛔ Missing pipeline module: {exc}", file=sys.stderr)
        return 1

    _knowledge_dir = str(_REPO_ROOT / "knowledge")
    _cache_dir = str(_CACHE_DIR)

    # Step 1: Verify (injection scan + KB cross-check)
    verifier = ResponseVerifier(knowledge_dir=_knowledge_dir)
    injection_clean, kb_check = verifier.verify(response_text, query)

    if not injection_clean:
        print("⛔ Injection detected in agent response — discarding.", file=sys.stderr)
        # Record the injection event
        try:
            health_tracker = AgentHealthTracker(registry=reg)
            health_tracker.record_invocation(
                agent_name=agent_name,
                success=False,
                latency_ms=0,
                quality_score=0.0,
                injection_detected=True,
            )
        except Exception:
            pass
        return 1

    # Step 2: Score
    quality_score = score_agent_response(response_text, query, kb_check=kb_check)

    # Step 3: Integrate
    from lib.agent_invoker import AgentResult  # noqa: PLC0415
    from datetime import datetime, timezone as tz  # noqa: PLC0415
    agent_result = AgentResult(
        agent_name=agent_name,
        response=response_text,
        invoked_at=datetime.now(tz.utc),
        latency_ms=0,
    )
    integrator = ResponseIntegrator()
    integration = integrator.integrate(
        agent=agent,
        agent_result=agent_result,
        kb_check=kb_check,
    )

    # Step 4: Cache high-quality responses
    try:
        extractor = KnowledgeExtractor(
            cache_dir=_cache_dir,
            agent_name=agent_name,
        )
        extractor.extract_and_cache(
            response=response_text,
            query=query,
            quality_score=quality_score,
        )
    except Exception as exc:
        # Non-blocking — caching failure doesn't break the pipeline
        print(f"⚠️ Cache write skipped: {exc}", file=sys.stderr)

    # Step 5: Record health metrics
    try:
        health_tracker = AgentHealthTracker(registry=reg)
        health_tracker.record_invocation(
            agent_name=agent_name,
            success=True,
            latency_ms=0,
            quality_score=quality_score,
        )
    except Exception as exc:
        print(f"⚠️ Health recording skipped: {exc}", file=sys.stderr)

    # Output: unified prose + quality metadata
    print(integration.unified_prose)
    print(f"\n> Quality: {quality_score:.2f} | "
          f"Confidence: {integration.confidence_label} | "
          f"KB corroborations: {len(integration.kb_corroborations)}")
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="AR-9 External Agent Manager — register, retire, inspect agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/agent_manager.py list\n"
            "  python scripts/agent_manager.py register\n"
            "  python scripts/agent_manager.py retire storage-deployment-expert\n"
            "  python scripts/agent_manager.py health\n"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    # list
    subparsers.add_parser("list", help="List all registered agents")

    # discover
    subparsers.add_parser("discover", help="Scan drop folder for unregistered agents")

    # register
    p_register = subparsers.add_parser("register", help="Register agents from drop folder")
    p_register.add_argument("--file", metavar="PATH", default=None,
                            help="Register from a specific .agent.md file")
    p_register.add_argument("--force", action="store_true", default=False,
                            help="Apply definition updates for already-registered agents (EA-3c)")

    # update (EA-3c): apply definition changes for registered agents
    p_update = subparsers.add_parser(
        "update",
        help="Apply definition changes for already-registered agents (alias: register --force)",
    )
    p_update.add_argument("--file", metavar="PATH", default=None,
                          help="Update from a specific .agent.md file")

    # retire
    p_retire = subparsers.add_parser("retire", help="Retire an agent")
    p_retire.add_argument("name", help="Agent name to retire")

    # reinstate
    p_reinstate = subparsers.add_parser("reinstate", help="Reinstate a suspended/degraded agent")
    p_reinstate.add_argument("name", help="Agent name to reinstate")

    # promote
    p_promote = subparsers.add_parser("promote", help="Promote trust tier one level")
    p_promote.add_argument("name", help="Agent name")

    # demote
    p_demote = subparsers.add_parser("demote", help="Demote trust tier one level")
    p_demote.add_argument("name", help="Agent name")

    # refresh-cache
    p_cache = subparsers.add_parser("refresh-cache", help="Invalidate knowledge cache")
    p_cache.add_argument("name", help="Agent name")

    # health
    p_health = subparsers.add_parser("health", help="Show health metrics")
    p_health.add_argument("name", nargs="?", default=None, help="Agent name (optional)")

    # info
    p_info = subparsers.add_parser("info", help="Show detailed info for one agent")
    p_info.add_argument("name", help="Agent name")

    # validate
    subparsers.add_parser("validate", help="Validate registry YAML integrity")

    # delegate (post-invocation pipeline)
    p_delegate = subparsers.add_parser(
        "delegate",
        help="Run post-invocation pipeline (verify + score + integrate + cache + health)",
    )
    p_delegate.add_argument("--agent", required=True, help="Agent name")
    p_delegate.add_argument("--query", required=True, help="Original user query")
    p_delegate.add_argument(
        "--response-file", default=None,
        help="Path to file with agent response (reads stdin if omitted)",
    )

    args = parser.parse_args(argv)

    if args.command == "list":
        return cmd_list()
    elif args.command == "discover":
        return cmd_discover()
    elif args.command == "register":
        return cmd_register(file_path=getattr(args, "file", None),
                            force=getattr(args, "force", False))
    elif args.command == "update":
        return cmd_update(file_path=getattr(args, "file", None))
    elif args.command == "retire":
        return cmd_retire(args.name)
    elif args.command == "reinstate":
        return cmd_reinstate(args.name)
    elif args.command == "promote":
        return cmd_promote(args.name)
    elif args.command == "demote":
        return cmd_demote(args.name)
    elif args.command == "refresh-cache":
        return cmd_refresh_cache(args.name)
    elif args.command == "health":
        return cmd_health(name=getattr(args, "name", None))
    elif args.command == "info":
        return cmd_info(args.name)
    elif args.command == "validate":
        return cmd_validate()
    elif args.command == "delegate":
        return cmd_delegate(
            agent_name=args.agent,
            query=args.query,
            response_file=getattr(args, "response_file", None),
        )
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
