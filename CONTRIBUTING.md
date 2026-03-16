# Contributing to Artha

## Prerequisites

- Python 3.11+ (3.12 also tested in CI)
- macOS, Linux, or Windows (macOS is primary dev platform)
- `age` encryption tool (for vault tests)
- Git

## Dev Setup

```bash
# Clone the repository
git clone https://github.com/vedprakash-m/artha.git
cd artha

# Create and activate a venv
python3 -m venv ~/.artha-venvs/.venv
source ~/.artha-venvs/.venv/bin/activate

# Install runtime + dev dependencies
pip install -r scripts/requirements.txt
pip install -e ".[dev]"

# Enable the PII pre-commit hook (prevents accidental PII commits)
git config core.hooksPath .githooks

# Copy the example profile
cp config/user_profile.example.yaml config/user_profile.yaml
# Edit config/user_profile.yaml with your details, or run /bootstrap in your AI CLI

# Verify everything works
make test
```

## Common Commands

| Command | Description |
|---------|-------------|
| `make test` | Run full test suite |
| `make lint` | Syntax-check all Python files |
| `make pii-scan` | Scan distributable files for PII leaks |
| `make validate` | Validate example profile against JSON schema |
| `make preflight` | Run Artha preflight checks |
| `make generate` | Regenerate `config/Artha.md` from core + identity |
| `make clean` | Remove `__pycache__` and `.pyc` files |
| `make check` | Run lint + test + pii-scan + validate (full CI locally) |

## Project Structure

```
scripts/              # Python runtime — connectors, skills, pipeline, vault
  connectors/         # Data source handlers (gmail, graph, imap, calendar, …)
  skills/             # Autonomous data-pull skills (weather, recalls, …)
  lib/                # Shared utilities (html_processing, common, …)
  actions/            # Action proposal handlers
config/               # Configuration files (Artha.md, schema, routing, …)
state/                # Domain state files (personal, gitignored)
prompts/              # Domain prompt templates (distributable)
tests/                # pytest test suite
  unit/               # Unit tests for scripts, connectors, skills
docs/                 # User-facing documentation
specs/                # Product specs (PRD, tech spec, UX spec)
```

## Build Pipeline

Artha's runtime instruction file (`config/Artha.md`) is **assembled**, not hand-edited:

```
config/Artha.identity.md  ─┐
                            ├─→  scripts/generate_identity.py  ─→  config/Artha.md
config/Artha.core.md      ─┘
```

- **`Artha.core.md`** — distributable system logic (version-controlled)
- **`Artha.identity.md`** — user-specific identity block (gitignored, generated from `user_profile.yaml`)
- **`Artha.md`** — assembled output (gitignored, loaded by AI CLIs)

After editing `Artha.core.md` or `user_profile.yaml`, run `make generate` to rebuild.

## Testing

```bash
# Run all tests
make test

# Run a specific test file
python -m pytest tests/unit/test_pipeline.py -v

# Run with coverage (if installed)
python -m pytest tests/ --cov=scripts --cov-report=term-missing
```

Tests should always pass before submitting a PR. The CI runs on Python 3.11 and 3.12.

## PII Safety

Artha has a 3-layer PII defense system. Before committing:

1. Run `make pii-scan` — scans distributable files for PII patterns
2. The CI `pii-check.yml` workflow scans all changed files on push
3. Never commit files listed in `.gitignore` (profiles, state, tokens)

## Code Style

- No strict formatter enforced yet — match surrounding code style
- Use type hints for function signatures
- Keep imports at module top (except lazy imports for optional deps)
- Use `from scripts.profile_loader import get` for config access — never hardcode paths

## Sensitive Files

These files are gitignored and must **never** be committed:

- `config/user_profile.yaml` — personal identity
- `config/settings.md` — legacy config (being consolidated)
- `config/artha_config.yaml` — legacy runtime config
- `config/Artha.identity.md` — generated identity block
- `config/Artha.md` — assembled instruction file
- `state/*.md` — domain state (plaintext)
- `*-token.json`, `*-creds.json` — OAuth tokens

## Git History & Privacy

**All commits from this maintainer use the GitHub noreply address** — no personal email is embedded in new commits:

```bash
git config user.email "vedprakash-m@users.noreply.github.com"
```

**Known limitation in early history:** Early commits (before the noreply address was adopted) contain the maintainer's personal email addresses in commit metadata and in some spec/config file content. This is a known issue. The working files at HEAD are clean (verified by the `pii-check.yml` CI workflow on every push). A full history rewrite with `git filter-repo` would break all existing forks and is not planned.

**For contributors:** Please configure your own noreply address before submitting PRs:

```bash
# Find your noreply address at: https://github.com/settings/emails
git config user.email "YOUR_USERNAME@users.noreply.github.com"
```
