from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import psycopg

from backend.common.behavior_contract import digest
from backend.infrastructure.product_migrations import migrate

T = datetime(2026, 11, 9, 12, tzinfo=UTC)


class Owner:
    def __init__(self, groups):
        self.groups = groups
        self.snapshots = {}
        self.fail = False
        self.prepares = []

    def get_behavior_groups(self, **kwargs):
        return {"items": self.groups, "nextPageToken": None}

    def prepare_behavior_history(self, p):
        self.prepares.append(p.copy())
        if p["requestKey"] not in self.snapshots:
            rows = []
            self.snapshots[p["requestKey"]] = {
                "snapshotId": str(uuid4()),
                "digest": digest(rows),
                "itemCount": 0,
                "capturedAt": T.isoformat(),
                "selection": p,
                "coverage": {
                    "mode": "UNKNOWN",
                    "revision": "unknown-v1",
                    "intervals": [],
                    "declaredBySource": None,
                },
            }
        if getattr(self, "lose_reply", False):
            self.lose_reply = False
            raise TimeoutError("owner committed but response lost")
        return self.snapshots[p["requestKey"]]

    def get_behavior_history_page(self, p):
        if self.fail:
            raise RuntimeError("simulated owner failure")
        s = next(
            x for x in self.snapshots.values() if x["snapshotId"] == p["snapshotId"]
        )
        if s["selection"]["clientId"] in getattr(self, "fail_clients", set()):
            raise RuntimeError("simulated group failure")
        return {
            "snapshotId": s["snapshotId"],
            "digest": s["digest"],
            "itemCount": 0,
            "items": [],
            "nextPageToken": None,
        }


@pytest.fixture
def jobs(database):
    from backend.services.analysis.profiles.refresh import ProfileRefresh

    db = database("analysis")
    migrate(db, "analysis")
    group = {"clientId": str(uuid4()), "currency": "BYN", "direction": "OUT"}
    owner = Owner([group])
    return db, owner, ProfileRefresh(db, owner), group


def test_discovery_creates_empty_group_and_atomic_three_windows(jobs):
    db, owner, w, g = jobs
    assert w.discover() == 1
    assert w.enqueue(now=T, refresh_seconds=300, batch=10) == 1
    assert w.drain(batch=10, budget_seconds=30) == 1
    assert w.drain(batch=10, budget_seconds=30) == 0
    assert db.one("SELECT count(*) AS n FROM profile_versions")["n"] == 3
    job = db.one("SELECT * FROM profile_refresh_jobs")
    assert job["status"] == "COMPLETED" and job["calculation_id"]
    assert (
        job["snapshot"]["snapshotId"]
        == next(iter(owner.snapshots.values()))["snapshotId"]
    )


def test_failed_retry_preserves_request_watermark_snapshot_and_algorithm(jobs):
    db, owner, w, g = jobs
    w.discover()
    w.enqueue(now=T, refresh_seconds=300, batch=10)
    owner.fail = True
    assert w.drain(batch=1, budget_seconds=30) == 0
    first = db.one("SELECT * FROM profile_refresh_jobs")
    assert first["status"] == "RETRY" and first["snapshot"]
    owner.fail = False
    with db.connect() as c:
        c.execute(
            "UPDATE profile_refresh_jobs SET available_at=now() WHERE id=%s",
            (first["id"],),
        )
    assert w.drain(batch=1, budget_seconds=30) == 1
    last = db.one("SELECT * FROM profile_refresh_jobs")
    assert last["context"] == first["context"] and last["snapshot"] == first["snapshot"]
    assert len(owner.prepares) == 1 and last["attempts"] == 2
    assert db.one("SELECT count(*) AS n FROM profile_calculations")["n"] == 1


def test_multiworker_enqueue_and_drain_do_not_duplicate(jobs):
    db, owner, w, g = jobs
    w.discover()
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(
            pool.map(
                lambda _: w.enqueue(now=T, refresh_seconds=300, batch=10), range(3)
            )
        )
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: w.drain(batch=2, budget_seconds=30), range(3)))
    assert db.one("SELECT count(*) AS n FROM profile_refresh_jobs")["n"] == 1
    assert db.one("SELECT count(*) AS n FROM profile_versions")["n"] == 3


def test_running_job_recovers_after_connection_loss_without_lease(jobs):
    db, owner, w, g = jobs
    w.discover()
    w.enqueue(now=T, refresh_seconds=300, batch=1)
    with db.connect() as c:
        job = c.execute("SELECT id FROM profile_refresh_jobs").fetchone()
        c.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s,0))",
            ("profile-refresh:" + str(job["id"]),),
        )
        c.execute("UPDATE profile_refresh_jobs SET status='RUNNING',attempts=1")
    # Closing the actual lock connection simulates a dead worker session.
    assert w.drain(batch=1, budget_seconds=30) == 1
    assert db.one("SELECT attempts FROM profile_refresh_jobs")["attempts"] == 2


def test_failing_group_does_not_starve_other_groups(jobs):
    db, owner, w, g = jobs
    owner.groups += [dict(g, clientId=str(uuid4())) for _ in range(4)]
    w.discover()
    assert w.enqueue(now=T, refresh_seconds=300, batch=10) == 5
    owner.fail_clients = {g["clientId"]}
    for _ in range(3):
        w.drain(batch=2, budget_seconds=30)
    assert db.one("SELECT count(*) AS n FROM profile_versions")["n"] == 12
    assert (
        db.one("SELECT count(*) AS n FROM profile_refresh_jobs WHERE status='RETRY'")[
            "n"
        ]
        == 1
    )


def test_completed_and_request_context_are_immutable(jobs):
    db, owner, w, g = jobs
    w.discover()
    w.enqueue(now=T, refresh_seconds=300, batch=1)
    w.drain(batch=1, budget_seconds=30)
    for query in [
        "UPDATE profile_refresh_jobs SET context='{}'",
        "UPDATE profile_refresh_jobs SET snapshot=NULL",
        "UPDATE profile_refresh_jobs SET status='RETRY'",
    ]:
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.connect() as c:
                c.execute(query)


def test_later_scheduled_cutoff_expires_old_history(jobs):
    db, owner, w, g = jobs
    w.discover()
    w.enqueue(now=T, refresh_seconds=300, batch=1)
    w.drain(batch=1, budget_seconds=30)
    assert w.enqueue(now=T + timedelta(days=1), refresh_seconds=300, batch=1) == 1
    w.drain(batch=1, budget_seconds=30)
    assert db.one("SELECT count(*) AS n FROM profile_versions")["n"] == 6


def test_save_then_failure_rolls_back_versions_and_recovers(jobs, monkeypatch):
    import backend.services.analysis.profiles.refresh as module

    db, owner, w, g = jobs
    w.discover()
    w.enqueue(now=T, refresh_seconds=300, batch=1)
    real = module.save_profile_versions

    def crash(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("crash before job completion")

    monkeypatch.setattr(module, "save_profile_versions", crash)
    assert w.drain(batch=1, budget_seconds=30) == 0
    assert db.one("SELECT count(*) AS n FROM profile_versions")["n"] == 0
    monkeypatch.setattr(module, "save_profile_versions", real)
    with db.connect() as c:
        c.execute("UPDATE profile_refresh_jobs SET available_at=now()")
    assert w.drain(batch=1, budget_seconds=30) == 1
    assert db.one("SELECT count(*) AS n FROM profile_versions")["n"] == 3


def test_session_lock_prevents_second_worker_from_claiming(jobs):
    db, owner, w, g = jobs
    w.discover()
    w.enqueue(now=T, refresh_seconds=300, batch=1)
    with db.connect() as c:
        job = c.execute("SELECT id FROM profile_refresh_jobs").fetchone()
        c.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s,0))",
            ("profile-refresh:" + str(job["id"]),),
        )
        assert w.drain(batch=1, budget_seconds=30) == 0
    assert w.drain(batch=1, budget_seconds=30) == 1


def test_owner_commit_lost_response_reuses_same_request_and_snapshot(jobs):
    db, owner, w, g = jobs
    w.discover()
    w.enqueue(now=T, refresh_seconds=300, batch=1)
    owner.lose_reply = True
    assert w.drain(batch=1, budget_seconds=30) == 0
    assert db.one("SELECT snapshot FROM profile_refresh_jobs")["snapshot"] is None
    with db.connect() as c:
        c.execute("UPDATE profile_refresh_jobs SET available_at=now()")
    assert w.drain(batch=1, budget_seconds=30) == 1
    assert len(owner.snapshots) == 1 and len(owner.prepares) == 2
    assert owner.prepares[0] == owner.prepares[1]


def test_old_algorithm_request_is_blocked_not_reinterpreted(jobs, monkeypatch):
    import backend.services.analysis.profiles.refresh as module

    db, owner, w, g = jobs
    w.discover()
    w.enqueue(now=T, refresh_seconds=300, batch=1)
    monkeypatch.setattr(module, "ALGORITHM", "some-future-version")
    assert w.drain(batch=1, budget_seconds=30) == 0
    row = db.one("SELECT status,last_error FROM profile_refresh_jobs")
    assert row == {"status": "BLOCKED", "last_error": "VERSION_MISMATCH"}
    assert not owner.prepares


@pytest.mark.parametrize("options", [{"batch":0},{"batch":True},{"budget_seconds":0},{"budget_seconds":301}])
def test_worker_limits_reject_unbounded_or_invalid_work(jobs,options):
    with pytest.raises(ValueError):jobs[2].drain(**options)
