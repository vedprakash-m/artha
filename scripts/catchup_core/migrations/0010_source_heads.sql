-- A unique observed version selects candidates; complete observation is required for withdrawal.
CREATE TABLE source_heads(
  connector TEXT NOT NULL, account_scope TEXT NOT NULL, upstream_id TEXT NOT NULL,
  signal_id TEXT REFERENCES signals(signal_id) ON DELETE CASCADE,
  complete INTEGER NOT NULL CHECK(complete IN (0,1)),
  PRIMARY KEY(connector,account_scope,upstream_id));
CREATE INDEX source_heads_signal ON source_heads(signal_id);
