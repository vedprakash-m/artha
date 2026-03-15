# Artha Quick Start Guide

Get from zero to your first personalized briefing in **3 minutes**.

## The Fast Way (macOS / Linux)

```bash
git clone https://github.com/vedprakash-m/artha.git
cd artha
bash setup.sh
```

`setup.sh` checks prerequisites, creates your virtual environment, installs dependencies, copies the profile template, and **runs a demo briefing** — all automatically.

Then edit `config/user_profile.yaml`, run `python scripts/generate_identity.py`, open your AI CLI, and say **"catch me up"**.

**What you need: Python 3.11+ and Git.** Everything else is optional until you're ready.

---

## Prerequisites

| Prerequisite | Why | Notes |
|---|---|---|
| **Python 3.11+** | Runs all Artha scripts | [python.org](https://www.python.org/downloads/) |
| **Git** | Clone the repo | [git-scm.com](https://git-scm.com/) |
| **`age`** | Encrypts sensitive state files — *optional on first run* | `brew install age` · `sudo apt install age` · [github.com/FiloSottile/age](https://github.com/FiloSottile/age#installation) |
| **An AI CLI** | Runtime — Artha runs *inside* your chosen CLI | [Gemini CLI](https://github.com/google-gemini/gemini-cli) · [GitHub Copilot](https://github.com/features/copilot) · [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) |

A Google account (Gmail + Calendar) is optional but recommended for the best experience.

---

## Step 1: Clone & Install

```bash
git clone https://github.com/vedprakash-m/artha.git
cd artha
bash setup.sh          # handles venv, deps, profile copy, and demo briefing
```

**Manual (Windows / advanced):**

```bash
# Create a Python virtual environment outside the project
# (prevents OneDrive/iCloud from uploading hundreds of library files)
python3 -m venv ~/.artha-venvs/.venv
source ~/.artha-venvs/.venv/bin/activate   # macOS/Linux
# Windows (PowerShell): & $HOME\.artha-venvs\.venv\Scripts\Activate.ps1

pip install -r scripts/requirements.txt
cp config/user_profile.example.yaml config/user_profile.yaml
python scripts/demo_catchup.py             # see what Artha produces before configuring
```

---

## Step 2: Create Your Profile

If `setup.sh` already copied the template for you, just open and edit it.
Otherwise:

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

# Store the PRIVATE key in your OS credential store (one command)
python scripts/vault.py store-key ~/age-key.txt
# Windows (PowerShell): python scripts/vault.py store-key $HOME\age-key.txt

# Alternatively, store it manually:
#   python3 -c "import os, keyring; keyring.set_password('age-key', 'artha', open(os.path.expanduser('~/age-key.txt')).read().strip()); print('Done.')"
# Linux note: if keyring raises "No recommended backend was available", install
# one of: python3-secretstorage (GNOME/KDE) or python3-keyrings.alt (plaintext fallback)
#   pip install secretstorage   # or: pip install keyrings.alt

# Copy the PUBLIC key (printed by age-keygen above) into your profile:
#   encryption.age_recipient in config/user_profile.yaml

# Then delete the key file — the private key is safely in your credential store
rm ~/age-key.txt              # macOS/Linux
# Windows (PowerShell): Remove-Item $HOME\age-key.txt
```

---

## Step 5: Connect a Data Source

### Google (Gmail + Calendar) — recommended for most users

Before running the setup script you need a Google Cloud OAuth client (~15 min, one-time):

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) → create a project (e.g., "Artha Personal")
2. **APIs & Services → Library** — enable **Gmail API** and **Google Calendar API**
3. **APIs & Services → OAuth consent screen** → External → fill App name ("Artha") and your email → under **Test users**, add your own Gmail address
4. **APIs & Services → Credentials → + Create Credentials → OAuth 2.0 Client ID** → Desktop app → note `client_id` and `client_secret`
5. Run the setup script and paste them when prompted:

```bash
python scripts/setup_google_oauth.py
```

> **"This app isn't verified" warning:** During the Google login flow, Chrome/Firefox will show a red warning screen. Click **Advanced → Go to Artha (unsafe)** to proceed — this is expected for personal OAuth apps that haven't been submitted to Google for review.

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
