"""Analysis-owned temporal knowledge. No reads from the Case database."""

import json
from copy import deepcopy
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from jsonschema import Draft202012Validator, FormatChecker

from backend.common.behavior_contract import digest
from backend.common.commands import owned_connection
from backend.common.data import DomainError, uuid
from backend.services.analysis.profiles.history_policy import reference, validate_reference
from backend.services.analysis.profiles.observation import timestamp

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "contracts/events/case.feedback.v1.schema.json"
    ).read_text()
)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def canonical(payload):
    p = deepcopy(payload)
    for key in ("case_id", "transaction_id", "client_id"):
        p[key] = uuid(p[key])
    p["state_changed_at"] = timestamp(p["state_changed_at"]).isoformat()
    if p["decision"]:
        p["decision"]["id"] = uuid(p["decision"]["id"])
        p["decision"]["decided_at"] = timestamp(p["decision"]["decided_at"]).isoformat()
    return p


class FeedbackEventService:
    def verify_duplicate(self, c, event):
        row = c.execute(
            "SELECT payload_hash FROM feedback_journal WHERE event_id=%s",
            (event["event_id"],),
        ).fetchone()
        if not row or row["payload_hash"] != digest(canonical(event["payload"])):
            raise DomainError("CONFLICT", "Feedback event ID has conflicting content")

    def process(self, c, event):
        VALIDATOR.validate(event)
        p = canonical(event["payload"])
        fingerprint = digest(p)
        # A singleton row serializes BOTH revision allocation and commit order.
        # Rolling back this txn rolls back the increment, journal and projection.
        c.execute("SELECT revision FROM feedback_revision_state WHERE id=1 FOR UPDATE")
        previous = c.execute(
            "SELECT payload_hash FROM feedback_journal WHERE event_id=%s",
            (event["event_id"],),
        ).fetchone()
        if previous:
            self.verify_duplicate(c, event)
            return
        old = c.execute(
            "SELECT * FROM feedback_projection WHERE case_id=%s", (p["case_id"],)
        ).fetchone()
        if old and (
            str(old["transaction_id"]) != p["transaction_id"]
            or str(old["client_id"]) != p["client_id"]
        ):
            raise DomainError("CONFLICT", "Case feedback binding is immutable")
        other = c.execute(
            "SELECT case_id FROM feedback_projection WHERE transaction_id=%s",
            (p["transaction_id"],),
        ).fetchone()
        if other and str(other["case_id"]) != p["case_id"]:
            raise DomainError("CONFLICT", "Transaction already belongs to another Case")
        same = c.execute(
            "SELECT payload_hash FROM feedback_journal WHERE case_id=%s AND case_version=%s LIMIT 1",
            (p["case_id"], p["case_version"]),
        ).fetchone()
        if same and same["payload_hash"] != fingerprint:
            raise DomainError("CONFLICT", "Case version has conflicting feedback")
        applied = not old or p["case_version"] > old["case_version"]
        revision = c.execute(
            "UPDATE feedback_revision_state SET revision=revision+1 WHERE id=1 RETURNING revision"
        ).fetchone()["revision"]
        c.execute(
            "INSERT INTO feedback_journal VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())",
            (
                revision,
                event["event_id"],
                p["case_id"],
                p["transaction_id"],
                p["client_id"],
                p["case_version"],
                Jsonb(p),
                fingerprint,
                applied,
            ),
        )
        if applied:
            c.execute(
                """INSERT INTO feedback_projection VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(case_id) DO UPDATE SET case_version=excluded.case_version,payload=excluded.payload,knowledge_revision=excluded.knowledge_revision""",
                (
                    p["case_id"],
                    p["transaction_id"],
                    p["client_id"],
                    p["case_version"],
                    Jsonb(p),
                    revision,
                ),
            )


def capture(c, mode):
    # The SHARE lock waits for any in-flight writer to commit. The next statement
    # sees its committed revision under READ COMMITTED. No nextval/time heuristic.
    state = c.execute(
        "SELECT revision FROM feedback_revision_state WHERE id=1 FOR SHARE"
    ).fetchone()
    captured = c.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
    return reference(state["revision"], captured, mode)


def selection(source, ref, rows):
    validate_reference(ref)
    if not rows or ref["revision"] == 0:
        return []
    scope = {str(r["id"]): r.get("clientId") for r in rows}
    # A fresh owned reader can see the frozen committed prefix even if the
    # caller's business transaction started earlier at REPEATABLE READ.
    connection = (
        owned_connection(source)
        if isinstance(source, psycopg.Connection)
        else source.connect()
    )
    with connection as c:
        state = c.execute(
            "SELECT revision FROM feedback_revision_state WHERE id=1"
        ).fetchone()
        if state is None:
            raise DomainError("DATA_ACCESS_ERROR", "Feedback revision state is missing")
        current = state["revision"]
        if ref["revision"] > current:
            raise DomainError("CONFLICT", "Feedback revision is unavailable")
        facts = c.execute(
            """SELECT DISTINCT ON(transaction_id) * FROM feedback_journal
            WHERE transaction_id=ANY(%s::uuid[]) AND knowledge_revision<=%s AND applied
            ORDER BY transaction_id,case_version DESC,knowledge_revision DESC""",
            (list(scope), ref["revision"]),
        ).fetchall()
    result = []
    for row in facts:
        p = row["payload"]
        decision = p["decision"]
        if str(row["client_id"]) != scope[str(row["transaction_id"])]:
            raise DomainError(
                "DATA_ACCESS_ERROR", "Feedback and owner history client differ"
            )
        if (
            p["case_state"] == "CLOSED"
            and decision
            and decision["outcome"] == "UNUSUAL_CONFIRMED"
            and timestamp(decision["decided_at"]) <= timestamp(ref["capturedAt"])
        ):
            result.append(
                dict(
                    transactionId=p["transaction_id"],
                    caseId=p["case_id"],
                    caseVersion=p["case_version"],
                    decisionId=decision["id"],
                    outcome=decision["outcome"],
                    decidedAt=decision["decided_at"],
                    knowledgeRevision=row["knowledge_revision"],
                    learnedAt=row["learned_at"].isoformat(),
                )
            )
    return sorted(result, key=lambda r: r["transactionId"])


def status(db):
    with db.connect() as c:
        revision = c.execute(
            "SELECT revision FROM feedback_revision_state WHERE id=1"
        ).fetchone()["revision"]
        counts = c.execute(
            "SELECT count(*) AS learned,count(*) FILTER(WHERE applied) AS applied,max(learned_at) AS latest FROM feedback_journal"
        ).fetchone()
    return dict(
        revision=revision,
        learned=counts["learned"],
        applied=counts["applied"],
        latestLearnedAt=counts["latest"].isoformat() if counts["latest"] else None,
    )
