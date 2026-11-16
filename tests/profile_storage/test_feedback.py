from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest

from backend.common.data import DomainError
from backend.common.events import make_event
from backend.infrastructure.product_migrations import migrate


def event(
    case=None,
    tx=None,
    client=None,
    version=1,
    outcome="UNUSUAL_CONFIRMED",
    state="CLOSED",
):
    now = datetime.now(UTC) - timedelta(seconds=1)
    decision = (
        None
        if state == "OPEN" or outcome is None
        else dict(id=str(uuid4()), outcome=outcome, decided_at=now.isoformat())
    )
    return make_event(
        dict(
            case_id=case or str(uuid4()),
            transaction_id=tx or str(uuid4()),
            client_id=client or str(uuid4()),
            case_version=version,
            review_cycle=1,
            case_state=state,
            decision=decision,
            state_changed_at=now.isoformat(),
        )
    )


@pytest.fixture
def feedback(database):
    from backend.services.analysis.feedback.events import FeedbackEventService

    db = database("analysis")
    migrate(db, "analysis")
    return db, FeedbackEventService()


def apply(db, handler, e):
    with db.connect() as c:
        handler.process(c, e)


def test_revision_freeze_duplicate_stale_reopen_and_current_knowledge(feedback):
    from backend.services.analysis.feedback.events import capture, selection

    db, handler = feedback
    e = event()
    with db.connect() as c:
        r0 = capture(c, "FIRST_REQUEST_KNOWLEDGE")
    apply(db, handler, e)
    with db.connect() as c:
        r1 = capture(c, "WITH_CURRENT_KNOWLEDGE")
    rows = [dict(id=e["payload"]["transaction_id"], clientId=e["payload"]["client_id"])]
    assert selection(db, r0, rows) == []
    selected = selection(db, r1, rows)
    assert len(selected) == 1 and selected[0]["outcome"] == "UNUSUAL_CONFIRMED"
    apply(db, handler, e)
    assert db.one("SELECT revision FROM feedback_revision_state")["revision"] == 1
    reopen = event(
        e["payload"]["case_id"],
        e["payload"]["transaction_id"],
        e["payload"]["client_id"],
        3,
        state="OPEN",
    )
    apply(db, handler, reopen)
    stale = event(
        e["payload"]["case_id"],
        e["payload"]["transaction_id"],
        e["payload"]["client_id"],
        2,
        outcome="EXPLAINED",
    )
    apply(db, handler, stale)
    with db.connect() as c:
        r3 = capture(c, "WITH_CURRENT_KNOWLEDGE")
    assert selection(db, r3, rows) == [] and selection(db, r1, rows) == selected
    assert db.one("SELECT case_version FROM feedback_projection")["case_version"] == 3


def test_same_version_conflict_and_binding_do_not_change_revision(feedback):
    db, handler = feedback
    e = event()
    apply(db, handler, e)
    p = e["payload"]
    for bad in [
        event(
            p["case_id"], p["transaction_id"], p["client_id"], 1, outcome="EXPLAINED"
        ),
        event(p["case_id"], str(uuid4()), p["client_id"], 2),
    ]:
        with pytest.raises(DomainError):
            apply(db, handler, bad)
    assert db.one("SELECT revision FROM feedback_revision_state")["revision"] == 1


def test_counter_rollback_and_concurrent_commits_form_prefix(feedback):
    from backend.services.analysis.feedback.events import capture

    db, handler = feedback
    with pytest.raises(RuntimeError):
        with db.connect() as c:
            handler.process(c, event())
            raise RuntimeError("crash before commit")
    assert db.one("SELECT revision FROM feedback_revision_state")["revision"] == 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: apply(db, handler, event()), range(8)))
    with db.connect() as c:
        ref = capture(c, "WITH_CURRENT_KNOWLEDGE")
    assert ref["revision"] == 8
    assert [
        r["knowledge_revision"]
        for r in db.query(
            "SELECT knowledge_revision FROM feedback_journal ORDER BY knowledge_revision"
        )
    ] == list(range(1, 9))


def test_journal_is_immutable(feedback):
    db, handler = feedback
    apply(db, handler, event())
    for sql in [
        "DELETE FROM feedback_journal",
        "UPDATE feedback_journal SET payload='{}'",
    ]:
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.connect() as c:
                c.execute(sql)


def test_case_close_reopen_receipt_and_outbox_are_atomic(database, monkeypatch):
    from backend.services.case.lifecycle.feedback import CaseFeedback
    from backend.services.case.persistence.repository import CaseRepository
    from backend.services.case.lifecycle.service import CaseService
    from backend.services.case.events.analysis import CaseEventService

    db = database("case")
    migrate(db, "case")
    actor = SimpleNamespace(id=str(uuid4()), role="MANAGER", is_active=True)
    tx = str(uuid4())
    payload = dict(
        transaction_id=tx,
        client_id=str(uuid4()),
        result_id=str(uuid4()),
        run_id=str(uuid4()),
        score=80,
        threshold=60,
        calculated_at=datetime.now(UTC).isoformat(),
        data_status="SUFFICIENT",
    )
    with db.connect() as c:
        CaseEventService().process(c, make_event(payload))
        c.execute(
            "UPDATE review_cases SET status='IN_PROGRESS',assignee_id=%s WHERE transaction_id=%s",
            (actor.id, tx),
        )
    row = db.one("SELECT * FROM review_cases WHERE transaction_id=%s", (tx,))
    service = CaseService(
        CaseRepository(db), SimpleNamespace(get_current_user=lambda: actor)
    )
    args = dict(
        caseId=str(row["id"]),
        reason="confirmed fixture",
        expectedVersion=row["row_version"],
        outcome="UNUSUAL_CONFIRMED",
        idempotencyKey="close:" + str(uuid4()),
    )
    real = CaseFeedback.publish

    def fail(*a, **k):
        raise RuntimeError("outbox failure")

    monkeypatch.setattr(CaseFeedback, "publish", fail)
    with pytest.raises(RuntimeError):
        service.close_case(actor, args)
    assert (
        db.one(
            "SELECT count(*) AS n FROM case_decisions WHERE case_id=%s", (row["id"],)
        )["n"]
        == 0
    )
    monkeypatch.setattr(CaseFeedback, "publish", real)
    closed = service.close_case(actor, args)
    service.close_case(actor, args)
    assert db.one("SELECT count(*) AS n FROM outbox_events")["n"] == 1
    service.reopen_case(
        actor,
        str(row["id"]),
        "reconsider",
        closed.row_version,
        "reopen:" + str(uuid4()),
    )
    assert db.one("SELECT count(*) AS n FROM outbox_events")["n"] == 2
    latest = db.one(
        "SELECT payload FROM outbox_events ORDER BY created_at DESC,id DESC LIMIT 1"
    )["payload"]["payload"]
    assert latest["case_state"] == "OPEN" and latest["decision"] is None


def test_capture_waits_for_inflight_commit_instead_of_exposing_allocated_revision(
    feedback,
):
    from queue import Queue
    import time
    from backend.services.analysis.feedback.events import capture

    db, handler = feedback
    writer = db.connect()
    try:
        handler.process(writer, event())
        writer_pid = writer.info.backend_pid
        pid = Queue()

        def freeze():
            with db.connect() as c:
                pid.put(c.info.backend_pid)
                return capture(c, "WITH_CURRENT_KNOWLEDGE")

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(freeze)
            reader_pid = pid.get(timeout=2)
            deadline = time.monotonic() + 2
            blocked = False
            while time.monotonic() < deadline:
                row = db.one("SELECT pg_blocking_pids(%s) AS blockers", (reader_pid,))
                if writer_pid in row["blockers"]:
                    blocked = True
                    break
                time.sleep(0.02)
            writer.commit()
            result = future.result(timeout=3)
        assert blocked and result["revision"] == 1
    finally:
        writer.close()


def test_future_business_decision_is_not_applied_to_earlier_capture(feedback):
    from backend.services.analysis.feedback.events import capture, selection

    db, handler = feedback
    e = event()
    e["payload"]["decision"]["decided_at"] = (
        datetime.now(UTC) + timedelta(days=1)
    ).isoformat()
    apply(db, handler, e)
    with db.connect() as c:
        ref = capture(c, "WITH_CURRENT_KNOWLEDGE")
    assert (
        selection(
            db,
            ref,
            [
                dict(
                    id=e["payload"]["transaction_id"],
                    clientId=e["payload"]["client_id"],
                )
            ],
        )
        == []
    )


def test_source_bootstrap_is_full_state_and_idempotent(database):
    from backend.services.case.lifecycle.feedback import CaseFeedback
    from backend.services.case.events.analysis import CaseEventService

    db = database("case")
    migrate(db, "case")
    tx_ids = [str(uuid4()), str(uuid4())]
    with db.connect() as c:
        for tx in tx_ids:
            CaseEventService().process(
                c,
                make_event(
                    dict(
                        transaction_id=tx,
                        client_id=str(uuid4()),
                        result_id=str(uuid4()),
                        run_id=str(uuid4()),
                        score=80,
                        threshold=60,
                        calculated_at=datetime.now(UTC).isoformat(),
                        data_status="SUFFICIENT",
                    )
                ),
            )
        c.execute(
            "UPDATE review_cases SET status='CLOSED',row_version=row_version+1 WHERE transaction_id=%s",
            (tx_ids[0],),
        )
    total = db.one("SELECT count(*) AS n FROM review_cases")["n"]
    assert CaseFeedback.bootstrap(db) == total and CaseFeedback.bootstrap(db) == 0
    events = [
        r
        for r in db.query("SELECT payload FROM outbox_events")
        if r["payload"]["payload"]["transaction_id"] in tx_ids
    ]
    assert {r["payload"]["payload"]["case_state"] for r in events} == {"OPEN", "CLOSED"}
    assert all(r["payload"]["payload"]["decision"] is None for r in events)
    row = db.one("SELECT * FROM review_cases WHERE transaction_id=%s", (tx_ids[0],))
    with db.connect() as c:
        old_id = CaseFeedback.publish(c, str(row["id"]))
    assert len(db.query("SELECT * FROM outbox_events")) == total
    with pytest.raises(DomainError, match="version changed"):
        with db.connect() as c:
            c.execute(
                "UPDATE review_cases SET updated_at=clock_timestamp() WHERE id=%s",
                (row["id"],),
            )
            CaseFeedback.publish(c, str(row["id"]))
    assert db.one(
        "SELECT event_id FROM case_feedback_publications WHERE case_id=%s", (row["id"],)
    )["event_id"] == UUID(old_id)


def test_feedback_status_exposes_counts_without_journal_payload(feedback):
    from backend.services.analysis.feedback.events import status

    db, handler = feedback
    assert status(db) == dict(revision=0, learned=0, applied=0, latestLearnedAt=None)
    apply(db, handler, event())
    value = status(db)
    assert value["revision"] == value["learned"] == value["applied"] == 1
    assert value["latestLearnedAt"] and "payload" not in value
