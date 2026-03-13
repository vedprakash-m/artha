# Artha Quick Start Guide

Get from zero to your first personalized briefing in under 15 minutes.

## Prerequisites

- macOS 13+ or Windows 11 (Linux supported with minor path differences)
- Python 3.11+
- A Google account (Gmail + Calendar) — optional but recommended for the best experience

---

## Step 1: Clone the Repository

```bash
git clone https://github.com/<your-org>/artha.git
cd artha
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
      primary: "you@example.com"

location:
  city: "Your City"
  timezone: "America/Chicago"   # IANA timezone name
```

> **Tip:** Run `/bootstrap` in an Artha session for a guided interview that
> populates all domains step-by-step.

---

## Step 3: Install Dependencies

```bash
python -m venv ~/.artha-venvs/.venv
source ~/.artha-venvs/.venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r scripts/requirements.txt
```

The first script you run will auto-bootstrap the venv if it isn't already active.

---

## Step 4: Connect a Data Source

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

## Step 5: Run Your First Catch-Up

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
