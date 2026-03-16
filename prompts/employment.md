---
schema_version: "1.0"
domain: employment
priority: P3
sensitivity: elevated
last_updated: 2026-03-13T00:00:00
---
# Employment Domain Prompt

## Purpose
Track employment status, work benefits, HR communications, performance reviews,
compensation changes, PTO balances, and employer-related administrative matters.

## Sender Signatures (route here)
- `*@workday.com`, `*@adp.com`, `*@servicenow.com`
- Subject: HR, human resources, benefits enrollment, open enrollment
- Subject: performance review, compensation, salary, bonus, RSU, stock vest
- Subject: PTO, time off, leave, FMLA
- Subject: onboarding, offboarding, badge, building access
- Subject: training, compliance training, mandatory training
- Internal HR distribution lists

## Extraction Rules
For each employment email, extract:
1. **Topic** — benefits, compensation, HR admin, performance, PTO, training
2. **Action required** — enrollment deadline, review submission, training completion
3. **Deadline** — when is action due?
4. **Financial impact** — compensation change, benefit cost, RSU vest amount
5. **Status** — informational, action needed, urgent

## Alert Thresholds
🔴 **CRITICAL**:
- Employment status change (termination, layoff notice, role elimination)
- Benefits enrollment deadline within 48 hours
- Compliance training past due
- Immigration-linked employment change (affects visa status — cross-ref immigration domain)

🟠 **URGENT**:
- Open enrollment deadline within 7 days
- Performance review submission due within 7 days
- RSU vest date approaching (tax planning cross-ref with finance domain)
- PTO balance approaching use-it-or-lose-it deadline

🟡 **STANDARD**:
- Open enrollment period announced
- Compensation statement available
- Training due within 30 days
- PTO balance update

🔵 **LOW** (suppress from briefing unless user asks):
- Company-wide announcements (all-hands, town halls)
- General HR newsletters
- Routine building/badge access notifications

## Deduplication
- Unique key: topic + date + action
- Benefits enrollment: unique by enrollment period + plan type
- Training: unique by training name + due date

## State File Update Protocol
Read `state/employment.md` first. Then:
1. **Status**: Update current employment status and role
2. **Benefits**: Update benefits enrollment status, deadlines, elections
3. **Compensation**: Update salary, bonus, RSU vest schedule (amounts in finance domain)
4. **PTO**: Update accrued/used/remaining balances
5. **Reviews**: Track upcoming and past performance review dates
6. **Training**: Track required training and completion status

## PII Redaction
- Employee ID: keep only last 4 digits → `****[last4]`
- Salary/compensation: OK to store (needed for financial planning cross-ref)
- SSN on tax forms: `***-**-[last4]` (defer to finance domain)
- Keep: employer name, role title, dates, benefit plan names

## Briefing Format
```
### Employment
• [Topic]: [status/action] — [deadline if applicable]
• Benefits: [enrollment status] — [next deadline]
• PTO: [X days remaining] of [Y total]
```

## Cross-Domain Links
- **Immigration**: Employment changes MUST trigger immigration domain review (visa sponsorship dependency)
- **Finance**: RSU vests, bonus payouts, salary changes feed into finance domain
- **Insurance**: Employer-provided insurance tracked in insurance domain; employment changes trigger coverage review
- **Health**: Employer wellness programs, HSA/FSA balances

---
