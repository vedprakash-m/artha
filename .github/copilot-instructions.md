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
| `/goals leading` | | Goal scorecard with leading indicators |
| `/domain <name>` | `deep dive on <name>` | Deep-dive into a single domain (§5) |
| `/domains` | `show domains` | List all domains with enable/disable status |
| `/domains enable <name>` | | Enable a domain |
| `/domains disable <name>` | | Disable a domain |
| `/domains info <name>` | | Show full domain details |
| `/items` | `show open items`, `open items` | Display all open action items (§5) |
| `/items add` | | Interactively add a new open item |
| `/items done OI-NNN` | | Mark an open item as done |
| `/items quick` | `anything quick I can knock out?` | Show ≤5 min phone-ready tasks |
| `/dashboard` | `show dashboard`, `artha dashboard` | Life dashboard (§5) |
| `/scorecard` | `life scorecard` | 7-dimension life quality assessment |
| `/cost` | | Monthly API cost estimate |
| `/health` | `system health` | System integrity check |
| `/eval` | | Catch-up evaluation report (full) |
| `/eval perf` | | Performance trends only |
| `/eval accuracy` | | Accuracy only (acceptance rate, signal:noise) |
| `/eval freshness` | | Domain staleness and OAuth health |
| `/bootstrap` | | Guided state file population |
| `/bootstrap <domain>` | | Bootstrap a specific domain |
| `/bootstrap quick` | | Rapid setup — top 3–5 fields per domain |
| `/bootstrap validate` | | Validation-only report — no file modifications |
| `/bootstrap integration` | | Guided setup for a new data integration |
| `/decisions` | `show decisions` | View active decision log |
| `/scenarios` | `show scenarios` | View/run scenario analyses |
| `/relationships` | `relationship pulse` | Relationship graph overview |
| `/diff` | | State changes since last catch-up |
| `/diff 7d` | | State changes in last 7 days |
| `/privacy` | | Privacy surface disclosure |
| `/teach <topic>` | `explain <topic>` | Domain-aware explanation |
| `/power` | `power half hour` | Focused 30-min action session |
| `/items defer OI-NNN` | | Defer an open item |
| `/radar` | `show radar`, `ai radar` | AI trend signals dashboard (PR-3) |
| `/radar topic add <name>` | | Add topic to Interest Graph |
| `/radar topic list` | | List Interest Graph topics |
| `/radar topic remove <name>` | | Remove topic from Interest Graph |
| `/radar run` | | Pull fresh signals from newsletter backlog |
| `/pr` | `content calendar` | PR Manager — content calendar for this week |
| `/pr threads` | | Narrative thread progress |
| `/pr voice` | | Active voice profile display |
| `/pr moments` | | All detected moments with convergence scores |
| `/pr history` | | Post history (last 30 days) |
| `/pr draft <platform>` | | Generate draft for a platform (li, fb, ig, wa) |
| `/stage` | `show stage` | List all active content cards |
| `/stage preview <CARD-ID>` | | Show full card with draft content |
| `/stage approve <CARD-ID>` | | Mark card approved; emit copy-ready content |
| `/stage draft <CARD-ID>` | | Trigger draft generation for a seed card |
| `/stage posted <CARD-ID> <platform>` | | Log a post as published |
| `/stage dismiss <CARD-ID>` | | Archive a card without posting |
| `/stage history [year]` | | Browse cross-year archived cards |
| `/work` | `work briefing` | Full work-only briefing (specs/work.md §5) |
| `/work pulse` | | 30-second work status snapshot |
| `/work prep` | | Meeting preparation with readiness scoring |
| `/work sprint` | | Sprint and delivery health |
| `/work return [window]` | `work return 4d` | Absence recovery (PTO/travel/sick) |
| `/work connect` | | Review-cycle evidence assembly |
| `/work people <name>` | | Person lookup — org context, collaboration history |
| `/work docs` | | Recent work artifacts |
| `/work bootstrap` | | Guided work setup (cold-start interview) |
| `/work health` | | Work connector and policy health |
| `/work notes [meeting-id]` | | Post-meeting action capture |
| `/work decide <context>` | | Structured decision support (Phase 3) |
| `/work live <meeting-id>` | | Live meeting assist (Phase 2) |
| `/work connect-prep` | | Connect cycle preparation — goals, evidence, narrative |
| `/work connect-prep --calibration` | | Calibration defense brief |
| `/work sources [query]` | | Data source registry lookup |
| `/work sources add <url> [context]` | | Register a new data source |
| `/work newsletter [period]` | | Team newsletter draft (Phase 2) |
| `/work deck [topic]` | | LT deck content assembly (Phase 2) |
| `/work memo` | | Status memo via Narrative Engine |
| `/work memo --weekly` | | Auto-drafted weekly status |
| `/work talking-points <topic>` | | Meeting-ready talking points (Phase 3) |
| `/work refresh` | | Explicit live connector refresh |
| `/work promo-case` | | Promotion readiness assessment — scope arc, evidence density, visibility events (Phase 3) |
| `/work promo-case --narrative` | | Full promotion narrative Markdown draft (Phase 3) |
| `/work journey [project]` | | Project timeline with milestone evidence and scope arc (Phase 3) |
| `/work remember <text>` | | Instant micro-capture — appended to work-notes (Phase 2) |

These are NOT built-in Copilot CLI commands — they are Artha workflow
triggers defined in `config/Artha.md`. Execute them exactly as documented
in that file.

**Work OS note:** `/work` commands are a separate surface from personal
commands. They read only from `state/work/` and never access personal state.
See `specs/work.md` for the full Work OS specification.
