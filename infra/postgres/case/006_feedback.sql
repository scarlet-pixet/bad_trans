CREATE TABLE outbox_events(
 id UUID PRIMARY KEY,aggregate_id UUID NOT NULL,event_type TEXT NOT NULL,payload JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL,published_at TIMESTAMPTZ,attempts INTEGER NOT NULL CHECK(attempts>=0));
CREATE TABLE case_feedback_publications(
 case_id UUID PRIMARY KEY REFERENCES review_cases(id),case_version BIGINT NOT NULL CHECK(case_version>0),
 event_id UUID NOT NULL UNIQUE REFERENCES outbox_events(id),payload_hash CHAR(64) NOT NULL);
CREATE FUNCTION protect_feedback_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.id IS DISTINCT FROM OLD.id OR NEW.aggregate_id IS DISTINCT FROM OLD.aggregate_id OR
 NEW.event_type IS DISTINCT FROM OLD.event_type OR NEW.payload IS DISTINCT FROM OLD.payload OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
  RAISE EXCEPTION 'Outbox event content is immutable' USING ERRCODE='23514';
 END IF; RETURN NEW;
END $$;
CREATE TRIGGER case_outbox_immutable BEFORE UPDATE ON outbox_events FOR EACH ROW EXECUTE FUNCTION protect_feedback_outbox();
CREATE FUNCTION protect_case_decision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Decisions are immutable; reopen and decide in a new cycle' USING ERRCODE='23514'; END $$;
CREATE TRIGGER case_decision_immutable BEFORE UPDATE OR DELETE ON case_decisions FOR EACH ROW EXECUTE FUNCTION protect_case_decision();
