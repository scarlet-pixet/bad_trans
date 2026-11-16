"""Feedback knowledge is pinned independently of source-history time."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from backend.common.events import make_event
from backend.infrastructure.product_migrations import migrate
from backend.services.analysis.assessments.requests import freeze
from backend.services.analysis.events.transactions import AnalysisEventService
from backend.services.analysis.feedback.events import FeedbackEventService
from tests.profile_storage.test_feedback import event
from tests.profile_storage.test_flow import HistoryClient
from tests.profile_storage.test_refresh_jobs import Owner


@pytest.fixture
def jobs(database):
    from backend.services.analysis.profiles.refresh import ProfileRefresh

    db = database("analysis")
    migrate(db, "analysis")
    group = dict(clientId=str(uuid4()), currency="BYN", direction="OUT")
    owner = Owner([group])
    return db, owner, ProfileRefresh(db, owner), group


def publish(db, client):
    e = event(tx=client.rows[0]["id"], client=client.tx["clientId"])
    with db.connect() as c:
        FeedbackEventService().process(c, e)
    return e


def metric(db, result):
    return db.one(
        "SELECT metrics FROM behavior_profiles WHERE id=%s", (result["profile_id"],)
    )["metrics"]


def test_failed_assessment_replay_keeps_r0_new_manual_analysis_uses_r1(
    database, monkeypatch
):
    db = database("analysis")
    migrate(db, "analysis")
    owner = HistoryClient()
    e = make_event(dict(transaction_id=owner.tx["id"]))
    import backend.services.analysis.events.transactions as module

    real = module.assess

    def fail(*args, **kwargs):
        raise RuntimeError("failed after durable context/history")

    monkeypatch.setattr(module, "assess", fail)
    with pytest.raises(RuntimeError):
        with db.connect() as c:
            AnalysisEventService(owner).process(c, e)
    frozen = db.one("SELECT * FROM assessment_requests")
    assert frozen["context"]["feedbackReference"]["revision"] == 0
    confirmed = publish(db, owner)
    monkeypatch.setattr(module, "assess", real)
    with db.connect() as c:
        AnalysisEventService(owner).process(c, e)
    first = db.one(
        "SELECT * FROM analysis_results WHERE transaction_id=%s", (owner.tx["id"],)
    )
    p0 = metric(db, first)
    assert p0["observedCount"] == p0["includedCount"] == 20 and p0["excludedCount"] == 0
    with db.connect() as c:
        AnalysisEventService(owner).process(
            c, e, initiated_by=str(uuid4()), request_key="new:" + uuid4().hex
        )
    results = db.query(
        "SELECT * FROM analysis_results WHERE transaction_id=%s ORDER BY calculated_at",
        (owner.tx["id"],),
    )
    p1 = metric(db, results[-1])
    assert (
        p1["observedCount"] == 20
        and p1["includedCount"] == 19
        and p1["excludedCount"] == 1
    )
    assert p1["feedbackReference"]["knowledgeMode"] == "WITH_CURRENT_KNOWLEDGE"
    assert p1["exclusions"][0]["decisionId"] == confirmed["payload"]["decision"]["id"]
    assert metric(db, first) == p0
    assert (
        db.one(
            "SELECT context FROM assessment_requests WHERE request_key=%s",
            (frozen["request_key"],),
        )["context"]
        == frozen["context"]
    )


def test_pending_legacy_request_keeps_all_observed_and_original_context(database):
    db = database("analysis")
    migrate(db, "analysis")
    owner = HistoryClient()
    e = make_event(dict(transaction_id=owner.tx["id"]))
    with db.connect() as c:
        template = freeze(c, owner, e, None, "template:" + uuid4().hex)
    context = dict(template["context"])
    context.pop("feedbackReference")
    key = "legacy:" + uuid4().hex
    with db.connect() as c:
        c.execute(
            "INSERT INTO assessment_requests(request_key,intent_hash,context) VALUES (%s,%s,%s)",
            (key, template["intent_hash"], Jsonb(context)),
        )
    publish(db, owner)
    with db.connect() as c:
        AnalysisEventService(owner).process(c, e, request_key=key)
    result = db.one(
        "SELECT * FROM analysis_results WHERE transaction_id=%s", (owner.tx["id"],)
    )
    profile = metric(db, result)
    assert (
        profile["sampleSize"] == 20
        and profile["algorithmVersion"] == "behavior-profile-v3.1"
    )
    assert profile["metricsSchemaVersion"] == 3 and "feedbackReference" not in profile
    assert (
        db.one("SELECT context FROM assessment_requests WHERE request_key=%s", (key,))[
            "context"
        ]
        == context
    )


def test_feedback_enqueues_second_job_in_same_slot_and_retry_keeps_old_revision(jobs):
    db, owner, worker, group = jobs
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    worker.discover()
    assert worker.enqueue(now=now) == 1
    owner.fail = True
    assert worker.drain(batch=1) == 0
    old = db.one("SELECT * FROM profile_refresh_jobs")
    with db.connect() as c:
        FeedbackEventService().process(c, event(client=group["clientId"]))
        c.execute("UPDATE profile_refresh_jobs SET available_at=now()")
    owner.fail = False
    assert worker.drain(batch=1) == 1
    assert (
        db.one("SELECT context FROM profile_refresh_jobs WHERE id=%s", (old["id"],))[
            "context"
        ]
        == old["context"]
    )
    assert worker.enqueue(now=now) == 1
    assert worker.drain(batch=1) == 1
    jobs_ = db.query("SELECT * FROM profile_refresh_jobs ORDER BY feedback_revision")
    assert [j["feedback_revision"] for j in jobs_] == [0, 1]
    assert jobs_[0]["as_of"] == jobs_[1]["as_of"]
    assert db.one("SELECT count(*) AS n FROM profile_versions")["n"] == 6


def test_legacy_scheduled_request_blocked_then_new_context_enqueued(jobs):
    db, owner, worker, group = jobs
    from datetime import UTC, datetime
    from backend.services.analysis.profiles.history_policy import LEGACY_VERSION, LEGACY_HASH

    context = dict(
        clientId=group["clientId"],
        currency="BYN",
        direction="OUT",
        asOf=datetime.now(UTC).isoformat(),
        ingestedBefore=datetime.now(UTC).isoformat(),
        algorithmVersion="behavior-profile-v3.1",
        metricsSchemaVersion=3,
        historyPolicyVersion=LEGACY_VERSION,
        historyPolicyHash=LEGACY_HASH,
    )
    worker.discover()
    with db.connect() as c:
        c.execute(
            "INSERT INTO profile_refresh_jobs(id,client_id,currency,direction,as_of,algorithm_version,context) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid4()),
                group["clientId"],
                "BYN",
                "OUT",
                context["asOf"],
                context["algorithmVersion"],
                Jsonb(context),
            ),
        )
    assert worker.drain(batch=1) == 0
    old = db.one("SELECT * FROM profile_refresh_jobs")
    assert (
        old["status"] == "BLOCKED"
        and old["last_error"] == "VERSION_MISMATCH"
        and old["context"] == context
    )
    assert worker.enqueue() == 1 and worker.drain(batch=1) == 1


def test_manual_profile_first_attempt_is_durable_even_when_command_rolls_back(database):
    from backend.services.analysis.profiles.requests import prepare

    db = database("analysis")
    migrate(db, "analysis")
    owner = HistoryClient()
    actor = SimpleNamespace(id=str(uuid4()))
    key = uuid4().hex
    with pytest.raises(RuntimeError):
        with db.connect() as c:
            first = prepare(c, owner.tx, owner.t, actor.id, key)
            raise RuntimeError("rollback business command")
    publish(db, owner)
    with db.connect() as c:
        retry = prepare(c, owner.tx, owner.t, actor.id, key)
    assert (
        retry["context"] == first["context"]
        and retry["context"]["feedbackReference"]["revision"] == 0
    )
