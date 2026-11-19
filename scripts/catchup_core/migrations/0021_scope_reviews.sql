CREATE TABLE scope_reviews(
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_type TEXT NOT NULL CHECK(object_type IN ('signal','fact','fact_version')),
  object_id TEXT NOT NULL,
  object_version TEXT NOT NULL,
  old_scope TEXT CHECK(old_scope IS NULL OR old_scope IN ('personal','work','mixed','unknown')),
  new_scope TEXT NOT NULL CHECK(new_scope IN ('personal','work','mixed','unknown')),
  permission_generation INTEGER NOT NULL CHECK(permission_generation>=0),
  reviewer TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
  authz_digest TEXT NOT NULL
) STRICT;
CREATE INDEX scope_reviews_object ON scope_reviews(object_type,object_id,object_version);
CREATE TRIGGER scope_reviews_no_update BEFORE UPDATE ON scope_reviews
BEGIN SELECT RAISE(ABORT, 'scope reviews are append-only'); END;
CREATE TRIGGER scope_reviews_no_delete BEFORE DELETE ON scope_reviews
BEGIN SELECT RAISE(ABORT, 'scope reviews are append-only'); END;

DELETE FROM scope_bindings
WHERE binding_id IN (
  SELECT duplicate.binding_id
  FROM scope_bindings duplicate
  JOIN (
    SELECT object_type,object_id,object_version,permission_generation
    FROM scope_bindings
    GROUP BY object_type,object_id,object_version,permission_generation
    HAVING COUNT(*) > 1
  ) repeated
    ON duplicate.object_type=repeated.object_type
   AND duplicate.object_id=repeated.object_id
   AND duplicate.object_version=repeated.object_version
   AND duplicate.permission_generation=repeated.permission_generation
);
CREATE UNIQUE INDEX scope_bindings_unique
ON scope_bindings(object_type,object_id,object_version,permission_generation);

CREATE TABLE source_enrollments(
  enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_name TEXT NOT NULL,
  connector TEXT NOT NULL,
  provider_account_id TEXT NOT NULL,
  account_scope TEXT NOT NULL,
  query_digest TEXT NOT NULL,
  policy_digest TEXT NOT NULL,
  subjects_json TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('enroll','revoke')),
  permission_generation INTEGER NOT NULL CHECK(permission_generation>=0),
  scope_epoch INTEGER NOT NULL CHECK(scope_epoch>=0),
  proof TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  reviewed_at TEXT NOT NULL
) STRICT;
CREATE INDEX source_enrollments_current
ON source_enrollments(source_name,enrollment_id DESC);
CREATE TRIGGER source_enrollments_no_update BEFORE UPDATE ON source_enrollments
BEGIN SELECT RAISE(ABORT, 'source enrollments are append-only'); END;
CREATE TRIGGER source_enrollments_no_delete BEFORE DELETE ON source_enrollments
BEGIN SELECT RAISE(ABORT, 'source enrollments are append-only'); END;

CREATE TABLE signal_source_enrollments(
  signal_id TEXT NOT NULL PRIMARY KEY REFERENCES signals(signal_id),
  enrollment_id INTEGER NOT NULL REFERENCES source_enrollments(enrollment_id),
  metadata_marker TEXT NOT NULL
) STRICT;

CREATE TABLE coverage_holds(
  hold_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_name TEXT NOT NULL,
  connector TEXT NOT NULL,
  account_scope TEXT NOT NULL,
  provider_account_id TEXT NOT NULL,
  upstream_id TEXT NOT NULL,
  upstream_version TEXT NOT NULL,
  permission_generation INTEGER NOT NULL CHECK(permission_generation>=0),
  state TEXT NOT NULL CHECK(state IN ('held','admitted','excluded','tombstoned','deleted')),
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  UNIQUE(source_name,provider_account_id,upstream_id,upstream_version,permission_generation)
) STRICT;
CREATE INDEX coverage_holds_current
ON coverage_holds(source_name,state,last_seen);

ALTER TABLE connector_coverage
ADD COLUMN record_count INTEGER NOT NULL DEFAULT 0 CHECK(record_count>=0);

CREATE TABLE scope_admission_state(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  epoch INTEGER NOT NULL CHECK(epoch>=0),
  verified_migration INTEGER,
  receipt_digest TEXT
) STRICT;
INSERT INTO scope_admission_state(singleton,epoch,verified_migration,receipt_digest)
VALUES(1,0,NULL,NULL);
