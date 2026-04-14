# Artha — OneDrive Sync Conflict Resolution Protocol

> **Status**: Active — operational runbook  
> **Created**: 2026-04-14 (DEBT-005)  
> **Applies to**: All `state/*.md` files synced via OneDrive across multiple machines

---

## What is a Sync Conflict?

When two machines write to the same state file while one is offline (or before OneDrive
has synced the latest version), OneDrive creates a **conflict copy** alongside the
original.

Common conflict copy filename patterns:
- `state/finance-DESKTOP-WORKPC.md` — machine-name suffix (Windows)
- `state/health (1).md` — numbered copy
- `state/immigration-conflict.md` — explicit conflict tag
- `state/goals-LAPTOP-HOME.md` — laptop-name suffix

Artha's preflight check (`python scripts/preflight.py`) detects these automatically
and emits a P1 WARNING.

---

## Step-by-Step Resolution Protocol

### 1. Identify both files

```bash
# List all conflict copies in state/
ls state/ | grep -E '\-conflict|\-DESKTOP|\-LAPTOP| \([0-9]\)|\([0-9]+\)'

# Or run preflight to get a formatted list:
python scripts/preflight.py
```

### 2. Diff the two files

```bash
# Example: finance.md vs finance-DESKTOP-PC.md
diff state/finance.md "state/finance-DESKTOP-PC.md"
```

Look for differences in:
- `updated_at:` frontmatter field — **keep the file with the later timestamp**
- Any factual changes (new entries, balance updates, status changes)

### 3. Determine the winner

| Scenario | Action |
|----------|--------|
| One file has a later `updated_at` and is a strict superset | Keep the later file; discard the older |
| Both files have unique content (diverged edits) | Manual merge required — see §4 below |
| Conflict copy is identical to the original | Delete the conflict copy |

### 4. Merge diverged edits (if necessary)

For structured YAML frontmatter files (e.g., `open_items.md`, `goals.md`):

```bash
# Use a three-way merge with the last-known-good state as the base
# (Only if you have git history as the base)
git show HEAD:state/finance.md > /tmp/finance_base.md
diff3 STATE/finance.md /tmp/finance_base.md "state/finance-DESKTOP-PC.md" \
  | grep -v '|||' | grep -v '=======' | grep -v '>>>>>>>' > /tmp/finance_merged.md

# Review the result, then:
cp /tmp/finance_merged.md state/finance.md
```

For prose-heavy state files (e.g., `immigration.md`, `employment.md`):
1. Open both files side-by-side in an editor
2. Apply any unique entries from the conflict copy to the main file
3. Update `updated_at:` to the current timestamp: `date -u +"%Y-%m-%dT%H:%M:%SZ"`

### 5. Delete the conflict copy

```bash
rm "state/finance-DESKTOP-PC.md"
# Or for multiple conflicts:
ls state/ | grep -E '\-conflict|\-DESKTOP|\-LAPTOP| \([0-9]\)' | xargs -I{} rm "state/{}"
```

### 6. Verify preflight is clean

```bash
python scripts/preflight.py
# Expected: no conflict warnings
```

---

## Prevention

| Strategy | How |
|----------|-----|
| Encrypt before closing laptop | `python scripts/vault.py encrypt` — vault files are locked during sync |
| Use a single active session | Sign in on only one machine at a time when writing to sensitive state files |
| Keep OneDrive sync current | Confirm sync is complete before opening state files on a second machine |

---

## Adding to Ignore List (Suppress Repeat Alerts)

If a specific conflict copy is intentionally kept (e.g., as a backup reference), add it
to `sync_conflict_ignore` in `config/user_profile.yaml`:

```yaml
sync:
  conflict_ignore:
    - "state/finance-DESKTOP-PC.md"   # manual backup copy, not a real conflict
```

This suppresses the preflight warning for that specific file.

---

## Conflict Copy Naming Reference

| Pattern | Source |
|---------|--------|
| `filename-DEVICE-NAME.ext` | Windows OneDrive client |
| `filename (1).ext` | OneDrive web or macOS client |
| `filename-conflict.ext` | OneDrive conflict marker |
| `filename-LAPTOP-NAME.ext` | Laptop machine name suffix |

---

*Last updated: 2026-04-14 (DEBT-005 implementation)*
