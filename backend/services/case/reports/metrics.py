"""Manager aggregates over Case-owned data only."""

from backend.common.commands import require_role


class CaseMetrics:
    def authorize_transaction(self, actor, transactionId):
        from backend.common.data import uuid, DomainError
        from backend.common.workspace_contract import checked

        require_role(actor, "ANALYST", "MANAGER")
        checked("CaseGrantRequest", dict(transactionId=transactionId))
        row = self.db.one(
            "SELECT assignee_id,status FROM review_cases WHERE transaction_id=%s",
            (uuid(transactionId),),
        )
        if not row or (
            actor.role == "ANALYST"
            and not (
                str(row["assignee_id"]) == actor.id
                or (row["assignee_id"] is None and row["status"] == "NEW")
            )
        ):
            raise DomainError("FORBIDDEN", "Случай недоступен")
        return checked("CaseGrant", dict(allowed=True))

    def __init__(self, db):
        self.db = db

    def get_case_metrics(self, actor):
        require_role(actor, "MANAGER")
        statuses = self.db.query(
            "SELECT status,count(*)::int AS count FROM review_cases GROUP BY status"
        )
        loads = self.db.query(
            "SELECT assignee_id,count(*)::int AS count FROM review_cases WHERE status<>'CLOSED' GROUP BY assignee_id"
        )
        outcomes = self.db.query(
            "SELECT outcome,count(*)::int AS count FROM case_decisions GROUP BY outcome"
        )
        trend = self.db.query(
            "SELECT created_at::date AS day,count(*)::int AS count FROM review_cases WHERE created_at>=now()-interval '14 days' GROUP BY 1 ORDER BY 1"
        )
        return {
            "statuses": {r["status"]: r["count"] for r in statuses},
            "loads": [
                {
                    "assigneeId": str(r["assignee_id"]) if r["assignee_id"] else None,
                    "count": r["count"],
                }
                for r in loads
            ],
            "outcomes": {r["outcome"]: r["count"] for r in outcomes},
            "trend": [
                {"day": r["day"].isoformat(), "count": r["count"]} for r in trend
            ],
        }
