"""Actual owner RPC/Kafka/scheduler on explicitly configured audit copies."""

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from backend.infrastructure.clients import RemoteClient


def test_new_client_background_profiles_pages_late_event(storage_stand, monkeypatch):
    filename = os.getenv("PROFILE_STORAGE_RUNTIME")
    if not filename:
        pytest.skip("Explicit audit runtime required")
    runtime = json.loads(Path(filename).read_text())
    monkeypatch.setenv("SERVICE_TOKEN", runtime["token"])
    actor = SimpleNamespace(
        id=str(uuid4()), login="stage4-audit", role="MANAGER", is_active=True
    )
    transaction = RemoteClient(
        "transaction", actor, address=f"127.0.0.1:{runtime['transactionPort']}"
    )
    analysis = RemoteClient(
        "analysis", actor, address=f"127.0.0.1:{runtime['analysisPort']}"
    )
    params = {k: storage_stand[k] for k in ["host", "port", "user", "password"]}
    client, account = str(uuid4()), str(uuid4())
    with psycopg.connect(**params, dbname=runtime["databases"]["transaction"]) as c:
        c.execute(
            "INSERT INTO bank_clients VALUES (%s,%s,'Stage4 runtime',NULL,true)",
            (client, client),
        )
        c.execute(
            "INSERT INTO accounts VALUES (%s,%s,%s,'BYN','OPEN')",
            (account, client, account),
        )
    page_args = dict(
        clientId=client,
        windowDays=30,
        currency="BYN",
        direction="OUT",
        history=True,
        limit=1,
    )
    assert (
        analysis._invoke("get_behavior_profile_page", page_args)["historyStatus"]
        == "NO_HISTORY"
    )
    now = datetime.now(UTC)

    def register(at):
        key = "stage4:" + uuid4().hex
        return transaction._invoke(
            "register_transaction",
            {
                "input": dict(
                    idempotencyKey=key,
                    source="stage4",
                    externalId=key,
                    accountId=account,
                    currency="BYN",
                    direction="OUT",
                    amount="100.00",
                    occurredAt=at.isoformat(),
                )
            },
        )

    operation = register(now - timedelta(minutes=1))
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        with psycopg.connect(**params, dbname=runtime["databases"]["analysis"]) as c:
            job = c.execute(
                "SELECT id FROM profile_refresh_jobs WHERE client_id=%s AND status='COMPLETED'",
                (client,),
            ).fetchone()
            event = c.execute(
                "SELECT id FROM analysis_results WHERE transaction_id=%s",
                (operation["id"],),
            ).fetchone()
        if job and event:
            break
        time.sleep(0.5)
    assert job and event, (
        "Both background refresh and Kafka EVENT must actually complete"
    )
    page = analysis._invoke("get_behavior_profile_page", page_args)
    assert page["totalCount"] >= 2 and page["nextPageToken"]
    selection = page["selectionId"]
    ids = []
    while True:
        assert page["selectionId"] == selection
        for p in page["items"]:
            ids.append(p["id"])
            assert (
                p["algorithmVersion"] == "behavior-profile-v3.2"
                and p["metricsSchemaVersion"] == 4
            )
            assert p["dynamics"]["status"] == "UNKNOWN_COVERAGE"
        if not page["nextPageToken"]:
            break
        page = analysis._invoke(
            "get_behavior_profile_page",
            dict(page_args, pageToken=page["nextPageToken"]),
        )
    assert len(set(ids)) == len(ids)
    latest_args = dict(page_args, history=False, limit=10)
    before = analysis._invoke("get_behavior_profile_page", latest_args)["items"][0][
        "asOf"
    ]
    late = register(now - timedelta(days=60))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if analysis._invoke("get_detection_context", {"transactionId": late["id"]}):
            break
        time.sleep(0.5)
    else:
        pytest.fail("Late operation must actually be analyzed")
    after = analysis._invoke("get_behavior_profile_page", latest_args)["items"][0][
        "asOf"
    ]
    assert datetime.fromisoformat(after) >= datetime.fromisoformat(before)
    status = analysis._invoke("get_profile_refresh_status", {})
    assert (
        status["counts"]["COMPLETED"] > 0 and status["discovery"]["last_error"] is None
    )
