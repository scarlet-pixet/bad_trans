CREATE TABLE IF NOT EXISTS profile_versions (
  id UUID PRIMARY KEY, client_id UUID NOT NULL, currency CHAR(3) NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN ('IN','OUT')),
  window_days INTEGER NOT NULL CHECK(window_days IN (7,30,90)),
  version INTEGER NOT NULL CHECK(version > 0),
  as_of TIMESTAMPTZ NOT NULL, ingested_before TIMESTAMPTZ NOT NULL,
  cutoff_transaction_id UUID, origin TEXT NOT NULL,
  metrics JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(client_id,currency,direction,window_days,version)
);
CREATE INDEX IF NOT EXISTS profile_versions_latest ON profile_versions(client_id,currency,direction,window_days,as_of DESC,version DESC);
CREATE TABLE IF NOT EXISTS detection_context (
  result_id UUID PRIMARY KEY REFERENCES analysis_results(id),
  transaction_id UUID NOT NULL, details JSONB NOT NULL
);
