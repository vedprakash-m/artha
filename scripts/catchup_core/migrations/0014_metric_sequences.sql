-- A missing/torn telemetry append must not turn a resumed Catch-up into a measured pass.
-- Allocate before the invocation starts; the optional metrics sink remains non-authoritative.
CREATE TABLE metric_sequences (
    catchup_id TEXT NOT NULL,
    implementation_version TEXT NOT NULL,
    last_sequence INTEGER NOT NULL CHECK(last_sequence >= 1),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (catchup_id, implementation_version)
);
CREATE INDEX metric_sequence_window ON metric_sequences(implementation_version, updated_at DESC);
