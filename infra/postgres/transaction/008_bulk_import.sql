CREATE TABLE bulk_import_jobs (
    id UUID PRIMARY KEY,
    author_id UUID NOT NULL,
    author_role TEXT NOT NULL CHECK (author_role IN ('MANAGER','ANALYST')),
    file_hash CHAR(64) NOT NULL CHECK (file_hash ~ '^[0-9a-f]{64}$'),
    columns JSONB NOT NULL CHECK (jsonb_typeof(columns)='array'),
    status TEXT NOT NULL CHECK (status IN ('QUEUED','RUNNING','COMPLETED','PARTIAL','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    UNIQUE (author_id,file_hash)
);

CREATE INDEX bulk_import_jobs_claim ON bulk_import_jobs (created_at,id)
    WHERE status IN ('QUEUED','RUNNING');
CREATE INDEX bulk_import_jobs_author ON bulk_import_jobs (author_id,created_at DESC,id DESC);

CREATE TABLE bulk_import_rows (
    job_id UUID NOT NULL REFERENCES bulk_import_jobs(id),
    line INTEGER NOT NULL CHECK (line >= 2),
    payload JSONB,
    state TEXT NOT NULL CHECK (state IN ('PENDING','ADDED','DUPLICATE','ERROR')),
    transaction_id UUID REFERENCES transactions(id),
    error_code TEXT,
    message TEXT,
    PRIMARY KEY (job_id,line),
    CHECK ((state='ERROR')=(error_code IS NOT NULL)),
    CHECK ((state IN ('ADDED','DUPLICATE'))=(transaction_id IS NOT NULL)),
    CHECK (state<>'PENDING' OR payload IS NOT NULL)
);
CREATE INDEX bulk_import_pending ON bulk_import_rows (job_id,line) WHERE state='PENDING';
