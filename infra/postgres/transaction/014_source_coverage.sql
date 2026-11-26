ALTER TABLE transactions ADD COLUMN time_precision text
    CHECK (time_precision IN ('EXACT','DATE'));
UPDATE transactions SET time_precision='DATE' WHERE source='banksim';

CREATE TABLE source_observation_declarations (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES accounts(id),
    source varchar(32) NOT NULL,
    channel text NOT NULL CHECK (channel IN ('POS','ONLINE','ATM','BRANCH','BANK_API','UNKNOWN')),
    direction text NOT NULL CHECK (direction IN ('IN','OUT')),
    feature text NOT NULL CHECK (feature IN ('DAY','HOUR')),
    revision varchar(100) NOT NULL,
    intervals jsonb NOT NULL CHECK (jsonb_typeof(intervals)='array'),
    known_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (account_id, source, channel, direction, feature, revision)
);
CREATE INDEX ix_source_observation_latest ON source_observation_declarations
    (account_id, direction, source, channel, feature, known_at DESC, id DESC);
