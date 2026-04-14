# Artha Workflow Modules

The catch-up workflow (§2 in Artha.core.md) is a 21-step pipeline. This
directory documents each composable phase for customization and extension.

## Phase Architecture

The workflow is organized into **5 phases**, each containing a group of steps
that execute together:

| Phase | Steps | Module | Purpose |
|-------|-------|--------|---------|
| **Preflight** | 0–2b | [preflight.md](preflight.md) | Gate checks, decrypt, health-check |
| **Fetch** | 3–4d | [fetch.md](fetch.md) | Periodic triggers, data pull, context loading |
| **Process** | 5–7b | [process.md](config/workflow/process.md) | PII filter, domain routing, state updates |
| **Reason** | 8–11 | [reason.md](reason.md) | Cross-domain reasoning, research, briefing synthesis |
| **Finalize** | 12–19b | [finalize.md](finalize.md) | Alerts, actions, email, re-encrypt, calibration |

## Customization

### Skipping steps

Set `workflow.skip_steps` in `config/user_profile.yaml`:
```yaml
workflow:
  skip_steps:
    - 1b    # Skip To Do sync
    - 14    # Skip email briefing
    - 15    # Skip To Do push
```

### Adding custom post-catch-up hooks

Place scripts in `~/.artha-plugins/hooks/post-catchup/` and they will
execute after Step 18 (re-encrypt). Each hook receives the briefing path
as its first argument.

### Phase dependencies

```
Preflight → Fetch → Process → Reason → Finalize
                                  ↑
                           Cross-domain reasoning
                           requires all domains
                           to be processed first
```

## Canonical Reference

The authoritative workflow definition is in `config/Artha.core.md` §2.
These module files are documentation and customization guides — they do
NOT replace or override the core workflow.

## Metrics & Evaluation

The workflow instruments key phases for performance tracking:

| File | Source | Contents |
|------|--------|----------|
| `tmp/pipeline_metrics.json` | `pipeline.py` | Per-connector wall-clock timing, total fetch duration |
| `tmp/skills_metrics.json` | `skill_runner.py` | Per-skill execution times |
| `tmp/catchup_metrics.json` | `lib/metrics.py` | Phase-level timing, counters, eval scores |

Run `python scripts/eval_runner.py` for a full report covering performance
trends, accuracy (acceptance rates), signal:noise ratio, and data freshness.
See `config/observability.md` for the eval command reference.
