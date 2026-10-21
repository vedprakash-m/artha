CREATE TABLE capability_evidence(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, capability TEXT NOT NULL, operation_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('last succeeded','degraded')), recorded_at TEXT NOT NULL,
  UNIQUE(capability,operation_id));
CREATE TABLE correction_notices(
  catchup_id TEXT NOT NULL, fact_id TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
  corrected_at TEXT NOT NULL, reason TEXT NOT NULL,
  PRIMARY KEY(catchup_id,fact_id,corrected_at));
CREATE TABLE goal_observations(
  goal_id TEXT PRIMARY KEY, payload_encrypted TEXT NOT NULL, key_version INTEGER NOT NULL,
  recorded_at TEXT NOT NULL);
ALTER TABLE alerts ADD COLUMN evidence_version TEXT;
