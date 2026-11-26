ALTER TABLE managed_operation_events ADD COLUMN time_precision text
    CHECK (time_precision IN ('EXACT','DATE'));
