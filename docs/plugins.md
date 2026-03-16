# Artha Plugin System

Artha supports user-contributed connectors and skills via a plugin directory.
Plugins are loaded at runtime alongside the built-in modules.

## Plugin Directory

```
~/.artha-plugins/
├── connectors/          # Custom email/calendar/data connectors
│   └── my_connector.py
├── skills/              # Custom background data skills
│   └── my_skill.py
└── hooks/               # Post-catch-up hooks (future)
    └── post-catchup/
        └── my_hook.sh
```

Create this directory structure if it doesn't exist:
```bash
mkdir -p ~/.artha-plugins/{connectors,skills,hooks/post-catchup}
```

---

## Custom Connectors

A connector plugin fetches data from an external source and returns JSONL
records. Place your connector at `~/.artha-plugins/connectors/<name>.py`.

### Required Interface

```python
"""
~/.artha-plugins/connectors/my_connector.py
"""

def fetch(auth_context: dict, since: str, max_results: int = 50) -> list[dict]:
    """Fetch records from the data source.

    Args:
        auth_context: Credentials dict from lib/auth.py
        since: ISO-8601 timestamp — fetch records newer than this
        max_results: Maximum records to return

    Returns:
        List of dicts, each with at minimum:
          - id: str (unique record identifier)
          - subject: str
          - date_iso: str (ISO-8601)
          - body: str (text content)
          - source: str (connector name)
    """
    ...

def health_check(auth_context: dict) -> bool:
    """Return True if the connector can reach its data source."""
    ...
```

### Registering in connectors.yaml

```yaml
connectors:
  my_source:
    type: custom
    enabled: true
    fetch:
      handler: "connectors.my_connector"   # matches filename
      max_results: 50
    auth:
      provider: keyring
      service: "artha-my-connector"
```

The pipeline will load `~/.artha-plugins/connectors/my_connector.py` if
`connectors.my_connector` is not in the built-in allowlist.

---

## Custom Skills

A skill plugin runs a background data-fetch task (e.g., scraping a government
website, checking an API). Place it at `~/.artha-plugins/skills/<name>.py`.

### Required Interface

```python
"""
~/.artha-plugins/skills/my_skill.py
"""
from pathlib import Path

class MySkill:
    def __init__(self, artha_dir: Path):
        self.artha_dir = artha_dir

    def execute(self) -> dict:
        """Run the skill and return results.

        Returns:
            dict with:
              - status: "success" | "failed"
              - data: dict (skill-specific payload)
              - compare_fields: list[str] (fields for delta detection)
              - error: str (only if status == "failed")
        """
        ...

def get_skill(artha_dir: Path):
    """Factory function — required entry point."""
    return MySkill(artha_dir)
```

### Registering in skills.yaml

```yaml
skills:
  my_skill:
    enabled: true
    cadence: daily       # every_run | daily | weekly
    priority: P1         # P0 = critical (failure logged as error)
    compare_fields:
      - status
      - last_checked
```

The skill runner will load `~/.artha-plugins/skills/my_skill.py` if
`my_skill` is not in the built-in allowlist.

---

## Security

- Plugins are **not** sandboxed — they run with the same permissions as Artha
- Only install plugins you trust
- Plugin code is never synced to OneDrive (lives in `~/.artha-plugins/`)
- Built-in connectors and skills are always preferred over plugins with the
  same name (allowlist takes precedence)

## Limitations

- Plugins cannot override built-in modules (allowlist wins)
- No hot-reload — restart the catch-up session to pick up new plugins
- Plugin errors are logged but do not halt the catch-up workflow
