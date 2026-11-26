CREATE TABLE assessment_selections (
    request_key varchar(128) PRIMARY KEY,
    parameters jsonb NOT NULL,
    transaction_ids jsonb NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
