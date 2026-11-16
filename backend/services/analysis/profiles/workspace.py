"""Explicit immutable profile IDs and keyset history; never cache latest lists."""

from datetime import datetime, UTC

from backend.common.commands import require_role
from backend.common.data import DomainError, uuid
from backend.services.analysis.profiles.pages import encode, decode
from backend.services.analysis.profiles.observation import timestamp
from backend.infrastructure.workspace_access import transaction_access
from backend.common.workspace_contract import checked


class WorkspaceService:
    def __init__(self, db):
        self.db = db

    def compare_profile_versions(self, actor, firstId, secondId):
        require_role(actor, "MANAGER")
        from backend.services.analysis.profiles.comparison import compare_profiles

        first = self.get_profile_version(actor, uuid(firstId))
        second = self.get_profile_version(actor, uuid(secondId))
        return checked("ProfileComparison", compare_profiles(first, second))

    def get_profile_version(self, actor, id):
        require_role(actor, "MANAGER", "ANALYST")
        checked("ProfileRequest", dict(id=id))
        row = self.db.one("SELECT * FROM profile_versions WHERE id=%s", (uuid(id),))
        if not row:
            raise DomainError("NOT_FOUND", "Версия профиля не найдена")
        if actor.role == "ANALYST":
            tx = row.get("cutoff_transaction_id")
            if not tx:
                raise DomainError(
                    "FORBIDDEN", "Доступен профиль конкретной операции случая"
                )
            transaction_access(actor, str(tx))
        metadata = dict(
            id=str(row["id"]),
            clientId=str(row["client_id"]),
            currency=row["currency"],
            direction=row["direction"],
            windowDays=row["window_days"],
            version=row["version"],
            origin=row["origin"],
            asOf=row["as_of"].isoformat(),
            createdAt=row["created_at"].isoformat(),
            calculationId=str(row["calculation_id"]) if row["calculation_id"] else None,
            metricsSchemaVersion=row["metrics_schema_version"],
        )
        value = dict(
            schemaVersion=1,
            metadata=metadata,
            metrics=row["metrics"],
            servedAt=datetime.now(UTC).isoformat(),
            reportAvailability="AVAILABLE",
        )
        return checked("ProfileVersion", value)

    def get_result_history(self, actor, transactionId, limit=20, pageToken=None):
        checked(
            "HistoryRequest",
            dict(transactionId=transactionId, limit=limit, pageToken=pageToken),
        )
        require_role(actor, "MANAGER", "ANALYST")
        tx = uuid(transactionId)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DomainError("BAD_INPUT", "Размер страницы: 1–100")
        token = None
        if pageToken is not None:
            token = decode(pageToken)
            if (
                set(token) != {"transactionId", "at", "id"}
                or token["transactionId"] != tx
            ):
                raise DomainError("BAD_INPUT", "Курсор другой операции")
            token = dict(
                transactionId=tx, at=timestamp(token["at"]), id=uuid(token["id"])
            )
        transaction_access(actor, tx)
        rows = self.db.query(
            """SELECT id,run_id,calculated_at,assessment_score,assessment_status,score FROM analysis_results
            WHERE transaction_id=%s AND (%s::timestamptz IS NULL OR (calculated_at,id)<(%s,%s::uuid))
            ORDER BY calculated_at DESC,id DESC LIMIT %s""",
            (
                tx,
                token["at"] if token else None,
                token["at"] if token else None,
                token["id"] if token else None,
                limit + 1,
            ),
        )
        items = [
            dict(
                resultId=str(r["id"]),
                runId=str(r["run_id"]),
                calculatedAt=r["calculated_at"].isoformat(),
                assessmentScore=r["assessment_score"],
                legacyScore=r["score"],
                assessmentStatus=r["assessment_status"] or "LEGACY",
            )
            for r in rows[:limit]
        ]
        last = rows[limit - 1] if len(rows) > limit else None
        value = dict(
            schemaVersion=1,
            transactionId=tx,
            items=items,
            nextPageToken=encode(
                dict(
                    transactionId=tx,
                    at=last["calculated_at"].isoformat(),
                    id=str(last["id"]),
                )
            )
            if last
            else None,
        )
        return checked("ResultHistory", value)
