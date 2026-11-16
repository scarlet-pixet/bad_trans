CREATE TABLE assessment_requests (
  request_key VARCHAR(128) PRIMARY KEY,
  intent_hash CHAR(64) NOT NULL,
  context JSONB NOT NULL CHECK(jsonb_typeof(context)='object'),
  snapshot JSONB CHECK(snapshot IS NULL OR jsonb_typeof(snapshot)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE FUNCTION protect_assessment_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.request_key IS DISTINCT FROM OLD.request_key OR NEW.intent_hash IS DISTINCT FROM OLD.intent_hash OR NEW.context IS DISTINCT FROM OLD.context OR
    (OLD.snapshot IS NOT NULL AND NEW.snapshot IS DISTINCT FROM OLD.snapshot) THEN
   RAISE EXCEPTION 'Assessment request context is immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER assessment_request_immutable BEFORE UPDATE ON assessment_requests FOR EACH ROW EXECUTE FUNCTION protect_assessment_request();
ALTER TABLE analysis_results
 ADD COLUMN assessment_score SMALLINT CHECK(assessment_score BETWEEN 0 AND 100),
 ADD COLUMN assessment_status TEXT,
 ADD COLUMN assessment_algorithm_version TEXT,
 ADD COLUMN assessment_policy_version TEXT,
 ADD COLUMN assessment_policy_hash CHAR(64),
 ADD COLUMN review_required BOOLEAN,
 ADD COLUMN assessment_request_key VARCHAR(128) UNIQUE REFERENCES assessment_requests(request_key),
 ADD CONSTRAINT result_assessment_consistent CHECK(
  (assessment_status IS NULL AND assessment_score IS NULL AND assessment_request_key IS NULL AND assessment_algorithm_version IS NULL AND assessment_policy_version IS NULL AND assessment_policy_hash IS NULL AND review_required IS NULL) OR
  (assessment_status IS NOT NULL AND assessment_algorithm_version IS NOT NULL AND assessment_policy_version IS NOT NULL AND assessment_policy_hash IS NOT NULL AND assessment_request_key IS NOT NULL AND
   ((assessment_status='UNAVAILABLE' AND assessment_score IS NULL AND review_required IS NULL AND score=0) OR
    (assessment_status IN ('COMPLETE','PARTIAL') AND assessment_score IS NOT NULL AND score=assessment_score AND review_required IS NOT NULL AND review_required=(assessment_score>=threshold)))));
CREATE FUNCTION protect_published_rule() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Published rule versions are immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER published_rule_immutable BEFORE UPDATE OR DELETE ON rule_versions FOR EACH ROW WHEN (OLD.status='PUBLISHED') EXECUTE FUNCTION protect_published_rule();
-- System migration actor UUID marks a policy import, not employee approval.
INSERT INTO analysis_rules(id,code,name,description) VALUES ('1e2f8fa7-02a0-521b-ac60-90e2609d6f9c','AMOUNT_DEVIATION','AMOUNT_DEVIATION','Versioned demonstration assessment, not a banking norm');
INSERT INTO rule_versions VALUES ('4de91bcb-9271-5d11-8b1d-ff8692c03359','1e2f8fa7-02a0-521b-ac60-90e2609d6f9c',1,'{"policyVersion":"behavior-demo-v1","enabled":true,"min_samples":20,"min_active_days":7,"mad_scale":"1.4826","mad_multiplier":"3","iqr_multiplier":"1.5","constant_fraction":"0.25"}'::jsonb,40,'PUBLISHED','00000000-0000-0000-0000-000000000005',now());
INSERT INTO analysis_rules(id,code,name,description) VALUES ('a575b82b-dbae-5409-801c-76294dfd9f9f','PROFILE_FREQUENCY','PROFILE_FREQUENCY','Versioned demonstration assessment, not a banking norm');
INSERT INTO rule_versions VALUES ('c86be59e-4fce-5d93-88e1-4fa1fa485b22','a575b82b-dbae-5409-801c-76294dfd9f9f',1,'{"policyVersion":"behavior-demo-v1","enabled":true,"min_reference_hours":14,"min_confirmed_days":14,"mad_scale":"1.4826","mad_multiplier":"3","iqr_multiplier":"1.5","constant_add":2}'::jsonb,40,'PUBLISHED','00000000-0000-0000-0000-000000000005',now());
INSERT INTO analysis_rules(id,code,name,description) VALUES ('38175a4e-76f8-5d08-890d-ad2dd2d441bf','UNUSUAL_TIME','UNUSUAL_TIME','Versioned demonstration assessment, not a banking norm');
INSERT INTO rule_versions VALUES ('22d30571-5a1d-5d70-9748-ce7e437888c4','38175a4e-76f8-5d08-890d-ad2dd2d441bf',1,'{"policyVersion":"behavior-demo-v1","enabled":true,"min_samples":100,"min_active_days":14,"min_confirmed_days":28,"alpha":"1","hour_probability":"0.01","weekday_probability":"0.03"}'::jsonb,10,'PUBLISHED','00000000-0000-0000-0000-000000000005',now());
INSERT INTO analysis_rules(id,code,name,description) VALUES ('65711bf5-59ee-51b7-81d1-01f0b35cd76b','COUNTERPARTY_NOVELTY','COUNTERPARTY_NOVELTY','Versioned demonstration assessment, not a banking norm');
INSERT INTO rule_versions VALUES ('649979d7-6907-5d9d-947a-44b38e73c918','65711bf5-59ee-51b7-81d1-01f0b35cd76b',1,'{"policyVersion":"behavior-demo-v1","enabled":true,"min_samples":20,"min_active_days":7,"min_known_fraction":"0.90"}'::jsonb,10,'PUBLISHED','00000000-0000-0000-0000-000000000005',now());
INSERT INTO analysis_rules(id,code,name,description) VALUES ('d01b6644-0e2a-5eec-b232-6b5fd15bbf00','ACTIVITY_CHANGE','ACTIVITY_CHANGE','Versioned demonstration assessment, not a banking norm');
INSERT INTO rule_versions VALUES ('4259e427-397d-5e91-8d5d-539639931579','d01b6644-0e2a-5eec-b232-6b5fd15bbf00',1,'{"policyVersion":"behavior-demo-v1","enabled":true,"min_short":5,"min_prior":20,"min_prior_active_days":7,"lower_ratio":"0.25","upper_ratio":"4"}'::jsonb,40,'PUBLISHED','00000000-0000-0000-0000-000000000005',now());
CREATE FUNCTION validate_assessment_result() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r analysis_results%ROWTYPE; d JSONB; req JSONB; total INTEGER;
BEGIN
 SELECT * INTO r FROM analysis_results WHERE id=NEW.id;
 IF r.assessment_status IS NULL THEN RETURN NULL; END IF;
 SELECT details INTO d FROM detection_context WHERE result_id=r.id;
 SELECT context INTO req FROM assessment_requests WHERE request_key=r.assessment_request_key;
 IF d IS NULL OR jsonb_typeof(d->'checks') IS DISTINCT FROM 'array' OR jsonb_array_length(d->'checks') NOT BETWEEN 1 AND 5 THEN
  RAISE EXCEPTION 'Assessment checks are required' USING ERRCODE='23514';
 END IF;
 SELECT coalesce(sum((v->>'contribution')::integer),0) INTO total FROM jsonb_array_elements(d->'checks') v;
 IF total<>coalesce(r.assessment_score,0) OR
    d->'assessmentScore' IS DISTINCT FROM coalesce(to_jsonb(r.assessment_score),'null'::jsonb) OR
    d->>'assessmentStatus' IS DISTINCT FROM r.assessment_status OR
    d->>'assessmentAlgorithmVersion' IS DISTINCT FROM r.assessment_algorithm_version OR
    d->>'assessmentPolicyVersion' IS DISTINCT FROM r.assessment_policy_version OR
    d->>'assessmentPolicyHash' IS DISTINCT FROM r.assessment_policy_hash::text OR
    d->'reviewRequired' IS DISTINCT FROM coalesce(to_jsonb(r.review_required),'null'::jsonb) OR
    d->'rules' IS DISTINCT FROM req->'rules' OR
    r.data_status IS DISTINCT FROM (CASE WHEN r.assessment_status='COMPLETE' THEN 'SUFFICIENT' ELSE 'INSUFFICIENT' END) THEN
  RAISE EXCEPTION 'Assessment score/context mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER assessment_result_complete AFTER INSERT ON analysis_results
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_assessment_result();
