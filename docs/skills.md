# Artha Skill Development Guide

Skills are pluggable modules that extend Artha's domain knowledge with
real-time data (weather, stock prices, transit alerts, etc.).

## Skill Architecture

```
scripts/skills/
├── __init__.py          # get_skill() factory, list_skills()
├── base_skill.py        # BaseSkill abstract base class
├── noaa_weather.py      # Example: NOAA weather skill
└── <your_skill>.py      # Your new skill
```

Each skill is a Python class that inherits from `BaseSkill` and exposes a
standardized fetch interface so the catch-up pipeline can call it uniformly.

---

## BaseSkill Contract

```python
from scripts.skills.base_skill import BaseSkill

class MySkill(BaseSkill):
    name = "my_skill"          # unique snake_case identifier
    domain = "home"            # which Artha domain this enriches
    cadence = "daily"          # "realtime" | "daily" | "weekly"
    trigger_keywords: list[str]  # email/event keywords that trigger fetch

    def fetch(self) -> dict:
        """
        Fetch live data and return a structured dict.
        Must be idempotent and cacheable.
        Raises: SkillUnavailableError if the data source is unreachable.
        """
        ...

    def compare_fields(self, previous: dict, current: dict) -> list[str]:
        """
        Compare two fetch() results and return a list of human-readable
        change descriptions. Return [] if nothing changed.
        """
        ...
```

### Cadence Values

| Cadence | Refresh Behavior |
|---------|-----------------|
| `realtime` | Fetched on every catch-up, no caching |
| `daily` | Cached for 24 hours; re-fetched if stale |
| `weekly` | Cached for 7 days |

---

## Creating a New Skill

### 1. Create the skill file

```python
# scripts/skills/my_skill.py
from __future__ import annotations
from pathlib import Path
from .base_skill import BaseSkill, SkillUnavailableError


class MySkill(BaseSkill):
    name = "my_skill"
    domain = "home"
    cadence = "daily"
    trigger_keywords = ["utility", "power outage", "billing"]

    def __init__(self, config: dict) -> None:
        self.api_key = config.get("api_key", "")
        self._cache: dict | None = None

    def fetch(self) -> dict:
        try:
            # ... call your API ...
            return {"status": "ok", "data": {...}}
        except Exception as exc:
            raise SkillUnavailableError(f"my_skill fetch failed: {exc}") from exc

    def compare_fields(self, previous: dict, current: dict) -> list[str]:
        changes = []
        if previous.get("data") != current.get("data"):
            changes.append("Data changed since last fetch")
        return changes


def get_skill(artha_dir: Path) -> MySkill:
    """Factory function — required by the skill loader."""
    import sys
    sys.path.insert(0, str(artha_dir / "scripts"))
    from profile_loader import get as _pget
    return MySkill(config={"api_key": _pget("integrations.my_skill.api_key", "")})
```

### 2. Register in skills.yaml

```yaml
# config/skills.yaml
skills:
  my_skill:
    enabled: true
    module: scripts.skills.my_skill
    domain: home
    cadence: daily
```

### 3. Test your skill

```bash
python -c "
from pathlib import Path
from scripts.skills.my_skill import get_skill
skill = get_skill(Path('.'))
print(skill.fetch())
"
```

---

## get_skill() Factory

The `scripts/skills/__init__.py` module provides a `get_skill()` loader:

```python
from scripts.skills import get_skill

skill = get_skill("noaa_weather", artha_dir=Path("."))
data = skill.fetch()
```

`get_skill()` reads `config/skills.yaml`, imports the module by dotted path,
and calls the module-level `get_skill(artha_dir)` factory.

---

## Error Handling

- Skills should raise `SkillUnavailableError` (from `base_skill`) for
  recoverable fetch failures (network timeout, API rate limit, etc.).
- The catch-up pipeline catches `SkillUnavailableError` and logs a warning
  rather than aborting the entire briefing.
- Do NOT raise bare exceptions for expected failure modes.

---

## PII Policy

- Skills must never log or print raw PII to stdout/stderr.
- API responses containing PII must be passed through `pii_guard.py` before
  being embedded in state files.
- Skills must read user config from `profile_loader` — never hardcode
  names, coordinates, email addresses, or account IDs.

---

## Built-In Skill Registry

| Skill | Module | Domain | Cadence | Data Source | Description |
|-------|--------|--------|---------|-------------|-------------|
| `uscis_status` | `scripts/skills/uscis_status.py` | immigration | daily | Public lookup | USCIS case status polling |
| `property_tax` | `scripts/skills/property_tax.py` | finance | yearly | `state/home.md` | Property tax deadline alerts |
| `king_county_tax` | `scripts/skills/king_county_tax.py` | finance | yearly | Public lookup | King County WA tax records |
| `visa_bulletin` | `scripts/skills/visa_bulletin.py` | immigration | monthly | Public lookup | Visa bulletin priority date tracking |
| `noaa_weather` | `scripts/skills/noaa_weather.py` | home | daily | NOAA API | Weather and severe event alerts |
| `nhtsa_recalls` | `scripts/skills/nhtsa_recalls.py` | vehicle | weekly | NHTSA API | Vehicle safety recall tracking |
| `passport_expiry` | `scripts/skills/passport_expiry.py` | immigration | weekly | `state/immigration.md` | Passport expiry at 180/90/60-day thresholds |
| `subscription_monitor` | `scripts/skills/subscription_monitor.py` | digital | weekly | `state/digital.md` | Price change + trial-to-paid detection |
| `financial_resilience` | `scripts/skills/financial_resilience.py` | finance | weekly | `state/finance.md` (vault) | Burn rate, emergency fund runway, single-income stress scenario |
