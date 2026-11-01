-- Pipeline working set: extracted candidates (encrypted) and the Briefing publication record.

CREATE TABLE candidates(
  signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  payload_encrypted TEXT NOT NULL, key_version INTEGER NOT NULL,
  PRIMARY KEY(signal_id, idx));

CREATE TABLE briefings(
  catchup_id TEXT NOT NULL, version INTEGER NOT NULL, text_hmac TEXT NOT NULL,
  published_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('published','superseded')),
  PRIMARY KEY(catchup_id, version));
