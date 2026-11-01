-- Estimated tokens are recorded with each admission (a size-based estimate, never provider usage).
ALTER TABLE resource_admissions ADD COLUMN estimated_tokens INTEGER NOT NULL DEFAULT 0
  CHECK(typeof(estimated_tokens)='integer' AND estimated_tokens BETWEEN 0 AND 2147483647);
UPDATE resource_admissions SET estimated_tokens=(prompt_bytes+3)/4;

-- Actual provider usage is reported later and stored apart from the estimate. A receipt never changes.
CREATE TABLE resource_usage(
  catchup_id TEXT NOT NULL,
  request_key TEXT NOT NULL,
  input_tokens INTEGER NOT NULL CHECK(typeof(input_tokens)='integer' AND input_tokens BETWEEN 0 AND 2147483647),
  output_tokens INTEGER NOT NULL CHECK(typeof(output_tokens)='integer' AND output_tokens BETWEEN 0 AND 2147483647),
  source TEXT NOT NULL CHECK(length(source) BETWEEN 1 AND 512 AND length(trim(source))>0),
  reported_at TEXT NOT NULL CHECK(length(reported_at)>0),
  PRIMARY KEY(catchup_id,request_key),
  FOREIGN KEY(catchup_id,request_key) REFERENCES resource_admissions(catchup_id,request_key) ON DELETE CASCADE
) STRICT;
CREATE TRIGGER resource_usage_immutable BEFORE UPDATE ON resource_usage
BEGIN SELECT RAISE(ABORT,'usage receipt is immutable'); END;

-- A monotonic admission counter. A copy of it is sealed outside the restorable set, so a restored
-- older ledger can be detected and its lost spend counted as uncertain instead of refunded.
CREATE TABLE resource_state(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  serial INTEGER NOT NULL CHECK(typeof(serial)='integer' AND serial>=0)
) STRICT;
INSERT INTO resource_state SELECT 1,count(*) FROM resource_admissions;

CREATE TABLE resource_uncertain_spend(
  spend_id INTEGER PRIMARY KEY AUTOINCREMENT,
  month_utc TEXT NOT NULL CHECK(length(month_utc)=7 AND month_utc GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]' AND substr(month_utc,6,2) BETWEEN '01' AND '12'),
  requests INTEGER NOT NULL CHECK(typeof(requests)='integer' AND requests>=0),
  tokens INTEGER NOT NULL CHECK(typeof(tokens)='integer' AND tokens>=0),
  reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 512),
  recorded_at TEXT NOT NULL CHECK(length(recorded_at)>0)
) STRICT;
CREATE INDEX resource_uncertain_month ON resource_uncertain_spend(month_utc);
