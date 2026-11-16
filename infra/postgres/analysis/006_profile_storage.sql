CREATE TABLE profile_calculations (
  id UUID PRIMARY KEY,
  job_key VARCHAR(128) NOT NULL UNIQUE,
  context JSONB NOT NULL CHECK(jsonb_typeof(context)='object'),
  context_hash CHAR(64) NOT NULL,
  history_snapshot_id UUID,
  history_digest CHAR(64),
  snapshot_captured_at TIMESTAMPTZ,
  status TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK(status<>'COMPLETED' OR (history_snapshot_id IS NOT NULL AND history_digest IS NOT NULL AND snapshot_captured_at IS NOT NULL))
);

ALTER TABLE profile_versions
  ADD COLUMN calculation_id UUID REFERENCES profile_calculations(id),
  ADD COLUMN metrics_schema_version INTEGER,
  ADD COLUMN algorithm_version TEXT,
  ADD COLUMN history_policy_version TEXT,
  ADD COLUMN history_policy_hash CHAR(64),
  ADD COLUMN time_zone TEXT,
  ADD COLUMN feedback_revision BIGINT,
  ADD COLUMN coverage_revision TEXT,
  ADD CONSTRAINT profile_versions_id_window_unique UNIQUE(id,window_days),
  ADD CONSTRAINT profile_versions_calculation_window_unique UNIQUE(calculation_id,window_days),
  ADD CONSTRAINT profile_versions_origin_subject CHECK(calculation_id IS NULL OR
    ((origin='EVENT' AND cutoff_transaction_id IS NOT NULL) OR
     (origin IN ('MANUAL','SCHEDULED') AND cutoff_transaction_id IS NULL))),
  ADD CONSTRAINT profile_versions_context_required CHECK(calculation_id IS NULL OR (
    metrics_schema_version IS NOT NULL AND metrics_schema_version>0 AND
    algorithm_version IS NOT NULL AND history_policy_version IS NOT NULL AND
    history_policy_hash IS NOT NULL AND time_zone IS NOT NULL AND time_zone='UTC' AND feedback_revision>=0 AND
    feedback_revision IS NOT NULL AND coverage_revision IS NOT NULL));

ALTER TABLE analysis_results ADD COLUMN calculation_id UUID REFERENCES profile_calculations(id);
ALTER TABLE detection_context
  ADD COLUMN context_schema_version INTEGER NOT NULL DEFAULT 1 CHECK(context_schema_version IN (1,2)),
  ADD COLUMN calculation_id UUID REFERENCES profile_calculations(id),
  ADD CONSTRAINT detection_context_version_required CHECK(
    (context_schema_version=1 AND calculation_id IS NULL) OR
    (context_schema_version=2 AND calculation_id IS NOT NULL));

CREATE TABLE analysis_result_profiles (
  result_id UUID NOT NULL REFERENCES analysis_results(id),
  window_days INTEGER NOT NULL CHECK(window_days IN (7,30,90)),
  profile_version_id UUID NOT NULL,
  PRIMARY KEY(result_id,window_days),
  FOREIGN KEY(profile_version_id,window_days) REFERENCES profile_versions(id,window_days)
);

CREATE FUNCTION reject_profile_snapshot_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Profile snapshots and result links are immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER profile_version_immutable BEFORE UPDATE OR DELETE ON profile_versions
  FOR EACH ROW EXECUTE FUNCTION reject_profile_snapshot_change();
CREATE TRIGGER result_profile_link_immutable BEFORE UPDATE OR DELETE ON analysis_result_profiles
  FOR EACH ROW EXECUTE FUNCTION reject_profile_snapshot_change();
CREATE TRIGGER result_v2_immutable BEFORE UPDATE OR DELETE ON analysis_results
  FOR EACH ROW WHEN (OLD.calculation_id IS NOT NULL) EXECUTE FUNCTION reject_profile_snapshot_change();
CREATE TRIGGER context_v2_immutable BEFORE UPDATE OR DELETE ON detection_context
  FOR EACH ROW WHEN (OLD.context_schema_version=2) EXECUTE FUNCTION reject_profile_snapshot_change();

CREATE FUNCTION validate_profile_calculation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cid UUID; c profile_calculations%ROWTYPE;
BEGIN
  cid := (to_jsonb(NEW)->>(CASE WHEN TG_TABLE_NAME='profile_calculations' THEN 'id' ELSE 'calculation_id' END))::uuid;
  IF cid IS NULL THEN RETURN NULL; END IF;
  SELECT * INTO c FROM profile_calculations WHERE id=cid;
  IF c.status='COMPLETED' AND (SELECT count(*) FROM profile_versions WHERE calculation_id=cid)<>3 THEN
    RAISE EXCEPTION 'Calculation must contain windows 7/30/90' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM profile_versions p WHERE calculation_id=cid AND (
    p.client_id::text IS DISTINCT FROM c.context->>'clientId' OR
    p.currency::text IS DISTINCT FROM c.context->>'currency' OR
    p.direction IS DISTINCT FROM c.context->>'direction' OR
    p.as_of IS DISTINCT FROM (c.context->>'asOf')::timestamptz OR
    p.ingested_before IS DISTINCT FROM (c.context->>'ingestedBefore')::timestamptz OR
    p.cutoff_transaction_id IS DISTINCT FROM (c.context->>'cutoffTransactionId')::uuid OR
    p.origin IS DISTINCT FROM c.context->>'origin' OR
    p.algorithm_version IS DISTINCT FROM c.context->>'algorithmVersion' OR
    p.metrics_schema_version IS DISTINCT FROM (c.context->>'metricsSchemaVersion')::integer OR
    p.history_policy_version IS DISTINCT FROM c.context->>'historyPolicyVersion' OR
    p.history_policy_hash::text IS DISTINCT FROM c.context->>'historyPolicyHash' OR
    p.feedback_revision IS DISTINCT FROM (c.context->>'feedbackRevision')::bigint OR
    p.coverage_revision IS DISTINCT FROM c.context->>'coverageRevision' OR
    p.time_zone IS DISTINCT FROM c.context->>'timeZone')) THEN
    RAISE EXCEPTION 'Profile context mismatch' USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER calculation_complete AFTER INSERT OR UPDATE ON profile_calculations
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_profile_calculation();
CREATE CONSTRAINT TRIGGER calculation_profile_context AFTER INSERT ON profile_versions
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_profile_calculation();

CREATE FUNCTION protect_calculation_context() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.job_key IS DISTINCT FROM OLD.job_key OR
     NEW.context IS DISTINCT FROM OLD.context OR NEW.context_hash IS DISTINCT FROM OLD.context_hash OR
     (OLD.history_snapshot_id IS NOT NULL AND (NEW.history_snapshot_id IS DISTINCT FROM OLD.history_snapshot_id OR
       NEW.history_digest IS DISTINCT FROM OLD.history_digest OR NEW.snapshot_captured_at IS DISTINCT FROM OLD.snapshot_captured_at)) OR
     (OLD.status='COMPLETED' AND NEW IS DISTINCT FROM OLD) THEN
    RAISE EXCEPTION 'Calculation context is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER calculation_context_immutable BEFORE UPDATE ON profile_calculations
  FOR EACH ROW EXECUTE FUNCTION protect_calculation_context();

CREATE FUNCTION validate_result_profile_links() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rid UUID; r analysis_results%ROWTYPE; d detection_context%ROWTYPE;
BEGIN
  rid := (to_jsonb(NEW)->>(CASE WHEN TG_TABLE_NAME='analysis_results' THEN 'id' ELSE 'result_id' END))::uuid;
  SELECT * INTO r FROM analysis_results WHERE id=rid;
  SELECT * INTO d FROM detection_context WHERE result_id=rid;
  IF r.calculation_id IS NULL THEN
    IF d.context_schema_version=2 OR EXISTS(SELECT 1 FROM analysis_result_profiles WHERE result_id=rid) THEN
      RAISE EXCEPTION 'Legacy result cannot have versioned links' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
  END IF;
  IF d.result_id IS NULL OR d.context_schema_version<>2 OR d.calculation_id<>r.calculation_id OR
     d.transaction_id<>r.transaction_id OR
     NOT EXISTS(SELECT 1 FROM analysis_result_profiles WHERE result_id=rid AND window_days=30) OR
     EXISTS(SELECT 1 FROM analysis_result_profiles l JOIN profile_versions p ON p.id=l.profile_version_id
       WHERE l.result_id=rid AND p.calculation_id IS DISTINCT FROM r.calculation_id) OR
     NOT EXISTS(SELECT 1 FROM profile_calculations c JOIN behavior_profiles p ON p.id=r.profile_id
       WHERE c.id=r.calculation_id AND c.status='COMPLETED' AND
       c.context->>'origin'='EVENT' AND r.transaction_id::text=c.context->>'cutoffTransactionId' AND
       p.client_id::text=c.context->>'clientId' AND p.currency::text=c.context->>'currency' AND
       p.direction=c.context->>'direction' AND p.window_end=(c.context->>'asOf')::timestamptz AND
       p.ingested_before=(c.context->>'ingestedBefore')::timestamptz AND
       p.cutoff_transaction_id IS NOT DISTINCT FROM (c.context->>'cutoffTransactionId')::uuid) THEN
    RAISE EXCEPTION 'Result requires matching context and primary W30 profile' USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER result_profiles_complete AFTER INSERT OR UPDATE ON analysis_results
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_result_profile_links();
CREATE CONSTRAINT TRIGGER result_profile_links_complete AFTER INSERT ON analysis_result_profiles
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_result_profile_links();
CREATE CONSTRAINT TRIGGER result_context_complete AFTER INSERT OR UPDATE ON detection_context
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_result_profile_links();
