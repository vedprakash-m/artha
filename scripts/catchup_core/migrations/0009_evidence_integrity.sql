ALTER TABLE alerts ADD COLUMN dates_encrypted TEXT;
ALTER TABLE alerts ADD COLUMN dates_key_version INTEGER;
CREATE TABLE goal_observation_facts(
  goal_id TEXT NOT NULL REFERENCES goal_observations(goal_id) ON DELETE CASCADE,
  fact_id TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
  PRIMARY KEY(goal_id,fact_id));
-- Previous observations lack a reliable dependency map; recompute on demand.
DELETE FROM goal_observations;
