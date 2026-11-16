-- Owner-only immutable inputs. No FK into Analysis or Case databases.
CREATE TABLE behavior_observation_coverage (
  id UUID PRIMARY KEY,
  client_id UUID NOT NULL REFERENCES bank_clients(id),
  currency CHAR(3) NOT NULL CHECK(currency ~ '^[A-Z]{3}$'),
  direction TEXT NOT NULL CHECK(direction IN ('IN','OUT')),
  mode TEXT NOT NULL CHECK(mode IN ('DECLARED_COMPLETE','FIRST_SEEN_ONLY','UNKNOWN')),
  intervals JSONB NOT NULL CHECK(jsonb_typeof(intervals)='array'),
  declared_by_source TEXT NOT NULL,
  known_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  revision TEXT NOT NULL,
  UNIQUE(client_id,currency,direction,revision)
);

CREATE TABLE behavior_history_snapshots (
  id UUID PRIMARY KEY,
  request_key VARCHAR(128) NOT NULL UNIQUE,
  request_hash CHAR(64) NOT NULL,
  client_id UUID NOT NULL,
  currency CHAR(3) NOT NULL CHECK(currency ~ '^[A-Z]{3}$'),
  direction TEXT NOT NULL CHECK(direction IN ('IN','OUT')),
  window_start TIMESTAMPTZ NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  ingested_before TIMESTAMPTZ NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL,
  item_count INTEGER NOT NULL CHECK(item_count BETWEEN 0 AND 10000),
  digest CHAR(64) NOT NULL,
  coverage JSONB NOT NULL CHECK(jsonb_typeof(coverage)='object'),
  CHECK(window_start < as_of)
);

CREATE TABLE behavior_history_snapshot_items (
  snapshot_id UUID NOT NULL REFERENCES behavior_history_snapshots(id),
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  transaction_id UUID NOT NULL,
  facts JSONB NOT NULL CHECK(jsonb_typeof(facts)='object'),
  PRIMARY KEY(snapshot_id,ordinal),
  UNIQUE(snapshot_id,transaction_id)
);

CREATE FUNCTION reject_behavior_input_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Behavior inputs are immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER history_snapshot_immutable BEFORE UPDATE OR DELETE ON behavior_history_snapshots
  FOR EACH ROW EXECUTE FUNCTION reject_behavior_input_change();
CREATE TRIGGER history_items_immutable BEFORE UPDATE OR DELETE ON behavior_history_snapshot_items
  FOR EACH ROW EXECUTE FUNCTION reject_behavior_input_change();
CREATE TRIGGER coverage_immutable BEFORE UPDATE OR DELETE ON behavior_observation_coverage
  FOR EACH ROW EXECUTE FUNCTION reject_behavior_input_change();

CREATE FUNCTION validate_behavior_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE sid UUID; expected INTEGER; actual INTEGER; lo INTEGER; hi INTEGER;
BEGIN
  sid := (to_jsonb(NEW)->>(CASE WHEN TG_TABLE_NAME='behavior_history_snapshots' THEN 'id' ELSE 'snapshot_id' END))::uuid;
  SELECT item_count INTO expected FROM behavior_history_snapshots WHERE id=sid;
  SELECT count(*),min(ordinal),max(ordinal) INTO actual,lo,hi
    FROM behavior_history_snapshot_items WHERE snapshot_id=sid;
  IF expected IS NULL OR expected<>actual OR (actual>0 AND (lo<>0 OR hi<>actual-1)) THEN
    RAISE EXCEPTION 'Incomplete behavior history snapshot' USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER history_snapshot_complete AFTER INSERT ON behavior_history_snapshots
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_behavior_snapshot();
CREATE CONSTRAINT TRIGGER history_items_complete AFTER INSERT ON behavior_history_snapshot_items
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_behavior_snapshot();
