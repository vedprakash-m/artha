ALTER TABLE facts ADD COLUMN obligation_key TEXT NOT NULL DEFAULT '';
CREATE INDEX facts_obligation ON facts(domain,predicate,primary_subject_id,obligation_key,status);
ALTER TABLE connector_coverage ADD COLUMN catchup_id TEXT;
ALTER TABLE connector_coverage ADD COLUMN checked_at TEXT;
CREATE TABLE briefing_drafts(
  catchup_id TEXT PRIMARY KEY,
  payload_encrypted TEXT NOT NULL,
  key_version INTEGER NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE fact_version_evidence(
  fact_id TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
  recorded_at TEXT NOT NULL,
  signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
  span_start INTEGER NOT NULL, span_end INTEGER NOT NULL,
  PRIMARY KEY(fact_id,recorded_at,signal_id,span_start));
