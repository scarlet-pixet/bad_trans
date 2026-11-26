ALTER TABLE transactions ADD COLUMN navigation_xid xid8;
ALTER TABLE transactions ALTER COLUMN navigation_xid SET DEFAULT pg_current_xact_id();
ALTER TABLE bank_clients ADD COLUMN navigation_xid xid8;
ALTER TABLE bank_clients ALTER COLUMN navigation_xid SET DEFAULT pg_current_xact_id();

CREATE INDEX ix_transactions_navigation_time ON transactions (occurred_at,id);
CREATE INDEX ix_transactions_navigation_amount ON transactions (amount,id);
CREATE INDEX ix_clients_navigation_name ON bank_clients (display_name,id);
CREATE INDEX ix_clients_navigation_segment_name ON bank_clients (segment,display_name,id);

CREATE TABLE client_navigation_revision (
    id boolean PRIMARY KEY CHECK (id),
    version bigint NOT NULL DEFAULT 0
);
INSERT INTO client_navigation_revision (id) VALUES (true);

CREATE FUNCTION change_client_navigation_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE client_navigation_revision SET version=version+1 WHERE id=true;
    RETURN NULL;
END
$$;
CREATE TRIGGER client_navigation_changed AFTER UPDATE OF display_name,external_id,segment,is_active OR DELETE
    ON bank_clients FOR EACH STATEMENT EXECUTE FUNCTION change_client_navigation_revision();
