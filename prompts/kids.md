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
important dates for Parth (17, 11th grade) and Trisha (12, 7th grade).
Flag academic issues early. Surface scheduling needs for parents.

## Sender Signatures (route here)
- `*@seattleschools.org` (or actual school domain — update from first email received)
- `*@washingtonarts.org` or school arts program domain
- `*@schoology.com`, `*@powerschool.com`, ParentSquare, Remind, Talking Points
- Subject: grade, assignment, absent, tardy, attendance, missing work, progress report
- Subject: AP, SAT, ACT, PSAT, college application, Common App, scholarship
- Subject: soccer, practice, game, piano, recital, concert, performance
- Subject: orthodontist, pediatrician, vaccination (for kids)
- Subject: PTA, parent meeting, Back to School, school event, spring pictures, field trip
- Subject: report card, transcript, GPA, cumulative

## Extraction Rules
For each kids email, extract:
1. **Child**: Parth or Trisha (or both)?
2. **Category**: academic / extracurricular / health / admin / event
3. **Item**: what specifically happened?
4. **Urgency**: is there an action or deadline for parents?
5. **Date**: when? (assignment due, event date, appointment)

## Alert Thresholds
🔴 **CRITICAL**:
- Attendance: absence or multiple tardies in one week
- Grade below C on any assignment/test for Parth (AP courses especially)
- Parth: any college application deadline within 30 days
- Missing assignment that will affect grade

🟠 **URGENT**:
- Test/quiz coming up within 3 days — so parent can check in
- School event requiring parent presence (no RSVP yet)
- Kids medical appointment needs scheduling
- Parth SAT/ACT registration deadline approaching

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
Read `state/kids.md` first. Then:
1. **Parth section**: Update academic alerts, upcoming deadlines
2. **Trisha section**: Update academic alerts, upcoming deadlines
3. **Shared Calendar**: Add school events with date + who it affects
4. Archive completed items (tests passed, events attended)
5. Keep last 30 days of activity inline; move older to archive

## Parent Action Triggers
- Create Action Proposal if email requires parent response (RSVP, permission slip, payment)
- Create Action Proposal for appointment scheduling if health item mentioned
- Flag college prep items for dedicated /domain kids review

## Briefing Format
```
### Kids
**Parth**: [bullet per item — grade, deadline, event]
**Trisha**: [bullet per item — grade, deadline, event]
• Shared: [school event date + who]
```

## Important Context
- Parth is likely in college prep mode — AP exams, SAT/ACT, college visits matter
- Trisha is in middle school — grade stability and after-school activities are primary focus
- Both kids have after-school activities that create scheduling needs for parents
- Indian-American family context: academic performance is high priority
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
    description: "SAT/ACT practice score trend (Parth only)"
    source: kids.md — academic.sat_scores[]
    target: "Positive or stable trend toward target score"
    alert_yellow: "Score plateau for 2+ practice tests"
    alert_red: "Score declining trend"
    applies_to: [Parth]
    briefing_trigger: "yellow or red; surface 90 days before exam"

  college_timeline_adherence:
    description: "College prep milestones completed on schedule (Parth)"
    source: kids.md — college_prep.milestones[]
    target: "All milestones on schedule"
    alert_yellow: "1 milestone approaching deadline (< 14 days) with no action"
    alert_red: "Missed deadline OR application window closing within 7 days"
    applies_to: [Parth]
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
👨‍👧‍👦 Kids Leading: Parth — Assignments [✓/⚠] GPA [▲▼] SAT [score/trend] | Trisha — Assignments [✓/⚠] GPA [▲▼]
```
