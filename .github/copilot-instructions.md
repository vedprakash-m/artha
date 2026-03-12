# Artha — Personal Intelligence OS

This is an Artha session. Full operating instructions are in `config/Artha.md`.

Read `config/Artha.md` now and follow all instructions within it before responding to any message.

## Custom Artha Commands

The following commands are Artha-specific. When the user types any of these
(with or without the `/` prefix), treat them as the described trigger and
execute the corresponding workflow from `config/Artha.md`.

| Command | Aliases | Action |
|---------|---------|--------|
| `/catch-up` | `catch me up`, `morning briefing`, `SITREP`, `run catch-up` | Full catch-up workflow (§2) |
| `/catch-up flash` | | Force flash briefing format |
| `/catch-up deep` | | Extended briefing with trend analysis |
| `/catch-up standard` | | Force standard format |
| `/status` | `artha status` | Quick health check — no email fetch (§5) |
| `/goals` | `show goals`, `goal pulse` | Goal scorecard (§5) |
| `/goals sprint new` | | Create a new goal sprint |
| `/domain <name>` | `deep dive on <name>` | Deep-dive into a single domain (§5) |
| `/items` | `show open items`, `open items` | Display all open action items (§5) |
| `/items add` | | Interactively add a new open item |
| `/items done OI-NNN` | | Mark an open item as done |
| `/items quick` | `anything quick I can knock out?` | Show ≤5 min phone-ready tasks |
| `/dashboard` | `show dashboard`, `artha dashboard` | Life dashboard (§5) |
| `/scorecard` | `life scorecard` | 7-dimension life quality assessment |
| `/cost` | | Monthly API cost estimate |
| `/health` | `system health` | System integrity check |
| `/bootstrap` | | Guided state file population |
| `/bootstrap <domain>` | | Bootstrap a specific domain |
| `/decisions` | `show decisions` | View active decision log |
| `/scenarios` | `show scenarios` | View/run scenario analyses |
| `/relationships` | `relationship pulse` | Relationship graph overview |
| `/diff` | | State changes since last catch-up |
| `/diff 7d` | | State changes in last 7 days |
| `/privacy` | | Privacy surface disclosure |
| `/teach <topic>` | `explain <topic>` | Domain-aware explanation |
| `/power` | `power half hour` | Focused 30-min action session |
| `/items defer OI-NNN` | | Defer an open item |

These are NOT built-in Copilot CLI commands — they are Artha workflow
triggers defined in `config/Artha.md`. Execute them exactly as documented
in that file.
