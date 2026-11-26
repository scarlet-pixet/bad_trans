"""Own the durable queue and publish one assessment per operation and run."""
import logging
import time
from datetime import UTC

from psycopg import OperationalError
from psycopg.types.json import Jsonb

from backend.common.behavior_contract import digest
from backend.common.commands import require_role
from backend.common.data import DomainError, uuid
from backend.common.events import ident, make_event
from backend.common.reassessment_contract import MAX_OPERATIONS, selection
from backend.common.receipts import receipt
from backend.infrastructure.resilience import current_trace
from backend.services.analysis.assessments.calculation import POLICY, validate_rules
from backend.services.analysis.assessments.comparison import COMPARISON_POLICY
from backend.services.analysis.events.transactions import AnalysisEventService
from backend.services.analysis.feedback.events import capture
from backend.services.analysis.profiles.bulk import policy as profile_policy
from backend.services.analysis.profiles.observation import timestamp

MAX_ATTEMPTS = 5
BATCH_SIZE = 10
RETRY_DELAY = 3
BUSY = ("PENDING", "RUNNING")
RETRY_CODES = {"DATA_ACCESS_ERROR", "DEPENDENCY_UNAVAILABLE", "UNAVAILABLE", "TIMEOUT"}


def failure(exc):
    if isinstance(exc, DomainError):
        reason = str(exc).split(":", 1)[0]
        if reason in ("HISTORY_LIMIT", "PERIOD_LIMIT", "VERSION_MISMATCH"):
            return reason, False
        return exc.code, exc.code in RETRY_CODES
    if isinstance(exc, (OperationalError, TimeoutError)):
        return "DEPENDENCY_UNAVAILABLE", True
    return "INTERNAL", False


class PeriodReassessment:
    def __init__(self, db, client):
        self.db = db
        self.client = client

    def start(self, actor, input):
        require_role(actor, "MANAGER")
        scope = selection(input["period"], input.get("clientId"))
        versions = input["ruleVersionIds"]
        if not isinstance(versions, list) or not 1 <= len(versions) <= 5:
            raise DomainError("BAD_INPUT", "Выберите от одной до пяти версий правил")
        ids = sorted(uuid(value) for value in versions)
        if len(ids) != len(set(ids)):
            raise DomainError("BAD_INPUT", "Версии правил должны различаться")
        intent = dict(scope, ruleVersionIds=ids)

        def enqueue(connection):
            rows = connection.execute(
                """SELECT v.*,r.code FROM rule_versions v JOIN analysis_rules r ON r.id=v.rule_id
                WHERE v.status='PUBLISHED' AND v.id=ANY(%s::uuid[])""",
                (ids,),
            ).fetchall()
            if len(rows) != len(ids):
                raise DomainError("BAD_INPUT", "Версия правила отсутствует или не опубликована")
            rules = validate_rules(rows)
            reference = capture(connection, "WITH_CURRENT_KNOWLEDGE")
            context = dict(
                rules=rules,
                policy=POLICY,
                profilePolicy=profile_policy(),
                comparisonPolicy=COMPARISON_POLICY,
                feedbackReference=reference,
                ingestedBefore=timestamp(reference["capturedAt"]).astimezone(UTC).isoformat(),
            )
            run_id = ident()
            connection.execute(
                """INSERT INTO analysis_runs
                (id,mode,scope,cutoff,status,initiated_by,counters,created_at)
                VALUES (%s,'REANALYSIS',%s,%s,'PENDING',%s,'{}',%s)""",
                (run_id, Jsonb(intent), scope["period"]["to"], actor.id, context["ingestedBefore"]),
            )
            connection.execute(
                "INSERT INTO period_reassessment_jobs(run_id,context,correlation_id) VALUES (%s,%s,%s)",
                (run_id, Jsonb(context), current_trace()),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO run_rule_versions(run_id,version_id) VALUES (%s,%s)",
                    [(run_id, version) for version in ids],
                )
            return dict(id=run_id)

        with self.db.connect() as connection:
            result = receipt(connection, actor, "startPeriodReassessment", input["idempotencyKey"], intent, enqueue)
        row = self.db.one("SELECT id,status,created_at FROM analysis_runs WHERE id=%s", (result["id"],))
        return dict(id=result["id"], status=row["status"], createdAt=row["created_at"].isoformat())

    def discover(self, connection, run):
        scope = selection(run["scope"]["period"], run["scope"]["clientId"])
        watermark = run["context"]["ingestedBefore"]
        snapshot = self.client._invoke(
            "prepare_assessment_selection",
            dict(requestKey="period:" + str(run["run_id"]), scope=scope, ingestedBefore=watermark),
        )
        ids = snapshot["ids"]
        if (
            snapshot["parameters"] != dict(scope=scope, ingestedBefore=watermark)
            or not isinstance(ids, list)
            or len(ids) > MAX_OPERATIONS
            or len(ids) != len({uuid(value) for value in ids})
            or digest(ids) != snapshot["digest"]
        ):
            raise DomainError("DATA_ACCESS_ERROR", "Состав периода не соответствует ответу Transaction")
        summary = dict(digest=snapshot["digest"], count=len(ids), capturedAt=snapshot["capturedAt"])
        with connection.transaction():
            if ids:
                connection.execute(
                    """INSERT INTO period_reassessment_items(run_id,transaction_id,position)
                    SELECT %s,id,position FROM unnest(%s::uuid[]) WITH ORDINALITY AS rows(id,position)""",
                    (run["run_id"], ids),
                )
            connection.execute(
                "UPDATE period_reassessment_jobs SET selection=%s,last_error=NULL WHERE run_id=%s",
                (Jsonb(summary), run["run_id"]),
            )
            self.update_progress(connection, run["run_id"])

    def process_item(self, connection, run, item):
        key = (run["run_id"], item["transaction_id"])
        attempts = item["attempts"] + 1
        if attempts > MAX_ATTEMPTS:
            self.fail_item(connection, key, "INTERRUPTED", False)
            return
        connection.execute(
            """UPDATE period_reassessment_items SET attempts=%s,state='RUNNING'
            WHERE run_id=%s AND transaction_id=%s""",
            (attempts, *key),
        )
        try:
            with connection.transaction():
                event = make_event({"transaction_id": str(key[1])})
                event["correlation_id"] = run["correlation_id"]
                AnalysisEventService(self.client).process(
                    connection, event, run_id=str(key[0]), rule_ids=run["scope"]["ruleVersionIds"],
                    initiated_by=str(run["initiated_by"]),
                    ingested_before=run["context"]["ingestedBefore"],
                    request_key="period:" + digest([str(value) for value in key]),
                    frozen_context=run["context"],
                )
                connection.execute(
                    """UPDATE period_reassessment_items SET state='COMPLETED',last_error=NULL,finished_at=now()
                    WHERE run_id=%s AND transaction_id=%s""",
                    key,
                )
        except Exception as exc:
            if connection.closed:
                raise
            code, retryable = failure(exc)
            logging.warning("period.reassessment.item run=%s operation=%s error=%s type=%s",
                            *key, code, type(exc).__name__)
            self.fail_item(connection, key, code, retryable and attempts < MAX_ATTEMPTS)

    @staticmethod
    def fail_item(connection, key, code, retry):
        connection.execute(
            """UPDATE period_reassessment_items SET state=%s,last_error=%s,
                available_at=now()+%s*interval '1 second',finished_at=CASE WHEN %s THEN NULL ELSE now() END
                WHERE run_id=%s AND transaction_id=%s""",
            ("RETRY" if retry else "FAILED", code, RETRY_DELAY, retry, *key),
        )

    @staticmethod
    def update_progress(connection, run_id):
        row = connection.execute(
            """SELECT count(*)::int AS total,
                count(*) FILTER (WHERE i.state='FAILED')::int AS failed,
                count(*) FILTER (WHERE i.state='RETRY')::int AS retrying,
                count(*) FILTER (WHERE i.state IN ('PENDING','RUNNING','RETRY'))::int AS pending,
                count(*) FILTER (WHERE i.state='COMPLETED')::int AS processed,
                count(*) FILTER (WHERE a.assessment_status='UNAVAILABLE')::int AS unavailable
                FROM period_reassessment_items i LEFT JOIN analysis_results a USING (run_id, transaction_id)
                WHERE i.run_id=%s""",
            (run_id,),
        ).fetchone()
        status = "RUNNING"
        if not row["pending"]:
            status = "COMPLETED"
            if row["failed"]:
                status = "PARTIAL" if row["processed"] else "FAILED"
        connection.execute(
            """UPDATE analysis_runs SET status=%s,counters=%s,
                completed_at=CASE WHEN %s THEN now() ELSE NULL END WHERE id=%s""",
            (status, Jsonb(dict(row, assessed=row["processed"] - row["unavailable"])), status != "RUNNING", run_id),
        )

    def tick(self):
        candidates = self.db.query(
            """SELECT j.run_id FROM period_reassessment_jobs j JOIN analysis_runs r ON r.id=j.run_id
                WHERE r.status IN ('PENDING','RUNNING') AND j.available_at<=now()
                ORDER BY j.available_at,r.created_at,r.id LIMIT 16"""
        )
        worked = False
        for candidate in candidates:
            with self.db.connect() as connection:
                connection.autocommit = True
                key = candidate["run_id"]
                lock = connection.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired",
                    ("period-reassessment:" + str(key),),
                ).fetchone()
                if not lock["acquired"]:
                    continue
                run = connection.execute(
                    """SELECT j.*,r.status,r.scope,r.initiated_by FROM period_reassessment_jobs j
                    JOIN analysis_runs r ON r.id=j.run_id WHERE j.run_id=%s AND j.available_at<=now()""",
                    (key,),
                ).fetchone()
                if not run or run["status"] not in BUSY:
                    continue
                # Session advisory lock is released when this owned connection closes.
                if run["context"]["policy"] != POLICY or run["context"]["profilePolicy"] != profile_policy():
                    self.fail_job(connection, run, "VERSION_MISMATCH", False)
                    worked = True
                    continue
                if run["selection"] is None:
                    try:
                        self.discover(connection, run)
                    except Exception as exc:
                        if connection.closed:
                            raise
                        self.fail_job(connection, run, *failure(exc))
                    worked = True
                    continue
                items = connection.execute(
                    """SELECT transaction_id,attempts FROM period_reassessment_items
                    WHERE run_id=%s AND state IN ('PENDING','RUNNING','RETRY') AND available_at<=now()
                    ORDER BY position LIMIT %s""",
                    (key, BATCH_SIZE),
                ).fetchall()
                for item in items:
                    self.process_item(connection, run, item)
                # Also finalizes a run interrupted after its last item commit.
                self.update_progress(connection, key)
                worked = worked or bool(items)
        return worked

    @staticmethod
    def fail_job(connection, run, code, retryable):
        attempts = run["discovery_attempts"] + 1
        retry = retryable and attempts < MAX_ATTEMPTS
        with connection.transaction():
            connection.execute(
                """UPDATE period_reassessment_jobs SET discovery_attempts=%s,last_error=%s,
                    available_at=now()+%s*interval '1 second' WHERE run_id=%s""",
                (attempts, code, RETRY_DELAY, run["run_id"]),
            )
            if not retry:
                if run["selection"] is not None:
                    connection.execute(
                        """UPDATE period_reassessment_items SET state='FAILED',last_error=%s,finished_at=now()
                        WHERE run_id=%s AND state IN ('PENDING','RUNNING','RETRY')""",
                        (code, run["run_id"]),
                    )
                    PeriodReassessment.update_progress(connection, run["run_id"])
                else:
                    connection.execute(
                        "UPDATE analysis_runs SET status='FAILED',completed_at=now() WHERE id=%s", (run["run_id"],)
                    )

    def loop(self):
        while True:
            try:
                if self.tick():
                    continue
            except Exception:
                logging.exception("period.reassessment.worker")
            time.sleep(1)
