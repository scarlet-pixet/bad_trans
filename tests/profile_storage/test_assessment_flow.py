from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from backend.common.events import make_event
from backend.infrastructure.product_migrations import migrate
from backend.services.analysis.events.transactions import AnalysisEventService
from backend.services.analysis.profiles.service import ProfileService
from tests.profile_storage.test_flow import HistoryClient


def process(db, client, event):
    with db.connect() as c:
        AnalysisEventService(client).process(c, event)


def test_new_assessment_nullable_complete_records_and_pinned_retry(
    database, monkeypatch
):
    db = database("analysis")
    migrate(db, "analysis")
    client = HistoryClient()
    event = make_event({"transaction_id": client.tx["id"]})
    event["occurred_at"] = client.t.isoformat()
    import backend.services.analysis.events.transactions as module

    real = module.assess

    def fail(*a, **k):
        raise RuntimeError("crash after history checkpoint")

    monkeypatch.setattr(module, "assess", fail)
    with pytest.raises(RuntimeError):
        process(db, client, event)
    frozen = db.one("SELECT * FROM assessment_requests")
    assert (
        frozen["snapshot"]
        and db.one(
            "SELECT count(*) AS n FROM analysis_results WHERE transaction_id=%s",
            (client.tx["id"],),
        )["n"]
        == 0
    )
    # Publish a later definition between attempts; retry must retain the old IDs.
    with db.connect() as c:
        c.execute(
            "INSERT INTO rule_versions SELECT gen_random_uuid(),rule_id,version+1,parameters,0,status,published_by,now() FROM rule_versions WHERE id=%s",
            (frozen["context"]["rules"][0]["id"],),
        )
    monkeypatch.setattr(module, "assess", real)
    process(db, client, event)
    result = db.one(
        "SELECT * FROM analysis_results WHERE transaction_id=%s", (client.tx["id"],)
    )
    assert result["assessment_score"] == 40 and result["assessment_status"] == "PARTIAL"
    assert result["threshold"] == 40 and result["score"] == 40
    context = ProfileService(db, None).get_detection_context(
        SimpleNamespace(role="MANAGER", is_active=True), client.tx["id"]
    )
    assert (
        len(context["checks"]) == 5
        and context["assessmentAlgorithmVersion"] == "behavior-assessment-v3.0"
    )
    assert context["rules"] == frozen["context"]["rules"]
    assert context["assessmentScore"] == sum(
        c["contribution"] for c in context["checks"]
    )
    assert db.one("SELECT * FROM assessment_requests")["context"] == frozen["context"]


def test_unavailable_is_not_false_normal_and_legacy_rows_untouched(database):
    db = database("analysis")
    migrate(db, "analysis")
    client = HistoryClient()
    client.rows = []
    from backend.common.behavior_contract import digest

    client.snapshot.update(itemCount=0, digest=digest([]))
    event = make_event({"transaction_id": client.tx["id"]})
    event["occurred_at"] = client.t.isoformat()
    process(db, client, event)
    r = db.one(
        "SELECT * FROM analysis_results WHERE transaction_id=%s", (client.tx["id"],)
    )
    assert (
        r["assessment_score"] is None
        and r["assessment_status"] == "UNAVAILABLE"
        and r["score"] == 0
    )
    assert r["review_required"] is None

    class Labels:
        def _invoke(self, *a):
            return [
                dict(
                    transaction_id=client.tx["id"],
                    scenario="unavailable",
                    expected=False,
                )
            ]

    q = ProfileService(db, Labels()).get_quality(
        SimpleNamespace(role="MANAGER", is_active=True), str(uuid4())
    )
    assert q["pending"] == 1 and q["evaluated"] == 0
    assert (
        db.one(
            "SELECT count(*) AS n FROM analysis_results WHERE assessment_status IS NULL"
        )["n"]
        > 0
    )


def test_published_rule_and_request_are_immutable(database):
    db = database("analysis")
    migrate(db, "analysis")
    client = HistoryClient()
    event = make_event({"transaction_id": client.tx["id"]})
    process(db, client, event)
    for statement in [
        "UPDATE rule_versions SET weight=1 WHERE status='PUBLISHED'",
        "UPDATE assessment_requests SET context='{}'",
        "UPDATE assessment_requests SET snapshot=NULL",
    ]:
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.connect() as c:
                c.execute(statement)


def test_case_new_unavailable_legacy_reanalysis_preserves_decision(database):
    from backend.services.case.events.analysis import CaseEventService
    from datetime import UTC, datetime

    db = database("case")
    migrate(db, "case")
    tx = str(uuid4())
    client = str(uuid4())
    now = datetime.now(UTC)

    def event(score, status=None, offset=0):
        p = dict(
            result_id=str(uuid4()),
            transaction_id=tx,
            client_id=client,
            run_id=str(uuid4()),
            score=score,
            threshold=40 if status else 60,
            data_status="INSUFFICIENT",
            calculated_at=(now + timedelta(seconds=offset)).isoformat(),
        )
        if status:
            p.update(
                assessment_score=None if status == "UNAVAILABLE" else score,
                assessment_status=status,
                assessment_policy_version="behavior-demo-v1",
                review_required=None if status == "UNAVAILABLE" else score >= 40,
            )
        return make_event(p)

    e = event(40, "PARTIAL")
    with db.connect() as c:
        CaseEventService().process(c, e)
    row = db.one("SELECT * FROM review_cases WHERE transaction_id=%s", (tx,))
    assert row and row["priority"] == "LOW"
    with db.connect() as c:
        c.execute("UPDATE review_cases SET status='CLOSED' WHERE id=%s", (row["id"],))
        c.execute(
            "INSERT INTO case_decisions VALUES (%s,%s,%s,1,'EXPLAINED','test',now())",
            (str(uuid4()), row["id"], str(uuid4())),
        )
    for e in [event(0, "UNAVAILABLE", 1), event(0, "COMPLETE", 2), event(80, None, 3)]:
        with db.connect() as c:
            CaseEventService().process(c, e)
    last = db.one("SELECT * FROM review_cases WHERE transaction_id=%s", (tx,))
    assert (
        last["id"] == row["id"]
        and last["status"] == "CLOSED"
        and last["latest_score"] == 80
    )
    assert (
        db.one(
            "SELECT count(*) AS n FROM case_decisions WHERE case_id=%s", (row["id"],)
        )["n"]
        == 1
    )


def test_database_rejects_score_that_does_not_equal_check_contributions(
    database, monkeypatch
):
    import backend.services.analysis.events.transactions as module

    db = database("analysis")
    migrate(db, "analysis")
    client = HistoryClient()
    event = make_event({"transaction_id": client.tx["id"]})
    real = module.assess

    def corrupt(*args, **kwargs):
        result = real(*args, **kwargs)
        result["assessmentScore"] += 1
        return result

    monkeypatch.setattr(module, "assess", corrupt)
    with pytest.raises(psycopg.errors.CheckViolation):
        process(db, client, event)
    assert (
        db.one(
            "SELECT count(*) AS n FROM analysis_results WHERE transaction_id=%s",
            (client.tx["id"],),
        )["n"]
        == 0
    )


@pytest.mark.parametrize(
    "ids",
    [
        [],
        ["bad-id"],
        ["00000000-0000-0000-0000-000000000001"],
        ["22e19a56-c9b2-5650-865f-d8aef08d428a"],
    ],
)
def test_invalid_or_legacy_selected_rules_fail_before_owner_call(database, ids):
    from backend.common.data import DomainError

    db = database("analysis")
    migrate(db, "analysis")
    client = HistoryClient()
    event = make_event({"transaction_id": client.tx["id"]})

    def forbidden(*a):
        raise AssertionError("Owner must not be called for invalid configuration")

    client.get_transaction = forbidden
    with pytest.raises(DomainError):
        with db.connect() as c:
            AnalysisEventService(client).process(c, event, rule_ids=ids)


def test_case_legacy_high_then_unavailable_preserves_priority(database):
    from backend.services.case.events.analysis import CaseEventService
    from datetime import UTC, datetime

    db = database("case")
    migrate(db, "case")
    tx = str(uuid4())
    stamp = datetime.now(UTC)
    payload = dict(
        result_id=str(uuid4()),
        transaction_id=tx,
        client_id=str(uuid4()),
        run_id=str(uuid4()),
        score=80,
        threshold=60,
        data_status="SUFFICIENT",
        calculated_at=stamp.isoformat(),
    )
    with db.connect() as c:
        CaseEventService().process(c, make_event(payload))
    high = db.one("SELECT * FROM review_cases WHERE transaction_id=%s", (tx,))
    assert high["priority"] == "HIGH"
    payload.update(
        result_id=str(uuid4()),
        score=0,
        threshold=40,
        assessment_score=None,
        assessment_status="UNAVAILABLE",
        assessment_policy_version="behavior-demo-v1",
        review_required=None,
        calculated_at=(stamp + timedelta(seconds=1)).isoformat(),
    )
    with db.connect() as c:
        CaseEventService().process(c, make_event(payload))
    latest = db.one("SELECT * FROM review_cases WHERE transaction_id=%s", (tx,))
    assert (
        latest["priority"] == "HIGH"
        and latest["initial_result_id"] == high["initial_result_id"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"assessment_status": "BAD"},
        {"assessment_score": None},
        {"review_required": False},
        {"assessment_policy_version": "unrecognized"},
    ],
)
def test_case_rejects_inconsistent_new_contract(database, changes):
    from backend.services.case.events.analysis import CaseEventService
    from datetime import UTC, datetime

    db = database("case")
    migrate(db, "case")
    payload = dict(
        result_id=str(uuid4()),
        transaction_id=str(uuid4()),
        client_id=str(uuid4()),
        run_id=str(uuid4()),
        score=40,
        threshold=40,
        data_status="SUFFICIENT",
        calculated_at=datetime.now(UTC).isoformat(),
        assessment_score=40,
        assessment_status="COMPLETE",
        assessment_policy_version="behavior-demo-v1",
        review_required=True,
    )
    payload.update(changes)
    with pytest.raises(ValueError):
        with db.connect() as c:
            CaseEventService().process(c, make_event(payload))
