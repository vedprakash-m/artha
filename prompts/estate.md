---
schema_version: "1.0"
domain: estate
priority: P1
sensitivity: high
last_updated: 2026-03-14T00:00:00
---
# Estate Domain Prompt

## Purpose
Track estate planning documents, attorney communications, and beneficiary designations.
Low frequency but high stakes.

## Sender Signatures
- Estate attorney, trust attorney
- Subject: will, trust, estate plan, POA, advance directive, beneficiary
- Subject: executor, notarize, trust funding, probate

## Extraction Rules
1. **Document type**: will, trust, POA, AHCD, beneficiary designation
2. **Person(s) affected**
3. **Status**: draft, review, signed, filed, needs update
4. **Attorney notes**: any action items or responses needed
5. **Deadline**: if any

## Alert Thresholds
🟠 **URGENT**:
- Attorney requests response or signature within 14 days
- Beneficiary designation conflict identified
🟡 **STANDARD**:
- Document received / signed / filed confirmation
- Annual estate review reminder

## Briefing Format
Only include if there's an estate event or action item.

---

## Digital Estate Inventory (I-08)

> **Ref: specs/improve.md I-08** — The filled-in version lives in `state/estate.md.age`
> (encrypted, gitignored). This template defines the structure to maintain.
> The template itself uses placeholders only — never fill personal data here.

Maintain the following inventory in `state/estate.md` (encrypted at rest via vault):

### Legal Documents
| Document | Status | Location | Last Reviewed | Attorney |
|----------|--------|----------|---------------|----------|
| Will | [draft/signed/filed] | [physical location] | YYYY-MM-DD | [name] |
| Trust | [status] | [location] | YYYY-MM-DD | [name] |
| POA (Financial) | [status] | [location] | YYYY-MM-DD | [name] |
| POA (Healthcare) | [status] | [location] | YYYY-MM-DD | [name] |
| AHCD | [status] | [location] | YYYY-MM-DD | [name] |

### Password & Access Recovery
| System | Access Method | Recovery Location | Last Verified |
|--------|--------------|-------------------|---------------|
| 1Password Vault | Master password | [sealed envelope location] | YYYY-MM-DD |
| Email (primary) | Recovery email/phone | [documented where] | YYYY-MM-DD |
| Bank (primary) | Online banking | [recovery method] | YYYY-MM-DD |
| Crypto wallet | Seed phrase | [physical storage] | YYYY-MM-DD |

### Beneficiary Designations
| Account | Current Beneficiary | Last Updated | Institution |
|---------|--------------------|--------------|-------------|
| 401(k) | [name] | YYYY-MM-DD | [institution] |
| Life Insurance | [name] | YYYY-MM-DD | [provider] |
| IRA | [name] | YYYY-MM-DD | [institution] |

### Auto-Renewing Services (to cancel upon incapacitation)
| Service | Monthly Cost | Cancellation Method | Critical? |
|---------|-------------|---------------------|-----------|
| [service] | $[X] | [method] | yes/no |

### Emergency Contacts & Roles
| Role | Name | Phone | Relationship |
|------|------|-------|--------------|
| Executor | [name] | [phone] | [relation] |
| Backup executor | [name] | [phone] | [relation] |
| Attorney | [name] | [phone] | [firm] |
| Financial advisor | [name] | [phone] | [firm] |

### Periodic Review Rules
- **Quarterly**: Review auto-renewing services list
- **Annually**: Full estate inventory review, verify beneficiary designations
- **On life event** (marriage, birth, move, job change): Full review

**Stale document alert:** If `last_reviewed` on any legal document exceeds 12 months:
🟡 "Your [document] was last reviewed [N] months ago. Schedule a review."

If `last_reviewed` on Password & Access Recovery exceeds 6 months:
🟡 "Your emergency access procedures were last verified [N] months ago. Verify now."

**Note:** `state/estate.md.age` is encrypted. `make pii-scan` enforces that this
prompt file contains only placeholder values (never real personal data).
