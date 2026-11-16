-- Durable scheduler state belongs exclusively to Analysis.
CREATE TABLE profile_refresh_groups (
  client_id UUID NOT NULL,
  currency CHAR(3) NOT NULL CHECK(currency ~ '^[A-Z]{3}$'),
  direction TEXT NOT NULL CHECK(direction IN ('IN','OUT')),
  next_due_at TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01T00:00:00Z',
  discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(client_id,currency,direction)
);
CREATE INDEX profile_refresh_groups_due ON profile_refresh_groups(next_due_at,client_id,currency,direction);
INSERT INTO profile_refresh_groups(client_id,currency,direction)
  SELECT DISTINCT client_id,currency,direction FROM profile_versions ON CONFLICT DO NOTHING;
CREATE TABLE profile_scheduler_state (
  id INTEGER PRIMARY KEY CHECK(id=1),
  discovery_cursor TEXT,
  last_discovered_at TIMESTAMPTZ,
  last_error TEXT
);
INSERT INTO profile_scheduler_state(id) VALUES (1);

CREATE TABLE profile_refresh_jobs (
  id UUID PRIMARY KEY,
  client_id UUID NOT NULL,
  currency CHAR(3) NOT NULL,
  direction TEXT NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  algorithm_version TEXT NOT NULL,
  context JSONB NOT NULL CHECK(jsonb_typeof(context)='object'),
  snapshot JSONB CHECK(snapshot IS NULL OR jsonb_typeof(snapshot)='object'),
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','RUNNING','RETRY','COMPLETED','BLOCKED')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
  available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  last_error TEXT,
  calculation_id UUID REFERENCES profile_calculations(id),
  FOREIGN KEY(client_id,currency,direction) REFERENCES profile_refresh_groups(client_id,currency,direction),
  UNIQUE(client_id,currency,direction,as_of,algorithm_version),
  CHECK(status<>'COMPLETED' OR (calculation_id IS NOT NULL AND snapshot IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE UNIQUE INDEX profile_refresh_one_active_group ON profile_refresh_jobs(client_id,currency,direction)
  WHERE status IN ('PENDING','RUNNING','RETRY');
CREATE INDEX profile_refresh_work ON profile_refresh_jobs(available_at,created_at,id)
  WHERE status IN ('PENDING','RUNNING','RETRY');
CREATE FUNCTION protect_profile_refresh_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.context IS DISTINCT FROM OLD.context OR
     NEW.client_id IS DISTINCT FROM OLD.client_id OR NEW.currency IS DISTINCT FROM OLD.currency OR
     NEW.direction IS DISTINCT FROM OLD.direction OR NEW.as_of IS DISTINCT FROM OLD.as_of OR
     NEW.algorithm_version IS DISTINCT FROM OLD.algorithm_version OR
     (OLD.snapshot IS NOT NULL AND NEW.snapshot IS DISTINCT FROM OLD.snapshot) OR
     (OLD.status='COMPLETED' AND NEW IS DISTINCT FROM OLD) THEN
    RAISE EXCEPTION 'Refresh request/snapshot/completion is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER profile_refresh_request_immutable BEFORE UPDATE ON profile_refresh_jobs
  FOR EACH ROW EXECUTE FUNCTION protect_profile_refresh_request();

-- Page composition is pinned, even if newer/late-committed versions appear.
CREATE TABLE profile_page_selections (
  id UUID PRIMARY KEY,
  filter_hash CHAR(64) NOT NULL,
  selected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '1 hour'
);
CREATE TABLE profile_page_items (
  selection_id UUID NOT NULL REFERENCES profile_page_selections(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal>0),
  profile_id UUID NOT NULL REFERENCES profile_versions(id),
  PRIMARY KEY(selection_id,ordinal),
  UNIQUE(selection_id,profile_id)
);
CREATE INDEX profile_page_expiry ON profile_page_selections(expires_at);
