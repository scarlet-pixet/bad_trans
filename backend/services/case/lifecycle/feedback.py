"""Full-state Case feedback staged atomically with decisions/reopening."""

from datetime import UTC
from uuid import NAMESPACE_URL, uuid5

from backend.common.behavior_contract import digest
from backend.common.data import DomainError
from backend.common.events import make_event, add_outbox


class CaseFeedback:
    @staticmethod
    def publish(c, case_id):
        row = c.execute(
            "SELECT * FROM review_cases WHERE id=%s FOR UPDATE", (case_id,)
        ).fetchone()
        if not row:
            raise DomainError("NOT_FOUND", "Case missing for feedback")
        decision = None
        if row["status"] == "CLOSED":
            d = c.execute(
                "SELECT * FROM case_decisions WHERE case_id=%s AND review_cycle=%s",
                (case_id, row["review_cycle"]),
            ).fetchone()
            if d:
                decision = dict(
                    id=str(d["id"]),
                    outcome=d["outcome"],
                    decided_at=d["decided_at"].astimezone(UTC).isoformat(),
                )
        payload = dict(
            case_id=str(row["id"]),
            transaction_id=str(row["transaction_id"]),
            client_id=str(row["client_id"]),
            case_version=row["row_version"],
            review_cycle=row["review_cycle"],
            case_state="CLOSED" if row["status"] == "CLOSED" else "OPEN",
            decision=decision,
            state_changed_at=row["updated_at"].astimezone(UTC).isoformat(),
        )
        fingerprint = digest(payload)
        old = c.execute(
            "SELECT * FROM case_feedback_publications WHERE case_id=%s", (case_id,)
        ).fetchone()
        if old and old["case_version"] >= row["row_version"]:
            if (
                old["case_version"] != row["row_version"]
                or old["payload_hash"] != fingerprint
            ):
                raise DomainError(
                    "CONFLICT", "Case feedback version changed without a new revision"
                )
            return str(old["event_id"])
        event = make_event(payload)
        event["event_id"] = str(
            uuid5(
                NAMESPACE_URL, f"badtrans:case.feedback:{case_id}:{row['row_version']}"
            )
        )
        event["occurred_at"] = payload["state_changed_at"]
        add_outbox(c, "case.feedback.v1", str(row["id"]), event)
        c.execute(
            """INSERT INTO case_feedback_publications VALUES (%s,%s,%s,%s)
            ON CONFLICT(case_id) DO UPDATE SET case_version=excluded.case_version,event_id=excluded.event_id,payload_hash=excluded.payload_hash""",
            (case_id, row["row_version"], event["event_id"], fingerprint),
        )
        return event["event_id"]

    @staticmethod
    def bootstrap(db, batch=20):
        if type(batch) is not int or not 1 <= batch <= 100:
            raise ValueError("Invalid bootstrap batch")
        with db.connect() as c:
            rows = c.execute(
                """SELECT r.id FROM review_cases r LEFT JOIN case_feedback_publications f ON f.case_id=r.id
                WHERE f.case_id IS NULL ORDER BY r.created_at,r.id LIMIT %s FOR UPDATE OF r SKIP LOCKED""",
                (batch,),
            ).fetchall()
            for row in rows:
                CaseFeedback.publish(c, str(row["id"]))
        return len(rows)
