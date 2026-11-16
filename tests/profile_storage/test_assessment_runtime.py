"""Real Kafka analysis -> Case and compatible reanalysis, audit copies only."""

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.infrastructure.clients import RemoteClient
from tools.assessment_evaluation import example


def wait(fn, seconds=35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.4)
    pytest.fail("Actual asynchronous operation did not complete")


def test_actual_new_policy_case_reanalysis_and_unavailable(storage_stand, monkeypatch):
    filename = os.getenv("PROFILE_STORAGE_RUNTIME")
    if not filename:
        pytest.skip("Explicit isolated runtime required")
    r = json.loads(Path(filename).read_text())
    params = {k: storage_stand[k] for k in ["host", "port", "user", "password"]}
    if "case" not in r["databases"]:
        pytest.skip("Isolated Case owner must be started")
    monkeypatch.setenv("SERVICE_TOKEN", r["token"])
    actor = SimpleNamespace(id=str(uuid4()), login="stage5-audit", role="MANAGER")
    txn = RemoteClient(
        "transaction", actor, address=f"127.0.0.1:{r['transactionPort']}"
    )
    analysis = RemoteClient("analysis", actor, address=f"127.0.0.1:{r['analysisPort']}")
    client, account = str(uuid4()), str(uuid4())
    t = datetime.now(UTC) - timedelta(minutes=1)

    def query(owner, sql, args=()):
        with psycopg.connect(
            **params, dbname=r["databases"][owner], row_factory=dict_row
        ) as c:
            return c.execute(sql, args).fetchone()

    with psycopg.connect(**params, dbname=r["databases"]["transaction"]) as c:
        c.execute(
            "INSERT INTO bank_clients VALUES (%s,%s,'Assessment audit',NULL,true)",
            (client, client),
        )
        c.execute(
            "INSERT INTO accounts VALUES (%s,%s,%s,'BYN','OPEN')",
            (account, client, account),
        )
        cp = c.execute("SELECT id FROM counterparties ORDER BY id LIMIT 1").fetchone()[
            0
        ]
        for d in range(1, 31):
            for i in range(5):
                key = str(uuid4())
                c.execute(
                    "INSERT INTO transactions(id,source,external_id,account_id,counterparty_id,amount,currency,direction,occurred_at,ingested_at) VALUES (%s,'assessment-audit',%s,%s,%s,100,'BYN','OUT',%s,%s)",
                    (
                        key,
                        key,
                        account,
                        cp,
                        t - timedelta(days=d, minutes=5 + i),
                        t - timedelta(days=d),
                    ),
                )
        c.execute(
            "INSERT INTO behavior_observation_coverage VALUES (%s,%s,'BYN','OUT','DECLARED_COMPLETE',%s,'controlled-audit-fixture',now()-interval '2 seconds','audit-complete')",
            (
                str(uuid4()),
                client,
                Jsonb(
                    [
                        {
                            "from": (t - timedelta(days=90)).isoformat(),
                            "to": t.isoformat(),
                        }
                    ]
                ),
            ),
        )
    key = "assessment:" + uuid4().hex
    operation = txn._invoke(
        "register_transaction",
        {
            "input": dict(
                source="assessment-audit",
                externalId=key,
                idempotencyKey=key,
                accountId=account,
                counterpartyId=str(cp),
                amount="1000.00",
                currency="BYN",
                direction="OUT",
                occurredAt=t.isoformat(),
            )
        },
    )
    first = wait(
        lambda: analysis._invoke(
            "get_detection_context", {"transactionId": operation["id"]}
        )
    )
    assert (
        first["assessmentScore"] == 40
        and first["assessmentStatus"] == "COMPLETE"
        and len(first["checks"]) == 5
    )
    case = wait(
        lambda: query(
            "case",
            "SELECT * FROM review_cases WHERE transaction_id=%s",
            (operation["id"],),
        )
    )
    assert case["priority"] == "LOW" and case["latest_assessment_score"] == 40
    from confluent_kafka import Producer

    event = query(
        "analysis",
        "SELECT payload FROM outbox_events WHERE payload->'payload'->>'result_id'=%s",
        (first["resultId"],),
    )["payload"]
    producer = Producer({"bootstrap.servers": r["kafkaBootstrap"]})
    producer.produce(
        "analysis.completed.v1", key=operation["id"], value=json.dumps(event).encode()
    )
    assert producer.flush(8) == 0

    def duplicate_seen():
        for line in (
            Path(r["logs"]["case"])
            .read_text(encoding="utf-8", errors="replace")
            .splitlines()
        ):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if (
                record.get("event") == "kafka.consumed"
                and record.get("eventId") == event["event_id"]
                and record.get("duplicate") is True
            ):
                return True
        return False

    wait(duplicate_seen)
    assert (
        query(
            "case",
            "SELECT count(*) AS n FROM review_cases WHERE transaction_id=%s",
            (operation["id"],),
        )["n"]
        == 1
    )
    with psycopg.connect(**params, dbname=r["databases"]["case"]) as c:
        c.execute("UPDATE review_cases SET status='CLOSED' WHERE id=%s", (case["id"],))
        c.execute(
            "INSERT INTO case_decisions VALUES (%s,%s,%s,1,'EXPLAINED','audit decision',now())",
            (str(uuid4()), case["id"], actor.id),
        )
    rule = query(
        "analysis",
        "SELECT r.id, max(v.version) AS version FROM analysis_rules r JOIN rule_versions v ON v.rule_id=r.id WHERE r.code='AMOUNT_DEVIATION' GROUP BY r.id",
    )
    from backend.services.analysis.assessments.calculation import default_rules

    cfg = next(x for x in default_rules() if x["code"] == "AMOUNT_DEVIATION")[
        "parameters"
    ]
    pos, _ = example("amount_upper", datetime(2026, 11, 8, 12, tzinfo=UTC))
    neg, _ = example("normal", datetime(2026, 11, 8, 12, tzinfo=UTC))
    analysis._invoke(
        "publish_rule_version",
        {
            "input": dict(
                ruleId=str(rule["id"]),
                version=rule["version"] + 1,
                parameters=cfg,
                weight=0,
                controlExamples=[dict(pos, expected=True), dict(neg, expected=False)],
                idempotencyKey="publish:" + uuid4().hex,
            )
        },
    )
    analysis._invoke(
        "analyze_transaction",
        {
            "transactionId": operation["id"],
            "idempotencyKey": "reanalyze:" + uuid4().hex,
        },
    )
    latest = analysis._invoke(
        "get_detection_context", {"transactionId": operation["id"]}
    )
    assert latest["assessmentScore"] == 0 and latest["resultId"] != first["resultId"]
    updated = wait(
        lambda: query(
            "case",
            "SELECT * FROM review_cases WHERE transaction_id=%s AND latest_result_id=%s",
            (operation["id"], latest["resultId"]),
        )
    )
    assert updated["id"] == case["id"] and updated["status"] == "CLOSED"
    assert (
        query(
            "case",
            "SELECT count(*) AS n FROM case_decisions WHERE case_id=%s",
            (case["id"],),
        )["n"]
        == 1
    )
    old = analysis._invoke(
        "get_detection_context",
        {"transactionId": operation["id"], "resultId": first["resultId"]},
    )
    assert old == first
    # Restore a modern weight40 via a NEW version, never mutate PUBLISHED rows.
    analysis._invoke(
        "publish_rule_version",
        {
            "input": dict(
                ruleId=str(rule["id"]),
                version=rule["version"] + 2,
                parameters=cfg,
                weight=40,
                controlExamples=[dict(pos, expected=True), dict(neg, expected=False)],
                idempotencyKey="restore:" + uuid4().hex,
            )
        },
    )
    emptyclient, emptyaccount = str(uuid4()), str(uuid4())
    with psycopg.connect(**params, dbname=r["databases"]["transaction"]) as c:
        c.execute(
            "INSERT INTO bank_clients VALUES (%s,%s,'Unavailable audit',NULL,true)",
            (emptyclient, emptyclient),
        )
        c.execute(
            "INSERT INTO accounts VALUES (%s,%s,%s,'BYN','OPEN')",
            (emptyaccount, emptyclient, emptyaccount),
        )
    key = "empty:" + uuid4().hex
    empty = txn._invoke(
        "register_transaction",
        {
            "input": dict(
                source="assessment-audit",
                externalId=key,
                idempotencyKey=key,
                accountId=emptyaccount,
                amount="100.00",
                currency="BYN",
                direction="OUT",
                occurredAt=t.isoformat(),
            )
        },
    )
    unavailable = wait(
        lambda: analysis._invoke(
            "get_detection_context", {"transactionId": empty["id"]}
        )
    )
    assert (
        unavailable["assessmentScore"] is None
        and unavailable["assessmentStatus"] == "UNAVAILABLE"
        and unavailable["reviewRequired"] is None
    )
    outbox = query(
        "analysis",
        "SELECT id FROM outbox_events WHERE payload->'payload'->>'result_id'=%s",
        (unavailable["resultId"],),
    )
    wait(
        lambda: query(
            "case",
            "SELECT event_id FROM inbox_events WHERE event_id=%s",
            (outbox["id"],),
        )
    )
    assert (
        query(
            "case",
            "SELECT count(*) AS n FROM review_cases WHERE transaction_id=%s",
            (empty["id"],),
        )["n"]
        == 0
    )
