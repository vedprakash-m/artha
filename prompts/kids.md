---
schema_version: "1.0"
domain: kids
priority: P0
sensitivity: medium
last_updated: 2026-03-07T22:52:33
---
# Kids Domain Prompt

## Purpose
Track academic progress, school activities, extracurriculars, appointments, and
important dates for children defined in §1 (primary family members).
Flag academic issues early. Surface scheduling needs for parents.

## Sender Signatures (route here)
- Family's school domains (defined in `user_profile.yaml` under each child's `school.email_domain`) — update from first email received
- `*@schoology.com` or school arts program domain
- `*@schoology.com`, `*@powerschool.com`, ParentSquare, Remind, Talking Points
- Subject: grade, assignment, absent, tardy, attendance, missing work, progress report
- Subject: AP, SAT, ACT, PSAT, college application, Common App, scholarship
- Subject: soccer, practice, game, piano, recital, concert, performance
- Subject: orthodontist, pediatrician, vaccination (for kids)
- Subject: PTA, parent meeting, Back to School, school event, spring pictures, field trip
- Subject: report card, transcript, GPA, cumulative

## Extraction Rules
For each kids email, extract:
1. **Child**: which family child? (defined in §1)
2. **Category**: academic / extracurricular / health / admin / event
3. **Item**: what specifically happened?
4. **Urgency**: is there an action or deadline for parents?
5. **Date**: when? (assignment due, event date, appointment)

## Alert Thresholds
🔴 **CRITICAL**:
- Attendance: absence or multiple tardies in one week
- Grade below C on any assignment/test (AP/advanced courses especially)
- College application deadline within 30 days (for older child)
- Missing assignment that will affect grade

🟠 **URGENT**:
- Test/quiz coming up within 3 days — so parent can check in
- School event requiring parent presence (no RSVP yet)
- Kids medical appointment needs scheduling
- SAT/ACT registration deadline approaching (for older children)

🟡 **STANDARD**:
- Returned assignment with grade (above C)
- School calendar update (semester dates, breaks)
- ParentSquare general announcements
- Soccer/piano schedule update
- Report card received

🔵 **LOW** (note but don't surface unless queried):
- Fundraiser, spirit day, club announcements
- General school newsletters
- Marketing from schools

## Deduplication
- Academic items: unique key = assignment name + class + due date
- Events: unique key = event name + date
- If same item received from multiple sources (Schoology + ParentSquare), merge into one entry

## State File Update Protocol
Read `state/kids.md` first. Then update per child (one section per child defined in §1):
1. **Per-child academic section**: Update academic alerts, upcoming deadlines
2. **Shared Calendar**: Add school events with date + who it affects
3. Archive completed items (tests passed, events attended)
4. Keep last 30 days of activity inline; move older to archive

## Parent Action Triggers
- Create Action Proposal if email requires parent response (RSVP, permission slip, payment)
- Create Action Proposal for appointment scheduling if health item mentioned
- Flag college prep items for dedicated /domain kids review

## Briefing Format
```
### Kids
[Per-child sections based on §1 family definitions]
• Shared: [school event date + who]
```

## Important Context
- Older children may be in college prep mode — AP exams, SAT/ACT, college visits matter if applicable
- Younger children — grade stability and after-school activities are primary focus
- Both kids have after-school activities that create scheduling needs for parents
- Family cultural context (§1): academic performance priority and family values apply
- ParentSquare is primary school communication platform — do not filter as spam

---

## Leading Indicators

> **Purpose (TS §6.1):** Early warning signals for academic or extracurricular drift before grades or test scores suffer. Surfaced in briefing when trending unfavorably.

```yaml
leading_indicators:

  assignment_completion_rate:
    description: "% of announced assignments/projects completed on time (per quarter)"
    source: kids.md — derived from email signals: missing_assignments, late_work notices
    target: "≥ 95% on-time completion"
    alert_yellow: "1 missing assignment in rolling 30 days"
    alert_red: "2+ missing assignments OR teacher flagged concern"
    per_child: true
    briefing_trigger: "yellow or red"

  gpa_trend:
    description: "GPA trajectory quarter-over-quarter"
    source: kids.md — academic.gpa_history
    target: "Stable or improving"
    alert_yellow: "GPA dropped > 0.3 points quarter-over-quarter"
    alert_red: "GPA dropped > 0.5 points OR below 3.0"
    per_child: true
    briefing_trigger: "yellow or red"

  test_score_trajectory:
    description: "SAT/ACT practice score trend (college-bound child only)"
    source: kids.md — academic.sat_scores[]
    target: "Positive or stable trend toward target score"
    alert_yellow: "Score plateau for 2+ practice tests"
    alert_red: "Score declining trend"
    applies_to: [college_bound_child]  # child with college_prep: true in profile
    briefing_trigger: "yellow or red; surface 90 days before exam"

  college_timeline_adherence:
    description: "College prep milestones completed on schedule (college-bound child)"
    source: kids.md — college_prep.milestones[]
    target: "All milestones on schedule"
    alert_yellow: "1 milestone approaching deadline (< 14 days) with no action"
    alert_red: "Missed deadline OR application window closing within 7 days"
    applies_to: [college_bound_child]  # child with college_prep: true in profile
    briefing_trigger: "always surface in SAT/college season (Aug–Jan)"

  extracurricular_engagement:
    description: "Consistency in after-school activities"
    source: kids.md — activities[].attendance_pattern
    target: "No unexplained absences in quarter"
    alert_yellow: "2+ unexplained absences in rolling 30 days"
    briefing_trigger: "yellow only"
```

**Leading indicator summary line (in briefing):**
```
👨‍👧‍👦 Kids Leading: [child1] — Assignments [✓/⚠] GPA [▲▼] [SAT score/trend if applicable] | [child2] — Assignments [✓/⚠] GPA [▲▼]
```

---

## Phase 2B Expansions

### Canvas LMS Integration
When `canvas_fetch.py` runs (during preflight or catch-up Step 4), its output updates the
`## Canvas Academic Data` section in `state/kids.md` automatically. The domain prompt should:
1. Read the Canvas section for assignment due dates and recent grades
2. Cross-reference approaching deadlines with open_items.md
3. Surface assignments due in ≤3 days as 🟠 Urgent or 🟡 Standard

### Paid Enrichment Tracker (F4.8, F4.9)
Track after-school and enrichment activities + costs. Maintain in `state/kids.md → activities`:
```yaml
activities:
  - person: "[child name from §1]"
    name: "[activity name]"
    provider: "[organization/instructor]"
    type: sports|music|academic|arts|STEM|other
    frequency: "[e.g., weekly, twice weekly]"
    schedule: "[day/time]"
    season: "[fall|spring|summer|year-round]"
    monthly_cost: XXXX
    semester_cost: XXXX
    payment_due: YYYY-MM-DD
    auto_renew: true|false
    notes: ""
```

**Semester cost summary** (generate at semester start — August and January):
Aggregate costs: `total_activities_spend = sum of all semester_cost values`
Surface in briefing: "Activities for [semester]: [N activities] — $[total] total semester cost"
Cross-reference with finance.md for budget alignment.

**Alert thresholds:**
🟡 STANDARD: Activity payment due ≤7 days → remind in finance section
🟡 STANDARD: New season registration emails → surface opportunity + cost

### College Application Countdown (F4.11 — for child with `college_prep: true` in profile)
Track in `state/kids.md → college_prep.milestones`. Reference the child's `class_of` year from profile.
Milestone dates are examples — populate from the child's actual college prep plan:
```yaml
college_prep:
  target_graduation: YYYY  # from profile: family.children[].milestones.class_of
  milestones:
    - name: "PSAT"
      target_date: YYYY-MM-DD
      status: pending     # pending|done|scheduled
      notes: ""
      color: amber        # green|amber|red (auto-computed by Artha)
    - name: "SAT (first attempt)"
      target_date: YYYY-MM-DD
      status: pending
      notes: ""
      color: amber
    - name: "Campus visits"
      target_date: YYYY-MM-DD
      status: pending
      notes: ""
      color: green
    - name: "College essay drafts"
      target_date: YYYY-MM-DD
      status: pending
      notes: ""
      color: green
    - name: "Early Decision applications"
      target_date: YYYY-MM-DD
      status: pending
      notes: ""
      color: green
    - name: "Regular Decision applications"
      target_date: YYYY-MM-DD
      status: pending
      notes: ""
      color: green
    - name: "Decisions received"
      target_date: YYYY-MM-DD
      status: pending
      notes: ""
      color: green
```

**Color computation rules:**
- `green`: target_date > 60 days away AND status != done
- `amber`: target_date ≤ 60 days away AND status == pending → 🟡 Standard alert
- `red`: target_date ≤ 14 days AND status == pending → 🟠 Urgent alert
- `done` (any): shown with ✅ prefix

**Briefing display** (surfaces automatically when milestone within 60 days):
```
🎓 College Countdown ([child name] — Class of [year]):
  🟠 [milestone]: [N] days away — action needed
  🟡 [milestone]: [N] days away — start preparing
  ✅ [completed milestone]
```
Show year-round but highlight during Aug–Jan application season.

**SAT prep tracking:**
```yaml
sat_scores:
  - date: YYYY-MM-DD
    type: practice|official
    score: XXXX
    section_math: XXXX
    section_reading: XXXX
    notes: ""
target_score: 1400    # adjustable
```
Surface trend: "SAT trend: [score1] → [score2] → [score3] [↑↓→] (target: [N])"
