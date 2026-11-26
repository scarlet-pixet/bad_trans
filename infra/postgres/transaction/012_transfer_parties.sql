CREATE TABLE operation_transfers (
    id uuid PRIMARY KEY,
    namespace text NOT NULL,
    external_ref text NOT NULL,
    parties jsonb NOT NULL,
    owner_relation text NOT NULL CHECK (owner_relation IN ('SAME_OWNER','DIFFERENT_OWNERS','UNKNOWN')),
    owner_evidence text CHECK (owner_evidence = 'BANK_ACCOUNT_DIRECTORY'),
    UNIQUE (namespace, external_ref),
    CHECK ((owner_relation = 'UNKNOWN') = (owner_evidence IS NULL))
);

CREATE TABLE operation_transfer_entries (
    transaction_id uuid PRIMARY KEY REFERENCES transactions(id),
    transfer_id uuid NOT NULL REFERENCES operation_transfers(id),
    direction text NOT NULL CHECK (direction IN ('IN','OUT')),
    UNIQUE (transfer_id, direction)
);
