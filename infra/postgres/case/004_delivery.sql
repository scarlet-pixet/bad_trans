CREATE TABLE IF NOT EXISTS event_failures (
    id UUID PRIMARY KEY, topic TEXT NOT NULL, partition_id INT NOT NULL,
    offset_id BIGINT NOT NULL, payload BYTEA NOT NULL, error TEXT NOT NULL,
    attempts INT NOT NULL, status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(topic, partition_id, offset_id)
);
CREATE TABLE IF NOT EXISTS notification_jobs (
    id UUID PRIMARY KEY, case_id UUID NOT NULL REFERENCES review_cases(id),
    type TEXT NOT NULL, assignee_id UUID, created_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ, correlation_id UUID
);

ALTER TABLE event_failures ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now();
