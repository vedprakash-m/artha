# Artha - Personal Intelligence OS

[![CI](https://github.com/vedprakash-m/artha/actions/workflows/ci.yml/badge.svg)](https://github.com/vedprakash-m/artha/actions/workflows/ci.yml)
[![PII Check](https://github.com/vedprakash-m/artha/actions/workflows/pii-check.yml/badge.svg)](https://github.com/vedprakash-m/artha/actions/workflows/pii-check.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Your life, organized by AI.**

Artha gives your AI assistant (Claude, Gemini, or GitHub Copilot) deep, persistent context about your life — health, finance, family, goals, home, immigration — so every conversation starts where the last one left off.

> No server. No database. No Docker. Just your files + your AI assistant.
> 🔒 Your data stays on your machine. No telemetry. No cloud sync.

---

## Get Started

**You need: Python 3.11+ and Git.**

```bash
git clone https://github.com/vedprakash-m/artha.git
cd artha
bash setup.sh
```

Setup runs a demo briefing in under 10 seconds, then offers a 2-minute wizard to personalize Artha for your life. After the wizard, open your AI CLI and say: **catch me up**

<details>
<summary><b>Windows (PowerShell)</b></summary>

```powershell
git clone https://github.com/vedprakash-m/artha.git
cd artha
.\setup.ps1
```

`setup.ps1` mirrors `setup.sh` — creates a venv at `$HOME\.artha-venvs\.venv-win`, installs dependencies, runs the demo, then offers the setup wizard.

</details>

<details>
<summary><b>⚙ Advanced Setup — encryption, connectors, full prerequisites</b></summary>

### Prerequisites

| Prerequisite | Why | Install |
|---|---|---|
| **Python 3.11+** | Runs all Artha scripts | [python.org](https://www.python.org/downloads/) |
| **Git** | Clone the repo | [git-scm.com](https://git-scm.com/) |
| **`age`** | Encrypts sensitive state files | `brew install age` (macOS) · `sudo apt install age` (Ubuntu 22.04+) · `winget install FiloSottile.age` (Windows) |
| **Node.js 18+** | Only for Claude Code / Gemini CLI npm install | [nodejs.org](https://nodejs.org/) — skip if using GitHub Copilot |
| **System keyring** *(Linux only)* | Stores keys and tokens | Pre-installed on GNOME/KDE. Headless: `pip install keyrings.alt` |
| **An AI CLI** | Runtime — Artha runs *inside* your AI CLI | [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) · [Gemini CLI](https://github.com/google-gemini/gemini-cli) · [GitHub Copilot](https://github.com/features/copilot) |

> **Which AI CLI?** Gemini CLI has a free tier. Claude Code has rate-limited free access. GitHub Copilot has a free tier in VS Code. All three work. See [docs/supported-clis.md](docs/supported-clis.md).

### Set Up Encryption

```bash
age-keygen -o ~/age-key.txt
# Paste the age1... public key into config/user_profile.yaml → encryption.age_recipient
python scripts/vault.py store-key ~/age-key.txt   # store private key in OS keyring
python scripts/vault.py status                    # verify: credential store key ✓
rm ~/age-key.txt                                  # delete plaintext after verified
```

### Connect Data Sources

```bash
python scripts/setup_google_oauth.py    # Gmail + Google Calendar
python scripts/setup_msgraph_oauth.py   # Outlook + Calendar (optional)
python scripts/setup_todo_lists.py      # Microsoft To Do (optional)
python scripts/setup_ha_token.py        # Home Assistant IoT (optional, LAN only)
```

See [docs/google-oauth-setup.md](docs/google-oauth-setup.md) for a walkthrough.

### Preflight Check

```bash
python scripts/preflight.py --first-run   # advisory mode for new installs
python scripts/preflight.py --fix         # fix auto-correctable issues
```

### Migrating from Legacy Settings

```bash
python scripts/migrate.py --dry-run   # preview changes
python scripts/migrate.py             # write config/user_profile.yaml
```

</details>

---

## What You Get

- **Daily briefings** across 24 life domains (health, finance, goals, immigration, family, home, and more) — with action items, goal progress, and proactive nudges
- **Goal Intelligence Engine** — conversational goal management with metric progress bars, sprint planning, and cross-domain pattern alerts
- **Action Layer** — propose, approve, and execute real-world actions (email, calendar, reminders) through a human-gated queue with full audit trail; deterministic per-type schema validation, composite-key idempotency (TTL-windowed dedup), and OCC version-field conflict detection prevent silent data races. **Proposal quality gates** (entity viability, temporal coherence, 5-factor confidence scoring, category-aware dedup, loop circuit breaker) ensure only high-signal proposals reach the queue. Run `python3 scripts/action_orchestrator.py --apply-suggestions` to review and apply learned suppression preferences.
- **Work OS** — professional briefings, meeting prep, sprint health, career evidence capture, and knowledge graph for your work context. Daily cadence commands: `work standup` / `work standup teams` (PAW format, ≤120 words, Teams variant), `work plan` (3-I filtered weekly plan with growth lens annotations), `work 11 <alias>` (PAW 1:1 prep with people card lookup). Sprint status uses IBA escalation taxonomy (Impediments / Blockers / Asks). See [UX Spec §32](specs/artha-ux-spec.md).
- **Windows Always-On Mode (ACI)** — `artha_engine.py` singleton hosts the Telegram bot (workout logging, watch alerts), daily 7am PT pipeline schedule, and 30-min health watchdog. Multi-LLM failover (Copilot gpt-5.4-mini → Gemini → Claude) for free-form Telegram Q&A. See [Tech Spec §34](specs/artha-tech-spec.md) for architecture.
- **Hermes Home Intelligence (FR-28)** — After each pipeline run, Artha writes a Personal OS context snapshot to `sensor.artha_context` in Home Assistant. The Hermes HA add-on reads this entity to answer life-context-aware home queries ("what's on the calendar today?", "what are the top priorities?") — no real-time M2M protocol required. Work OS data is never exported. Runs every 4 hours via Mac LaunchAgent even without a full catch-up.
- **Pluggable connectors** — Gmail, Google Calendar, Outlook, WhatsApp, iMessage, Canvas LMS, Home Assistant (LAN-only), and more
- **External Agent Composition v3.0 (EAR-3)** — route questions to specialized domain agents; persistent agent memory, TF-IDF lexical fallback routing, heartbeat preflight, user correction tracking, SOUL principles enforcement, fan-out and chain orchestration; trust tiers, PII scrubbing, injection defense, and quality-gated responses. Background pre-compute agents (Capital, Logistics, Readiness, Tribe) run on cron to compute deterministic summaries before the LLM is invoked — eliminating LLM arithmetic and data-freshness races
- **Runtime guardrails** — 7 enforced checks block silent PII leaks, vault mis-access, and prompt injection before they reach state files; wave0 hard-gate, memory sensitivity isolation (high-PII domains blocked from shared memory), AI signal range enforcement, HMAC replay protection across restarts, and session-level precondition + graceful degradation map; Phase 1/2 architectural hardening complete (FSM orchestrator, OCC state writes, idempotency layer with autonomy-level gating, TF-IDF routing, tool boundary enforcement)
- **Data health linting (KB-LINT)** — six-pass cross-domain linter embedded in every briefing; stale dates, orphan references, cross-domain contradictions, and format drift detected automatically; `lint` command for on-demand audit with `--fix` auto-remediation
- **Session undo & checkpointing** — `/undo [domain]` reverts any accidental write; interrupted catch-ups resume from the last completed phase (4-hour TTL)
- **Adaptive signal scoring** — every briefing item ranked by urgency × impact × freshness; low-signal items suppressed, high-priority items promoted automatically
- **Persistent memory** — flat-file memory records cross-session facts with natural-language recall and synonym expansion (no embedding infrastructure required)
- **Domain training loop** — per-domain accuracy tracking detects underperforming domains and generates correction suggestions automatically
- **Golden-set eval framework** — parametrized regression tests with quality dimension gates ensure briefing quality never regresses

---

## Development

```bash
pip install -e ".[dev]"
make test      # run test suite
make check     # lint + tests + PII scan + schema validation
make start     # re-run setup.sh from scratch
```

```bash
python artha.py --doctor   # 11-point health check for your installation
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

---

## Documentation

Full guides are in the [docs/](docs/) folder: quickstart, supported CLIs, connectors, skills, channels, actions, Work OS, backup & restore, security, and troubleshooting.

---

## License

[AGPL v3](LICENSE) — Copyleft. If you distribute a modified version (including as a hosted service), you must release your modifications under AGPL v3.
