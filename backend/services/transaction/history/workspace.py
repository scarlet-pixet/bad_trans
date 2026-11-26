"""Owner-side group discovery and pinned history endpoints."""
from backend.common.commands import require_role

class HistoryWorkspace:
    def get_client_labels(self, actor, ids):
        from backend.common.data import DomainError, uuid

        require_role(actor, "MANAGER")
        if not isinstance(ids, list) or len(ids) > 20:
            raise DomainError("BAD_INPUT", "Запросите до 20 подписей")
        rows = self.db.query(
            "SELECT id,display_name FROM bank_clients WHERE id=ANY(%s::uuid[])",
            ([uuid(identifier) for identifier in ids],),
        )
        return {str(row["id"]): row["display_name"] for row in rows}

    def prepare_assessment_selection(self, actor, requestKey, scope, ingestedBefore):
        from backend.services.transaction.history.assessment_selection import prepare
        return prepare(self.db, actor, requestKey, scope, ingestedBefore)

    def prepare_profile_groups(self, actor, parameters):
        from backend.services.transaction.history.profile_selection import ProfileSelection
        return ProfileSelection(self.db).prepare(actor, parameters)

    def profile_group_page(self, actor, parameters):
        from backend.services.transaction.history.profile_selection import ProfileSelection
        return ProfileSelection(self.db).page(actor, parameters)

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
