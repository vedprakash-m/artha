-- A persisted Fact-set generation detects additions/removals as well as edits to selected evidence.
CREATE TABLE fact_generation(singleton INTEGER PRIMARY KEY CHECK(singleton=1),generation INTEGER NOT NULL);
INSERT INTO fact_generation VALUES(1,0);
CREATE TRIGGER fact_generation_insert AFTER INSERT ON facts BEGIN
  UPDATE fact_generation SET generation=generation+1 WHERE singleton=1;
END;
CREATE TRIGGER fact_generation_update AFTER UPDATE ON facts BEGIN
  UPDATE fact_generation SET generation=generation+1 WHERE singleton=1;
END;
CREATE TRIGGER fact_generation_delete AFTER DELETE ON facts BEGIN
  UPDATE fact_generation SET generation=generation+1 WHERE singleton=1;
END;
