ALTER TABLE analysis_runs DROP CONSTRAINT analysis_runs_status_check;
ALTER TABLE analysis_runs ADD CONSTRAINT analysis_runs_status_check
    CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED'));

CREATE TABLE period_reassessment_jobs (
    run_id uuid PRIMARY KEY REFERENCES analysis_runs(id),
    context jsonb NOT NULL,
    selection jsonb,
    discovery_attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    correlation_id text NOT NULL
);

CREATE TABLE period_reassessment_items (
    run_id uuid NOT NULL REFERENCES period_reassessment_jobs(run_id),
    transaction_id uuid NOT NULL,
    position integer NOT NULL,
    state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING', 'RUNNING', 'RETRY', 'COMPLETED', 'FAILED')),
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    finished_at timestamptz,
    PRIMARY KEY (run_id, transaction_id),
    UNIQUE (run_id, position)
);

CREATE INDEX period_reassessment_pending ON period_reassessment_items (run_id, position)
    WHERE state IN ('PENDING', 'RUNNING', 'RETRY');
