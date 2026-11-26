"""Employee-safe processing evidence from the Analysis-owned database."""

from backend.common.commands import require_role
from backend.common.data import uuid
from backend.infrastructure.failure_review import original
from backend.infrastructure.workspace_access import transaction_access

TOPIC = "transaction.registered.v1"


def delivery_state(rows, event_id, transaction_id):
    states = set()
    for row in rows:
        event = original(row, "analysis")
        if not event or (
            uuid(event["event_id"]), uuid(event["payload"]["transaction_id"])
        ) != (event_id, transaction_id):
            continue
        if row["status"] == "RESOLVED":
            states.add("UNKNOWN")
        elif row["status"] == "RETRY_PENDING" or row["review_state"] == "WAITING":
            states.add("PENDING")
        elif row["status"] in ("PENDING", "PUBLISHED"):
            states.add("FAILED")

    if "UNKNOWN" in states:
        return "UNKNOWN"
    if "PENDING" in states or not states:
        return "PENDING"
    return "FAILED"


def get_processing(db, actor, transaction_id, event_id=None):
    require_role(actor, "MANAGER", "ANALYST")
    tx = uuid(transaction_id)
    transaction_access(actor, tx)
    event = uuid(event_id) if event_id else None
    with db.connect() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        result = connection.execute(
            """SELECT id,assessment_status,assessment_score,score
            FROM analysis_results WHERE transaction_id=%s
            ORDER BY calculated_at DESC,id DESC LIMIT 1""",
            (tx,),
        ).fetchone()
        if result:
            status = result["assessment_status"]
            if status == "UNAVAILABLE":
                state = "UNAVAILABLE"
            elif status == "PARTIAL" and result["assessment_score"] is not None:
                state = "PARTIAL"
            elif (status == "COMPLETE" and result["assessment_score"] is not None
                  or status is None and result["score"] is not None):
                state = "SCORED"
            else:
                state = "UNKNOWN"
            return {"state": state, "resultId": str(result["id"])}

        if not event or connection.execute(
            "SELECT 1 FROM inbox_events WHERE event_id=%s AND event_type=%s", (event, TOPIC)
        ).fetchone():
            return {"state": "UNKNOWN", "resultId": None}

        # Match the exact validated original. The byte filter also works for
        # older parked rows and safely excludes unrelated or malformed payloads.
        rows = connection.execute(
            """SELECT f.topic,f.payload,f.status,r.state AS review_state
            FROM event_failures f LEFT JOIN event_failure_reviews r ON r.failure_id=f.id
            WHERE f.topic=%s AND position(convert_to(%s,'UTF8') in f.payload)>0""",
            (TOPIC, event),
        ).fetchall()
        return {"state": delivery_state(rows, event, tx), "resultId": None}
