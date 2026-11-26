CREATE TABLE event_failure_reviews (
    failure_id uuid PRIMARY KEY REFERENCES event_failures(id),
    state text NOT NULL CHECK(state IN ('OPEN','WAITING','RETRY_FAILED','CLOSED')),
    proof jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_failure_review_queue ON event_failure_reviews(updated_at, failure_id) WHERE state='WAITING';

CREATE TABLE event_failure_review_actions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    failure_id uuid NOT NULL REFERENCES event_failure_reviews(failure_id),
    action text NOT NULL,
    actor_id uuid,
    reason text,
    event_id uuid,
    proof jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_failure_review_actions ON event_failure_review_actions(failure_id,id DESC);
