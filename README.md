# Artha - Personal Intelligence OS

[![CI](https://github.com/vedprakash-m/artha/actions/workflows/ci.yml/badge.svg)](https://github.com/vedprakash-m/artha/actions/workflows/ci.yml)
[![PII Check](https://github.com/vedprakash-m/artha/actions/workflows/pii-check.yml/badge.svg)](https://github.com/vedprakash-m/artha/actions/workflows/pii-check.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Your life, organized by AI.**

Artha gives your AI assistant (Claude, Gemini, or GitHub Copilot) persistent context about your life: health, finance,
family, goals, home, immigration, work. Every conversation starts where the last one ended.

No server, no Docker. Plain files in a folder, sensitive ones encrypted with `age`, and your AI CLI. No telemetry.

---

## Get Started

You need **Python 3.11+**, **Git**, and an AI CLI ([Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview), [Gemini CLI](https://github.com/google-gemini/gemini-cli) or GitHub Copilot).

```bash
git clone https://github.com/vedprakash-m/artha.git
cd artha
bash setup.sh          # Windows: .\setup.ps1
```

Setup runs a demo briefing (fictional data), then a 2-minute wizard that creates your profile. The wizard does **not**
connect your accounts, so finish these before your first real briefing:

1. **Connect a source.** Gmail and Google Calendar: `python scripts/setup_google_oauth.py`. Outlook, iCloud and others:
   see [docs/quickstart.md](docs/quickstart.md#step-5-connect-a-data-source).
2. **Encrypt sensitive files** (recommended): install `age`, run `age-keygen`, then `python scripts/vault.py store-key`
   as described in [docs/quickstart.md](docs/quickstart.md#step-4-set-up-encryption).
3. **Check it:** `python artha.py --doctor`
4. Open your AI CLI in this folder and run **`/bootstrap`** for a guided interview that fills in your family, finances and
   goals (skip it to fill them in over time), then say **catch me up**.

Stuck? [docs/troubleshooting.md](docs/troubleshooting.md).

---

## Everyday commands

Type these as plain words in your AI CLI (no leading `/`):

| Say | You get |
|---|---|
| `brief` / `catch me up` | Briefing: open items, calendar, alerts |
| `items` | Open action items |
| `goals` | Goal progress |
| `domain finance` | Deep dive on one domain (health, immigration, kids, home...) |
| `work` | Work briefing |
| `guide` | All commands |

---

## Two laptops

Artha runs in **Light mode**: files in this folder, one active laptop at a time, synced by OneDrive or similar.
When you finish on one laptop, a hook seals a small handoff record automatically; the other laptop checks it when it
starts and stays read-only until the files match. Nothing to do by hand. If a laptop says "unverified", wait for the
sync to finish. See [docs/light-artha-second-laptop.md](docs/light-artha-second-laptop.md) (setup checklist) and
[docs/sync-conflicts.md](docs/sync-conflicts.md) (if a sync conflict appears).

---

## Development

```bash
pip install -e ".[dev]"
make check                              # lint, tests, PII scan, schema validation
python tools/run_tests_isolated.py -q   # full suite in an isolated copy (never touches live state)
git config core.hooksPath .githooks     # privacy scans on commit and push
```

See [CONTRIBUTING.md](CONTRIBUTING.md). More guides are in [docs/](docs/): supported CLIs, connectors, skills, actions,
Work OS, backup and restore, security.

---

## License

[AGPL v3](LICENSE). If you distribute a modified version (including as a hosted service), you must release your
modifications under AGPL v3.
