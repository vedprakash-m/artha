CREATE TABLE feedback(
  feedback_id TEXT PRIMARY KEY, obligation_key TEXT NOT NULL,
  response TEXT NOT NULL CHECK(response IN ('useful','dismiss','snooze','acknowledge','correct')),
  reason TEXT NOT NULL, actor TEXT NOT NULL, recorded_at TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'unreviewed');
CREATE TABLE briefing_facts(
  catchup_id TEXT NOT NULL, fact_id TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
  PRIMARY KEY(catchup_id,fact_id));
