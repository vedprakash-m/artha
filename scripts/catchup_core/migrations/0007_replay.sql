-- RA-22: a correction or dismissal becomes a replay case; this links the case to the Signals it holds
-- so that forgetting a Signal deletes the case bundle too (anti-resurrection, §7.9).
CREATE TABLE replay_cases(
  feedback_id TEXT NOT NULL REFERENCES feedback(feedback_id) ON DELETE CASCADE,
  signal_id TEXT NOT NULL,
  PRIMARY KEY(feedback_id, signal_id));
CREATE INDEX replay_cases_signal ON replay_cases(signal_id);
