# Artha — Personal Intelligence OS

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Your context-aware personal chief of staff, powered by AI CLIs.**

Artha is an open-source **Personal Intelligence OS** — a structured system that gives AI assistants (Gemini CLI, GitHub Copilot, Claude) deep, persistent context about your life so they can be genuinely useful across every domain: health, finance, immigration, kids, travel, home, work, and more.

---

## What Artha Does

Instead of starting every AI conversation from scratch, Artha:

- **Maintains structured state** across 18 life domains in plain Markdown files
- **Runs a daily catch-up** that processes your email, calendar, and data sources into an actionable briefing
- **Guards your privacy** — all outbound AI queries are pre-screened by `pii_guard.py` before leaving your machine
- **Encrypts sensitive state** (health, finance, immigration) with `age` encryption at rest
- **Runs autonomous skills** (USCIS status, property tax, weather, vehicle recalls) on a schedule
- **Tracks open action items** and syncs them to Microsoft To Do
- **Works cross-platform** — macOS, Windows, Linux — with a pure-Python implementation

---

## Quick Start

### Prerequisites

- Python 3.11+ 
- `age` encryption tool ([installation guide](https://github.com/FiloSottile/age#installation))
- At least one AI CLI: [Gemini CLI](https://github.com/google-gemini/gemini-cli), [GitHub Copilot CLI](https://github.com/github/gh-copilot), or [Claude CLI](https://www.anthropic.com/claude)

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/vedprakash-m/artha.git
cd artha

# 2. Create your profile (copy + edit the example)
cp config/user_profile.example.yaml config/user_profile.yaml
# Edit config/user_profile.yaml with your details

# 3. Generate identity and assemble Artha.md
python scripts/generate_identity.py --validate
python scripts/generate_identity.py

# 4. Set up Python environment and dependencies
python scripts/preflight.py --fix

# 5. Configure OAuth (Gmail + Google Calendar)
python scripts/setup_google_oauth.py

# 6. Run pre-flight check
python scripts/preflight.py

# 7. Run your first catch-up
# In Gemini CLI:    gemini --artha_context
# In GitHub Copilot: @copilot run-catch-up
# Or load Artha.md directly into any AI CLI session
```

### Migrating from Legacy Settings

If you have an existing `config/settings.md`, migrate it:

```bash
python scripts/migrate.py --dry-run   # preview
python scripts/migrate.py             # write config/user_profile.yaml
```

---

## Project Structure

```
config/
  Artha.md              ← Assembled from identity + core (auto-generated)
  Artha.core.md         ← Distributable system logic template
  Artha.identity.md     ← Your §1 Identity block (auto-generated, gitignored)
  user_profile.yaml     ← Your personal config (gitignored)
  user_profile.example.yaml ← Template for new users
  routing.example.yaml  ← Email routing template
  skills.yaml           ← Skill scheduler configuration
  presets/cultural/      ← Cultural context presets for identity generation

scripts/
  generate_identity.py  ← Assembles Artha.md from profile + core
  preflight.py          ← Pre-catch-up health gate
  pii_guard.py          ← PII pre-screening for outbound AI queries
  safe_cli.py           ← PII-safe AI CLI wrapper
  vault.py              ← age encryption for sensitive state files
  profile_loader.py     ← Profile access (all scripts use this)
  migrate.py            ← Migrate legacy settings.md to user_profile.yaml
  _bootstrap.py         ← Cross-platform venv re-exec helper
  skill_runner.py       ← Autonomous background skill scheduler
  skills/               ← Individual data-pull skills
  lib/                  ← Shared library modules

state/
  templates/            ← Blank starter files for new users
  *.md                  ← Your live domain state (gitignored)

prompts/                ← Domain-specific reasoning prompts for AI CLIs
tests/                  ← pytest test suite (130+ test cases)
.githooks/              ← Git hooks (PII & secrets pre-commit guard)
docs/                   ← Extended documentation
```

---

## Privacy & Security

Artha is **privacy-first, local-first**. Your personal data never leaves your machine unscreened.

- **Three-layer PII defense**:
  1. `pii_guard.py` — regex-based PII scanner (SSN, credit cards, passport numbers, ITINs, and more)
  2. `vault.py` — `age` encryption at rest for sensitive state files (health, finance, immigration, estate, insurance, vehicle, contacts)
  3. Pre-commit git hook — blocks PII, secrets, and forbidden files from ever reaching the repository
- **Audit logging**: Every vault operation is logged to `state/audit.md`
- **Gitignored personal data**: `user_profile.yaml`, all state files, tokens, briefings, and encrypted `.age` files are `.gitignore`d
- **No telemetry, no cloud dependencies**: Everything runs locally

See [docs/security.md](docs/security.md) for the full threat model and security architecture.

---

## Documentation

- [Quick Start Guide](docs/quickstart.md)
- [Domain Reference](docs/domains.md)
- [Skills System](docs/skills.md)
- [Security Model](docs/security.md)
- [Supported AI CLIs](docs/supported-clis.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Changelog](CHANGELOG.md)

---

## Contributing

Contributions are welcome. Please ensure:

1. All tests pass: `python -m pytest tests/`
2. No PII in committed files (the pre-commit hook enforces this)
3. New scripts use `scripts/lib/` shared modules
4. State file changes include corresponding `state/templates/` updates

---

## License

[AGPL v3](LICENSE) — Copyleft. If you distribute a modified version (including as a hosted service), you must release your modifications under AGPL v3. This prevents PII-handling tooling from becoming opaque commercial SaaS.
