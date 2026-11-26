from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from backend.common.behavior_contract import MAX_HISTORY_ITEMS, digest
from backend.common.data import DomainError
from backend.infrastructure.product_migrations import migrate
from backend.services.transaction.history.snapshots import HistorySnapshots


@pytest.fixture
def history(database):
    db = database("transaction")
    migrate(db, "transaction")
    client, account = str(uuid4()), str(uuid4())
    t = datetime(2026, 11, 5, 12, tzinfo=UTC)
    with db.connect() as c:
        c.execute(
            "INSERT INTO bank_clients VALUES (%s,%s,'Snapshot test',NULL,true)",
            (client, client),
        )
        c.execute(
            "INSERT INTO accounts VALUES (%s,%s,%s,'BYN','OPEN')",
            (account, client, account),
        )

    def add(at=None, ingested=None, direction="OUT", conn=None):
        identifier = str(uuid4())
        query = """INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at)
                   VALUES (%s,'audit',%s,%s,100,'BYN',%s,%s,%s)"""
        params = (
            identifier,
            identifier,
            account,
            direction,
            at or t - timedelta(days=1),
            ingested or t - timedelta(hours=1),
        )
        if conn is not None:
            conn.execute(query, params)
        else:
            with db.connect() as c:
                c.execute(query, params)
        return identifier

    parameters = dict(
        requestKey="history-test",
        clientId=client,
        currency="BYN",
        direction="OUT",
        windowStart=(t - timedelta(days=90)).isoformat(),
        asOf=t.isoformat(),
        ingestedBefore=t.isoformat(),
    )
    return db, HistorySnapshots(db), parameters, add


def test_boundaries_fixed_snapshot_after_late_commit_and_key_conflict(history):
    db, service, p, add = history
    included = add(at=datetime.fromisoformat(p["windowStart"]))
    add(at=datetime.fromisoformat(p["asOf"]))
    add(ingested=datetime.fromisoformat(p["ingestedBefore"]))
    add(direction="IN")
    with db.connect() as late:
        later_id = add(conn=late)
        first = service.prepare(p)
    assert first["itemCount"] == 1
    assert service.prepare(p) == first
    page = service.page({"snapshotId": first["snapshotId"], "limit": 100})
    assert [r["id"] for r in page["items"]] == [included]
    assert first["coverage"]["mode"] == "UNKNOWN"
    second = service.prepare(dict(p, requestKey="new-snapshot"))
    assert second["itemCount"] == 2 and second["digest"] != first["digest"]
    assert later_id in {
        r["id"] for r in service.page({"snapshotId": second["snapshotId"]})["items"]
    }
    with pytest.raises(DomainError) as e:
        service.prepare(dict(p, direction="IN"))
    assert e.value.code == "CONFLICT"


def test_pagination_is_snapshot_bound_and_concurrent_prepare_is_idempotent(history):
    _, service, p, add = history
    for _ in range(3):
        add()
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda _: service.prepare(p), range(4)))
    assert len({s["snapshotId"] for s in snapshots}) == 1
    sid = snapshots[0]["snapshotId"]
    page = service.page({"snapshotId": sid, "limit": 2})
    following = service.page(
        {"snapshotId": sid, "pageToken": page["nextPageToken"], "limit": 2}
    )
    assert len(page["items"]) == 2 and len(following["items"]) == 1
    assert {r["id"] for r in page["items"]}.isdisjoint(
        {r["id"] for r in following["items"]}
    )
    other = service.prepare(dict(p, requestKey="other"))
    with pytest.raises(DomainError):
        service.page(
            {"snapshotId": other["snapshotId"], "pageToken": page["nextPageToken"]}
        )


@pytest.mark.parametrize(
    "change", [{"limit": True}, {"limit": 0}, {"pageToken": "broken"}, {"surprise": 1}]
)
def test_invalid_page_is_explicit(history, change):
    _, service, p, _ = history
    sid = service.prepare(p)["snapshotId"]
    with pytest.raises(DomainError) as e:
        service.page(dict(snapshotId=sid, **change))
    assert e.value.code == "BAD_INPUT"


def test_immutable_inputs_and_incomplete_snapshot_are_rejected(history):
    db, service, p, add = history
    add()
    sid = service.prepare(p)["snapshotId"]
    for query in [
        "DELETE FROM behavior_history_snapshots WHERE id=%s",
        "DELETE FROM behavior_history_snapshot_items WHERE snapshot_id=%s",
        "UPDATE behavior_history_snapshots SET digest=repeat('0',64) WHERE id=%s",
        """INSERT INTO behavior_history_snapshot_items
           SELECT snapshot_id,ordinal+1,gen_random_uuid(),facts
           FROM behavior_history_snapshot_items WHERE snapshot_id=%s""",
    ]:
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.connect() as c:
                c.execute(query, (sid,))
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.connect() as c:
            c.execute(
                """INSERT INTO behavior_history_snapshots SELECT %s,'incomplete',request_hash,client_id,currency,
                       direction,window_start,as_of,ingested_before,captured_at,1,digest,coverage
                       FROM behavior_history_snapshots WHERE id=%s""",
                (uuid4(), sid),
            )


@pytest.mark.parametrize(
    "change",
    [
        {"direction": "BOTH"},
        {"currency": "byn"},
        {"clientId": "bad-uuid"},
        {"asOf": "2026-11-05T12:00:00"},
        {"ingestedBefore": "bad-date"},
        {"windowStart": "2020-01-01T00:00:00Z"},
        {"unexpected": True},
        {"requestKey": " "},
    ],
)
def test_prepare_rejects_invalid_context(history, change):
    _, service, p, _ = history
    with pytest.raises(DomainError) as e:
        service.prepare(dict(p, **change))
    assert e.value.code == "BAD_INPUT"


def test_large_history_is_ordered_immutable_and_rejects_overflow(history):
    db, service, p, add = history
    with pytest.raises(DomainError) as e:
        service.page({"snapshotId": str(uuid4())})
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(DomainError) as e:
        service.prepare(dict(p, clientId=str(uuid4())))
    assert e.value.code == "NOT_FOUND"
    identifier = add()
    def populate(first, last):
        with db.connect() as c:
            c.execute("SET LOCAL statement_timeout = '30s'")
            c.execute(
            """INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at)
            SELECT md5(g::text)::uuid,'limit-audit',g::text,account_id,amount,currency,direction,occurred_at,ingested_at
                FROM transactions CROSS JOIN generate_series(%s::integer,%s::integer) g WHERE id=%s""",
                (first, last, identifier),
            )
    populate(1, 9999)
    snapshot = service.prepare(p)
    assert snapshot["itemCount"] == 10000
    add()
    above_old_limit = service.prepare(dict(p, requestKey="larger-than-legacy"))
    assert above_old_limit["itemCount"] == 10001
    token, facts = None, []
    while True:
        page = service.page({"snapshotId": above_old_limit["snapshotId"], "pageToken": token, "limit": 500})
        facts.extend(page["items"])
        token = page["nextPageToken"]
        if token is None:
            break
    assert len({fact["id"] for fact in facts}) == 10001
    assert [fact["id"] for fact in facts] == sorted(fact["id"] for fact in facts)
    assert digest(facts) == above_old_limit["digest"]
    populate(10000, MAX_HISTORY_ITEMS - 2)
    at_limit = service.prepare(dict(p, requestKey="at-history-limit"))
    assert at_limit["itemCount"] == MAX_HISTORY_ITEMS
    add()
    with pytest.raises(DomainError, match="HISTORY_LIMIT"):
        service.prepare(dict(p, requestKey="over-history-limit"))
    assert service.prepare(p) == snapshot
    assert service.prepare(dict(p, requestKey="larger-than-legacy")) == above_old_limit
    assert db.one("SELECT count(*) AS n FROM behavior_history_snapshots")["n"] == 3


def test_coverage_learned_after_watermark_does_not_leak_into_old_selection(history):
    from psycopg.types.json import Jsonb

    db, service, p, add = history
    add()
    learned = datetime.now(UTC)-timedelta(minutes=2)
    cutoff = learned-timedelta(hours=1)
    watermark = learned-timedelta(seconds=1)
    p=dict(p,windowStart=(cutoff-timedelta(days=90)).isoformat(),asOf=cutoff.isoformat(),ingestedBefore=watermark.isoformat())
    with db.connect() as c:
        c.execute(
            """INSERT INTO behavior_observation_coverage(id,client_id,currency,direction,mode,intervals,
            declared_by_source,known_at,revision) VALUES (%s,%s,'BYN','OUT','DECLARED_COMPLETE',%s,'audit-source',%s,'complete-v1')""",
            (
                uuid4(),
                p["clientId"],
                Jsonb([{"from": p["windowStart"], "to": p["asOf"]}]),
                watermark + timedelta(seconds=1),
            ),
        )
    original = service.prepare(p)
    assert original["coverage"]["mode"] == "UNKNOWN"
    current = service.prepare(
        dict(
            p,
            requestKey="coverage-current",
            ingestedBefore=(watermark + timedelta(hours=1)).isoformat(),
        )
    )
    assert current["coverage"]["mode"] == "DECLARED_COMPLETE"
    assert current["coverage"]["revision"] == "complete-v1"
    assert service.prepare(p) == original
