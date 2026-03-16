# Backup & Restore

Artha uses a **Grandfather-Father-Son (GFS)** rotating backup strategy. Every successful `vault.py encrypt` run automatically snapshots all state and config files into a tiered catalog — enough to rebuild a fresh Artha install from backup alone.

---

## What Gets Backed Up

The backup registry is declared in `config/user_profile.yaml → backup`. By default:

| Type | Count | Example |
|---|---|---|
| Encrypted state (`*.md.age`) | 9 files | finance, health, immigration… |
| Plain state (`*.md`) | 22 files | goals, home, kids, open_items… |
| Config files | 4 files | user_profile.yaml, routing.yaml… |

Plain state and config files are encrypted on-the-fly before being stored, so all backups are encrypted at rest.

---

## Backup Tiers

| Tier | When | Retention |
|---|---|---|
| **daily** | Mon–Sat | Last 7 snapshots |
| **weekly** | Every Sunday | Last 4 snapshots |
| **monthly** | Last day of month | Last 12 snapshots |
| **yearly** | Dec 31 | Unlimited (never pruned) |

Tier priority: `yearly > monthly > weekly > daily` — a Sunday on Dec 31 is always stored as `yearly`.

---

## Storage Layout

Each GFS backup is a single ZIP containing all registered files for that day.

```
backups/
  daily/
    2026-03-14.zip        ← one ZIP per day, all registered files inside
    2026-03-13.zip
  weekly/
    2026-03-08.zip
  monthly/
    2026-02-28.zip
  yearly/
    2025-12-31.zip
  manifest.json           ← outer catalog: ZIP keys → sha256, tier, date, file_count
```

Inside each ZIP:
```
manifest.json                 ← internal: sha256 + restore_path per file
state/immigration.md.age      ← encrypted state: copied as-is
state/goals.md.age            ← plain state: encrypted on-the-fly
config/user_profile.yaml.age  ← config: encrypted on-the-fly
```

---

## CLI Commands

```bash
# Create a backup now (also runs automatically on every vault.py encrypt)
python scripts/backup.py snapshot

# Show ZIP catalog, tier counts, and last validation date
python scripts/backup.py status

# Validate the newest backup ZIP (decrypt all files + 5 integrity checks each)
python scripts/backup.py validate

# Validate a single domain or specific date
python scripts/backup.py validate --domain finance
python scripts/backup.py validate --date 2026-02-28

# Preview a full restore without writing anything
python scripts/backup.py restore --dry-run

# Restore all files from a snapshot
python scripts/backup.py restore --date 2026-03-14 --confirm

# Restore a single domain only
python scripts/backup.py restore --domain finance --confirm

# Restore state files only, skip config (safe on an already-configured system)
python scripts/backup.py restore --data-only --confirm

# Cold-start install on a new machine from a ZIP
python scripts/backup.py install /path/to/2026-03-14.zip --confirm
python scripts/backup.py install /path/to/2026-03-14.zip --data-only --confirm
```

> `vault.py` forwards backup commands for backward compatibility: `python scripts/vault.py backup-status` → runs `backup.py status`.

---

## Fresh-Install Rebuild (Cold-Start)

A backup ZIP is fully self-sufficient to rebuild Artha from scratch:

1. Clone or copy the Artha directory to the new machine
2. Install `age`: `brew install age` (macOS) or `winget install FiloSottile.age` (Windows)
3. Create the venv: `python scripts/preflight.py` (auto-creates on first run)
4. Import your private key:
   ```bash
   python scripts/backup.py import-key
   # Paste your AGE-SECRET-KEY-... and press Ctrl-D
   ```
5. Check readiness: `python scripts/backup.py preflight`
6. Restore: `python scripts/backup.py install /path/to/YYYY-MM-DD.zip --confirm`

---

## Key Backup — Do This Now

Your age private key is the **single point of failure**. Without it, every backup ZIP is unrecoverable.

```bash
python scripts/backup.py export-key
```

Store the output in your password manager (1Password, Bitwarden, etc.) or a printed copy in a safe. **Not** in email, cloud notes, or any synced file.

---

## Validation Checks

When `validate-backup` runs, each file in the ZIP is verified:

1. **SHA-256 integrity** — matches checksum in the internal manifest
2. **`age` decrypt succeeds** — key is accessible, ciphertext intact
3. **Non-empty output** — decrypted content is not blank
4. **YAML frontmatter** — file begins with `---` (state files; config files exempt)
5. **Word count ≥ 30** — file contains meaningful content (state files only)

Failures are logged to `state/audit.md` with `BACKUP_VALIDATE_FAIL`.

---

## Health Monitoring

`vault.py health` warns if:
- No backups exist yet
- No validation has ever been run
- Last validation was more than 35 days ago

**Recommended**: run `backup.py validate` monthly, or after any significant state update. Auto-validation inside `backup.py snapshot` covers weekly checks automatically.
