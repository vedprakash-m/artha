-- Source times belong to a belief version. Unknown historical times stay unknown.
ALTER TABLE fact_versions ADD COLUMN observed_at TEXT;
ALTER TABLE fact_versions ADD COLUMN occurred_at TEXT;
UPDATE fact_versions SET
    observed_at=(SELECT observed_at FROM facts WHERE facts.fact_id=fact_versions.fact_id),
    occurred_at=(SELECT occurred_at FROM facts WHERE facts.fact_id=fact_versions.fact_id)
WHERE EXISTS(SELECT 1 FROM facts WHERE facts.fact_id=fact_versions.fact_id
             AND facts.recorded_at=fact_versions.recorded_at);
