"""Admin decisions and retry requests owned by the service that parked the message."""
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from psycopg.types.json import Jsonb

from backend.common.commands import require_role, required_text
from backend.common.data import DomainError, uuid
from backend.common.receipts import receipt

TOPICS = {"analysis": {"transaction.registered.v1", "case.feedback.v1"}, "case": {"analysis.completed.v1"}}


@lru_cache
def validator(topic):
    path = Path(__file__).resolve().parents[1] / "contracts/events" / (topic + ".schema.json")
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")), format_checker=FormatChecker())


def original(row, owner):
    if row["topic"] not in TOPICS[owner]:
        return None
    try:
        event = json.loads(bytes(row["payload"]))
        validator(row["topic"]).validate(event)
        return event
    except (ValueError, ValidationError, UnicodeError, TypeError):
        return None


def evidence(connection, topic, event):
    if event is None:
        return None
    event_id, payload = uuid(event["event_id"]), event["payload"]
    accepted = connection.execute(
        "SELECT 1 FROM inbox_events WHERE event_id=%s AND event_type=%s", (event_id, topic)
    ).fetchone()
    proof = dict(eventId=event_id, accepted=bool(accepted))
    if topic == "transaction.registered.v1":
        tx = uuid(payload["transaction_id"])
        result = connection.execute(
            """SELECT a.id,(a.assessment_request_key=%s) AS exact FROM analysis_results a
            JOIN analysis_runs r ON r.id=a.run_id
            WHERE a.transaction_id=%s AND r.mode='AUTO'
            ORDER BY (a.assessment_request_key=%s) DESC NULLS LAST,a.calculated_at DESC,a.id DESC LIMIT 1""",
            ("event:"+event_id, tx, "event:"+event_id),
        ).fetchone()
        if result:
            proof.update(transactionId=tx,resultId=str(result["id"]),
                         matchedBy="EVENT" if result["exact"] else "TRANSACTION")
    elif topic == "analysis.completed.v1" and accepted:
        proof["resultId"] = uuid(payload["result_id"])
        case = connection.execute(
            "SELECT id FROM review_cases WHERE transaction_id=%s", (uuid(payload["transaction_id"]),)
        ).fetchone()
        if case:
            proof["caseId"] = str(case["id"])
    return proof


def record(connection, failure_id, action, actor, reason, event, proof):
    connection.execute(
        """INSERT INTO event_failure_review_actions(failure_id,action,actor_id,reason,event_id,proof)
        VALUES (%s,%s,%s,%s,%s,%s)""",
        (failure_id, action, actor, reason, event["event_id"] if event else None, Jsonb(proof) if proof else None),
    )


def select(connection, failure_id, *, lock=False):
    statement = """SELECT f.*,coalesce(r.state,'OPEN') AS review_state,r.proof,r.updated_at
        FROM event_failures f LEFT JOIN event_failure_reviews r ON r.failure_id=f.id WHERE f.id=%s"""
    if lock:
        connection.execute("SELECT id FROM event_failures WHERE id=%s FOR UPDATE", (failure_id,)).fetchone()
    row = connection.execute(statement, (failure_id,)).fetchone()
    if row is None:
        raise DomainError("NOT_FOUND", "Ошибка обработки не найдена")
    return row


class FailureReview:
    def __init__(self, db, owner):
        self.db, self.owner = db, owner

    def wire(self, row):
        event = original(row,self.owner)
        return dict(id=str(row["id"]),owner=self.owner,state=row["review_state"],transportStatus=row["status"],
                    topic=row["topic"],partition=row["partition_id"],offset=row["offset_id"],
                    eventId=event["event_id"] if event else None,valid=event is not None,
                    cause=row["error"].split(":",1)[0], attempts=row["attempts"],createdAt=row["created_at"],
                    updatedAt=row["updated_at"],proof=row["proof"])

    def list(self, actor, state="ACTIVE", offset=0):
        require_role(actor,"ADMIN")
        if state not in ("ACTIVE","WAITING","CLOSED") or type(offset) is not int or not 0<=offset<=100_000:
            raise DomainError("BAD_INPUT", "Некорректный отбор ошибок")
        values = {"ACTIVE":["OPEN","RETRY_FAILED"],"WAITING":["WAITING"],"CLOSED":["CLOSED"]}[state]
        rows = self.db.query(
            """SELECT f.*,coalesce(r.state,'OPEN') AS review_state,r.proof,r.updated_at
            FROM event_failures f LEFT JOIN event_failure_reviews r ON r.failure_id=f.id
            WHERE coalesce(r.state,'OPEN')=ANY(%s) ORDER BY f.created_at DESC,f.id DESC
            LIMIT 21 OFFSET %s""",(values,offset),
        )
        return dict(items=[self.wire(row) for row in rows[:20]],nextOffset=offset+20 if len(rows)>20 else None)

    def get(self, actor, id):
        require_role(actor,"ADMIN")
        failure_id=uuid(id)
        with self.db.connect() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row=select(connection,failure_id)
            actions=connection.execute(
                """SELECT action,actor_id AS "actorId",reason,event_id AS "eventId",proof,
                created_at AS "createdAt" FROM event_failure_review_actions
                WHERE failure_id=%s ORDER BY id DESC LIMIT 100""",(failure_id,),
            ).fetchall()
        return dict(self.wire(row),actions=actions)

    def change(self, actor, id, action, reason, idempotencyKey):
        require_role(actor,"ADMIN")
        failure_id=uuid(id)
        if action not in ("VERIFY","RETRY","CLOSE"):
            raise DomainError("BAD_INPUT","Выберите действие разбора")
        reason=required_text(reason,"Обоснование",500) if action!="VERIFY" else None
        if reason is not None and len(reason)<10:
            raise DomainError("BAD_INPUT","Укажите причину или принятое решение (не менее 10 символов)")

        def perform(connection):
            row=select(connection,failure_id,lock=True)
            if row["review_state"]=="CLOSED":
                if action=="CLOSE":
                    return {"id":failure_id}
                raise DomainError("CONFLICT","Разбор уже завершён")
            if row["review_state"]=="WAITING":
                raise DomainError("CONFLICT","Дождитесь результата повторной обработки")
            event=original(row,self.owner)
            if action=="RETRY" and event is None:
                raise DomainError("BAD_INPUT","Формат исходного сообщения некорректен. Завершите разбор с обоснованием.")
            if action=="RETRY" and row["status"]=="RETRY_PENDING":
                raise DomainError("CONFLICT","Событие уже находится в очереди автоматического повтора")
            proof=evidence(connection,row["topic"],event)
            state={"VERIFY":row["review_state"],"RETRY":"WAITING","CLOSE":"CLOSED"}[action]
            connection.execute(
                """INSERT INTO event_failure_reviews(failure_id,state,proof) VALUES (%s,%s,%s)
                ON CONFLICT(failure_id) DO UPDATE SET state=excluded.state,proof=excluded.proof,updated_at=now()""",
                (failure_id,state,Jsonb(proof) if proof else None),
            )
            record(connection,failure_id,action,actor.id,reason,event,proof)
            return {"id":failure_id}

        with self.db.connect() as connection:
            receipt(connection,actor,"failureReview",idempotencyKey,dict(id=failure_id,action=action,reason=reason),perform)
        return self.get(actor,failure_id)


def retry_one(module, connection, dispatch, process):
    """Reprocess the stored original under the usual inbox lock and transaction."""
    row=connection.execute(
        """SELECT f.* FROM event_failure_reviews r JOIN event_failures f ON f.id=r.failure_id
        WHERE r.state='WAITING' ORDER BY r.updated_at,r.failure_id FOR UPDATE OF f SKIP LOCKED LIMIT 1"""
    ).fetchone()
    if row is None:
        return
    review = connection.execute(
        "SELECT state FROM event_failure_reviews WHERE failure_id=%s", (row["id"],),
    ).fetchone()
    if review["state"] != "WAITING":
        return
    event=original(row,module.SERVICE)
    proof=None
    state="RETRY_FAILED"
    note="INVALID_ORIGINAL"
    if event is not None:
        try:
            handler,checker=dispatch[row["topic"]]
            with connection.transaction():
                process(module,connection,handler,row["topic"],checker,bytes(row["payload"]))
                proof=evidence(connection,row["topic"],event)
                if not proof or not proof["accepted"]:
                    raise RuntimeError("Missing inbox confirmation")
            state,note="CLOSED",None
        except Exception as error:
            code = getattr(error, "code", None)
            note = code if isinstance(code, str) else type(error).__name__
    connection.execute(
        "UPDATE event_failure_reviews SET state=%s,proof=%s,updated_at=now() WHERE failure_id=%s",
        (state,Jsonb(proof) if proof else None,row["id"]),
    )
    record(connection,row["id"],"RETRY_CONFIRMED" if state=="CLOSED" else "RETRY_FAILED",None,note,event,proof)
