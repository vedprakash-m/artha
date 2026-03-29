# Artha — Your Personal Intelligence System

Read `config/Artha.md` now and follow all instructions within it before
responding to any message.

## Core Routing (fallback if Artha.md not loaded)
If you cannot read Artha.md, use this minimal routing:
- `/brief` → Run `python scripts/pipeline.py`, read state files, produce briefing
- `/work` → Read all files in `state/work/`, produce work briefing
- `/items` → Read `state/open_items.md`, display action items
- `/goals` → Read `state/goals.md`, display goal progress
- `/domain <name>` → Read `prompts/<name>.md` + `state/<name>.md`, produce deep dive
- `/content` → Read `state/pr_manager.md` + `state/gallery.yaml`, show content calendar
- `/guide` → Show the seven commands above with brief descriptions
- `/health` → Run `python scripts/preflight.py`, display system status

## The Essentials
| Command | What it does |
|---------|-------------|
| `/brief` | Morning briefing — alerts, calendar, domains, goals, action items |
| `/work` | Work briefing — meetings, sprint, career, people, open threads |
| `/items` | Your action items across every life domain |
| `/goals` | Goal progress, sprints, leading indicators |
| `/domain <name>` | Deep dive — finance, immigration, health, kids, or any of 20 domains |
| `/content` | Content creation — moments, drafts, publishing pipeline |
| `/guide` | Discover everything else I can do |

Natural language also works everywhere — "catch me up", "what's my visa status?",
"prep me for my 2pm", "mark the tax return done" all route correctly.

## GitHub Copilot (VS Code) Tips
- Scripts are suggested, not auto-run — execute in the terminal when prompted.
- MCP tools provide direct access to GitHub, Kusto, DevBox, and more.
- Custom agents `@artha-work-msft` and `@artha-work` available for work briefings.
- Use `/guide` for Artha's command discovery (not `/help`, which is intercepted).
- VS Code built-ins (`/fix`, `/explain`, `/tests`, `/doc`, `/new`, `/clear`, `/help`) do not conflict with Artha's seven commands.
