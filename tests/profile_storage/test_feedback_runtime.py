"""Real Case outbox -> Kafka -> Analysis journal, isolated restored owners only."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import grpc
import psycopg
import pytest
from confluent_kafka import Producer
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from backend.infrastructure.clients import RemoteClient
from tests.profile_storage.test_assessment_runtime import wait


def test_actual_feedback_closed_reopen_explained_duplicate_and_consumer_outage(
    storage_stand, monkeypatch
):
    filename = os.getenv("PROFILE_STORAGE_RUNTIME")
    if not filename:
        pytest.skip("Explicit isolated runtime required")
    r = json.loads(Path(filename).read_text())
    assert r["mode"] == "native" and r["kafkaContainer"] != "bad_trans-kafka-1"
    monkeypatch.setenv("SERVICE_TOKEN", r["token"])
    manager = SimpleNamespace(id=str(uuid4()), login="feedback-manager", role="MANAGER")
    analyst = SimpleNamespace(id=str(uuid4()), login="feedback-analyst", role="ANALYST")
    analysis = RemoteClient(
        "analysis", manager, address=f"127.0.0.1:{r['analysisPort']}"
    )
    cases = RemoteClient("case", analyst, address=f"127.0.0.1:{r['casePort']}")
    case_manager = RemoteClient("case", manager, address=f"127.0.0.1:{r['casePort']}")
    params = {k: storage_stand[k] for k in ("host", "port", "user", "password")}

    def query(owner, statement, args=()):
        with psycopg.connect(
            **params,
            dbname=r["databases"][owner],
            row_factory=dict_row,
            connect_timeout=5,
        ) as c:
            return c.execute(statement, args).fetchone()

    t = datetime.now(UTC) - timedelta(minutes=1)
    client, account, historical, subject = (str(uuid4()) for _ in range(4))
    with psycopg.connect(**params, dbname=r["databases"]["transaction"]) as c:
        c.execute(
            "INSERT INTO bank_clients VALUES (%s,%s,'Feedback audit',NULL,true)",
            (client, client),
        )
        c.execute(
            "INSERT INTO accounts VALUES (%s,%s,%s,'BYN','OPEN')",
            (account, client, account),
        )
        for d in range(21):
            key = historical if d == 0 else str(uuid4())
            c.execute(
                "INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at) VALUES (%s,'feedback-audit',%s,%s,%s,'BYN','OUT',%s,%s)",
                (
                    key,
                    key,
                    account,
                    "1000.00" if d == 0 else "100.00",
                    t - timedelta(days=d + 1),
                    t - timedelta(seconds=10),
                ),
            )
        c.execute(
            "INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at) VALUES (%s,'feedback-audit',%s,%s,1000,'BYN','OUT',%s,%s)",
            (subject, subject, account, t, t - timedelta(seconds=10)),
        )

    def analyze(identifier):
        analysis._invoke(
            "analyze_transaction",
            dict(transactionId=identifier, idempotencyKey="feedback:" + uuid4().hex),
        )
        return analysis._invoke("get_detection_context", dict(transactionId=identifier))

    historical_result = analyze(historical)
    assert historical_result["assessmentScore"] == 40
    case = wait(
        lambda: query(
            "case", "SELECT * FROM review_cases WHERE transaction_id=%s", (historical,)
        )
    )
    first = analyze(subject)

    def profile(context):
        return query(
            "analysis",
            "SELECT metrics FROM profile_versions WHERE id=%s",
            (context["primaryProfileVersionId"],),
        )["metrics"]

    p0 = profile(first)
    assert p0["observedCount"] == p0["includedCount"] == 21
    taken = cases._invoke(
        "take_case",
        dict(
            caseId=str(case["id"]),
            expectedVersion=case["row_version"],
            idempotencyKey="take:" + uuid4().hex,
        ),
    )
    closed = cases._invoke(
        "close_case",
        dict(
            input=dict(
                caseId=str(case["id"]),
                expectedVersion=taken["rowVersion"],
                reason="Confirmed audit only",
                outcome="UNUSUAL_CONFIRMED",
                idempotencyKey="close:" + uuid4().hex,
            )
        ),
    )

    def learned(version):
        return query(
            "analysis",
            "SELECT * FROM feedback_projection WHERE case_id=%s AND case_version=%s",
            (case["id"], version),
        )

    fact = wait(lambda: learned(closed["rowVersion"]))
    second = analyze(subject)
    p1 = profile(second)
    assert (
        p1["observedCount"] == 21
        and p1["includedCount"] == 20
        and p1["excludedCount"] == 1
    )
    assert p1["exclusions"][0]["decisionId"] == fact["payload"]["decision"]["id"]
    assert profile(first) == p0
    assert (
        analysis._invoke(
            "get_detection_context",
            dict(transactionId=subject, resultId=first["resultId"]),
        )
        == first
    )
    pub = query(
        "case",
        "SELECT payload FROM outbox_events WHERE aggregate_id=%s ORDER BY created_at DESC LIMIT 1",
        (case["id"],),
    )["payload"]
    producer = Producer({"bootstrap.servers": r["kafkaBootstrap"]})
    producer.produce(
        "case.feedback.v1", key=str(case["id"]), value=json.dumps(pub).encode()
    )
    assert producer.flush(8) == 0

    def duplicate_seen():
        for line in (
            Path(r["logs"]["analysis"]).read_text(errors="replace").splitlines()
        ):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if (
                record.get("eventId") == pub["event_id"]
                and record.get("duplicate") is True
            ):
                return True
        return False

    wait(duplicate_seen)
    assert (
        query(
            "analysis",
            "SELECT count(*) AS n FROM feedback_journal WHERE event_id=%s",
            (pub["event_id"],),
        )["n"]
        == 1
    )
    # Stop exactly this audit owner, then commit/re-publish a reopening while it is down.
    wrapper = Path(r["processes"]["analysis"]["wrapper"]).resolve()
    root = Path(__file__).resolve().parents[2]
    assert wrapper.is_relative_to(root / ".local") and "profile-feedback-" in str(
        wrapper
    )
    env = os.environ.copy()
    env["AUDIT_WRAPPER"] = str(wrapper)
    command = "$p=Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -and $_.CommandLine.Contains($env:AUDIT_WRAPPER)}; $p | ForEach-Object {Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue}"
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        env=env,
        check=True,
        timeout=12,
        capture_output=True,
    )
    with grpc.insecure_channel(f"127.0.0.1:{r['analysisPort']}") as channel:
        with pytest.raises(grpc.FutureTimeoutError):
            grpc.channel_ready_future(channel).result(timeout=1)
    reopened = case_manager._invoke(
        "reopen_case",
        dict(
            caseId=str(case["id"]),
            expectedVersion=closed["rowVersion"],
            reason="Reconsider audit only",
            idempotencyKey="reopen:" + uuid4().hex,
        ),
    )
    wait(
        lambda: query(
            "case",
            "SELECT id FROM outbox_events WHERE aggregate_id=%s AND (payload->'payload'->>'case_version')::int=%s AND published_at IS NOT NULL",
            (case["id"], reopened["rowVersion"]),
        )
    )
    assert learned(reopened["rowVersion"]) is None
    env.update(
        SERVICE_NAME="analysis",
        SERVICE_TOKEN=r["token"],
        DATABASE_URL=make_conninfo(**params, dbname=r["databases"]["analysis"]),
        KAFKA_BOOTSTRAP=r["kafkaBootstrap"],
        TRANSACTION_GRPC=f"127.0.0.1:{r['transactionPort']}",
        GRPC_BIND=f"127.0.0.1:{r['analysisPort']}",
        PROFILE_POLL_SECONDS="1",
        PROFILE_REFRESH_BATCH="2",
        PROFILE_REFRESH_SECONDS="300",
        PYTHONUNBUFFERED="1",
        PYTHONIOENCODING="utf-8",
    )
    with Path(r["logs"]["analysis"]).open("ab") as output:
        proc = subprocess.Popen(
            [sys.executable, str(wrapper)],
            cwd=root,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    r["processes"]["analysis"]["pid"] = proc.pid
    Path(filename).write_text(json.dumps(r))
    # Hard-killed consumers keep their broker session until its timeout. The
    # first attempt observed recovery after ~42s; allow the actual group rebalance.
    wait(lambda: learned(reopened["rowVersion"]), seconds=70)
    assert profile(analyze(subject))["includedCount"] == 21
    # Older full state after a newer one must not roll back the projection.
    stale = json.loads(json.dumps(pub))
    stale["event_id"] = str(uuid4())
    producer.produce(
        "case.feedback.v1", key=str(case["id"]), value=json.dumps(stale).encode()
    )
    assert producer.flush(8) == 0
    wait(
        lambda: query(
            "analysis",
            "SELECT event_id FROM feedback_journal WHERE event_id=%s",
            (stale["event_id"],),
        )
    )
    assert learned(reopened["rowVersion"])
    taken = cases._invoke(
        "take_case",
        dict(
            caseId=str(case["id"]),
            expectedVersion=reopened["rowVersion"],
            idempotencyKey="take:" + uuid4().hex,
        ),
    )
    explained = cases._invoke(
        "close_case",
        dict(
            input=dict(
                caseId=str(case["id"]),
                expectedVersion=taken["rowVersion"],
                reason="Explained audit only",
                outcome="EXPLAINED",
                idempotencyKey="close:" + uuid4().hex,
            )
        ),
    )
    wait(lambda: learned(explained["rowVersion"]))
    assert profile(analyze(subject))["includedCount"] == 21 and profile(first) == p0
