ALTER TABLE facts ADD COLUMN scope_hmac TEXT;
ALTER TABLE fact_versions ADD COLUMN scope_hmac TEXT;

CREATE TABLE scope_bindings(
  binding_id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_type TEXT NOT NULL CHECK(object_type IN ('signal','fact','fact_version','curated')),
  object_id TEXT NOT NULL,
  object_version TEXT NOT NULL,
  source_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  content_hmac TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope IN ('personal','work','mixed','unknown')),
  provenance TEXT NOT NULL CHECK(provenance IN ('source_review','trusted_writer')),
  permission_generation INTEGER NOT NULL CHECK(permission_generation>=0),
  proof TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;
CREATE INDEX scope_bindings_object ON scope_bindings(object_type,object_id,object_version);
