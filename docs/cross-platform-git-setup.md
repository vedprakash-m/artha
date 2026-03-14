# Cross-Platform Git + OneDrive Setup for Artha

## Context

The Artha repo lives inside an OneDrive-synced folder on both Mac and Windows:
- **Mac:** `~/Library/CloudStorage/OneDrive-Personal/Artha/`
- **Windows:** `C:\Users\vemishra\OneDrive - Personal\Artha\` (or similar)

This creates a conflict: OneDrive syncs `.git/` between machines, corrupting the
git index and causing phantom "modified" files on the other machine.

---

## What Was Done on Mac (already applied)

The macOS OneDrive File Provider exclusion attribute has been set on the `.git`
directory so OneDrive **never uploads it**:

```bash
xattr -w com.apple.fileprovider.ignore#P 1 \
  ~/Library/CloudStorage/OneDrive-Personal/Artha/.git
```

This means:
- Mac's `.git/` stays local — never syncs to OneDrive cloud
- Windows will **not** receive Mac's `.git/` changes via OneDrive
- Each machine manages its own `.git/` independently
- GitHub is the sync layer for all code changes

---

## One-Time Windows Setup

Run this **once** in PowerShell from the repo folder, then `re-clone` or set the
exclusion on the existing `.git`:

### Option 1 — Fresh clone (recommended, cleanest)

```powershell
# 1. Delete the OneDrive-synced .git that may be stale/corrupt
Remove-Item -Recurse -Force "C:\Users\vemishra\OneDrive - Personal\Artha\.git"

# 2. Clone fresh from GitHub into the same folder
#    (--no-checkout keeps existing working files intact)
git clone --no-checkout https://github.com/vedprakash-m/artha.git temp_git
Move-Item temp_git\.git "C:\Users\vemishra\OneDrive - Personal\Artha\.git"
Remove-Item -Recurse temp_git

# 3. Reset HEAD so git knows which files are tracked
cd "C:\Users\vemishra\OneDrive - Personal\Artha"
git checkout HEAD -- .

# 4. Exclude .git from OneDrive sync on Windows
#    (prevents Windows .git from syncing back to OneDrive cloud)
attrib +U ".git" /S /D
```

### Option 2 — Keep existing .git but fix the index

```powershell
cd "C:\Users\vemishra\OneDrive - Personal\Artha"

# Refresh git index to clear phantom modified files
git fetch origin
git update-index --refresh
git reset --hard origin/main   # WARNING: discards any local uncommitted changes

# Exclude .git from OneDrive sync
attrib +U ".git" /S /D
```

> **Note on `attrib +U`:** This sets the "Unpin" attribute which tells OneDrive
> to not upload this item. Verify it worked by checking OneDrive's sync status
> icon on the `.git` folder — it should show no sync icon.

---

## Ongoing Workflow (Both Machines)

### Golden rule: pull before you work, push when done

```
Mac:     git pull origin main  →  make changes  →  git push origin main
Windows: git pull origin main  →  make changes  →  git push origin main
```

Never work on both machines simultaneously without pushing/pulling in between.

### Standard commands

```bash
# Before starting any work session
git pull origin main

# After making changes
git add -A
git commit -m "your message"
git push origin main
```

### If you see phantom "modified" files (stale index)

```bash
git update-index --refresh
git status   # should be clean now
```

If still dirty after that:
```bash
git diff <file>   # check if there are real content changes
# If no real changes:
git restore <file>
```

---

## What Each Machine Owns

| Item | Mac | Windows | OneDrive Cloud |
|---|---|---|---|
| `.git/` directory | ✅ local only | ✅ local only | ❌ excluded |
| Working files (`scripts/`, `config/`, etc.) | ✅ OneDrive sync | ✅ OneDrive sync | ✅ synced |
| Personal state files (`state/`, `config/user_profile.yaml`, etc.) | ✅ OneDrive sync | ✅ OneDrive sync | ✅ synced |
| Git history / commits | via GitHub | via GitHub | ❌ not involved |

---

## What Is and Is Not Committed to GitHub

### Committed (public, safe)
- All Python scripts under `scripts/`
- Config templates: `config/*.example.yaml`, `config/*.schema.json`
- Prompts: `prompts/*.md`
- Tests: `tests/`
- Docs: `docs/`, `specs/`, `README.md`, `CHANGELOG.md`

### Gitignored (personal, stays in OneDrive only)
- `config/user_profile.yaml` — personal profile data
- `config/artha_config.yaml` — ToDo list IDs
- `config/settings.md` — legacy personal config
- `config/routing.yaml` — personal email routing rules
- `config/Artha.identity.md` — generated identity block
- `config/Artha.md` — assembled instruction file
- `state/*.md`, `state/*.md.age` — all state files
- `briefings/`, `summaries/` — generated outputs

These files sync between Mac ↔ Windows via OneDrive only, never via GitHub.

---

## File That Syncs and Is Safe to Ignore

After the Mac setup, OneDrive may sync a stale `.git` **file** (not directory)
to Windows — a 1-line text file from the brief `gitdir` pointer experiment that
was immediately reverted. If you see a `.git` *file* (not directory) on Windows,
delete it and re-do Option 1 or 2 above.

---

## Summary

```
OneDrive = sync for working files + personal state
GitHub   = sync for committed code changes
.git/    = local to each machine, never synced
```

The machines are **not** real-time collaborative editing environments — they are
two independent git clients that happen to share working files via OneDrive.
Treat every work session as: `pull → work → push`.
