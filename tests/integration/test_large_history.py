"""Exercise a 50,000-operation client through the normal Kafka assessment."""

import json
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import requests

from backend.common.behavior_contract import MAX_HISTORY_ITEMS


def test_kafka_assesses_an_operation_with_large_owner_history():
    project = os.environ["BAD_TRANS_PROJECT"]
    assert re.fullmatch(r"bad_trans_test_[a-f0-9]{10}", project)
    api = os.environ["GRAPHQL_URL"]
    dsn = os.environ["SUMMARY_TEST_DSN"]
    assert urlsplit(api).hostname == urlsplit(dsn).hostname == "127.0.0.1"
    for service, container_port, host_port in (
        ("transaction-db", 5432, urlsplit(dsn).port),
        ("gateway", 8000, urlsplit(api).port),
    ):
        container = f"{project}-{service}-1"
        metadata = subprocess.check_output(["docker", "inspect", container], text=True)
        details = json.loads(metadata)[0]
        assert details["Config"]["Labels"]["com.docker.compose.project"] == project
        assert details["NetworkSettings"]["Ports"][f"{container_port}/tcp"] == [
            {"HostIp": "127.0.0.1", "HostPort": str(host_port)}
        ]

    stem = f"large-history-{uuid4().hex[:10]}"
    client, account = uuid4(), uuid4()
    # Backfill exercises the normal event path while keeping this fixture out
    # of the refresh scheduler's current 90-day window for other tests.
    cutoff = datetime.now(UTC).replace(microsecond=0) - timedelta(days=91)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO bank_clients VALUES (%s,%s,'Synthetic large history',NULL,true)", (client, stem)
        )
        connection.execute("INSERT INTO accounts VALUES (%s,%s,%s,'BYN','OPEN')", (account, client, stem))
        connection.execute(
            """INSERT INTO transactions
              (id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at)
              SELECT gen_random_uuid(),%s,g::text,%s,100,'BYN','OUT',
                     %s::timestamptz-make_interval(days=>1+g%%25),%s
              FROM generate_series(1,%s) g""",
            (stem, account, cutoff, cutoff - timedelta(minutes=1), MAX_HISTORY_ITEMS),
        )

    with requests.Session() as session:
        session.trust_env = False

        def call(query, variables=None, *, timeout=20):
            response = session.post(api, json={"query": query, "variables": variables or {}}, timeout=timeout)
            body = response.json()
            assert response.ok and not body.get("errors"), body
            return body["data"]

        credentials = call('mutation{signIn(login:"manager",password:"BadTrans-demo-2026!"){accessToken}}')
        session.headers["Authorization"] = "Bearer " + credentials["signIn"]["accessToken"]
        started = monotonic()
        operation = call("mutation($v:OperationEventInput!){recordOperationEvent(input:$v)}", {"v": {
            "source": stem, "operationRef": "subject", "eventRef": "subject-posted", "kind": "PURCHASE",
            "channel": "POS", "status": "POSTED", "currency": "BYN", "direction": "OUT",
            "accountId": str(account), "amount": "400.00", "occurredAt": cutoff.isoformat(),
        }})["recordOperationEvent"]["transactionId"]
        deadline = started + 120
        while monotonic() < deadline:
            result = call("query($id:ID!){detectionContext(transactionId:$id)}", {"id": operation})["detectionContext"]
            if result:
                break
            sleep(0.7)
        else:
            raise AssertionError(f"No assessment for large-history operation {operation}")
        elapsed = round(monotonic() - started, 3)
        started = monotonic()
        manual = call("mutation($v:ProfileInput!){buildProfile(input:$v){id status}}", {"v": {
            "clientId": str(client), "currency": "BYN", "direction": "OUT",
            "cutoff": cutoff.isoformat(), "idempotencyKey": f"{stem}-manual",
        }}, timeout=70)["buildProfile"]
        manual_elapsed = round(monotonic() - started, 3)
        assert manual["status"] == "COMPLETED"
        version = call("query($id:ID!){profileVersion(id:$id)}", {
            "id": result["primaryProfileVersionId"],
        })["profileVersion"]
        call("mutation{signOut}")

    basis = result["amountBasis"]
    check = next(item for item in result["checks"] if item["code"] == "AMOUNT_DEVIATION")
    assert basis["sampleSize"] == MAX_HISTORY_ITEMS
    assert basis["scope"] == "GENERAL"
    assert float(basis["statistics"]["medianAmount"]) == 100
    assert check["status"] == "TRIGGERED" and check["contribution"] == 40
    assert version["metrics"]["sampleSize"] == MAX_HISTORY_ITEMS
    assert version["metrics"]["omittedHistoryDetails"]["history_ids"] == MAX_HISTORY_ITEMS
    assert result["baseline"]["omittedHistoryDetails"]["history_ids"] == MAX_HISTORY_ITEMS
    proof = {
        "transactionId": operation, "resultId": result["resultId"], "profileId": result["primaryProfileVersionId"],
        "sampleSize": basis["sampleSize"], "medianAmount": basis["statistics"]["medianAmount"],
        "amountContribution": check["contribution"], "secondsToResult": elapsed,
        "manualProfileRunId": manual["id"], "manualBuildSeconds": manual_elapsed,
        "profileHistoryIdCount": version["metrics"]["omittedHistoryDetails"]["history_ids"],
    }
    (Path(os.environ["BAD_TRANS_EVIDENCE_DIR"]) / "large-history.json").write_text(
        json.dumps(proof, indent=2), encoding="utf-8"
    )
