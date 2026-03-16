# Artha Actions Guide

Artha defines write actions in `config/actions.yaml`.  The AI CLI reads this
file to know what actions are available, their friction level, and whether
human approval is required before execution.

---

## Built-in Actions

| Action | Type | Approval Required | Status |
|--------|------|-------------------|--------|
| `send_email` | email_send | ✅ Yes | Active |
| `send_whatsapp` | url_open | ✅ Yes | Active |
| `add_calendar_event` | calendar_write | ✅ Yes | Disabled (write scope pending) |
| `todo_sync` | api_call | No (automated) | Active |
| `run_pipeline` | api_call | No (read-only) | Active |
| `run_health_check` | api_call | No | Active |

---

## Security Model

All write actions are **human-gated by default**.  The AI will:

1. Propose the action with parameters clearly stated.
2. Wait for explicit approval ("yes", "do it", "confirm", "go ahead").
3. Execute the handler script with the approved parameters.
4. Log the execution to `state/audit.md`.

Actions with `pii_check: true` route through `safe_cli.py` before firing — any
PII present will be caught and the action blocked.

No action sends data to external services without explicit approval.

---

## Adding a Custom Action

### Step 1: Add a block to config/actions.yaml

```yaml
actions:
  my_action:
    type: api_call                    # email_send | calendar_write | url_open | api_call
    enabled: true
    handler: "scripts/actions/my_action.py"
    requires_approval: true           # true = human-gated; false = automated
    friction: low                     # low | medium | high
    description: >
      One sentence describing what this action does.
    params:
      key1: "{placeholder1}"
      key2: "{placeholder2 | optional}"
    pii_check: true                   # true = route through safe_cli.py first
    audit: true                       # true = log to state/audit.md
```

### Step 2: Write the handler script (optional)

If `handler` points to a script, create it at `scripts/actions/my_action.py`:

```python
#!/usr/bin/env python3
"""
scripts/actions/my_action.py — handler for the my_action action.
"""
import argparse, sys

def main(argv=None):
    p = argparse.ArgumentParser(prog="my_action.py")
    p.add_argument("--key1", required=True)
    p.add_argument("--key2", default="")
    args = p.parse_args(argv)
    # ... do the thing
    print(f"[my_action] Done: {args.key1}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

Actions with `handler: null` (like `send_whatsapp`) use OS-level operations
(`webbrowser.open()`, URL schemes) instead of a script.

### Step 3: Test

```bash
python scripts/actions/my_action.py --key1 "test value"
```

No regeneration needed.  The AI CLI reads `config/actions.yaml` directly on the
next session.

---

## Friction Levels

| Level | Meaning | Examples |
|-------|---------|---------|
| `low` | Quick action, easy to undo or retry | Send WhatsApp, add calendar event |
| `medium` | More consequential, a moment to confirm | Send formal email, delete item |
| `high` | Irreversible or high-impact | Cancel subscription, bulk delete |

The AI uses the friction level to calibrate how firmly it asks for confirmation.

---

## Audit Log Format

Every executed action (with `audit: true`) writes a line to `state/audit.md`:

```
[2026-03-13 08:45 UTC] ACTION send_email → to:someone@example.com subject:"Hello" status:sent
```

Review `state/audit.md` any time to see a full history of actions Artha has taken.
