CREATE TABLE document_field_requirements(
  binding_id TEXT PRIMARY KEY,
  signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
  fact_id TEXT REFERENCES facts(fact_id) ON DELETE CASCADE,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  payload TEXT NOT NULL,
  key_version INTEGER NOT NULL CHECK(key_version > 0));
CREATE INDEX document_field_requirement_signal ON document_field_requirements(signal_id);
CREATE INDEX document_field_requirement_fact ON document_field_requirements(fact_id);

CREATE TABLE document_field_confirmations(
  binding_id TEXT PRIMARY KEY REFERENCES document_field_requirements(binding_id) ON DELETE CASCADE,
  signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
  state TEXT NOT NULL CHECK(state IN ('confirmed','withdrawn')),
  payload TEXT NOT NULL,
  key_version INTEGER NOT NULL CHECK(key_version > 0));
CREATE INDEX document_field_confirmation_signal ON document_field_confirmations(signal_id);

CREATE TABLE document_sources(
  signal_id TEXT PRIMARY KEY REFERENCES signals(signal_id) ON DELETE CASCADE,
  method TEXT NOT NULL CHECK(method IN ('text','ocr','unknown')));

CREATE TABLE document_field_links(
  fact_id TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
  binding_id TEXT NOT NULL REFERENCES document_field_requirements(binding_id) ON DELETE CASCADE,
  signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
  fact_version TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  domain TEXT NOT NULL,
  predicate TEXT NOT NULL,
  signal_version TEXT NOT NULL,
  provenance_hmac TEXT NOT NULL,
  span_start INTEGER NOT NULL,
  span_end INTEGER NOT NULL,
  scope TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  proof TEXT NOT NULL,
  PRIMARY KEY(fact_id,binding_id));

CREATE TABLE document_field_facts(
  fact_id TEXT PRIMARY KEY REFERENCES facts(fact_id) ON DELETE CASCADE);