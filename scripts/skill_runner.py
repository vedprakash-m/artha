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
CACHE_FILE = ARTHA_DIR / "tmp" / "skills_cache.json"  # ephemeral, not synced
SKILLS_DIR = ARTHA_DIR / "scripts" / "skills"
_SCRIPTS_DIR = ARTHA_DIR / "scripts"

# Make scripts/ importable so _bootstrap (and skills) are resolvable
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

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
})

# Timeout for individual skill execution (seconds)
_SKILL_TIMEOUT = 30

# Add repo root so `import scripts.skills.X` resolves correctly
if str(ARTHA_DIR) not in sys.path:
    sys.path.append(str(ARTHA_DIR))

def load_config() -> Dict[str, Any]:
    if not SKILLS_CONFIG.exists():
        logging.warning(f"Config file {SKILLS_CONFIG} not found.")
        return {"skills": {}}
    with open(SKILLS_CONFIG, "r") as f:
        return yaml.safe_load(f) or {"skills": {}}

def load_cache() -> Dict[str, Any]:
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load cache: {e}")
        return {}

def should_run(skill_name: str, config: Dict[str, Any], cache: Dict[str, Any]) -> bool:
    """Enforce cadence control."""
    skill_cfg = config.get("skills", {}).get(skill_name, {})
    if not skill_cfg.get("enabled"):
        return False
    
    cadence = skill_cfg.get("cadence", "every_run")
    if cadence == "every_run":
        return True
    
    last_run_str = cache.get(skill_name, {}).get("last_run")
    if not last_run_str:
        return True # Cold start
    
    try:
        last_run = datetime.fromisoformat(last_run_str)
        now = datetime.now(timezone.utc)
        
        if cadence == "daily" and now - last_run < timedelta(days=1):
            logging.info(f"Skipping {skill_name} (daily cadence not yet reached)")
            return False
        if cadence == "weekly" and now - last_run < timedelta(weeks=1):
            logging.info(f"Skipping {skill_name} (weekly cadence not yet reached)")
            return False
    except Exception as e:
        logging.error(f"Cadence check failed for {skill_name}: {e}")
        return True
        
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
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    config = load_config()
    cache = load_cache()
    enabled_skills = [name for name, cfg in config.get("skills", {}).items() if should_run(name, config, cache)]
    
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
                
                new_cache[name] = {
                    "last_run": now_iso,
                    "current": res,
                    "previous": prev_skill_entry.get("current"),
                    "changed": is_changed
                }
                
            except Exception as exc:
                logging.error(f"Skill {name} generated an unhandled exception: {exc}")
                if config.get("skills", {}).get(name, {}).get("priority") == "P0":
                    exit_code = 1

    total_elapsed = round(time.monotonic() - run_start, 2)
    logging.info(f"Skills: {len(enabled_skills)} executed in {total_elapsed}s (parallel)")
    for sname, t in sorted(skill_timing.items()):
        logging.info(f"  {sname}: {t:.2f}s")

    # Write cache (encrypted by vault.py in Step 18)
    with open(CACHE_FILE, "w") as f:
        json.dump(new_cache, f, indent=2)

    # Write timing metrics to tmp/skills_metrics.json
    _write_skills_metrics(skill_timing, total_elapsed)
    
    logging.info(f"Skill execution complete. Cache updated at {CACHE_FILE}")
    sys.exit(exit_code)


def _write_skills_metrics(timing: Dict[str, float], total_elapsed: float) -> None:
    """Persist skill run metrics to tmp/skills_metrics.json."""
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
