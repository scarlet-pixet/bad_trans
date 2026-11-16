"""Real automatic path on the dedicated audit broker, never main Kafka."""

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.infrastructure.clients import RemoteClient


def test_automatic_event_snapshot_result_outbox_and_duplicate(
    storage_stand, monkeypatch
):
    filename = os.getenv("PROFILE_STORAGE_RUNTIME")
    if not filename:
        pytest.skip("An isolated Kafka runtime must be explicitly configured")
    runtime = json.loads(Path(filename).read_text(encoding="utf-8"))
    if not runtime.get("kafkaContainer"):
        pytest.skip("Dedicated audit Kafka was not started")
    kafka = runtime["kafkaContainer"]
    label = subprocess.check_output(
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "bad_trans.audit"}}',
            kafka,
        ],
        text=True,
    ).strip()
    assert label in {"profile-storage-stage2", "profile-feedback-stage6"} and kafka != "bad_trans-kafka-1"
    monkeypatch.setenv("SERVICE_TOKEN", runtime["token"])
    params = {k: storage_stand[k] for k in ("host", "port", "user", "password")}
    actor = SimpleNamespace(
        id=str(uuid4()), login="audit", role="MANAGER", is_active=True
    )
    transaction = RemoteClient(
        "transaction", actor, address=f"127.0.0.1:{runtime['transactionPort']}"
    )
    analysis = RemoteClient(
        "analysis", actor, address=f"127.0.0.1:{runtime['analysisPort']}"
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if transaction._invoke("health", {}).get(
            "asynchronousReady"
        ) and analysis._invoke("health", {}).get("asynchronousReady"):
            break
        time.sleep(0.5)
    else:
        pytest.fail("Isolated Kafka services did not become asynchronously ready")
    with psycopg.connect(**params, dbname=runtime["databases"]["transaction"]) as c:
        account = c.execute(
            "SELECT a.id,a.currency FROM accounts a JOIN bank_clients b ON b.id=a.client_id WHERE a.status='OPEN' AND b.is_active ORDER BY a.id LIMIT 1"
        ).fetchone()
    key = "stage2-kafka:" + uuid4().hex
    operation = transaction._invoke(
        "register_transaction",
        {
            "input": dict(
                idempotencyKey=key,
                source="stage2-kafka",
                externalId=key,
                accountId=str(account[0]),
                currency=account[1],
                direction="OUT",
                amount="1234.56",
                occurredAt=datetime.now(UTC).isoformat(),
            )
        },
    )
    deadline = time.monotonic() + 35
    result = None
    while time.monotonic() < deadline:
        with psycopg.connect(
            **params, dbname=runtime["databases"]["analysis"], row_factory=dict_row
        ) as c:
            result = c.execute(
                """SELECT a.id,a.calculation_id,p.history_snapshot_id,
                (SELECT count(*) FROM analysis_result_profiles l WHERE l.result_id=a.id) AS links,
                EXISTS(SELECT 1 FROM outbox_events o WHERE o.payload->'payload'->>'result_id'=a.id::text AND o.published_at IS NOT NULL) AS published
                FROM analysis_results a JOIN profile_calculations p ON p.id=a.calculation_id
                WHERE transaction_id=%s""",
                (operation["id"],),
            ).fetchone()
        if result and result["published"]:
            break
        time.sleep(0.5)
    assert result and result["published"] and result["links"] == 1
    with psycopg.connect(
        **params, dbname=runtime["databases"]["transaction"], row_factory=dict_row
    ) as c:
        event = c.execute(
            "SELECT payload FROM outbox_events WHERE payload->'payload'->>'transaction_id'=%s",
            (operation["id"],),
        ).fetchone()["payload"]
    script = "import json,sys;from confluent_kafka import Producer;p=Producer({'bootstrap.servers':'audit-kafka:9092'});p.produce('transaction.registered.v1',value=sys.argv[1]);assert p.flush(8)==0"
    if runtime.get("kafkaBootstrap"):
        from confluent_kafka import Producer

        producer = Producer({"bootstrap.servers": runtime["kafkaBootstrap"]})
        producer.produce("transaction.registered.v1", value=json.dumps(event).encode())
        assert producer.flush(8) == 0
    else:
        subprocess.run(
            [
                "docker",
                "exec",
                runtime["names"]["transaction"],
                "python",
                "-c",
                script,
                json.dumps(event),
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
    duplicate_seen = False
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if runtime.get("logs", {}).get("analysis"):
            log_text = Path(runtime["logs"]["analysis"]).read_text(
                encoding="utf-8", errors="replace"
            )
        else:
            logs = subprocess.run(
                ["docker", "logs", "--tail", "1500", runtime["names"]["analysis"]],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            log_text = logs.stdout + "\n" + logs.stderr
        for line in log_text.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if (
                record.get("event") == "kafka.consumed"
                and record.get("eventId") == event["event_id"]
                and record.get("duplicate") is True
            ):
                duplicate_seen = True
        if duplicate_seen:
            break
        time.sleep(0.3)
    assert duplicate_seen, (
        "Consumer must actually observe the duplicate before deduplication is declared verified"
    )
    with psycopg.connect(**params, dbname=runtime["databases"]["analysis"]) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM analysis_results WHERE transaction_id=%s",
                (operation["id"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) FROM inbox_events WHERE event_id=%s",
                (event["event_id"],),
            ).fetchone()[0]
            == 1
        )
