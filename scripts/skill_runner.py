#!/usr/bin/env python3
import os
import sys
import json
import time
import logging
import importlib
import importlib.util
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List

# Path setup — must precede venv bootstrap and third-party imports
ARTHA_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS_CONFIG = ARTHA_DIR / "config" / "skills.yaml"
# Canonical persistent cache location (state/ — persists across sessions, synced).
# Migration: if old tmp/ path exists and new state/ path doesn't, move on first run.
CACHE_FILE = ARTHA_DIR / "state" / "skills_cache.json"
_CACHE_FILE_LEGACY = ARTHA_DIR / "tmp" / "skills_cache.json"  # deprecated
SKILLS_DIR = ARTHA_DIR / "scripts" / "skills"
_SCRIPTS_DIR = ARTHA_DIR / "scripts"

# Make scripts/ importable so _bootstrap (and skills) are resolvable
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from lib.logger import begin_session_trace as _begin_session_trace  # type: ignore[import]
    _TRACE_AVAILABLE = True
except ImportError:
    _TRACE_AVAILABLE = False

# Ensure correct venv before third-party imports (no-op if already in venv or CI)
try:
    from _bootstrap import reexec_in_venv  # type: ignore[import]
    reexec_in_venv()
except ImportError:
    pass  # Running standalone without project structure; continue

# Third-party imports (available after venv is ensured)
import yaml

# Allowlisted skill modules — only these may be loaded dynamically
_ALLOWED_SKILLS: frozenset[str] = frozenset({
    "uscis_status", "property_tax", "king_county_tax",
    "visa_bulletin", "noaa_weather", "nhtsa_recalls",
    "passport_expiry", "subscription_monitor", "financial_resilience",
    # U-9 utilization uplift skills
    "relationship_pulse", "occasion_tracker", "bill_due_tracker",
    "credit_monitor", "school_calendar",
    # U-9.6 WhatsApp live enrichment
    "whatsapp_last_contact",
    # ARTHA-IOT Wave 2 — Home Assistant device monitor
    "home_device_monitor",
    # CONNECT Phase 2 — Mental health utilization tracker
    "mental_health_utilization",
    # PR-3 — AI Trend Radar
    "ai_trend_radar",
    # FR-25 Career Search Intelligence (Phase 1 — PDF generation)
    "career_pdf_generator",
    # FR-25 Career Search Intelligence (Phase 2 — portal scanning; deferred)
    "portal_scanner",
    # KB Quality Check — Work KB health monitoring
    "kb_quality_check",
})

# Short-name aliases (e.g. Claude passes "radar" → resolves to "ai_trend_radar")
_SKILL_ALIASES: dict[str, str] = {
    "radar": "ai_trend_radar",
}

# Timeout for individual skill execution (seconds)
_SKILL_TIMEOUT = 30

# Add repo root so `import scripts.skills.X` resolves correctly
if str(ARTHA_DIR) not in sys.path:
    sys.path.append(str(ARTHA_DIR))

# Import shared health-tracking library (non-fatal if unavailable)
try:
    from lib.skill_health import (
        is_zero_value as _is_zero_value,
        is_stable_value as _is_stable_value,
        update_health_counters as _update_health_counters,
        atomic_write_json as _atomic_write_json,
        CADENCE_REDUCTION as _CADENCE_REDUCTION,
    )
    _SKILL_HEALTH_AVAILABLE = True
except ImportError:
    _SKILL_HEALTH_AVAILABLE = False
    _CADENCE_REDUCTION: dict = {"every_run": "daily", "daily": "weekly"}

def load_config() -> Dict[str, Any]:
    if not SKILLS_CONFIG.exists():
        logging.warning(f"Config file {SKILLS_CONFIG} not found.")
        return {"skills": {}}
    with open(SKILLS_CONFIG, "r") as f:
        return yaml.safe_load(f) or {"skills": {}}

def _migrate_cache_if_needed() -> None:
    """Move skills_cache.json from tmp/ to state/ on first run (one-time migration)."""
    if not CACHE_FILE.exists() and _CACHE_FILE_LEGACY.exists():
        try:
            import shutil
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(_CACHE_FILE_LEGACY), str(CACHE_FILE))
            logging.info(f"Migrated skills_cache.json: tmp/ → state/ (persistent cache)")
        except Exception as e:
            logging.warning(f"Cache migration failed (will start fresh): {e}")


def load_cache() -> Dict[str, Any]:
    _migrate_cache_if_needed()
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            cache = json.load(f)
        # DEBT-MEM-002: enforce size cap at read time — a cache written before the cap
        # existed (or grown via conflict copy) may exceed 1MB; trim it now.
        if len(json.dumps(cache, indent=2).encode("utf-8")) > _CACHE_MAX_BYTES:
            logging.warning("SKILLS_CACHE_CAP_ENFORCED_AT_READ: cache exceeds 1MB — trimming")
            cache = _enforce_cache_size_cap(cache)
        return cache
    except Exception as e:
        logging.error(f"Failed to load cache: {e}")
        return {}


_CACHE_MAX_BYTES = 1_048_576  # 1MB — DEBT-028
_SKILLS_CACHE_TTL_DAYS = 7  # RD-45: evict entries older than 7 days


def _enforce_cache_size_cap(cache: Dict[str, Any]) -> Dict[str, Any]:
    """Evict stale (>TTL days) and oversized entries from the skills cache.

    RD-45: Per-entry TTL — evict entries whose cached_at timestamp is older
    than _SKILLS_CACHE_TTL_DAYS (default: 7). Only entries with a ``cached_at``
    field are subject to TTL; entries without it are grandfathered (legacy
    entries created before RD-45). TTL eviction runs first; then size cap
    eviction if the cache still exceeds 1MB.

    Only fires when the projected JSON size would exceed *_CACHE_MAX_BYTES*.
    Evicted entries are logged at WARNING level.
    """
    now = datetime.now(timezone.utc)
    ttl_cutoff = now - timedelta(days=_SKILLS_CACHE_TTL_DAYS)
    cache = dict(cache)  # shallow copy — do not mutate caller's dict

    # RD-45 Phase 1: TTL-based eviction (cached_at only — not last_run)
    # Entries without cached_at are grandfathered (pre-RD-45 format).
    to_evict_ttl = []
    for skill_name, entry in cache.items():
        if not isinstance(entry, dict):
            continue
        ts_str = entry.get("cached_at")  # RD-45: only use cached_at, not last_run
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts < ttl_cutoff:
                    to_evict_ttl.append(skill_name)
            except (ValueError, TypeError):
                pass  # unparseable timestamp — keep the entry

    for skill_name in to_evict_ttl:
        logging.warning(
            "SKILLS_CACHE_TTL_EVICTION: removed %s (older than %d days)",
            skill_name, _SKILLS_CACHE_TTL_DAYS,
        )
        del cache[skill_name]

    # Phase 2: Size-cap eviction (only if still over limit after TTL pass)
    encoded = json.dumps(cache, indent=2).encode("utf-8")
    if len(encoded) <= _CACHE_MAX_BYTES:
        return cache

    # Build eviction order: skills with a last_run value sorted oldest first;
    # skills without last_run are evicted last.
    def _sort_key(item: tuple) -> tuple:
        _, entry = item
        ts = entry.get("last_run") if isinstance(entry, dict) else ""
        return (ts or "", )

    eviction_order = sorted(cache.items(), key=_sort_key)

    for skill_name, _entry in eviction_order:
        if len(json.dumps(cache, indent=2).encode("utf-8")) <= _CACHE_MAX_BYTES:
            break
        logging.warning(
            "SKILLS_CACHE_EVICTION: removed %s to keep cache ≤ 1MB", skill_name
        )
        del cache[skill_name]

    return cache

def _cadence_elapsed(skill_name: str, cadence: str, cache: Dict[str, Any]) -> bool:
    """Return True if the skill is due to run under the given cadence."""
    if cadence == "every_run":
        return True

    last_run_str = cache.get(skill_name, {}).get("last_run")
    if not last_run_str:
        return True  # Cold start — always run

    try:
        last_run = datetime.fromisoformat(last_run_str)
        now = datetime.now(timezone.utc)
        if cadence == "daily" and now - last_run < timedelta(days=1):
            return False
        if cadence == "weekly" and now - last_run < timedelta(weeks=1):
            return False
    except Exception as e:
        logging.error(f"Cadence check failed for {skill_name}: {e}")
        return True

    return True


def should_run(skill_name: str, config: Dict[str, Any], cache: Dict[str, Any]) -> bool:
    """Enforce cadence control with R7 health-aware cadence reduction.

    R7 is checked AFTER the configured cadence passes — it can only reduce
    frequency, never increase it. A weekly skill stays weekly at minimum.
    P0 skills are exempt from R7 cadence reduction.
    """
    skill_cfg = config.get("skills", {}).get(skill_name, {})
    if not skill_cfg.get("enabled"):
        return False

    configured_cadence = skill_cfg.get("cadence", "every_run")

    # Existing cadence check runs first
    if not _cadence_elapsed(skill_name, configured_cadence, cache):
        logging.info(f"Skipping {skill_name} ({configured_cadence} cadence not yet reached)")
        return False

    # R7: health-aware cadence reduction (P1/P2 only, maturity >= measuring)
    # Applied AFTER configured cadence says skill is due — checks if consecutive
    # zeros warrant running less often than configured.
    priority = skill_cfg.get("priority", "P1")
    if priority != "P0":
        health = cache.get(skill_name, {}).get("health", {})
        maturity = health.get("maturity", "warming_up")
        consecutive_zero = health.get("consecutive_zero", 0)
        if consecutive_zero >= 10 and maturity != "warming_up":
            reduced_cadence = _CADENCE_REDUCTION.get(configured_cadence)
            if reduced_cadence and not _cadence_elapsed(skill_name, reduced_cadence, cache):
                # Record R7 skip in the loaded cache (shallow-copy safe for nested dicts)
                if skill_name in cache and "health" in cache[skill_name]:
                    cache[skill_name]["health"]["r7_skips"] = (
                        cache[skill_name]["health"].get("r7_skips", 0) + 1
                    )
                logging.info(
                    f"R7: skipping {skill_name} (cadence {configured_cadence}→{reduced_cadence}, "
                    f"consecutive_zeros={consecutive_zero})"
                )
                return False

    return True

def get_delta(skill_name: str, current_data: Any, prev_cache: Dict[str, Any], compare_fields: List[str]) -> bool:
    """Detect if meaningful fields have changed."""
    prev_data = prev_cache.get(skill_name, {}).get("current", {}).get("data", {})
    if not prev_data:
        return True # New data is a change
    
    # Generic comparison logic for complex data structures
    def get_val(data, field):
        if isinstance(data, dict):
            return data.get(field)
        return getattr(data, field, None)

    for field in compare_fields:
        if get_val(current_data, field) != get_val(prev_data, field):
            return True
            
    return False

def run_skill(skill_name: str, artha_dir: Path) -> Dict[str, Any]:
    """Dynamically load and execute a skill."""
    skill_name = _SKILL_ALIASES.get(skill_name, skill_name)
    if skill_name not in _ALLOWED_SKILLS:
        # Check for user-contributed plugin
        plugin_path = Path.home() / ".artha-plugins" / "skills" / f"{skill_name}.py"
        if not plugin_path.exists():
            logging.error(f"Skill '{skill_name}' is not in the allowlist: {sorted(_ALLOWED_SKILLS)}")
            return {"status": "failed", "error": f"Unknown skill: {skill_name}"}
    try:
        # Check for user plugin first
        plugin_path = Path.home() / ".artha-plugins" / "skills" / f"{skill_name}.py"
        if plugin_path.exists():
            spec = importlib.util.spec_from_file_location(f"skills.{skill_name}", plugin_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot create module spec for plugin: {plugin_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            module = importlib.import_module(f"scripts.skills.{skill_name}")
        skill_obj = module.get_skill(artha_dir)
        logging.info(f"Executing skill: {skill_name}")
        return skill_obj.execute()
    except ImportError:
        logging.error(f"Skill module {skill_name} not found in scripts/skills/")
        return {"status": "failed", "error": "Module not found"}
    except Exception as e:
        logging.error(f"Failed to load or run skill {skill_name}: {e}")
        return {"status": "failed", "error": str(e)}

def main():
    if _TRACE_AVAILABLE:  # AFW-11: tag all skill log events with a session trace ID
        _begin_session_trace()
    import argparse
    parser = argparse.ArgumentParser(description="Artha skill runner")
    parser.add_argument(
        "--skill", "-s",
        metavar="SKILL",
        help="Run only this skill (use canonical name or alias, e.g. 'radar')",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Bypass cadence check — force the skill to run even if recently executed",
    )
    cli = parser.parse_args()
    # Resolve alias if provided
    if cli.skill:
        cli.skill = _SKILL_ALIASES.get(cli.skill, cli.skill)

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    config = load_config()
    cache = load_cache()

    if cli.skill:
        # Single-skill mode: validate allowlist
        if cli.skill not in _ALLOWED_SKILLS:
            logging.error(f"Skill '{cli.skill}' is not in the allowlist.")
            return
        skill_cfg = config.get("skills", {}).get(cli.skill, {})
        if not skill_cfg.get("enabled"):
            logging.warning(f"Skill '{cli.skill}' is disabled in config/skills.yaml. Skipping.")
            return
        if cli.force or should_run(cli.skill, config, cache):
            enabled_skills = [cli.skill]
        else:
            logging.info(f"Skill '{cli.skill}' cadence not yet reached. Use --force to override.")
            return
    else:
        enabled_skills = [
            name for name, cfg in config.get("skills", {}).items()
            if should_run(name, config, cache)
        ]

    if not enabled_skills:
        logging.info("No skills due for execution. Skipping.")
        return

    # Results to persist
    new_cache = cache.copy()
    skill_timing: Dict[str, float] = {}
    
    exit_code = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    run_start = time.monotonic()
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        submit_times: Dict[str, float] = {name: time.monotonic() for name in enabled_skills}
        future_to_skill = {executor.submit(run_skill, name, ARTHA_DIR): name for name in enabled_skills}
        for future in as_completed(future_to_skill, timeout=_SKILL_TIMEOUT * len(enabled_skills)):
            name = future_to_skill[future]
            elapsed = round(time.monotonic() - submit_times[name], 3)
            skill_timing[name] = elapsed
            try:
                res = future.result()
                
                # Check for P0 failures
                priority = config.get("skills", {}).get(name, {}).get("priority", "P1")
                if res.get("status") == "failed" and priority == "P0":
                    logging.error(f"CRITICAL: P0 skill {name} failed: {res.get('error')}")
                    exit_code = 1
                
                # Update cache with delta detection
                prev_skill_entry = cache.get(name, {})
                
                # Get compare fields from the module
                try:
                    module = importlib.import_module(f"scripts.skills.{name}")
                    skill_obj = module.get_skill(ARTHA_DIR)
                    compare_fields = skill_obj.compare_fields
                    is_changed = get_delta(name, res.get("data", {}), cache, compare_fields)
                except Exception:
                    is_changed = True  # Default to changed if we can't detect
                
                # Build cache entry (carry forward previous health counters)
                base_entry: Dict[str, Any] = {
                    "last_run": now_iso,
                    "cached_at": now_iso,  # RD-45: per-entry TTL timestamp
                    "current": res,
                    "previous": prev_skill_entry.get("current"),
                    "changed": is_changed,
                }
                if "health" in prev_skill_entry:
                    base_entry["health"] = prev_skill_entry["health"]

                # Update health counters (non-blocking — never prevents cache write)
                if _SKILL_HEALTH_AVAILABLE:
                    try:
                        zero = _is_zero_value(name, res, prev_skill_entry.get("current"), config)
                        stable = _is_stable_value(res, prev_skill_entry.get("current"))
                        wall_ms = round(elapsed * 1000)
                        base_entry = _update_health_counters(base_entry, zero, stable, wall_ms)
                    except Exception as health_exc:
                        logging.debug(f"Health tracking failed for {name}: {health_exc}")

                new_cache[name] = base_entry

            except Exception as exc:
                logging.error(f"Skill {name} generated an unhandled exception: {exc}")
                if config.get("skills", {}).get(name, {}).get("priority") == "P0":
                    exit_code = 1

    total_elapsed = round(time.monotonic() - run_start, 2)
    logging.info(f"Skills: {len(enabled_skills)} executed in {total_elapsed}s (parallel)")
    for sname, t in sorted(skill_timing.items()):
        logging.info(f"  {sname}: {t:.2f}s")

    # DEBT-028: evict oldest entries so the cache never exceeds 1MB on disk.
    new_cache = _enforce_cache_size_cap(new_cache)

    # Write cache atomically (encrypted by vault.py in Step 18)
    # Uses fcntl.flock + tempfile + os.replace for concurrent write safety.
    if _SKILL_HEALTH_AVAILABLE:
        try:
            _atomic_write_json(CACHE_FILE, new_cache)
        except Exception as write_exc:
            logging.warning(f"Atomic write failed ({write_exc}); falling back to direct write")
            with open(CACHE_FILE, "w") as f:
                json.dump(new_cache, f, indent=2)
    else:
        with open(CACHE_FILE, "w") as f:
            json.dump(new_cache, f, indent=2)

    # Bridge: write tmp/occasion_tracker_output.json for pr_manager --step8
    _write_occasion_tracker_output(new_cache)

    # Bridge: write tmp/trend_scan.json if radar ran this cycle
    if "ai_trend_radar" in enabled_skills:
        write_radar_trend_scan_bridge()

    # Bridge: seed gallery.yaml with LinkedIn ideas from radar backlog (runs always —
    # backlog persists across radar cycles, so new cards can be seeded any run)
    write_radar_content_cards()

    # Write timing metrics (deprecated — timing now in unified cache health sub-dict).
    # Kept for backward compatibility with any external tooling that reads the file.
    _write_skills_metrics(skill_timing, total_elapsed)
    
    logging.info(f"Skill execution complete. Cache updated at {CACHE_FILE}")
    sys.exit(exit_code)


def _write_occasion_tracker_output(cache: Dict[str, Any]) -> None:
    """Write tmp/occasion_tracker_output.json from cached skill results.

    Bridges the occasion_tracker skill output to the format expected by
    pr_manager.run_step8() / MomentDetector.score_occasions().
    Only written when the skill returned at least one upcoming occasion.
    """
    oc_data = cache.get("occasion_tracker", {}).get("current", {}).get("data", {})
    upcoming = oc_data.get("upcoming", []) if isinstance(oc_data, dict) else []
    if not upcoming:
        return
    output_path = ARTHA_DIR / "tmp" / "occasion_tracker_output.json"
    try:
        output_path.write_text(json.dumps(upcoming, indent=2), encoding="utf-8")
        logging.info(f"Wrote {len(upcoming)} occasion(s) to {output_path.name}")
    except Exception as e:
        logging.warning(f"Could not write occasion_tracker_output.json: {e}")


def write_radar_trend_scan_bridge(
    signals_path: Path | None = None,
    output_path: Path | None = None,
) -> int:
    """Convert tmp/ai_trend_signals.json → tmp/trend_scan.json for pr_manager --step8.

    pr_manager.MomentDetector.score_from_trends() expects:
        [{"topic": "<str>", "relevance": "high|medium|low"}, ...]

    ai_trend_signals.json has:
        {"signals": [{"topic": "<str>", "relevance_score": 0.0–1.0, ...}, ...]}

    Returns the number of trends written.
    Called automatically by skill_runner after radar runs, or manually in Step 8s.
    """
    src = signals_path or (ARTHA_DIR / "tmp" / "ai_trend_signals.json")
    dst = output_path or (ARTHA_DIR / "tmp" / "trend_scan.json")
    if not src.exists():
        logging.debug("write_radar_trend_scan_bridge: no signals file at %s", src)
        return 0
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logging.warning("write_radar_trend_scan_bridge: could not read %s: %s", src, e)
        return 0

    def _score_to_label(score: float) -> str:
        # Calibrated to match surface_threshold=0.30 and try_worthy_threshold=0.55
        # "medium" starts at 0.28 so any signal that passed surface_threshold
        # maps to at least medium → magnitude=0.7 in score_from_trends.
        if score >= 0.55:
            return "high"
        if score >= 0.28:
            return "medium"
        return "low"

    signals = data.get("signals") or []
    trends = [
        {
            "topic": s.get("topic", ""),
            "relevance": _score_to_label(s.get("relevance_score", 0.0)),
            "try_worthy": s.get("try_worthy", False),
            "summary": s.get("summary", ""),
            "url": s.get("best_source_url", ""),
            "category": s.get("category", ""),
        }
        for s in signals
        if s.get("topic")
    ]
    try:
        dst.parent.mkdir(exist_ok=True)
        dst.write_text(json.dumps(trends, indent=2), encoding="utf-8")
        logging.info("Wrote %d trend(s) to %s (bridge from radar signals)", len(trends), dst.name)
    except OSError as e:
        logging.warning("write_radar_trend_scan_bridge: could not write %s: %s", dst, e)
        return 0
    return len(trends)


def write_radar_content_cards(
    backlog_path: Path | None = None,
    gallery_path: Path | None = None,
    min_relevance: float = 0.50,
    max_new_cards: int = 10,
) -> int:
    """Seed state/gallery.yaml with LinkedIn content ideas from state/radar_backlog.yaml.

    Reads active radar signals that have a linkedin_angle set and relevance_score
    above min_relevance, then inserts seed ContentCards so the `content` command
    surfaces them for review.  Idempotent — deduplicates by occasion string.

    Returns the number of new cards written.
    """
    src = backlog_path or (ARTHA_DIR / "state" / "radar_backlog.yaml")
    dst = gallery_path or (ARTHA_DIR / "state" / "gallery.yaml")

    if not src.exists():
        logging.debug("write_radar_content_cards: no backlog at %s", src)
        return 0

    try:
        raw = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
        signals = raw.get("signals", [])
    except Exception as e:
        logging.warning("write_radar_content_cards: could not read %s: %s", src, e)
        return 0

    candidates = [
        s for s in signals
        if s.get("status") == "active"
        and s.get("linkedin_angle", "").strip()
        and s.get("relevance_score", 0.0) >= min_relevance
    ]
    if not candidates:
        logging.debug("write_radar_content_cards: no qualifying signals (threshold=%.2f)", min_relevance)
        return 0

    gallery: dict = {"schema_version": "1.0", "last_updated": "", "cards": []}
    if dst.exists():
        try:
            loaded = yaml.safe_load(dst.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                gallery = loaded
        except Exception as e:
            logging.warning("write_radar_content_cards: could not read %s: %s", dst, e)

    existing_cards: list[dict] = gallery.get("cards") or []
    existing_occasions = {c.get("occasion", "").lower() for c in existing_cards}

    # Find max card sequence for current year to generate unique IDs
    today = datetime.now(timezone.utc)
    year = today.year
    year_prefix = f"CARD-{year}-"
    max_seq = 0
    for c in existing_cards:
        cid = c.get("id", "")
        if cid.startswith(year_prefix):
            try:
                max_seq = max(max_seq, int(cid[len(year_prefix):]))
            except ValueError:
                pass

    now_iso = today.isoformat()
    new_cards: list[dict] = []

    for sig in candidates[:max_new_cards]:
        topic = sig.get("topic", "").strip()
        angle = sig.get("linkedin_angle", "").strip()
        occasion = f"AI trend: {topic}"

        if occasion.lower() in existing_occasions:
            continue

        max_seq += 1
        card: dict = {
            "id":               f"CARD-{year}-{max_seq:03d}",
            "occasion":         occasion,
            "occasion_type":    "ai_trend",
            "event_date":       today.date().isoformat(),
            "created_at":       now_iso,
            "status":           "seed",
            "primary_thread":   angle,
            "alt_threads":      [],
            "convergence_score": round(sig.get("relevance_score", 0.5), 3),
            "flags":            [],
            "platform_exclude": [],
            "personalization":  {
                "source":      "radar",
                "signal_id":   sig.get("id", ""),
                "category":    sig.get("category", ""),
                "source_url":  sig.get("best_source_url", ""),
            },
            "drafts":           {},
            "visual":           {},
            "posting_window":   {},
            "archived_at":      None,
            "dismissed_reason": "",
            "reception":        {},
        }
        new_cards.append(card)
        existing_occasions.add(occasion.lower())

    if not new_cards:
        logging.debug("write_radar_content_cards: all qualifying signals already in gallery")
        return 0

    existing_cards.extend(new_cards)
    gallery["cards"] = existing_cards
    gallery["last_updated"] = now_iso

    try:
        dst.write_text(
            yaml.dump(gallery, default_flow_style=False, allow_unicode=True,
                      sort_keys=False, width=120),
            encoding="utf-8",
        )
        logging.info("RADAR_CONTENT_CARDS | added=%d seed card(s) to %s",
                     len(new_cards), dst.name)
    except OSError as e:
        logging.warning("write_radar_content_cards: could not write %s: %s", dst, e)
        return 0

    return len(new_cards)


def write_newsletter_jsonl(emails: List[Dict[str, Any]], output_path: Path | None = None) -> int:
    """Write a list of email dicts to tmp/newsletter_pipeline.jsonl for the radar skill.

    Called from catch-up Step 8s (or Step 7 social processing) after Gmail MCP fetch.
    Each email dict should have keys: from, subject, body, date, source.
    Returns the number of records written.

    CLI usage (from catch-up pipeline):
        python3 scripts/skill_runner.py --write-newsletter-jsonl < emails.json
    """
    out = output_path or (ARTHA_DIR / "tmp" / "newsletter_pipeline.jsonl")
    out.parent.mkdir(exist_ok=True)
    count = 0
    try:
        with out.open("w", encoding="utf-8") as fh:
            for email in emails:
                record = {
                    "source": email.get("source", "gmail"),
                    "from": email.get("from", ""),
                    "subject": email.get("subject", ""),
                    "body": email.get("body", email.get("snippet", "")),
                    "date": email.get("date", ""),
                    "url": email.get("url", ""),
                }
                fh.write(json.dumps(record) + "\n")
                count += 1
        logging.info(f"Wrote {count} newsletter record(s) to {out.name}")
    except Exception as e:
        logging.warning(f"Could not write newsletter_pipeline.jsonl: {e}")
    return count


def _write_skills_metrics(timing: Dict[str, float], total_elapsed: float) -> None:
    """DEPRECATED: timing data is now in state/skills_cache.json health sub-dict.
    Kept for backward compatibility. Will be removed in a future cleanup pass.
    """
    metrics_path = ARTHA_DIR / "tmp" / "skills_metrics.json"
    metrics_path.parent.mkdir(exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "skill_timing": timing,
        "wall_clock_seconds": total_elapsed,
        "skills_executed": len(timing),
    }
    try:
        existing = []
        if metrics_path.exists():
            existing = json.loads(metrics_path.read_text())
            if not isinstance(existing, list):
                existing = []
        existing.insert(0, entry)
        existing = existing[:50]
        metrics_path.write_text(json.dumps(existing, indent=2))
    except Exception:
        pass

if __name__ == "__main__":
    main()
