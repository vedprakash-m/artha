---
schema_version: "2.0"
last_updated: "YYYY-MM-DDTHH:MM:SS"
---
# Artha Settings — Capabilities & Feature Flags (Example)
# Identity & integration config → config/user_profile.yaml

> **NOTE:** This file is an example for reference only.
>
> All identity and integration configuration has moved to `config/user_profile.yaml`.
> This file contains ONLY system capabilities, feature flags, budget, and encryption config.
> Run `python scripts/migrate.py` to generate `user_profile.yaml` from a legacy `settings.md`.

---

## Budget
```yaml
monthly_api_budget_usd: 25
alert_at_percent: 80
currency: USD
timezone: America/Los_Angeles
```

## Encryption
```yaml
# After running: age-keygen — paste the PUBLIC key here:
age_recipient: ""
```

## WorkIQ Integration (v2.2)
```yaml
workiq:
  enabled: false
  version_pin: "0.x"
  query_variant: auto
  redact_keywords: []
  redact_replacement: "[REDACTED]"
  oi_trigger_keywords:
    - "Interview"
    - "Performance Review"
    - "360 Review"
```

## Capabilities
```yaml
capabilities:
  gmail_mcp: false
  calendar_mcp: false
  gemini_cli: false
  copilot_cli: false
  vault_encryption: false
  email_briefings: false
  weekly_summary: false
  action_proposals: false
  ensemble_reasoning: false
  todo_sync: false
  icloud_direct_api: false
  preflight_gate: true
  open_items_tracking: false
  workiq_calendar: false
```
