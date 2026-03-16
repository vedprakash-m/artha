# Artha Skills — Developer Reference

Skills are pluggable data-fetch modules that extend Artha with real-time
information from external APIs. Each skill enriches one domain with live data
that can't be extracted from email alone.

---

## Available Skills

| Skill Module | Domain | Cadence | Source |
|-------------|--------|---------|--------|
| `noaa_weather.py` | `home` | daily | NOAA Weather API (no key required) |
| `uscis_status.py` | `immigration` | daily | USCIS Case Status API |
| `visa_bulletin.py` | `immigration` | monthly | USCIS Visa Bulletin PDF |
| `nhtsa_recalls.py` | `vehicle` | weekly | NHTSA Recalls API (no key required) |
| `king_county_tax.py` | `finance` | yearly | King County Assessor (WA state) |
| `property_tax.py` | `finance` | yearly | Generic property tax scraper |

---

## BaseSkill Contract

All skills inherit from `base_skill.BaseSkill`. The base class provides:

```python
class BaseSkill(ABC):
    name: str       # unique snake_case identifier (set as class attribute)
    priority: str   # P0–P4, matches domain priority

    def pull(self) -> Any:
        """Fetch raw data from source (HTTP, local file, etc.)"""
        ...

    def parse(self, raw_data: Any) -> dict[str, Any]:
        """Transform raw data into structured Artha schema."""
        ...

    def execute(self) -> dict[str, Any]:
        """Orchestrate pull → parse with full error handling.
        Returns: {"name": ..., "status": "success"|"failed",
                  "timestamp": ISO8601, "data": {...} | "error": "..."}
        """
        ...

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable summary of the skill's last result."""
        ...
```

The `execute()` method is the primary entry point. It:
1. Calls `pull()` to get raw data
2. Calls `parse()` to structure it
3. Returns a standardized result dict
4. Catches all exceptions and sets `status: "failed"` with error message

---

## Creating a New Skill

### 1. Create `scripts/skills/my_skill.py`

```python
from __future__ import annotations
import urllib.request
from pathlib import Path
from .base_skill import BaseSkill


class MySkill(BaseSkill):
    """Fetch my data from an external source."""

    def __init__(self, config: dict, priority: str = "P2") -> None:
        super().__init__(name="my_skill", priority=priority)
        self.api_url = config.get("api_url", "https://api.example.com/data")

    def pull(self) -> str:
        with urllib.request.urlopen(self.api_url, timeout=10) as resp:
            return resp.read().decode()

    def parse(self, raw_data: str) -> dict:
        # Parse raw_data into structured result
        return {"result": raw_data[:100]}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "last_run": self.last_run,
            "status": self.status,
        }


def get_skill(artha_dir: Path) -> MySkill:
    """Factory: called by the skill loader. Must be named get_skill()."""
    import sys
    sys.path.insert(0, str(artha_dir / "scripts"))
    from profile_loader import get as _pget
    return MySkill(config={"api_url": _pget("integrations.my_skill.url", "")})
```

### 2. Register in `config/skills.yaml`

```yaml
skills:
  my_skill:
    enabled: true
    module: scripts.skills.my_skill
    domain: home
    cadence: daily
    description: "Fetch data from My Source"
```

### 3. Test it

```bash
cd /path/to/artha
python -c "
from pathlib import Path
from scripts.skills.my_skill import get_skill
skill = get_skill(Path('.'))
result = skill.execute()
print(result['status'], result.get('data'))
"
```

---

## Loading Skills

The skill runner (`scripts/skill_runner.py`) discovers and executes skills
based on `config/skills.yaml`:

```bash
# Run all enabled skills and print results
python scripts/skill_runner.py

# Run a single skill by name
python scripts/skill_runner.py --skill noaa_weather
```

---

## Error Handling

- `execute()` catches all exceptions internally — skills never crash the
  catch-up pipeline.
- Network timeouts should use `urllib.request.urlopen(timeout=10)` or
  equivalent; do not use unbounded requests.
- Log errors via `logging.error()` — do not print to stdout, which is
  reserved for structured output.

---

## PII Policy for Skills

- Never hardcode names, email addresses, coordinates, or account IDs.
- Read all personal config from `profile_loader` (dot-notation keys from
  `config/user_profile.yaml`).
- Raw API responses must be scanned through `pii_guard.py` before being
  written to state files.
- Skills must not log PII to `logging.*` calls.

---

## Dependency Policy

- Prefer stdlib over third-party packages.
- If a third-party package is required, add it to `scripts/requirements.txt`
  with an exact pinned version.
- Document the reason for the dependency in a comment near the import.
