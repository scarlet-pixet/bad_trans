from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.common.data import DomainError
from backend.infrastructure.product_migrations import migrate
from backend.services.analysis.assessments.calculation import default_rules
from backend.services.analysis.assessments.period_queries import compare, get
from backend.services.analysis.assessments.period_worker import MAX_ATTEMPTS, PeriodReassessment
from backend.services.analysis.events.transactions import AnalysisEventService
from backend.services.transaction.history.assessment_selection import prepare
from backend.services.transaction.history.snapshots import HistorySnapshots


def actor(role="MANAGER"):
    return SimpleNamespace(id=str(uuid4()), role=role, is_active=True)


class Owner:
    def __init__(self, db):
        self.db = db
        self.system = actor("SYSTEM")
        self.history = HistorySnapshots(db)

    def _invoke(self, method, arguments):
        assert method == "prepare_assessment_selection"
        return prepare(self.db, self.system, **arguments)

    def get_transaction(self, identifier):
        row = self.db.one(
            """SELECT t.*,a.client_id FROM transactions t JOIN accounts a ON a.id=t.account_id
            WHERE t.id=%s""",
            (identifier,),
        )
        return self.history.item(row) if row else None

    def prepare_behavior_history(self, parameters):
        return self.history.prepare(parameters)

    def get_behavior_history_page(self, parameters):
        return self.history.page(parameters)


def test_period_resumes_after_rollback_and_preserves_result_identity(database, monkeypatch):
    transaction, analysis = database("transaction"), database("analysis")
    migrate(transaction, "transaction")
    migrate(analysis, "analysis")
    client_id, account_id, subject, late = (str(uuid4()) for _ in range(4))
    cutoff = datetime.now(UTC).replace(microsecond=0) - timedelta(days=2)
    with transaction.connect() as connection:
        connection.execute("INSERT INTO bank_clients VALUES (%s,%s,'Audit','AUDIT',true)", (client_id, client_id))
        connection.execute("INSERT INTO accounts VALUES (%s,%s,%s,'BYN','OPEN')",
                           (account_id, client_id, account_id))
    statement = """INSERT INTO transactions
        (id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at)
        VALUES (%s,'period-test',%s,%s,100,'BYN','OUT',%s,%s)"""
    with transaction.connect() as connection:
        connection.execute(statement, (subject, subject, account_id, cutoff, cutoff))
    manager = actor()
    service = PeriodReassessment(analysis, Owner(transaction))
    rule_id = default_rules()[0]["id"]
    request = dict(period={"from": cutoff.isoformat(), "to": (cutoff + timedelta(seconds=1)).isoformat()},
                   clientId=client_id, ruleVersionIds=[rule_id], idempotencyKey="period")
    run_id = service.start(manager, request)["id"]
    assert service.start(manager, request)["id"] == run_id
    with pytest.raises(DomainError, match="Ключ уже использован"):
        service.start(manager, dict(request, clientId=str(uuid4())))
    service.tick()

    original = AnalysisEventService.process

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt("after publication inside the transaction")

    monkeypatch.setattr(AnalysisEventService, "process", crash)
    with pytest.raises(KeyboardInterrupt):
        service.tick()
    assert analysis.one("SELECT count(*) AS n FROM analysis_results WHERE run_id=%s", (run_id,))["n"] == 0
    assert get(analysis, manager, run_id, 0, 1)["results"][0]["state"] == "RUNNING"
    monkeypatch.setattr(AnalysisEventService, "process", original)

    with transaction.connect() as connection:
        connection.execute(statement, (late, late, account_id, cutoff, datetime.now(UTC)))
    service.tick()
    result = get(analysis, manager, run_id, 0, 1)
    row = result["results"][0]
    assert result["status"] == "COMPLETED" and result["counters"]["unavailable"] == 1
    assert str(row["transactionId"]) == subject and row["attempts"] == 2 and row["id"]
    assert result["nextOffset"] is None
    assert analysis.one(
        "SELECT count(*) AS n FROM outbox_events WHERE payload->'payload'->>'run_id'=%s", (run_id,)
    )["n"] == 1

    next_id = service.start(manager, dict(request, idempotencyKey="next"))["id"]
    service.tick()
    service.tick()
    comparison = compare(analysis, manager, run_id, next_id, 0, 1)
    assert comparison["commonCount"] == 1 and comparison["onlySecondCount"] == 1
    assert str(comparison["items"][0]["transactionId"]) == late
    assert get(analysis, manager, run_id, 0, 1)["results"][0]["id"] == row["id"]


def test_transient_owner_failure_has_a_retry_limit(database):
    analysis = database("analysis")
    migrate(analysis, "analysis")
    attempts = []

    def unavailable(method, arguments):
        attempts.append((method, arguments["requestKey"]))
        raise DomainError("DEPENDENCY_UNAVAILABLE", "Transaction offline")

    service = PeriodReassessment(analysis, SimpleNamespace(_invoke=unavailable))
    manager = actor()
    now = datetime.now(UTC)
    request = dict(
        period={"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
        ruleVersionIds=[default_rules()[0]["id"]], idempotencyKey="offline",
    )
    run_id = service.start(manager, request)["id"]
    for attempt in range(1, MAX_ATTEMPTS + 1):
        service.tick()
        result = get(analysis, manager, run_id, 0, 1)
        assert result["discoveryAttempts"] == attempt
        assert result["status"] == ("FAILED" if attempt == MAX_ATTEMPTS else "PENDING")
        assert result["error"] == "DEPENDENCY_UNAVAILABLE"
        with analysis.connect() as connection:
            connection.execute("UPDATE period_reassessment_jobs SET available_at=now() WHERE run_id=%s", (run_id,))
    assert not service.tick()
    assert attempts == [("prepare_assessment_selection", "period:" + run_id)] * MAX_ATTEMPTS
