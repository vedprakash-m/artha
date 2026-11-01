-- Detect migration/source/schema drift; this is not an independent authority or a same-user tamper-proof anchor.
CREATE TABLE schema_history (
    number INTEGER PRIMARY KEY CHECK(number >= 1),
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK(provenance IN ('applied', 'legacy_schema_verified'))
);
