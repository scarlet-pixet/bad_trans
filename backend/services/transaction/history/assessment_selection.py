"""Persist the ordered membership of a single Analysis period request."""
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from backend.common.behavior_contract import digest
from backend.common.commands import require_role, required_text
from backend.common.data import DomainError
from backend.common.reassessment_contract import MAX_OPERATIONS, selection


def prepare(db, actor, requestKey, scope, ingestedBefore):
    require_role(actor, "SYSTEM")
    key = required_text(requestKey, "requestKey", 128)
    try:
        watermark = datetime.fromisoformat(ingestedBefore)
        if watermark.tzinfo is None:
            raise ValueError()
    except (TypeError, ValueError):
        raise DomainError("BAD_INPUT", "Некорректная граница поступления") from None
    watermark = watermark.astimezone(UTC)
    normalized = selection(scope["period"], scope["clientId"])
    parameters = dict(scope=normalized, ingestedBefore=watermark.isoformat())
    interval, client_id = normalized["period"], normalized["clientId"]
    with db.connect() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("assessment-selection:" + key,))
        row = connection.execute(
            "SELECT * FROM assessment_selections WHERE request_key=%s", (key,)
        ).fetchone()
        if row is None:
            rows = connection.execute(
                """SELECT t.id FROM transactions t JOIN accounts a ON a.id=t.account_id
                WHERE t.occurred_at >= %s AND t.occurred_at < %s AND t.ingested_at < %s
                    AND (%s::uuid IS NULL OR a.client_id=%s)
                ORDER BY t.occurred_at,t.id LIMIT %s""",
                (interval["from"], interval["to"], watermark, client_id, client_id, MAX_OPERATIONS + 1),
            ).fetchall()
            if len(rows) > MAX_OPERATIONS:
                raise DomainError("BAD_INPUT", "PERIOD_LIMIT: сузьте период или укажите клиента")
            row = connection.execute(
                """INSERT INTO assessment_selections(request_key, parameters, transaction_ids)
                VALUES (%s,%s,%s) RETURNING *""",
                (key, Jsonb(parameters), Jsonb([str(row["id"]) for row in rows])),
            ).fetchone()
        if row["parameters"] != parameters:
            raise DomainError("CONFLICT", "Ключ отбора использован с другим периодом")
        ids = row["transaction_ids"]
        return dict(parameters=parameters, ids=ids, digest=digest(ids), capturedAt=row["captured_at"].isoformat())
