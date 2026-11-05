# Google OAuth Setup — Step-by-Step

This guide walks you through creating the Google Cloud OAuth credentials that Artha needs to read your Gmail and Google Calendar. This is a one-time, ~15-minute setup done entirely in your browser.

---

## Overview

Artha accesses Gmail and Google Calendar through Google's official OAuth flow — it never stores your Google password. You will create a personal "app" project in Google Cloud and grant it access to your own account only.

---

## Step 1 — Create a Google Cloud Project

1. Open [https://console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate).
2. Set **Project name** to `Artha Personal` (or any name you prefer).
3. Leave **Location** as `No organization`.
4. Click **Create**.

Wait a few seconds for the project to be created, then make sure it is selected in the top project picker bar.

---

## Step 2 — Enable Gmail and Calendar APIs

Enable both APIs in one click:

- **Gmail API:** [https://console.cloud.google.com/apis/library/gmail.googleapis.com](https://console.cloud.google.com/apis/library/gmail.googleapis.com)
  → Click **Enable**.

- **Google Calendar API:** [https://console.cloud.google.com/apis/library/calendar-json.googleapis.com](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
  → Click **Enable**.

---

## Step 3 — Configure the OAuth Consent Screen

1. Open [https://console.cloud.google.com/apis/credentials/consent](https://console.cloud.google.com/apis/credentials/consent).
2. Choose **External** and click **Create**.
3. Fill in the required fields:
   - **App name:** `Artha`
   - **User support email:** your Gmail address
   - **Developer contact email:** your Gmail address
4. Click **Save and Continue** through the Scopes page (no changes needed).
5. On the **Test users** page, click **+ Add Users** and add your own Gmail address.
6. Click **Save and Continue**, then **Back to Dashboard**.

> **Why "Test users"?** Because this is a personal, unverified app, Google restricts it to accounts you explicitly allow. Adding yourself as a test user lets the OAuth flow complete.

---

## Step 4 — Create OAuth 2.0 Credentials

1. Open [https://console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials).
2. Click **+ Create Credentials** → **OAuth 2.0 Client ID**.
3. Set **Application type** to **Desktop app**.
4. Set **Name** to `Artha Desktop` (or any name).
5. Click **Create**.
6. A dialog shows your **Client ID** and **Client Secret** — copy both. You can also download the JSON file.

---

## Step 5 — Run the Artha Setup Script

Back in your terminal (with the Artha virtual environment active):

```bash
python scripts/setup_google_oauth.py
```

For a read-only Briefing host, use:

```bash
python scripts/setup_google_oauth.py --read-only
```

Read-only setup requests only `gmail.readonly` and `calendar.readonly`, in
separate consent flows. It requires fresh consent even when tokens already
exist, rejects unexpected granted scopes before saving tokens, and reports
incomplete setup if either connection check fails. It does not request send,
event-write or Drive scopes. Existing setup without the flag retains its
existing scopes and skip behavior.

For programmatic read-only access, use
`build_service("gmail", "v1", read_only=True)` or
`build_service("calendar", "v3", read_only=True)`. These calls reject cached
tokens with missing or broader scopes instead of silently reauthorizing.
Read-only mode does not change other callers or enforce State File permissions.

The script will prompt you to paste your `client_id` and `client_secret`, then open a browser window for the OAuth login flow. Sign in with the same Gmail account you added as a test user.

---

## Troubleshooting

### "This app isn't verified" warning

Google shows a red warning screen because the app hasn't been submitted for Google's review process. This is **expected** for personal OAuth apps.

Click **Advanced** → **Go to Artha (unsafe)** to proceed. Your data stays on your machine.

### "Access blocked: This app's request is invalid"

This usually means the redirect URI doesn't match. Make sure you selected **Desktop app** in Step 4 (not "Web application").

### Token storage and reauthorization

After successful login, Artha stores verified OAuth token records in the local
platform credential store (Windows Credential Manager or macOS Keychain), not
a synced token JSON file. Read-only setup replaces local Gmail and Calendar
records after successful consent. It does not revoke older grants at Google;
remove those separately in your Google account if needed. Never share passwords
or token/client-secret values in chat.

---

## What Artha Can Access

With `--read-only`, Artha requests:

| Scope | Used for |
|-------|----------|
| `gmail.readonly` | Reading emails to extract action items and summaries |
| `calendar.readonly` | Reading calendar events for your daily briefing |

Without the flag, the existing Gmail flow also requests `gmail.send`, and the
Calendar flow also requests `calendar.events`. Those permissions support
separately approved actions; OAuth consent alone does not authorize an action.
The existing Drive helper requests `drive.file`; read-only mode does not support
Drive.
