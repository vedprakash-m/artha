# Artha — Personal Intelligence OS

[![CI](https://github.com/vedprakash-m/artha/actions/workflows/ci.yml/badge.svg)](https://github.com/vedprakash-m/artha/actions/workflows/ci.yml)
[![PII Check](https://github.com/vedprakash-m/artha/actions/workflows/pii-check.yml/badge.svg)](https://github.com/vedprakash-m/artha/actions/workflows/pii-check.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Your context-aware personal chief of staff, powered by AI CLIs.**

Artha is an open-source **Personal Intelligence OS** — a structured system that gives AI assistants (Gemini CLI, GitHub Copilot, Claude) deep, persistent context about your life so they can be genuinely useful across every domain: health, finance, family, home, travel, goals, and more.

> **No API server. No database. No Docker.** Just Markdown files + Python scripts + your AI CLI.

---

## What Artha Does

Instead of starting every AI conversation from scratch, Artha:

- **Maintains structured state** across 24 life domains in plain Markdown files
- **Runs a daily catch-up** that processes your email, calendar, and data sources into an actionable briefing
- **Unified data pipeline** — pluggable connectors (Gmail, Outlook, Google Calendar, iCloud, CalDAV, Canvas LMS, OneNote, RSS) pull data through a single `pipeline.py` orchestrator
- **Guards your privacy** — three-layer PII defense screens all outbound AI queries before they leave your machine
- **Encrypts sensitive state** (health, finance, immigration) with `age` encryption at rest
- **Runs autonomous skills** (property tax, weather, vehicle recalls, visa bulletin, passport expiry, subscription monitor) on a schedule
- **Tracks open action items** and syncs them to Microsoft To Do
- **Household-aware** — adapts briefings and active domains to your household type (single, couple, family, roommates) and tenure (owner vs. renter)
- **Telegram conversational bridge** — always-on mobile interface with 45+ command aliases, multi-LLM Q&A (Claude → Gemini → Copilot failover), ensemble mode, and write commands — all from your phone
- **Works cross-platform** — macOS, Windows, Linux — with a pure-Python implementation

---

## Supported Environments

Artha runs inside an AI CLI — the CLI is the runtime. These are the officially supported environments:

| Environment | Status | Connectors | Vault | Notes |
|---|---|---|---|---|
| **Claude Code** (local terminal) | ✅ Full support | All | System keyring | macOS, Windows, Linux |
| **Claude Cowork** (sandbox VM) | ✅ Supported | Gmail + Google Calendar only | `ARTHA_AGE_KEY` env var | MS Graph + iCloud blocked by VM proxy |
| **Gemini CLI** (local terminal) | ✅ Full support | All | System keyring | macOS, Windows, Linux |
| **GitHub Copilot** (VS Code) | ✅ Full support | All | System keyring | macOS, Windows, Linux |
| **Telegram bridge** (background service) | ✅ Full support | All | System keyring | Always-on mobile interface |

**Not supported:** Docker containers, bare SSH sessions without an AI CLI.

> Cowork VM limitations are handled gracefully — blocked connectors are noted in the briefing footer with a prompt to re-run from a local terminal for full data. See [docs/supported-clis.md](docs/supported-clis.md) for detailed setup instructions per environment.

---

## Quick Start (~30 minutes)

> **Time breakdown:** ~5 min clone & install · ~5 min profile setup · ~5 min encryption · ~15 min Google OAuth (one-time, optional) · ~5 min first catch-up.
>
> **Just want to see what Artha does first?** Run `python scripts/demo_catchup.py` for a sample briefing with no accounts or configuration required.

### What You'll Need

| Prerequisite | Why | Install |
|---|---|---|
| **Python 3.11+** | Runs all Artha scripts | [python.org](https://www.python.org/downloads/) |
| **Git** | Clone the repo | [git-scm.com](https://git-scm.com/) |
| **`age`** | Encrypts sensitive state files | `brew install age` (macOS) · `sudo apt install age` (Debian/Ubuntu 22.04+) · `sudo dnf install age` (Fedora) · `sudo pacman -S age` (Arch) · `winget install FiloSottile.age` (Windows) · or [download directly](https://github.com/FiloSottile/age/releases) |
| **Node.js 18+** | Required to install Claude Code or Gemini CLI | [nodejs.org](https://nodejs.org/) (includes `npm`) — skip if using GitHub Copilot only |
| **System keyring** *(Linux only)* | Stores encryption keys and OAuth tokens | Pre-installed on GNOME/KDE desktops. Headless servers: `pip install keyrings.alt` — see [Troubleshooting](docs/troubleshooting.md#no-recommended-backend-was-available-linux) |
| **An AI CLI** | Runtime — Artha runs *inside* your AI CLI | **Recommended for beginners:** [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) (terminal, `npm install -g @anthropic-ai/claude-code`) · [Gemini CLI](https://github.com/google-gemini/gemini-cli) (terminal, `npm install -g @google/gemini-cli`) · [GitHub Copilot](https://github.com/features/copilot) (VS Code extension) |

> **Which AI CLI should I use?**
> - **Gemini CLI** — Free tier available (generous for personal/daily use). Requires a Google account. Terminal-based.
> - **Claude Code** — Free tier available with rate limits; daily Artha catch-ups may need Claude Pro ($20/mo) for heavier email volumes.
> - **GitHub Copilot** — Free tier available in VS Code with limits; full use requires Copilot Pro ($10/mo). Editor-based.
>
> All three work with Artha. See [docs/supported-clis.md](docs/supported-clis.md) for a detailed comparison.

### Step 1 — Clone & Install

```bash
# Clone the repository
git clone https://github.com/vedprakash-m/artha.git
cd artha
```

> **Contributors/forkers only:** Enable the PII safety hook to prevent accidentally committing
> personal data: `git config core.hooksPath .githooks`
> Safe to skip if you're using Artha, not modifying its code.

<details>
<summary><b>Create the Python virtual environment — click to expand for your OS</b></summary>

**macOS / Linux:**
```bash
# Outside the project so OneDrive/iCloud don't upload library files
python3 -m venv ~/.artha-venvs/.venv
```

**Windows (PowerShell):**
```powershell
# On Windows, Python installs as "python" not "python3"
python -m venv $HOME\.artha-venvs\.venv
```

</details>

> **What's a virtual environment?** It's an isolated Python sandbox so Artha's packages don't
> conflict with other Python projects on your machine. We place it in `~/.artha-venvs/` (your
> home folder) rather than inside the project to prevent OneDrive/iCloud from uploading
> hundreds of library files.
>
> **Remember:** You need to activate the venv every time you open a new terminal:
> `source ~/.artha-venvs/.venv/bin/activate` (macOS/Linux) or see the block below (Windows).

<details>
<summary><b>Activate the virtual environment — click to expand for your OS</b></summary>

**macOS / Linux:**
```bash
source ~/.artha-venvs/.venv/bin/activate
```

**Windows (PowerShell):**
```powershell
& $HOME\.artha-venvs\.venv\Scripts\Activate.ps1
```

</details>

```bash
# Install dependencies
pip install -r scripts/requirements.txt
```

> **Want to see Artha in action first?** Run `python scripts/demo_catchup.py` — uses fictional
> data, no accounts or configuration needed. Continue to Step 2 when ready.

### Step 2 — Create Your Profile

```bash
# Copy the example profile
cp config/user_profile.example.yaml config/user_profile.yaml
```

Open `config/user_profile.yaml` in any text editor and fill in your details. At minimum, set:

- `family.primary_user.name` — your name
- `family.primary_user.emails.gmail` — your Gmail address
- `location.timezone` — your timezone in `Continent/City` format (e.g. `America/New_York`, `America/Chicago`, `Europe/London`). [Full list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones). Common values: `America/Los_Angeles` (PT), `America/Denver` (MT), `America/Chicago` (CT), `America/New_York` (ET).

> **Tip**: You can also run `/bootstrap` inside your AI CLI for a guided, conversational setup.

### Step 3 — Generate Artha's Instruction File

```bash
# Validate your profile first (catches errors before generating)
python scripts/generate_identity.py --validate

# Generate config/Artha.md (the file your AI CLI reads)
python scripts/generate_identity.py
```

This assembles `config/Artha.md` from your personal identity + the core system logic.

### Step 4 — Set Up Encryption

```bash
# Generate an age keypair
age-keygen -o ~/age-key.txt
# Output looks like:
#   Public key: age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p
```

> **⚠ Do this now:** Copy the `age1...` public key printed above — you'll need it in a moment.
> If your terminal scrolled, run this to extract it cleanly:
> ```bash
> grep "^age1" ~/age-key.txt
> ```

```bash
# 1. Paste the public key into your profile:
#    Open config/user_profile.yaml → search for "age_recipient" → paste the age1... value
#    It's near the bottom of the file under the "encryption" section. Example:
#      age_recipient: "age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p"
#    Tip: keep the string in double quotes to avoid YAML parsing issues.

# 2. Store the PRIVATE key in your OS credential store
python scripts/vault.py store-key ~/age-key.txt

# Linux note: if keyring raises "No recommended backend was available", install
# one of: python3-secretstorage (GNOME/KDE) or python3-keyrings.alt (plaintext fallback)
#   pip install secretstorage   # or: pip install keyrings.alt

# 3. Verify the key is stored correctly
python scripts/vault.py status
# Look for: credential store key ✓
```

> **Only delete the key file AFTER `vault.py status` shows the key is stored:**

<details>
<summary><b>Delete the key file — click to expand for your OS</b></summary>

**macOS / Linux:**
```bash
rm ~/age-key.txt
```

**Windows (PowerShell):**
```powershell
python scripts/vault.py store-key $HOME\age-key.txt
Remove-Item $HOME\age-key.txt
```

</details>

> **Alternatively**, if you prefer not to use the keyring, you can export the key to an environment
> variable instead: `export ARTHA_AGE_KEY=$(cat ~/age-key.txt)` (useful for headless Linux or CI).
> Run `python scripts/vault.py` with no arguments to see all available commands.

### Step 5 — Connect Your Data Sources

#### Google (Gmail + Calendar) — recommended

Before running the setup script you need a Google Cloud OAuth client (~15 min, one-time):

1. [Create a new project](https://console.cloud.google.com/projectcreate) (e.g., "Artha Personal")
2. Enable APIs: [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com) · [Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
3. [Configure OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent) → choose **External** → fill App name ("Artha") and your email → add yourself under **Test users**
4. [Create credentials](https://console.cloud.google.com/apis/credentials) → **+ Create Credentials** → **OAuth 2.0 Client ID** → **Desktop app** → copy `client_id` and `client_secret`
5. Run the setup script and paste the values when prompted:

> For a step-by-step walkthrough, see [docs/google-oauth-setup.md](docs/google-oauth-setup.md).

```bash
# Gmail + Google Calendar (recommended)
python scripts/setup_google_oauth.py

# Microsoft Outlook + Calendar (optional)
python scripts/setup_msgraph_oauth.py

# Microsoft To Do sync (optional)
python scripts/setup_todo_lists.py
```

> **"This app isn't verified" warning:** During the Google login flow, Chrome/Firefox will
> show a red warning screen. **This is completely safe** — you created this OAuth app yourself
> in Step 5, it only accesses your own Gmail/Calendar, and all data stays on your machine.
> No data is sent to any third party. Google shows this warning for any personal OAuth app that
> hasn't been submitted for Google's review process (which is only needed for public apps).
>
> To proceed: click **Advanced** (small text at bottom-left) → **Go to Artha (unsafe)**.
> See [docs/google-oauth-setup.md](docs/google-oauth-setup.md) for a screenshot walkthrough.

> **No cloud accounts yet?** You can explore Artha's output format first with: `python scripts/demo_catchup.py`

### Step 6 — Run Preflight Check

> **Expected output on first run:** Preflight will show a mix of `✓` passes and `⛔ NO-GO` failures — this is normal. Here's what to expect:
>
> | Check | Fresh-install result | When it resolves |
> |-------|---------------------|------------------|
> | vault.py health | ✓ Passes after Step 4 | Immediately after `store-key` |
> | Gmail / Calendar OAuth token | ⛔ Fails if Step 5 skipped | After running `setup_google_oauth.py` |
> | pipeline.py Gmail health | ⛔ Normal on first install | After first successful catch-up |
> | gmail_send.py health | ⛔ Normal on first install | Only if you enable email briefings |
>
> The only check that **must** pass before your first catch-up is `vault.py health ✓`. All OAuth-related failures are expected until Step 5 is complete.

```bash
python scripts/preflight.py --fix
```

This creates missing state files from templates and reports any issues with a fix hint.

### Step 7 — Run Your First Catch-Up

Open your AI CLI and say:

```
catch me up
```

> **Note:** `catch me up`, `/catch-up`, and `catchup` are all recognized aliases — your AI CLI
> understands natural language. From Telegram, use `catchup` or the shortcut `c`.

Or load `config/Artha.md` directly into a session. Artha will fetch your email, calendar, and data sources, then generate a personalized briefing.

### Not ready to connect accounts? Try Demo Mode first

```bash
python scripts/demo_catchup.py
```

This runs a complete sample briefing with fictional data — no accounts, no OAuth, no configuration required. A good way to see what Artha produces before committing to Steps 4–5.

### Migrating from Legacy Settings

If you have an existing `config/settings.md` from an earlier version:

```bash
python scripts/migrate.py --dry-run   # preview what will be migrated
python scripts/migrate.py             # write config/user_profile.yaml
```

---

## Architecture

Artha uses a **zero-server, pull-based** architecture where your AI CLI (Gemini, Copilot, Claude) _is_ the runtime:

```
┌─ AI CLI (runtime) ─────────────────────────────────────────────┐
│  Reads config/Artha.md → follows catch-up workflow             │
│  Calls pipeline.py → orchestrates connectors + skills          │
│  Writes state/*.md → generates briefing → proposes actions     │
└────────────────────────────────────────────────────────────────┘
         │                    │                    │
    Connectors            Skills              State Files
    (email, cal,       (tax, weather,       (24 domains,
     OneNote, LMS)      recalls, visa)       .age encrypted)
```

### Build Pipeline

The runtime instruction file (`config/Artha.md`) is **assembled**, not hand-edited:

```
config/Artha.identity.md  ─┐
                            ├─→  generate_identity.py  ─→  config/Artha.md
config/Artha.core.md      ─┘
```

- **`Artha.core.md`** — distributable system logic (version-controlled, safe to share)
- **`Artha.identity.md`** — personal identity block (auto-generated, gitignored)
- **`Artha.md`** — assembled output (gitignored, loaded by AI CLIs)

### Configuration (Single Source of Truth)

All personal configuration lives in one file: `config/user_profile.yaml`

| What | Where |
|---|---|
| Identity, family, location | `user_profile.yaml` |
| Household type + tenure | `user_profile.yaml → household` |
| Integrations (Gmail, Outlook, iCloud) | `user_profile.yaml → integrations` |
| Feature flags / capabilities | `user_profile.yaml → capabilities` |
| Encryption key | `user_profile.yaml → encryption` |
| API budget | `user_profile.yaml → budget` |
| To Do list IDs | `user_profile.yaml → integrations.microsoft_graph.todo_lists` |
| Domain manifest (24 domains, lazy-load flags) | `config/domain_registry.yaml` |
| Connector routing | `config/connectors.yaml` + `config/routing.yaml` |
| Skill scheduling | `config/skills.yaml` |

The profile is validated against `config/user_profile.schema.json` on every load.

---

## Telegram Conversational Bridge

Artha includes an **always-on Telegram bot** that lets you interact with your personal intelligence system from any device — no terminal required.

### What You Can Do

| Category | Examples |
|----------|---------|
| **Read commands** | `s` (status), `a` (alerts), `t` (tasks), `d kids` (domain), `g` (goals), `diff` (changes) |
| **AI Q&A** | Any free-form question — routed through Claude → Gemini → Copilot failover |
| **Ensemble** | `aa <question>` — asks all LLMs in parallel, consolidates via Haiku |
| **Write** | `items add Call attorney P0 estate 2026-03-20` · `done OI-005` |
| **Catch-up** | `catchup` — full pipeline from your phone |

45+ command aliases with single-letter shortcuts. Slash optional. Designed for one-thumb phone use.

### Setup

```bash
# Interactive setup (creates bot, configures chat ID, stores token in keyring)
python scripts/setup_channel.py --channel telegram

# Install as auto-start background service
python scripts/setup_channel.py --install-service

# Or run manually
python scripts/channel_listener.py --channel telegram
```

See [specs/conversational-bridge.md](specs/conversational-bridge.md) for the full design spec.

---

## Project Structure

```
config/
  Artha.md                ← Assembled runtime file (auto-generated, gitignored)
  Artha.core.md           ← Distributable system logic template
  Artha.identity.md       ← Personal identity block (auto-generated, gitignored)
  user_profile.yaml       ← Your personal config (gitignored)
  user_profile.example.yaml ← Template for new users
  user_profile.schema.json  ← JSON Schema for profile validation (includes household type enum)
  domain_registry.yaml    ← Authoritative manifest for all 24 domains (lazy-load flags, household filters)
  connectors.yaml         ← Connector configuration
  skills.yaml             ← Skill scheduler configuration
  actions.yaml            ← Action type definitions
  routing.yaml            ← Email sender→domain routing (gitignored)
  presets/                ← Cultural context presets for identity generation
  workflow/               ← Composable catch-up workflow phases
  prompt-overlays/        ← User-specific prompt extensions (gitignored)

scripts/
  pipeline.py             ← Unified data pipeline orchestrator
  generate_identity.py    ← Assembles Artha.md from profile + core
  preflight.py            ← Pre-catch-up health gate
  pii_guard.py            ← PII pre-screening for outbound AI queries
  safe_cli.py             ← PII-safe AI CLI wrapper
  foundation.py           ← Shared constants, crypto primitives, _config dict (leaf — no internal deps)
  vault.py                ← Session lifecycle: age encrypt/decrypt/lock/status (imports foundation + backup lazily)
  backup.py               ← GFS archive engine: standalone CLI (snapshot/restore/validate/install/export-key/import-key)
  profile_loader.py       ← Profile access API + domain registry + household type functions
  skill_runner.py         ← Autonomous background skill scheduler
  todo_sync.py            ← Microsoft To Do synchronization
  migrate_state.py        ← YAML front-matter migration DSL (AddField/RenameField/DeprecateField)
  vault_guard.py          ← Vault state pre-read validator (blocks reads of locked sensitive files)
  dashboard_view.py       ← /dashboard script-backed renderer (--format flash|standard|digest)
  domain_view.py          ← /domain script-backed renderer
  status_view.py          ← /status script-backed renderer (health-check stats, run history)
  goals_view.py           ← /goals script-backed renderer (scorecard table, progress bars)
  items_view.py           ← /items script-backed renderer (priority groups, --quick filter)
  scorecard_view.py       ← /scorecard script-backed renderer (5-dimension weekly scorecard)
  diff_view.py            ← /diff script-backed renderer

  connectors/             ← Pluggable data source handlers
    google_email.py       ← Gmail via Google API
    google_calendar.py    ← Google Calendar via Google API
    msgraph_email.py      ← Outlook via Microsoft Graph
    msgraph_calendar.py   ← Outlook Calendar via Microsoft Graph
    imap_email.py         ← Generic IMAP email (iCloud, etc.)
    caldav_calendar.py    ← CalDAV calendar
    canvas_lms.py         ← Canvas LMS for school grades
    onenote.py            ← OneNote via Microsoft Graph
    rss_feed.py           ← RSS 2.0 / Atom 1.0 feed connector (stdlib only)

  skills/                 ← Autonomous data-pull skills
    uscis_status.py       ← USCIS case status tracking
    visa_bulletin.py      ← Visa Bulletin priority date monitoring
    property_tax.py       ← Property tax deadline reminders
    noaa_weather.py       ← Weather for outdoor planning
    nhtsa_recalls.py      ← Vehicle recall monitoring
    passport_expiry.py    ← Passport expiry alerts (180/90/60 days; requires vault)
    subscription_monitor.py ← Subscription price change + trial-to-paid detection

  actions/                ← Action execution handlers (extensible)
  channels/               ← Output channel adapters (Telegram, etc.)
    base.py               ← ChannelAdapter protocol + dataclasses
    telegram.py           ← Telegram Bot API adapter
    registry.py           ← Channel loader from channels.yaml
  lib/                    ← Shared library modules
    html_processing.py    ← HTML stripping, footer removal, body trimming
    retry.py              ← Exponential backoff with jitter
    auth.py               ← OAuth token management
    common.py             ← Shared constants and utilities

state/
  templates/              ← Blank starter files for new users (auto-populated)
  *.md                    ← Your live domain state (gitignored)
  *.md.age                ← Encrypted sensitive state (gitignored)

prompts/                  ← Domain-specific reasoning prompts (24 domains; includes pets, renter overlay)
tests/                    ← pytest test suite (500+ test cases)
specs/                    ← Product, technical, and UX specifications
docs/                     ← User-facing documentation
briefings/                ← Generated daily briefings (gitignored)
```

---

## Privacy & Security

Artha is **privacy-first, local-first**. Your personal data never leaves your machine unscreened.

| Layer | What it does |
|---|---|
| **PII Scanner** (`pii_guard.py`) | Regex-based detection of SSN, credit cards, passports, ITINs, Aadhaar, PAN, and more |
| **AI Semantic Layer** | Domain prompts enforce PII awareness at the reasoning level |
| **Outbound Guard** (`safe_cli.py`) | Screens all CLI queries before they reach any AI API |
| **Encryption at Rest** (`vault.py`) | `age` encryption for sensitive state files (health, finance, immigration, etc.) |
| **Pre-commit Hook** | Blocks PII, secrets, and forbidden files from reaching git |
| **Advisory File Lock** | OS-level `flock`/`msvcrt` lock prevents concurrent encrypt/decrypt operations |
| **Cloud Sync Fence** | Detects OneDrive/Dropbox/iCloud in flight and waits for quiescence before vault operations |
| **Net-Negative Write Guard** | Prevents accidental data loss when updating state files; supports `ARTHA_FORCE_SHRINK` override with `.pre-shrink` pin |
| **Post-Encrypt Verification** | Verifies `.age` output ≥ plaintext size; aborts on truncation |
| **Deferred Plaintext Deletion** | Plaintext `.md` files are only removed after *all* domains encrypt successfully |
| **Encrypt-Failure Lockdown** | On partial encrypt failure, remaining plaintext files are `chmod 000` to prevent cloud sync of unencrypted data |
| **Auto-Lock Mtime Guard** | `auto-lock` skips encryption if any `.md` file was modified in the last 60 seconds (active write detection) |
| **Atomic `.bak` Guard** | Pre-decrypt `.bak` snapshot created atomically before every decrypt; auto-restored if write fails |
| **GFS Backup Rotation** | Every successful encrypt triggers a Grandfather-Father-Son snapshot into `backups/` (daily/weekly/monthly/yearly tiers) covering all 31 state files + 4 config files — one self-contained ZIP per tier-day |
| **Prune Protection** | GFS pruning pins any ZIP that is the sole carrier of a domain checksum — prevents accidental data loss during rotation |
| **Confirm Gate** | `restore` and `install` require `--confirm` (or `--dry-run`) to prevent accidental overwrites |
| **Pre-Restore Safety Backup** | Before a confirmed restore, live state files are saved to `backups/pre-restore/` |
| **Restore Validation** | `validate-backup` decrypts a backup to a temp dir and runs 5 integrity checks: SHA-256, decrypt success, non-empty, YAML frontmatter, word count ≥ 30 |
| **Key Health Monitoring** | `vault.py health` validates key format (`AGE-SECRET-KEY-` prefix) and warns if key has never been exported |
| **Audit Logging** | Every vault operation logged to `state/audit.md` |
| **CI PII Scanning** | GitHub Actions scans every push for PII leaks |

**Gitignored by design**: `user_profile.yaml`, all state files, tokens, briefings, and `.age` files are never committed.

No telemetry. No cloud dependencies. Everything runs locally.

See [docs/security.md](docs/security.md) for the full threat model.

---

## Backup & Restore

Artha uses a **Grandfather-Father-Son (GFS)** rotating backup strategy for **all state and config files**. Every successful `vault.py encrypt` run automatically snapshots files into a tiered backup catalog — enough to rebuild a fresh Artha install from backup alone.

### What Gets Backed Up

The backup registry is declared in `config/user_profile.yaml → backup` and is the authoritative, user-editable source of truth. By default:

| Type | Count | Example |
|---|---|---|
| Encrypted state (`*.md.age`) | 9 files | finance, health, immigration… |
| Plain state (`*.md`) | 22 files | goals, home, kids, open_items… |
| Config files | 4 files | user_profile.yaml, routing.yaml… |

Users without certain domains (e.g. no immigration) simply remove those entries from the registry.

**Plain state files and config files are encrypted on-the-fly** using your age key before being stored, so all backups are encrypted at rest regardless of source type.

### Backup Tiers

| Tier | When | Retention |
|---|---|---|
| **daily** | Mon–Sat | Last 7 snapshots |
| **weekly** | Every Sunday | Last 4 snapshots |
| **monthly** | Last day of each month | Last 12 snapshots |
| **yearly** | Dec 31 | Unlimited (never pruned) |

Tier promotion priority: `yearly > monthly > weekly > daily` — a Sunday that falls on Dec 31 is always stored as a yearly snapshot.

### Storage Layout

Each GFS backup is a **single ZIP file** containing all 34 registered files for that day. The ZIP is self-contained — it includes its own internal `manifest.json` so it can be validated or restored without needing the outer catalog.

```
backups/                      ← at project root (gitignored, syncs via OneDrive)
  daily/
    2026-03-14.zip            ← one ZIP per day, ALL registered files inside
    2026-03-13.zip
  weekly/
    2026-03-08.zip
  monthly/
    2026-02-28.zip
  yearly/
    2025-12-31.zip
  manifest.json               ← outer catalog: ZIP keys → sha256, tier, date, file_count
```

**Inside each ZIP:**
```
manifest.json                 ← internal: sha256 + restore_path per file
state/immigration.md.age      ← encrypted state: copied as-is
state/goals.md.age            ← plain state: encrypted on-the-fly
config/user_profile.yaml.age  ← config: encrypted on-the-fly
...                           ← all 35 files
```

### CLI Commands

```bash
# Create a backup snapshot now (also runs automatically on every vault.py encrypt)
python scripts/backup.py snapshot

# Show ZIP catalog, tier counts, and last validation date
python scripts/backup.py status

# Validate the newest backup ZIP (decrypt all files + 5 integrity checks each)
python scripts/backup.py validate

# Validate a single domain within the newest ZIP
python scripts/backup.py validate --domain finance

# Validate a specific snapshot date
python scripts/backup.py validate --date 2026-02-28

# Preview a full restore without writing anything
python scripts/backup.py restore --dry-run

# Restore all files from a specific snapshot (catalog-based)
python scripts/backup.py restore --date 2026-03-14 --confirm

# Restore a single domain only
python scripts/backup.py restore --domain finance --confirm

# Restore state files only, skip config (safe on an already-configured system)
python scripts/backup.py restore --data-only --confirm

# Cold-start install on a new machine from an explicit ZIP path
python scripts/backup.py install /path/to/2026-03-14.zip --confirm
python scripts/backup.py install /path/to/2026-03-14.zip --dry-run
python scripts/backup.py install /path/to/2026-03-14.zip --data-only --confirm

# Check age binary, keychain key, and backup dir are ready
python scripts/backup.py preflight
```

> **Note:** `vault.py` forwards backup commands for backward compatibility:
> `python scripts/vault.py backup-status` → runs `backup.py status`, etc.
>
> **Auto-validation:** `backup.py snapshot` automatically triggers `backup.py validate` if no validation has run in the past 7 days. This is non-fatal — a failed auto-validation is logged but does not abort the snapshot.

### Fresh-Install Rebuild (Cold-Start)

A backup ZIP is fully self-sufficient for rebuilding Artha from scratch:

1. Clone or copy the Artha directory to the new machine
2. Install `age`: `brew install age` (macOS) or `winget install FiloSottile.age` (Windows)
3. Create the venv: `python scripts/preflight.py` (auto-creates venv on first run)
4. Import your private key:
   ```bash
   python scripts/backup.py import-key
   # Paste your AGE-SECRET-KEY-... and press Ctrl-D
   ```
5. Check readiness: `python scripts/backup.py preflight`
6. Restore: `python scripts/backup.py install /path/to/YYYY-MM-DD.zip --confirm`

All config files and state files are restored to their original locations in a single command. No catalog access needed.

### Key Backup (do this NOW, before you need it)

Your age private key is the **single point of failure**. Without it, every backup ZIP is unrecoverable. Export and store it securely:

```bash
python scripts/backup.py export-key
```

Store the output in:
- Your password manager (1Password, Bitwarden, etc.)
- A printed copy in a fire safe
- **NOT** in email, cloud notes, or any synced file

### Restore Validation Checks

When `validate-backup` runs, each file inside the ZIP is verified in order:

1. **SHA-256 integrity** — matches checksum in the internal ZIP manifest
2. **`age` decrypt succeeds** — key is accessible and ciphertext is intact
3. **Non-empty output** — decrypted content is not blank
4. **YAML frontmatter** — file begins with `---` (valid state file structure; config files exempt)
5. **Word count ≥ 30** — file contains meaningful content, not a stub (state files only)

Any failure is logged to `state/audit.md` with `BACKUP_VALIDATE_FAIL`.

### Health Monitoring

`vault.py health` reports backup status as part of the system health check. It warns if:
- No backups exist yet (`⚠ no backups found`)
- No validation has ever been run (`⚠ never validated`)
- Last validation was more than 35 days ago (`⚠ validation overdue`)

> **Recommended**: run `backup.py validate` monthly, or after any significant state update. Auto-validation inside `backup.py snapshot` covers weekly checks automatically.

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
make test                 # or: python -m pytest tests/ --tb=short -q

# Full CI check (lint + tests + PII scan + schema validation)
make check

# Other useful targets
make lint                 # Syntax-check all Python files
make pii-scan             # Scan distributable files for PII leaks
make validate             # Validate example profile against JSON schema
make generate             # Regenerate config/Artha.md
make preflight            # Run Artha preflight checks
make clean                # Remove __pycache__
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for full development setup and guidelines.

---

## Documentation

| Guide | Description |
|---|---|
| [Quick Start](docs/quickstart.md) | Zero to first briefing in 15 minutes |
| [Domains](docs/domains.md) | 24 life domains explained |
| [Connectors](docs/connectors.md) | Email, calendar, LMS data sources |
| [Skills](docs/skills.md) | Autonomous background data pulls |
| [Actions](docs/actions.md) | Action types and approval flows |
| [Plugins](docs/plugins.md) | Extending with custom connectors and skills |
| [Security](docs/security.md) | Threat model and security architecture |
| [Supported CLIs](docs/supported-clis.md) | Gemini, Copilot, Claude setup |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and fixes |
| [Contributing](CONTRIBUTING.md) | Dev setup, testing, code style |
| [Changelog](CHANGELOG.md) | Version history |

---

## License

[AGPL v3](LICENSE) — Copyleft. If you distribute a modified version (including as a hosted service), you must release your modifications under AGPL v3.
