CREATE TABLE feedback_revision_state(id INTEGER PRIMARY KEY CHECK(id=1),revision BIGINT NOT NULL CHECK(revision>=0));
INSERT INTO feedback_revision_state VALUES (1,0);
CREATE TABLE feedback_journal(
 knowledge_revision BIGINT PRIMARY KEY CHECK(knowledge_revision>0),event_id UUID NOT NULL UNIQUE,
 case_id UUID NOT NULL,transaction_id UUID NOT NULL,client_id UUID NOT NULL,
 case_version BIGINT NOT NULL CHECK(case_version>0),payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
 payload_hash CHAR(64) NOT NULL,applied BOOLEAN NOT NULL,learned_at TIMESTAMPTZ NOT NULL);
CREATE INDEX feedback_journal_selection ON feedback_journal(transaction_id,knowledge_revision DESC) WHERE applied;
CREATE INDEX feedback_journal_owner_version ON feedback_journal(case_id,case_version);
CREATE TABLE feedback_projection(
 case_id UUID PRIMARY KEY,transaction_id UUID NOT NULL UNIQUE,client_id UUID NOT NULL,
 case_version BIGINT NOT NULL CHECK(case_version>0),payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
 knowledge_revision BIGINT NOT NULL UNIQUE REFERENCES feedback_journal(knowledge_revision));
CREATE FUNCTION protect_feedback_journal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Feedback knowledge journal is immutable' USING ERRCODE='23514'; END $$;
CREATE TRIGGER feedback_journal_immutable BEFORE UPDATE OR DELETE ON feedback_journal FOR EACH ROW EXECUTE FUNCTION protect_feedback_journal();
CREATE TABLE profile_build_requests(
 request_key VARCHAR(128) PRIMARY KEY,intent_hash CHAR(64) NOT NULL,
 context JSONB NOT NULL CHECK(jsonb_typeof(context)='object'),snapshot JSONB CHECK(snapshot IS NULL OR jsonb_typeof(snapshot)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TRIGGER profile_build_request_immutable BEFORE UPDATE ON profile_build_requests FOR EACH ROW EXECUTE FUNCTION protect_assessment_request();

-- A feedback revision may require another refresh in the same time slot.
ALTER TABLE profile_refresh_jobs ADD COLUMN feedback_revision BIGINT NOT NULL DEFAULT 0 CHECK(feedback_revision>=0);
DO $$ DECLARE r RECORD; BEGIN
 FOR r IN SELECT conname FROM pg_constraint WHERE conrelid='profile_refresh_jobs'::regclass AND contype='u' AND array_length(conkey,1)=5 LOOP
  EXECUTE format('ALTER TABLE profile_refresh_jobs DROP CONSTRAINT %I',r.conname);
 END LOOP;
END $$;
ALTER TABLE profile_refresh_jobs ADD CONSTRAINT refresh_slot_feedback_unique UNIQUE(client_id,currency,direction,as_of,algorithm_version,feedback_revision),
 ADD CONSTRAINT refresh_feedback_matches_context CHECK(
  (NOT (context ? 'feedbackReference') AND feedback_revision=0) OR
  ((context ? 'feedbackReference') AND coalesce((context->'feedbackReference'->>'revision')::bigint=feedback_revision,false)));
CREATE FUNCTION protect_refresh_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.feedback_revision IS DISTINCT FROM OLD.feedback_revision THEN
  RAISE EXCEPTION 'Refresh feedback revision is immutable' USING ERRCODE='23514';
 END IF; RETURN NEW;
END $$;
CREATE TRIGGER refresh_revision_immutable BEFORE UPDATE ON profile_refresh_jobs FOR EACH ROW EXECUTE FUNCTION protect_refresh_revision();
