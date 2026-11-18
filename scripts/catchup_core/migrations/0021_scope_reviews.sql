CREATE TABLE scope_reviews(
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_type TEXT NOT NULL CHECK(object_type IN ('signal','fact','fact_version')),
  object_id TEXT NOT NULL,
  object_version TEXT NOT NULL,
  old_scope TEXT CHECK(old_scope IS NULL OR old_scope IN ('personal','work','mixed','unknown','conflict')),
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
