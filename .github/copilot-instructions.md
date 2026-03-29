# Artha — Your Personal Intelligence System

This is an Artha session. Full operating instructions are in `config/Artha.md`.

Read `config/Artha.md` now and follow all instructions within it before responding to any message.

> **IMPORTANT — VS Code Copilot:** The user types Artha commands as plain text
> (e.g. `brief`, `work`, `items`) — **without** a leading `/` slash. VS Code
> intercepts `/` prefixed input as its own built-in slash commands, so `/brief`
> would fail with "Unknown command". Always treat bare words like `brief` as
> Artha commands using the routing table below.

## Core Routing
When the user's message matches any pattern below, execute the corresponding action.
Do NOT require a `/` prefix — bare words are the primary invocation form.

| User says (examples) | Route to | Action |
|---------------------|----------|--------|
| `brief`, `catch me up`, `morning briefing`, `SITREP` | **brief** | Run `python scripts/pipeline.py`, read state files, produce briefing |
| `work`, `what's happening at work`, `work briefing` | **work** | Read all files in `state/work/`, produce work briefing |
| `items`, `what's open`, `what's overdue`, `action items` | **items** | Read `state/open_items.md`, display action items |
| `goals`, `how are my goals`, `goal progress`, `sprint` | **goals** | Read `state/goals.md`, display goal progress |
| `domain <X>`, `tell me about <X>`, `<X> status` | **domain** | Read `prompts/<X>.md` + `state/<X>.md`, produce deep dive |
| `content`, `what should I post`, `draft a post` | **content** | Read `state/pr_manager.md` + `state/gallery.yaml`, show content calendar |
| `guide`, `what can you do`, `show commands` | **guide** | Show the seven commands above with brief descriptions |
| `health`, `system status`, `is everything OK` | **health** | Run `python scripts/preflight.py`, display system status |

For full command reference with sub-commands, read `config/commands.md`.

## GitHub Copilot (VS Code) Notes
- **Agent mode required for full functionality.** Use the Agent model selector
  (not Ask/Edit mode) so Artha can read files and run scripts.
- **Do NOT use `/` prefix** — type `brief` not `/brief`. VS Code intercepts `/` commands.
- Scripts are suggested, not auto-run — execute in the terminal when prompted.
- MCP tools provide direct access to GitHub, Kusto, DevBox, and more.
- Custom agents `@artha-work-msft` and `@artha-work` available for work briefings.
