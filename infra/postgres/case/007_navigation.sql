ALTER TABLE notifications ADD COLUMN navigation_xid xid8;
ALTER TABLE notifications ALTER COLUMN navigation_xid SET DEFAULT pg_current_xact_id();
ALTER TABLE notifications ADD COLUMN navigation_read_xid xid8;

CREATE FUNCTION note_notification_read() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.navigation_read_xid := pg_current_xact_id();
    RETURN NEW;
END
$$;
CREATE TRIGGER notification_first_read BEFORE UPDATE OF read_at ON notifications
    FOR EACH ROW WHEN (OLD.read_at IS NULL AND NEW.read_at IS NOT NULL)
    EXECUTE FUNCTION note_notification_read();

CREATE INDEX ix_notifications_navigation_time ON notifications (recipient_id,created_at,id);
CREATE INDEX ix_notifications_navigation_id ON notifications (recipient_id,id);

CREATE TABLE case_list_views (
    id uuid PRIMARY KEY,
    expires_at timestamptz NOT NULL
);
CREATE INDEX ix_case_list_views_expiry ON case_list_views (expires_at);
CREATE TABLE case_list_positions (
    view_id uuid NOT NULL REFERENCES case_list_views(id) ON DELETE CASCADE,
    position integer NOT NULL,
    case_id uuid NOT NULL,
    PRIMARY KEY (view_id,position)
);
