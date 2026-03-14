# Artha Troubleshooting Guide

## Preflight failures

### "Gmail OAuth token not found"

**Cause:** You haven't connected Gmail yet, or the token file is missing.

**Fix:**
```bash
python scripts/setup_google_oauth.py
```

### "Calendar OAuth token not found"

**Cause:** Same OAuth flow covers both Gmail and Calendar, but the token
file may be missing or corrupted.

**Fix:**
```bash
python scripts/setup_google_oauth.py --reauth
```

### "pii_guard.py failed"

**Cause:** The PII guard script failed its self-test. This usually means a
regex pattern file is malformed or a Python import failed.

**Fix:**
```bash
python scripts/pii_guard.py test
```

Look for FAIL lines in the output and correct the pattern.

---

## Authentication errors

### "Token expired / invalid_grant"

OAuth refresh tokens can expire if unused for 6+ months or if the app is
revoked in Google Account settings.

**Fix:**
```bash
# Re-run the OAuth flow for the affected service
python scripts/setup_google_oauth.py --reauth
python scripts/setup_msgraph_oauth.py --reauth
```

### "iCloud authentication failed"

iCloud requires an **app-specific password**, not your Apple ID password.

**Fix:**
1. Go to [appleid.apple.com](https://appleid.apple.com) → Security → App-Specific Passwords
2. Generate a new password named "Artha"
3. Re-run: `python scripts/setup_icloud_auth.py`

---

## Venv / dependency errors

### "No module named 'googleapiclient'"

The virtual environment isn't activated or the packages aren't installed.

**Fix:**
```bash
source ~/.artha-venvs/.venv/bin/activate
pip install -r scripts/requirements.txt
```

### "ModuleNotFoundError: No module named '_bootstrap'"

You're running a script from outside the `scripts/` directory, so the
relative import can't find `_bootstrap.py`.

**Fix:** Always run scripts from the repo root:
```bash
# ✅ Correct — run from repo root
python scripts/pipeline.py --health --source gmail

# ❌ Wrong (causes import errors)
cd scripts && python pipeline.py --health
```

---

## State file issues

### Vault decrypt fails ("wrong passphrase")

**Cause:** The age encryption key in your system keyring doesn't match the
encrypted state files (e.g., after a key rotation or migrating to a new machine).

**Fix:** You need the original private key that was used to encrypt. Retrieve it
from your keychain backup (macOS Keychain Access → search "age-key"). If the
key is lost, the encrypted state files cannot be recovered — you'll need to
re-populate from `/bootstrap`.

### State file shows `updated_by: bootstrap`

This is expected for a fresh install. The domain hasn't been populated yet.

**Fix:**
```bash
# In an Artha AI session:
/bootstrap <domain>

# Or run quick setup for all domains at once:
/bootstrap quick
```

---

## PII guard false positives

If `pii_guard.py` flags content that isn't real PII (e.g., a hex string that
looks like a phone number), you can temporarily suppress the check:

```bash
# Inspect what was flagged
python scripts/pii_guard.py scan < state/comms.md

# File a bug if the pattern is genuinely wrong
```

Do NOT disable the PII guard entirely. Tune the patterns instead.

---

## Common error codes

| Exit code | Meaning |
|-----------|---------|
| `0` | Success |
| `1` | General failure (see stderr) |
| `2` | Authentication error (token missing or expired) |
| `3` | PII guard blocked the output |
| `4` | Profile / config error (missing or malformed `user_profile.yaml`) |

---

## Getting help

1. Run `python scripts/preflight.py` — it checks all integrations and reports
   the exact failure point with actionable fix instructions.
2. Check `state/audit.md` (if populated) for historical error logs.
3. Open an issue on GitHub with the output of `preflight.py --json`.
