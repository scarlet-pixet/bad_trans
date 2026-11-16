CREATE TABLE IF NOT EXISTS source_runs (
  id UUID PRIMARY KEY, requested_by UUID NOT NULL, request_key TEXT NOT NULL,
  interval_seconds NUMERIC NOT NULL CHECK(interval_seconds BETWEEN 0.05 AND 30),
  status TEXT NOT NULL CHECK(status IN ('RUNNING','PAUSED','COMPLETED')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), next_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_error TEXT, UNIQUE(requested_by,request_key)
);
CREATE TABLE IF NOT EXISTS source_records (
  run_id UUID NOT NULL REFERENCES source_runs(id), sequence INTEGER NOT NULL,
  payload JSONB NOT NULL, expected BOOLEAN, scenario TEXT NOT NULL,
  phase TEXT NOT NULL CHECK(phase IN ('HISTORY','LIVE')),
  transaction_id UUID REFERENCES transactions(id),
  PRIMARY KEY(run_id,sequence)
);
CREATE INDEX IF NOT EXISTS source_records_pending ON source_records(run_id,sequence) WHERE transaction_id IS NULL;
