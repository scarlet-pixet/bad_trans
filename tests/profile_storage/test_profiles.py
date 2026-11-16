from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from backend.common.behavior_contract import digest
from backend.common.data import DomainError
from backend.infrastructure.product_migrations import migrate
from backend.services.analysis.profiles.storage import (
    HistoryBatch,
    save_profile_versions,
)


@pytest.fixture
def profiles(database):
    db = database("analysis")
    migrate(db, "analysis")
    t = datetime(2026, 11, 5, 12, tzinfo=UTC)
    tx = dict(id=str(uuid4()), clientId=str(uuid4()), currency="BYN", direction="OUT")
    rows = [
        dict(
            id=str(uuid4()),
            clientId=tx["clientId"],
            amount="100.00",
            currency="BYN",
            direction="OUT",
            counterpartyId=None,
            occurredAt=(t - timedelta(days=1)).isoformat(),
            ingestedAt=(t - timedelta(hours=1)).isoformat(),
        )
    ]
    history = HistoryBatch(
        rows,
        dict(
            snapshotId=str(uuid4()),
            digest=digest(rows),
            itemCount=1,
            capturedAt=t.isoformat(),
            selection=dict(
                requestKey="test",
                clientId=tx["clientId"],
                currency="BYN",
                direction="OUT",
                windowStart=(t - timedelta(days=90)).isoformat(),
                asOf=t.isoformat(),
                ingestedBefore=t.isoformat(),
            ),
            coverage=dict(
                mode="UNKNOWN",
                revision="unknown-v1",
                intervals=[],
                declaredBySource=None,
            ),
        ),
    )
    return db, tx, history, t


def insert_result(
    c, tx, versions, t, *, linked=True, context=True, result_transaction=None
):
    from psycopg.types.json import Jsonb

    run, result, legacy = uuid4(), uuid4(), uuid4()
    subject = result_transaction or tx["id"]
    cid = versions[0]["calculationId"]
    c.execute(
        """INSERT INTO analysis_runs VALUES (%s,'AUTO','{}',%s,'COMPLETED',NULL,'{}',%s,%s)""",
        (run, t, t, t),
    )
    c.execute(
        """INSERT INTO behavior_profiles VALUES (%s,%s,%s,'BYN','OUT',%s,%s,%s,%s,1,100,0,'{}','behavior-v2.1','INSUFFICIENT')""",
        (legacy, run, tx["clientId"], t - timedelta(days=30), t, tx["id"], t),
    )
    c.execute(
        """INSERT INTO analysis_results(id,transaction_id,run_id,profile_id,score,threshold,data_status,missing_reasons,calculated_at,calculation_id)
                 VALUES (%s,%s,%s,%s,0,60,'INSUFFICIENT','[]',%s,%s)""",
        (result, subject, run, legacy, t, cid),
    )
    if context:
        c.execute(
            """INSERT INTO detection_context(result_id,transaction_id,details,context_schema_version,calculation_id)
                     VALUES (%s,%s,%s,2,%s)""",
            (result, subject, Jsonb({"contextSchemaVersion": 2}), cid),
        )
    if linked:
        primary = next(v for v in versions if v["windowDays"] == 30)
        c.execute(
            "INSERT INTO analysis_result_profiles VALUES (%s,30,%s)",
            (result, primary["id"]),
        )
    return result


def test_same_context_reuses_three_versions_and_conflict_rejected(profiles):
    db, tx, rows, t = profiles
    with db.connect() as c:
        first = save_profile_versions(c, tx, rows, t, t, job_key="stable-job")
    with db.connect() as c:
        assert save_profile_versions(c, tx, rows, t, t, job_key="stable-job") == first
    assert db.one("SELECT count(*) AS n FROM profile_versions")["n"] == 3
    assert {v["windowDays"] for v in first} == {7, 30, 90}
    assert len({v["calculationId"] for v in first}) == 1
    with pytest.raises(DomainError) as e:
        with db.connect() as c:
            save_profile_versions(
                c, dict(tx, direction="IN"), rows, t, t, job_key="stable-job"
            )
    assert e.value.code == "CONFLICT"


def test_concurrent_same_calculation_and_atomic_failure(profiles):
    db, tx, rows, t = profiles

    def run(_):
        with db.connect() as c:
            return save_profile_versions(c, tx, rows, t, t)

    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(pool.map(run, range(4)))
    assert all(v == versions[0] for v in versions)
    with pytest.raises(RuntimeError):
        with db.connect() as c:
            save_profile_versions(
                c,
                {k: v for k, v in tx.items() if k != "id"},
                rows,
                t,
                t,
                origin="MANUAL",
            )
            raise RuntimeError("crash before commit")
    assert db.one("SELECT count(*) AS n FROM profile_calculations")["n"] == 1
    assert db.one("SELECT count(*) AS n FROM profile_versions")["n"] == 3


@pytest.mark.parametrize("linked,context", [(False, True), (True, False)])
def test_new_result_requires_primary_w30_and_context(profiles, linked, context):
    db, tx, rows, t = profiles
    with db.connect() as c:
        versions = save_profile_versions(c, tx, rows, t, t)
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.connect() as c:
            insert_result(c, tx, versions, t, linked=linked, context=context)


def test_valid_links_are_pinned_immutable_and_composite_fk_matches_window(profiles):
    db, tx, rows, t = profiles
    with db.connect() as c:
        versions = save_profile_versions(c, tx, rows, t, t)
        rid = insert_result(c, tx, versions, t)
    for statement, params in [
        ("DELETE FROM analysis_result_profiles WHERE result_id=%s", (rid,)),
        ("DELETE FROM detection_context WHERE result_id=%s", (rid,)),
        ("UPDATE profile_versions SET metrics='{}' WHERE id=%s", (versions[0]["id"],)),
        ("UPDATE analysis_results SET calculation_id=NULL WHERE id=%s", (rid,)),
        (
            "UPDATE profile_calculations SET context='{}' WHERE id=%s",
            (versions[0]["calculationId"],),
        ),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.connect() as c:
                c.execute(statement, params)
    v7 = next(v for v in versions if v["windowDays"] == 7)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.connect() as c:
            c.execute(
                "INSERT INTO analysis_result_profiles VALUES (%s,90,%s)",
                (rid, v7["id"]),
            )


def test_result_cannot_link_to_another_calculation(profiles):
    db, tx, rows, t = profiles
    with db.connect() as c:
        versions = save_profile_versions(c, tx, rows, t, t)
        other = save_profile_versions(
            c, {k: v for k, v in tx.items() if k != "id"}, rows, t, t, origin="MANUAL"
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.connect() as c:
            rid = insert_result(c, tx, versions, t)
            c.execute(
                "INSERT INTO analysis_result_profiles VALUES (%s,7,%s)",
                (rid, next(v for v in other if v["windowDays"] == 7)["id"]),
            )


def test_result_subject_must_match_profile_cutoff(profiles):
    db, tx, rows, t = profiles
    with db.connect() as c:
        versions = save_profile_versions(c, tx, rows, t, t)
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.connect() as c:
            insert_result(c, tx, versions, t, result_transaction=str(uuid4()))


@pytest.mark.parametrize("origin,subject", [("EVENT", False), ("SCHEDULED", True)])
def test_origin_requires_correct_subject_semantics(profiles, origin, subject):
    db, tx, rows, t = profiles
    value = tx if subject else {k: v for k, v in tx.items() if k != "id"}
    with pytest.raises(DomainError, match="subject"):
        with db.connect() as c:
            save_profile_versions(c, value, rows, t, t, origin=origin)
