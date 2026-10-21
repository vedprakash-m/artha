CREATE TABLE calendar_records(
  connector TEXT NOT NULL, account_scope TEXT NOT NULL, upstream_id TEXT NOT NULL,
  signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
  payload_encrypted TEXT NOT NULL, key_version INTEGER NOT NULL,
  PRIMARY KEY(connector,account_scope,upstream_id));
