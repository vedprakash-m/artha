-- Ledger schema v1 (re-artha Appendix A, confirmed by ADR storage-ledger).
-- IDs: <prefix>_<first 16 hex of keyed hash>; timestamps are ISO-8601 text (UTC).
-- Every connection sets PRAGMA foreign_keys=ON; schema version is PRAGMA user_version.

CREATE TABLE signals(
  signal_id TEXT PRIMARY KEY,
  connector TEXT NOT NULL, account_scope TEXT NOT NULL,
  upstream_id TEXT NOT NULL, upstream_version TEXT NOT NULL,
  occurred_at TEXT, observed_at TEXT NOT NULL,
  confidentiality TEXT NOT NULL CHECK(confidentiality IN ('ordinary','sensitive','restricted')),
  content_hmac TEXT NOT NULL, evidence_blob_ref TEXT NOT NULL,
  processing_state TEXT NOT NULL CHECK(processing_state IN
    ('discovered','fetched','decoded','normalized','extracted','reconciled','excluded')),
  exclusion_reason TEXT,
  UNIQUE(connector, account_scope, upstream_id, upstream_version));
CREATE INDEX signals_state ON signals(processing_state);

CREATE TABLE facts(
  fact_id TEXT PRIMARY KEY, domain TEXT NOT NULL, predicate TEXT NOT NULL,
  typed_value TEXT NOT NULL CHECK(value_encrypted = 1 OR json_valid(typed_value)),
  value_encrypted INTEGER NOT NULL DEFAULT 0 CHECK(value_encrypted IN (0,1)),
  key_version INTEGER, primary_subject_id TEXT NOT NULL,
  confidentiality TEXT NOT NULL CHECK(confidentiality IN ('ordinary','sensitive','restricted')),
  valid_from TEXT, valid_to TEXT, validity_unknown INTEGER NOT NULL DEFAULT 0,
  occurred_at TEXT, observed_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
  provenance_class TEXT NOT NULL CHECK(provenance_class IN
    ('verified_source','user_confirmed','legacy_unverified','disputed','model_hypothesis')),
  status TEXT NOT NULL CHECK(status IN ('current','superseded','disputed','unknown','retracted')),
  rule_version TEXT,
  CHECK(value_encrypted = 1 OR confidentiality <> 'restricted'),
  CHECK(value_encrypted = 0 OR key_version IS NOT NULL),
  CHECK(valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from));
CREATE INDEX facts_lookup ON facts(domain, predicate, status);

CREATE TABLE fact_subjects(
  fact_id TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE, subject_id TEXT NOT NULL, role TEXT,
  PRIMARY KEY(fact_id, subject_id));
CREATE TABLE fact_viewers(
  fact_id TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE, viewer_id TEXT NOT NULL,
  PRIMARY KEY(fact_id, viewer_id));
CREATE TABLE fact_versions(
  fact_id TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE, recorded_at TEXT NOT NULL,
  value_encrypted INTEGER NOT NULL, key_version INTEGER, valid_from TEXT, valid_to TEXT,
  validity_unknown INTEGER NOT NULL, provenance_class TEXT NOT NULL, evidence_digest TEXT NOT NULL,
  typed_value TEXT NOT NULL, status TEXT NOT NULL, rule_version TEXT,
  PRIMARY KEY(fact_id, recorded_at));
CREATE TABLE fact_evidence(
  fact_id TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
  signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
  span_start INTEGER NOT NULL, span_end INTEGER NOT NULL CHECK(span_end >= span_start),
  PRIMARY KEY(fact_id, signal_id, span_start));

CREATE TABLE alerts(
  alert_id TEXT PRIMARY KEY, obligation_key TEXT NOT NULL UNIQUE, domain TEXT NOT NULL,
  primary_subject_id TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('P1','P2','P3','P4')),
  deadline TEXT, latest_useful_action TEXT,
  status TEXT NOT NULL CHECK(status IN ('open','acknowledged','snoozed','resolved')),
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, suppressed_until TEXT,
  reopen_condition TEXT, resolution_ref TEXT);
CREATE TABLE alert_subjects(
  alert_id TEXT NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE, subject_id TEXT NOT NULL, role TEXT,
  PRIMARY KEY(alert_id, subject_id));
CREATE TABLE alert_viewers(
  alert_id TEXT NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE, viewer_id TEXT NOT NULL,
  PRIMARY KEY(alert_id, viewer_id));
CREATE TABLE alert_facts(
  alert_id TEXT NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE,
  fact_id TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
  PRIMARY KEY(alert_id, fact_id));

CREATE TABLE open_items(
  item_id TEXT PRIMARY KEY, owner TEXT NOT NULL, deadline TEXT,
  state TEXT NOT NULL CHECK(state IN ('open','done','deferred','blocked','not_proceeding')),
  description_encrypted TEXT NOT NULL, key_version INTEGER NOT NULL,
  source_alert_id TEXT REFERENCES alerts(alert_id) ON DELETE SET NULL,
  resolution_evidence TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  legacy_source_ref TEXT);

CREATE TABLE consent(
  consent_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, grantor_id TEXT NOT NULL,
  scope TEXT NOT NULL, operations TEXT NOT NULL, audience TEXT NOT NULL,
  granted_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT,
  revocation_version INTEGER NOT NULL DEFAULT 0);
CREATE INDEX consent_subject ON consent(subject_id);

CREATE TABLE tombstones(
  tombstone_id TEXT PRIMARY KEY, connector TEXT, account_scope TEXT, upstream_id TEXT,
  content_hmac TEXT, created_at TEXT NOT NULL, reason TEXT);
CREATE INDEX tombstones_key ON tombstones(connector, account_scope, upstream_id);
CREATE INDEX tombstones_hmac ON tombstones(content_hmac);

CREATE TABLE deletion_journal(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, tombstone_id TEXT NOT NULL, requested_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('requested','sealed','denied','deleted','receipted')),
  detail TEXT);
CREATE TABLE deletion_checkpoint(
  checkpoint_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
  tombstone_digest TEXT NOT NULL, permission_generation INTEGER NOT NULL);

CREATE TABLE stage_journal(
  catchup_id TEXT NOT NULL, stage TEXT NOT NULL, input_versions TEXT NOT NULL, status TEXT NOT NULL,
  artifact_ids TEXT NOT NULL, failure_class TEXT, updated_at TEXT NOT NULL,
  PRIMARY KEY(catchup_id, stage));

CREATE TABLE connector_coverage(
  connector TEXT NOT NULL, account_scope TEXT NOT NULL, requested_from TEXT, requested_to TEXT,
  cursor_generation TEXT, completed_at TEXT, status TEXT NOT NULL, blind_spots TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY(connector, account_scope));

CREATE TABLE signal_failures(
  signal_id TEXT NOT NULL, stage TEXT NOT NULL, error_class TEXT NOT NULL,
  first_attempt TEXT NOT NULL, last_attempt TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1,
  retry_eligible INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(signal_id, stage));

CREATE TABLE authority_generation(
  generation INTEGER PRIMARY KEY, host TEXT NOT NULL, promoted_at TEXT NOT NULL, former_host_revoked_at TEXT);
