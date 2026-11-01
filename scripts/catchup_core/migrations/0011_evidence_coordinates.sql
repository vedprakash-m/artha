-- Existing evidence used Python character coordinates. Never silently reinterpret history.
ALTER TABLE signals ADD COLUMN span_unit TEXT NOT NULL DEFAULT 'unicode-codepoint'
  CHECK(span_unit IN ('unicode-codepoint','utf8-byte'));

-- Durable provider cancellation intent. A crash cannot lose remaining evidence withdrawals.
CREATE TABLE calendar_cancellations(
  connector TEXT NOT NULL, account_scope TEXT NOT NULL, upstream_id TEXT NOT NULL,
  pending INTEGER NOT NULL CHECK(pending IN (0,1)),
  PRIMARY KEY(connector,account_scope,upstream_id));
