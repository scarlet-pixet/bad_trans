CREATE TABLE managed_operations (
    id uuid PRIMARY KEY,
    source varchar(32) NOT NULL,
    operation_ref varchar(100) NOT NULL,
    kind text NOT NULL CHECK (kind IN ('PURCHASE','TRANSFER','WITHDRAWAL','DEPOSIT','REFUND','REVERSAL')),
    channel text NOT NULL CHECK (channel IN ('POS','ONLINE','ATM','BRANCH','BANK_API','UNKNOWN')),
    status text NOT NULL CHECK (status IN ('AUTHORIZED','POSTED','CANCELLED')),
    account_id uuid NOT NULL REFERENCES accounts(id),
    currency varchar(3) NOT NULL,
    direction text NOT NULL CHECK (direction IN ('IN','OUT')),
    transaction_id uuid UNIQUE REFERENCES transactions(id),
    original_transaction_id uuid REFERENCES transactions(id),
    last_event_at timestamptz NOT NULL,
    UNIQUE (source, operation_ref),
    CHECK ((status = 'POSTED') = (transaction_id IS NOT NULL)),
    CHECK ((kind IN ('REFUND','REVERSAL')) = (original_transaction_id IS NOT NULL))
);

CREATE INDEX ix_managed_adjustments ON managed_operations(original_transaction_id)
    WHERE status = 'POSTED' AND original_transaction_id IS NOT NULL;

CREATE TABLE managed_operation_events (
    source varchar(32) NOT NULL,
    event_ref varchar(100) NOT NULL,
    operation_id uuid NOT NULL REFERENCES managed_operations(id),
    status text NOT NULL CHECK (status IN ('AUTHORIZED','POSTED','CANCELLED')),
    amount numeric(18,2) NOT NULL CHECK (amount > 0),
    occurred_at timestamptz NOT NULL,
    intent_hash char(64) NOT NULL,
    transaction_id uuid REFERENCES transactions(id),
    PRIMARY KEY (source, event_ref),
    UNIQUE (operation_id, status),
    CHECK ((status = 'POSTED') = (transaction_id IS NOT NULL))
);
