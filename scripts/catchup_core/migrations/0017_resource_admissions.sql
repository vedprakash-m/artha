CREATE TABLE resource_admissions(
  catchup_id TEXT NOT NULL CHECK(length(catchup_id) BETWEEN 1 AND 512),
  request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 1 AND 512),
  signal_id TEXT REFERENCES signals(signal_id) ON DELETE SET NULL,
  prompt_bytes INTEGER NOT NULL CHECK(typeof(prompt_bytes)='integer' AND prompt_bytes BETWEEN 0 AND 2147483647),
  month_utc TEXT NOT NULL CHECK(length(month_utc)=7 AND month_utc GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]' AND substr(month_utc,6,2) BETWEEN '01' AND '12'),
  admitted_at TEXT NOT NULL CHECK(length(admitted_at)>0),
  bundle_id TEXT NOT NULL CHECK(length(bundle_id) BETWEEN 1 AND 512 AND length(trim(bundle_id))>0),
  PRIMARY KEY(catchup_id,request_key)
) STRICT;
CREATE INDEX resource_admissions_month ON resource_admissions(month_utc);
CREATE INDEX resource_admissions_signal ON resource_admissions(signal_id,catchup_id);
CREATE INDEX resource_admissions_bundle ON resource_admissions(catchup_id,bundle_id);

CREATE TABLE resource_cache(
  catchup_id TEXT NOT NULL,
  request_key TEXT NOT NULL,
  signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
  output_encrypted TEXT NOT NULL CHECK(length(output_encrypted)>0),
  key_version INTEGER NOT NULL CHECK(key_version BETWEEN 1 AND 4294967295),
  cached_at TEXT NOT NULL CHECK(length(cached_at)>0),
  PRIMARY KEY(catchup_id,request_key),
  FOREIGN KEY(catchup_id,request_key) REFERENCES resource_admissions(catchup_id,request_key) ON DELETE CASCADE
) STRICT;
CREATE TRIGGER resource_cache_signal_insert BEFORE INSERT ON resource_cache
WHEN NOT EXISTS(SELECT 1 FROM resource_admissions WHERE catchup_id=NEW.catchup_id
  AND request_key=NEW.request_key AND signal_id=NEW.signal_id)
BEGIN SELECT RAISE(ABORT,'cache admission Signal mismatch'); END;
CREATE TRIGGER resource_cache_signal_update BEFORE UPDATE ON resource_cache
WHEN NOT EXISTS(SELECT 1 FROM resource_admissions WHERE catchup_id=NEW.catchup_id
  AND request_key=NEW.request_key AND signal_id=NEW.signal_id)
BEGIN SELECT RAISE(ABORT,'cache admission Signal mismatch'); END;

CREATE TABLE resource_cancellations(
  catchup_id TEXT PRIMARY KEY CHECK(length(catchup_id) BETWEEN 1 AND 512),
  actor TEXT NOT NULL CHECK(length(actor) BETWEEN 1 AND 512)
) STRICT;
CREATE TABLE resource_scheduler(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  cursor INTEGER NOT NULL CHECK(cursor>=0),
  priority_streak INTEGER NOT NULL CHECK(priority_streak>=0),
  last_connector TEXT NOT NULL,
  last_account_scope TEXT NOT NULL
) STRICT;
INSERT INTO resource_scheduler VALUES(1,0,0,'','');
CREATE TABLE resource_schedule_signals(
  signal_id TEXT PRIMARY KEY REFERENCES signals(signal_id) ON DELETE CASCADE,
  last_selected INTEGER NOT NULL CHECK(last_selected>0)
) STRICT;
CREATE INDEX resource_schedule_cursor ON resource_schedule_signals(last_selected);
