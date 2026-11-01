-- Explicit attribution is retained even for Signals that yield no Facts.
CREATE TABLE signal_access(
 signal_id TEXT PRIMARY KEY REFERENCES signals(signal_id) ON DELETE CASCADE,
 subjects TEXT NOT NULL CHECK(json_valid(subjects) AND json_type(subjects)='array'));
CREATE TABLE source_access(
 connector TEXT NOT NULL,account_scope TEXT NOT NULL,
 subjects TEXT NOT NULL CHECK(json_valid(subjects) AND json_type(subjects)='array'),
 PRIMARY KEY(connector,account_scope));
CREATE TABLE corpus_cases(
 case_id TEXT PRIMARY KEY,signal_id TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE);
CREATE INDEX corpus_case_signal ON corpus_cases(signal_id);
-- Frozen returned-output evidence is distinct from human labels.
CREATE TABLE catchup_evaluations(
 catchup_id TEXT PRIMARY KEY,payload TEXT NOT NULL,key_version INTEGER NOT NULL,created_at TEXT NOT NULL);
