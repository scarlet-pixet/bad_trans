from datetime import timedelta
from types import SimpleNamespace

import pytest

from backend.common.data import DomainError
from backend.services.analysis.profiles.storage import save_profile_versions
from tests.profile_storage.test_profiles import profiles


def service(db):
    from backend.services.analysis.profiles.pages import ProfilePages

    return ProfilePages(db)


ACTOR = SimpleNamespace(id="00000000-0000-0000-0000-000000000001",role="MANAGER", is_active=True)


def test_frozen_page_survives_new_versions_and_latest_uses_cutoff(profiles):
    db, tx, rows, t = profiles
    with db.connect() as c:
        save_profile_versions(c, tx, rows, t, t)
    s = service(db)
    page = s.get(ACTOR, windowDays=30, history=True, limit=1)
    old_ids = {x["id"] for x in page["items"]}
    with db.connect() as c:
        save_profile_versions(
            c,
            {k: v for k, v in tx.items() if k != "id"},
            rows,
            t,
            t,
            origin="SCHEDULED",
        )
    assert page["totalCount"] == 1 and page["nextPageToken"] is None
    again = s.get(ACTOR, windowDays=30, history=True, limit=10)
    assert again["totalCount"] == 2 and old_ids <= {x["id"] for x in again["items"]}


def test_two_page_history_is_materialized_and_filter_bound(profiles):
    db, tx, rows, t = profiles
    for origin in ["EVENT", "SCHEDULED", "MANUAL"]:
        with db.connect() as c:
            save_profile_versions(
                c,
                tx if origin == "EVENT" else {k: v for k, v in tx.items() if k != "id"},
                rows,
                t,
                t,
                origin=origin,
            )
    s = service(db)
    p = s.get(ACTOR, history=True, limit=1, currency="BYN")
    ids = [p["items"][0]["id"]]
    token = p["nextPageToken"]
    with db.connect() as c:
        c.execute(
            "INSERT INTO profile_versions(id,client_id,currency,direction,window_days,version,as_of,ingested_before,origin,metrics) SELECT gen_random_uuid(),client_id,currency,direction,window_days,99,as_of,ingested_before,'MANUAL',metrics FROM profile_versions LIMIT 1"
        )
    while token:
        q = s.get(ACTOR, history=True, limit=1, currency="BYN", pageToken=token)
        assert q["totalCount"] == 3 and q["selectionId"] == p["selectionId"]
        ids.extend(x["id"] for x in q["items"])
        token = q["nextPageToken"]
    assert len(set(ids)) == 3
    with pytest.raises(DomainError):
        s.get(
            ACTOR, history=True, limit=1, currency="USD", pageToken=p["nextPageToken"]
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
        {"currency": "byn"},
        {"direction": "BAD"},
        {"origin": "X"},
        {"history": 1},
        {"pageToken": "bad"},
        {"windowDays": True},
    ],
)
def test_invalid_page_input_is_domain_error(profiles, kwargs):
    with pytest.raises(DomainError):
        service(profiles[0]).get(ACTOR, **kwargs)


def test_empty_client_and_role(profiles):
    from uuid import uuid4

    s = service(profiles[0])
    p = s.get(ACTOR, clientId=str(uuid4()))
    assert p["items"] == [] and p["historyStatus"] == "NO_HISTORY"
    with pytest.raises(DomainError):
        s.get(SimpleNamespace(role="ADMIN", is_active=True))


def test_expired_page_cannot_silently_start_new_selection(profiles):
    db, tx, rows, t = profiles
    for origin in ["EVENT", "SCHEDULED"]:
        with db.connect() as c:
            save_profile_versions(
                c,
                tx if origin == "EVENT" else {k: v for k, v in tx.items() if k != "id"},
                rows,
                t,
                t,
                origin=origin,
            )
    s = service(db)
    p = s.get(ACTOR, history=True, limit=1)
    with db.connect() as c:
        c.execute(
            "UPDATE profile_page_selections SET expires_at=now()-interval '1 second'"
        )
    with pytest.raises(DomainError):
        s.get(ACTOR, history=True, limit=1, pageToken=p["nextPageToken"])


def test_late_old_event_does_not_replace_more_recent_asof(profiles):
    from backend.services.analysis.profiles.storage import HistoryBatch
    from uuid import uuid4

    db, tx, rows, t = profiles
    future = t + timedelta(hours=1)
    snapshot = dict(
        rows.snapshot,
        snapshotId=str(uuid4()),
        selection=dict(
            rows.snapshot["selection"],
            asOf=future.isoformat(),
            ingestedBefore=future.isoformat(),
            windowStart=(future - timedelta(days=90)).isoformat(),
        ),
    )
    newrows = HistoryBatch(rows, snapshot)
    with db.connect() as c:
        newer = save_profile_versions(
            c,
            {k: v for k, v in tx.items() if k != "id"},
            newrows,
            future,
            future,
            "SCHEDULED",
        )
    with db.connect() as c:
        save_profile_versions(c, tx, rows, t, t)
    page = service(db).get(ACTOR, clientId=tx["clientId"], windowDays=30)
    assert page["items"][0]["id"] == next(
        v["id"] for v in newer if v["windowDays"] == 30
    )


def test_event_and_scheduled_share_group_lock_without_duplicate_numbers(profiles):
    from concurrent.futures import ThreadPoolExecutor
    db,tx,rows,t=profiles
    def publish(origin):
        subject=tx if origin=="EVENT" else {k:v for k,v in tx.items() if k!="id"}
        with db.connect() as c:return save_profile_versions(c,subject,rows,t,t,origin=origin)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(publish,["EVENT","SCHEDULED"]))
    assert len({v["calculationId"] for group in results for v in group})==2
    assert db.one("SELECT count(*) AS n FROM profile_versions")["n"]==6
    assert all(r["n"]==2 and r["versions"]==[1,2] for r in db.query("SELECT window_days,count(*) AS n,array_agg(version ORDER BY version) AS versions FROM profile_versions GROUP BY window_days"))


def test_scheduled_window_loses_old_operations_without_new_transactions(profiles):
    from backend.services.analysis.profiles.storage import HistoryBatch
    from uuid import uuid4
    db,tx,rows,t=profiles
    group={k:v for k,v in tx.items() if k!="id"}
    with db.connect() as c:first=save_profile_versions(c,group,rows,t,t,"SCHEDULED")
    later=t+timedelta(days=8)
    snap=dict(rows.snapshot,snapshotId=str(uuid4()),selection=dict(rows.snapshot["selection"],asOf=later.isoformat(),ingestedBefore=later.isoformat(),windowStart=(later-timedelta(days=90)).isoformat()))
    with db.connect() as c:second=save_profile_versions(c,group,HistoryBatch(rows,snap),later,later,"SCHEDULED")
    assert next(x for x in first if x["windowDays"]==7)["sampleSize"]==1
    assert next(x for x in second if x["windowDays"]==7)["sampleSize"]==0
    assert next(x for x in second if x["windowDays"]==30)["sampleSize"]==1


def test_late_commit_does_not_change_frozen_page(profiles):
    db,tx,rows,t=profiles
    for origin in ["EVENT","SCHEDULED"]:
        with db.connect() as c:save_profile_versions(c,tx if origin=="EVENT" else {k:v for k,v in tx.items() if k!="id"},rows,t,t,origin=origin)
    writer=db.connect()
    try:
        writer.execute("INSERT INTO profile_versions(id,client_id,currency,direction,window_days,version,as_of,ingested_before,origin,metrics) SELECT gen_random_uuid(),client_id,currency,direction,window_days,99,as_of,ingested_before,'MANUAL',metrics FROM profile_versions WHERE window_days=30 LIMIT 1")
        s=service(db);p=s.get(ACTOR,history=True,limit=1)
        assert p["totalCount"]==2
        writer.commit()
        q=s.get(ACTOR,history=True,limit=1,pageToken=p["nextPageToken"])
        assert q["totalCount"]==2 and q["selectionId"]==p["selectionId"]
    finally:writer.close()


def test_current_page_token_reopens_first_page_after_new_profile(profiles):
    db, tx, rows, t = profiles
    with db.connect() as c:
        save_profile_versions(c, tx, rows, t, t)
    pages = service(db)
    first = pages.get(ACTOR, windowDays=30, history=True, limit=20)
    with db.connect() as c:
        save_profile_versions(c, {k: v for k, v in tx.items() if k != "id"}, rows, t, t, origin="SCHEDULED")
    reopened = pages.get(ACTOR, windowDays=30, history=True, limit=20, pageToken=first["currentPageToken"])
    assert reopened["selectionId"] == first["selectionId"]
    assert [p["id"] for p in reopened["items"]] == [p["id"] for p in first["items"]] and reopened["totalCount"] == 1
    assert pages.get(ACTOR, windowDays=30, history=True)["totalCount"] == 2
