# Artha Quick Start Guide

Get from zero to your first personalized briefing in under 15 minutes.

## Prerequisites

| Prerequisite | Why | Install |
|---|---|---|
| **Python 3.11+** | Runs all Artha scripts | [python.org](https://www.python.org/downloads/) |
| **Git** | Clone the repo | [git-scm.com](https://git-scm.com/) |
| **`age`** | Encrypts sensitive state files | [github.com/FiloSottile/age](https://github.com/FiloSottile/age#installation) |
| **An AI CLI** | Runtime — Artha runs *inside* your AI CLI | [Gemini CLI](https://github.com/google-gemini/gemini-cli) · [GitHub Copilot](https://github.com/github/gh-copilot) · [Claude](https://www.anthropic.com/claude) |

A Google account (Gmail + Calendar) is optional but recommended for the best experience.

---

## Step 1: Clone & Install

```bash
git clone https://github.com/vedprakash-m/artha.git
cd artha

# Create a Python virtual environment
python3 -m venv ~/.artha-venvs/.venv
source ~/.artha-venvs/.venv/bin/activate   # macOS/Linux
# Windows: ~/.artha-venvs/.venv/Scripts/Activate.ps1

# Install dependencies
pip install -r scripts/requirements.txt
```

---

## Step 2: Create Your Profile

Copy the example profile and fill in your details:

```bash
cp config/user_profile.example.yaml config/user_profile.yaml
```

Open `config/user_profile.yaml` in your editor. At minimum, set:

```yaml
family:
  primary_user:
    name: "Your Name"
    emails:
      gmail: "you@gmail.com"      # or outlook/icloud — at least one required

location:
  city: "Your City"
  timezone: "America/Chicago"   # IANA timezone name
```

> **Tip:** Run `/bootstrap` in an Artha session for a guided interview that
> populates all domains step-by-step.

---

## Step 3: Generate Artha's Instruction File

```bash
# Validate your profile first (catches errors before generating)
python scripts/generate_identity.py --validate

# Generate config/Artha.md (the file your AI CLI reads)
python scripts/generate_identity.py
```

---

## Step 4: Set Up Encryption

```bash
# Generate an age keypair
age-keygen -o ~/age-key.txt
# Output shows: Public key: age1xxxxxxxxxxxxxxxxxxxxxxx

# Store the PRIVATE key in your OS keychain
python3 -c "
import os, keyring
key_path = os.path.expanduser('~/age-key.txt')
keyring.set_password('age-key', 'artha', open(key_path).read().strip())
print('Key stored in keychain successfully.')
"

# Copy the PUBLIC key (printed by age-keygen above) into your profile:
#   encryption.age_recipient in config/user_profile.yaml

# Then delete the key file — the private key is safely in your keychain
rm ~/age-key.txt
```

---

## Step 5: Connect a Data Source

### Google (Gmail + Calendar) — recommended for most users

```bash
python scripts/setup_google_oauth.py
```

Follow the prompts: paste your OAuth client credentials from
[Google Cloud Console](https://console.cloud.google.com/) → Credentials →
Create OAuth 2.0 Client ID (Desktop app), then complete the browser consent flow.

### Outlook / Microsoft 365

```bash
python scripts/setup_msgraph_oauth.py
```

### iCloud Mail / Calendar

```bash
python scripts/setup_icloud_auth.py
```

### No cloud accounts — demo mode

```bash
python scripts/demo_catchup.py
```

This renders a sample briefing using fictional data so you can preview the
output format without connecting any accounts.

---

## Step 6: Run Preflight Check

```bash
python scripts/preflight.py --fix
```

This verifies all connections, creates missing state files from templates, and reports any issues.

---

## Step 7: Run Your First Catch-Up

Open an Artha-aware AI session (Claude Code, Gemini CLI, or GitHub Copilot)
and type:

```
/catch-up
```

Or run the preflight check first to confirm everything is connected:

```bash
python scripts/preflight.py
```

---

## Troubleshooting

See [docs/troubleshooting.md](troubleshooting.md) for common issues.
See [docs/supported-clis.md](supported-clis.md) for CLI-specific setup.
