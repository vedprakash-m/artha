---
schema_version: "1.0"
last_updated: "YYYY-MM-DDTHH:MM:SS"
---
# Artha Settings (Example)

> **NOTE:** This file is an example for reference only.
> 
> Personal configuration has moved to `config/user_profile.yaml` (Phase 0+).
> Run `python scripts/migrate.py` to generate `user_profile.yaml` from an
> existing `settings.md`.
>
> New users should copy `config/user_profile.example.yaml` to
> `config/user_profile.yaml` and fill it in directly.

---

## Identity
```yaml
family_name: "YourFamily"
primary_user: "FirstName (Nickname)"
family_members:
  - name: "FirstName"
    nickname: "Nickname"
    role: primary
  - name: "SpouseName"
    role: spouse
  - name: "ChildName"
    age: 16
    role: child
```

## Email Configuration
```yaml
briefing_email: "your.email@gmail.com"
gmail_accounts:
  - account: "your.email@gmail.com"
    label: primary
    from_outlook_label: from-outlook
    from_apple_label: from-apple
```

## Calendar Configuration
```yaml
calendars:
  primary: "primary"
  family: ""   # family calendar ID from Google Calendar settings
  holidays: "en.usa#holiday@group.v.calendar.google.com"
  catch_up_ids: "primary"
```

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
  todo_sync: false
  icloud_direct_api: false
  preflight_gate: true
  open_items_tracking: false
  workiq_calendar: false
```
