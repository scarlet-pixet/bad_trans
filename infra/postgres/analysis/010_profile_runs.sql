CREATE TABLE profile_runs (
 id UUID PRIMARY KEY,
 author_id UUID NOT NULL,
 idempotency_key TEXT NOT NULL,
 request_hash CHAR(64) NOT NULL,
 parameters JSONB NOT NULL,
 context JSONB NOT NULL,
 status TEXT NOT NULL DEFAULT 'QUEUED' CHECK(status IN
 ('QUEUED','DISCOVERING','RUNNING','EMPTY','COMPLETED','COMPLETED_WITH_SKIPS','PARTIAL','FAILED')),
 selection_snapshot JSONB,
 next_offset INTEGER NOT NULL DEFAULT 0 CHECK(next_offset>=0),
 discovery_attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT,
 available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 completed_at TIMESTAMPTZ,
 UNIQUE(author_id,idempotency_key)
);
CREATE TABLE profile_run_groups (
 run_id UUID NOT NULL REFERENCES profile_runs(id),
 client_id UUID NOT NULL,
 currency CHAR(3) NOT NULL,
 direction TEXT NOT NULL CHECK(direction IN ('IN','OUT')),
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN
 ('PENDING','RUNNING','RETRY','COMPLETED','SKIPPED','FAILED')),
 attempts INTEGER NOT NULL DEFAULT 0,
 available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 last_error TEXT,
 snapshot JSONB,
 calculation_id UUID REFERENCES profile_calculations(id),
 version_ids JSONB NOT NULL DEFAULT '[]',
 completed_at TIMESTAMPTZ,
 PRIMARY KEY(run_id,client_id,currency,direction),
 CHECK((status='COMPLETED')=(calculation_id IS NOT NULL))
);
CREATE INDEX profile_runs_pending ON profile_runs(available_at,created_at)
 WHERE status IN ('QUEUED','DISCOVERING','RUNNING');
