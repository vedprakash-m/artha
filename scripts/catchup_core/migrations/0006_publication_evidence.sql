CREATE TABLE briefing_signals(
  catchup_id TEXT NOT NULL,
  signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
  PRIMARY KEY(catchup_id,signal_id));
