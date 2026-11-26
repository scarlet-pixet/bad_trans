ALTER TABLE behavior_history_snapshots
  DROP CONSTRAINT behavior_history_snapshots_item_count_check,
  ADD CONSTRAINT behavior_history_snapshots_item_count_check CHECK (item_count BETWEEN 0 AND 50000);

-- Every ordinal is unique, and the header's deferred completeness check still
-- requires exactly item_count rows. With ordinals bounded on insert, a complete
-- immutable snapshot has no free slot and cannot gain another item.
DROP TRIGGER history_items_complete ON behavior_history_snapshot_items;
CREATE FUNCTION check_history_item_ordinal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE expected INTEGER;
BEGIN
  SELECT item_count INTO expected FROM behavior_history_snapshots WHERE id=NEW.snapshot_id;
  IF expected IS NULL OR NEW.ordinal >= expected THEN
    RAISE EXCEPTION 'Behavior history item ordinal outside declared count' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER history_items_ordinal BEFORE INSERT ON behavior_history_snapshot_items
  FOR EACH ROW EXECUTE FUNCTION check_history_item_ordinal();
