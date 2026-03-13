# Prompt Files — Contract & Schema Reference

Each file in `prompts/` defines the extraction rules, alert thresholds, and
state-write protocol for one Artha domain. The AI reads the relevant prompt
file before processing emails or updating a state file.

---

## File Naming Convention

```
prompts/<domain>.md
```

Where `<domain>` matches the `domain:` key in `config/skills.yaml` and the
state file name `state/<domain>.md`.

---

## Required Sections

Every prompt file must contain the following sections in order:

### 1. YAML Frontmatter

```yaml
---
schema_version: "1.0"
domain: <snake_case_domain_name>
priority: P0 | P1 | P2 | P3 | P4
sensitivity: standard | elevated | critical
last_updated: YYYY-MM-DDTHH:MM:SS
---
```

| Field | Required | Values |
|-------|----------|--------|
| `schema_version` | ✅ | `"1.0"` (current) |
| `domain` | ✅ | snake_case domain name |
| `priority` | ✅ | P0–P4 (determines briefing order) |
| `sensitivity` | ✅ | `standard` / `elevated` / `critical` |
| `last_updated` | ✅ | ISO 8601 datetime |

**Priority scale:**
- `P0` — Life-impacting deadlines (immigration, estate)
- `P1` — Financial health (finance, vehicle, insurance)
- `P2` — Family logistics (kids, health, home)
- `P3` — Planning & enrichment (travel, social, learning, goals)
- `P4` — Background maintenance (digital, shopping, boundary, comms, calendar)

### 2. Purpose

One paragraph describing what this domain tracks and why it matters.
Must be generic (no PII). Reference `§1` of the system prompt for family
definition.

```markdown
## Purpose
Track all [domain] matters for the family (defined in §1): ...
```

### 3. Sender Signatures

Email routing rules. The AI uses these to decide which domain prompt to
apply during email triage.

```markdown
## Sender Signatures (route here)
- `*@domain.gov` (any notice from this sender)
- Subject contains: keyword1, keyword2
- Body contains: specific phrase
```

Patterns support shell-style glob (`*`) for domain matching and substring
matching for subject/body. Case-insensitive.

### 4. Extraction Rules

Numbered list of exactly what data to extract from each matching email.
Be specific — the AI should be able to derive structured YAML from these rules.

```markdown
## Extraction Rules
For each [domain] email, extract:
1. **Field name** — description of what to capture
2. **Another field** — valid formats if applicable
```

For fields with constrained formats, specify the format explicitly:
- Dates: "ISO 8601 (YYYY-MM-DD)"
- Receipt numbers: "IOE/SRC/LIN + 10 digits"
- Amounts: "numeric, no currency symbol"

### 5. Alert Thresholds

Defines what triggers each alert level. Three tiers:

```markdown
## Alert Thresholds
🔴 **CRITICAL** — alert immediately:
- Condition that warrants same-day action

🟠 **URGENT** — alert in briefing:
- Condition that warrants action within the week

🟡 **STANDARD** — note in briefing:
- Routine update, no immediate action needed
```

### 6. Deduplication

How to identify duplicate emails and avoid double-counting state updates.

```markdown
## Deduplication
- Unique key: <field that uniquely identifies a record>
- If duplicate detected: <what to do — update in-place, skip, etc.>
```

### 7. State File Update Protocol

Exact instructions for how to write extracted data to `state/<domain>.md`.

```markdown
## State File Update Protocol
Read `state/<domain>.md` first. Then:
1. For new records: <where and how to add>
2. For updates: <how to update existing records>
3. For deletions / completions: <how to mark done>
```

---

## Optional Sections

These sections may be included when applicable:

### Sensitivity Notes

For `sensitivity: critical` or `elevated` domains — additional handling
instructions for sensitive data (encryption, PII masking in logs, etc.).

### Domain-Specific Enums

Valid values for constrained fields (e.g., visa categories, insurance types).

### Cross-Domain Links

When an extraction should also update another domain's state file
(e.g., an insurance email triggering an update to `finance.md`).

---

## PII Policy

Prompt files must contain **zero PII**. Specifically:
- No real names (use "primary user", "family member", or "child")
- No email addresses
- No account numbers, case numbers, or policy numbers
- No geographic identifiers more specific than country/region

All personal values are referenced via the system prompt's §1 family
definition, which is generated from `user_profile.yaml` by `generate_identity.py`.

The CI workflow `.github/workflows/pii-check.yml` scans all `prompts/*.md`
files on every commit.
