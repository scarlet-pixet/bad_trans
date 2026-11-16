"""Owner-side group discovery and pinned history endpoints."""
from backend.common.commands import require_role

class HistoryWorkspace:
    def get_behavior_groups(self, actor, pageToken=None, limit=100):
        from backend.services.transaction.history.groups import get_groups
        return get_groups(self.db, actor, pageToken, limit)

    def prepare_behavior_history(self, actor, parameters):
        from backend.services.transaction.history.snapshots import HistorySnapshots
        require_role(actor, "SYSTEM")
        return HistorySnapshots(self.db).prepare(parameters)

    def get_behavior_history_page(self, actor, parameters):
        from backend.services.transaction.history.snapshots import HistorySnapshots
        require_role(actor, "SYSTEM")
        return HistorySnapshots(self.db).page(parameters)

    def get_counterparties(self, actor):
        require_role(actor, 'ADMIN')
        return [dict(r, id=str(r['id'])) for r in self.db.query(
            'SELECT id,external_id AS "externalId",display_name AS "displayName" FROM counterparties ORDER BY display_name,id LIMIT 100')]
