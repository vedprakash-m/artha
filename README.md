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

- **Morning briefings** with action items across 24 life domains (health, finance, goals, immigration, kids, home, and more)
- **Action Layer** *(v1.3)* — Artha can now **act**, not just report. Propose, approve, and execute real actions — send emails, create calendar events, set reminders, draft WhatsApp messages — with a human-gated approval queue, full audit trail, and one-tap Telegram approval
- **PR Manager — Personal Narrative Engine** *(v1.4, opt-in)* — Fuses deep personal context with external awareness to generate platform-specific social content that sounds unmistakably *you*. Moment detection, convergence scoring, narrative threads (LinkedIn/Facebook/Instagram/WhatsApp), voice profile, 3-gate PII firewall. Activate with `/bootstrap pr_manager`.
- **Sense → Reason → Act → Learn** — proactive nudges between sessions, adaptive briefing format that learns your behavior, deterministic email signal extraction, cross-domain pattern alerts, async knowledge capture via `/remember`, monthly retrospectives, and a **persistent memory pipeline** (MEM v1.3.0) that deterministically extracts durable facts from every briefing into `state/memory.md` and builds a living self-model in `state/self_model.md`
- **Pluggable connectors** — Gmail, Google Calendar, Outlook, iCloud, Canvas LMS, OneNote, RSS, Apple Health, **WhatsApp** (Windows/macOS local DB), **iMessage** (macOS local DB), **Home Assistant** (local LAN, opt-in)
- **Multi-machine support** *(DUAL v1.3)* — Run Artha across two machines via an encrypted OneDrive bridge: Mac as the proposer/enricher (morning briefings, action proposals) and Windows as the executor (24/7 Telegram listener, action execution). Per-machine connector routing (`run_on: darwin/windows/all`) ensures each machine only fetches what it can access. Zero new cloud dependencies — the shared OneDrive folder is the transport.
- **Smart Home / IoT** *(v8.2)* — Home Assistant integration: device offline alerts, energy anomaly detection, printer supply levels, swim spa monitoring. LAN-only, privacy-first (no camera/audio, no cloud relay). Activate with `python scripts/setup_ha_token.py`
- **Encrypted state** for sensitive domains with `age` — health, finance, immigration at rest
- **Autonomous skills** — property tax, visa bulletin, passport expiry, vehicle recalls, subscription lifecycle monitor, financial resilience, **home device monitor**, coaching nudges
- **Telegram bridge** — conversational interface from your phone (45+ command aliases, `/remember` inbox capture, `/power` half-hour session, `/relationships` graph, multi-LLM Q&A, inline action approvals)
- **Household-aware** — adapts to single, couple, family, or roommate configurations; owner vs. renter
- **Works everywhere** — macOS, Windows (`setup.ps1`), Linux; pure Python
- **`--doctor`** — unified 11-point diagnostic: Python, venv, packages, age, keychain, tokens, state, PII hook, last catch-up

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

| Guide | Description |
|---|---|
| [Quick Start](docs/quickstart.md) | Zero to first briefing in 15 minutes |
| [Supported CLIs](docs/supported-clis.md) | Claude, Gemini, Copilot — setup and comparison |
| [Actions](docs/actions.md) | Action Layer: approve, execute, undo actions |
| [Connectors](docs/connectors.md) | Email, calendar, LMS data sources |
| [Skills](docs/skills.md) | Autonomous background data pulls |
| [Channels](docs/channels.md) | Telegram bridge setup |
| [Plugins](docs/plugins.md) | User-contributed connectors and skills |
| [Backup & Restore](docs/backup.md) | GFS snapshots, cold-start restore |
| [Security & Privacy](docs/security.md) | Threat model, encryption, PII defense |
| [Domains](docs/domains.md) | 24 life domains explained |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and fixes |
| [Contributing](CONTRIBUTING.md) | Dev setup, testing, code style |
| [Changelog](CHANGELOG.md) | Version history |

---

## License

[AGPL v3](LICENSE) — Copyleft. If you distribute a modified version (including as a hosted service), you must release your modifications under AGPL v3.
