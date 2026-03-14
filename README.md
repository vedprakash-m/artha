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

- **Maintains structured state** across 18 life domains in plain Markdown files
- **Runs a daily catch-up** that processes your email, calendar, and data sources into an actionable briefing
- **Unified data pipeline** — pluggable connectors (Gmail, Outlook, Google Calendar, iCloud, CalDAV, Canvas LMS, OneNote) pull data through a single `pipeline.py` orchestrator
- **Guards your privacy** — three-layer PII defense screens all outbound AI queries before they leave your machine
- **Encrypts sensitive state** (health, finance, immigration) with `age` encryption at rest
- **Runs autonomous skills** (property tax, weather, vehicle recalls, visa bulletin, immigration status) on a schedule
- **Tracks open action items** and syncs them to Microsoft To Do
- **Works cross-platform** — macOS, Windows, Linux — with a pure-Python implementation

---

## Quick Start (15 minutes)

### What You'll Need

| Prerequisite | Why | Install |
|---|---|---|
| **Python 3.11+** | Runs all Artha scripts | [python.org](https://www.python.org/downloads/) |
| **Git** | Clone the repo | [git-scm.com](https://git-scm.com/) |
| **`age`** | Encrypts sensitive state files | [github.com/FiloSottile/age](https://github.com/FiloSottile/age#installation) |
| **An AI CLI** | Runtime — Artha runs *inside* your AI CLI | [Gemini CLI](https://github.com/google-gemini/gemini-cli) · [GitHub Copilot](https://github.com/github/gh-copilot) · [Claude](https://www.anthropic.com/claude) |

### Step 1 — Clone & Install

```bash
# Clone the repository
git clone https://github.com/vedprakash-m/artha.git
cd artha

# Create a Python virtual environment
python3 -m venv ~/.artha-venvs/.venv
source ~/.artha-venvs/.venv/bin/activate   # macOS/Linux
# Windows: ~/.artha-venvs/.venv/Scripts/Activate.ps1

# Install dependencies
pip install -r scripts/requirements.txt
```

### Step 2 — Create Your Profile

```bash
# Copy the example profile
cp config/user_profile.example.yaml config/user_profile.yaml
```

Open `config/user_profile.yaml` in any text editor and fill in your details. At minimum, set:

- `family.primary_user.name` — your name
- `family.primary_user.emails.gmail` — your Gmail address
- `location.timezone` — your IANA timezone (e.g. `America/New_York`)

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
# Output: Public key: age1xxxxxxxxxxxxxxxxxxxxxxx

# Store the PRIVATE key in your OS keychain
python -c "import keyring; keyring.set_password('age-key', 'artha', open('$HOME/age-key.txt').read().strip())"

# Paste the PUBLIC key into your profile
# → Set encryption.age_recipient in config/user_profile.yaml
```

Then delete `~/age-key.txt` — the private key is now safely in your keychain.

### Step 5 — Connect Your Data Sources

```bash
# Gmail + Google Calendar (recommended)
python scripts/setup_google_oauth.py

# Microsoft Outlook + Calendar (optional)
python scripts/setup_msgraph_oauth.py

# Microsoft To Do sync (optional)
python scripts/setup_todo_lists.py
```

### Step 6 — Run Preflight Check

```bash
python scripts/preflight.py --fix
```

This verifies all connections, creates missing state files from templates, and reports any issues.

### Step 7 — Run Your First Catch-Up

Open your AI CLI and say:

```
catch me up
```

Or load `config/Artha.md` directly into a session. Artha will fetch your email, calendar, and data sources, then generate a personalized briefing.

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
    (email, cal,       (tax, weather,       (18 domains,
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
| Integrations (Gmail, Outlook, iCloud) | `user_profile.yaml → integrations` |
| Feature flags / capabilities | `user_profile.yaml → capabilities` |
| Encryption key | `user_profile.yaml → encryption` |
| API budget | `user_profile.yaml → budget` |
| To Do list IDs | `user_profile.yaml → integrations.microsoft_graph.todo_lists` |
| Connector routing | `config/connectors.yaml` + `config/routing.yaml` |
| Skill scheduling | `config/skills.yaml` |

The profile is validated against `config/user_profile.schema.json` on every load.

---

## Project Structure

```
config/
  Artha.md                ← Assembled runtime file (auto-generated, gitignored)
  Artha.core.md           ← Distributable system logic template
  Artha.identity.md       ← Personal identity block (auto-generated, gitignored)
  user_profile.yaml       ← Your personal config (gitignored)
  user_profile.example.yaml ← Template for new users
  user_profile.schema.json  ← JSON Schema for profile validation
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
  profile_loader.py       ← Profile access API (all scripts use this)
  skill_runner.py         ← Autonomous background skill scheduler
  todo_sync.py            ← Microsoft To Do synchronization

  connectors/             ← Pluggable data source handlers
    google_email.py       ← Gmail via Google API
    google_calendar.py    ← Google Calendar via Google API
    msgraph_email.py      ← Outlook via Microsoft Graph
    msgraph_calendar.py   ← Outlook Calendar via Microsoft Graph
    imap_email.py         ← Generic IMAP email (iCloud, etc.)
    caldav_calendar.py    ← CalDAV calendar
    canvas_lms.py         ← Canvas LMS for school grades
    onenote.py            ← OneNote via Microsoft Graph

  skills/                 ← Autonomous data-pull skills
    uscis_status.py       ← USCIS case status tracking
    visa_bulletin.py      ← Visa Bulletin priority date monitoring
    property_tax.py       ← Property tax deadline reminders
    noaa_weather.py       ← Weather for outdoor planning
    nhtsa_recalls.py      ← Vehicle recall monitoring

  actions/                ← Action execution handlers (extensible)
  lib/                    ← Shared library modules
    html_processing.py    ← HTML stripping, footer removal, body trimming
    retry.py              ← Exponential backoff with jitter
    auth.py               ← OAuth token management
    common.py             ← Shared constants and utilities

state/
  templates/              ← Blank starter files for new users (auto-populated)
  *.md                    ← Your live domain state (gitignored)
  *.md.age                ← Encrypted sensitive state (gitignored)

prompts/                  ← Domain-specific reasoning prompts (18 domains)
tests/                    ← pytest test suite (241+ test cases)
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
| **Net-Negative Write Guard** | Prevents accidental data loss when updating state files |
| **Atomic `.bak` Guard** | Pre-decrypt `.bak` snapshot created atomically before every decrypt; auto-restored if write fails |
| **GFS Backup Rotation** | Every successful encrypt triggers a Grandfather-Father-Son snapshot into `backups/` (daily/weekly/monthly/yearly tiers) covering all 31 state files + 4 config files — one self-contained ZIP per tier-day |
| **Restore Validation** | `validate-backup` decrypts a backup to a temp dir and runs 5 integrity checks: SHA-256, decrypt success, non-empty, YAML frontmatter, word count ≥ 30 |
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
python scripts/backup.py restore --date 2026-03-14

# Restore a single domain only
python scripts/backup.py restore --domain finance

# Restore state files only, skip config (safe on an already-configured system)
python scripts/backup.py restore --data-only

# Cold-start install on a new machine from an explicit ZIP path
python scripts/backup.py install /path/to/2026-03-14.zip
python scripts/backup.py install /path/to/2026-03-14.zip --dry-run
python scripts/backup.py install /path/to/2026-03-14.zip --data-only

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
6. Restore: `python scripts/backup.py install /path/to/YYYY-MM-DD.zip`

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
| [Domains](docs/domains.md) | 18 life domains explained |
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
