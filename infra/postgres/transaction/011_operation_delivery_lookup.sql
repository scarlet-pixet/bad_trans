CREATE INDEX IF NOT EXISTS ix_outbox_registered_transaction
    ON outbox_events ((payload #>> '{payload,transaction_id}'))
    WHERE event_type = 'transaction.registered.v1';
