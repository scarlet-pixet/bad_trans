"""A real network call between separately running owner applications."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import grpc
import psycopg
import pytest

from backend.common.data import DomainError
from backend.infrastructure.clients import EventTransactionClient, RemoteClient


def test_independent_runtime_snapshot_analysis_and_command_replay(
    storage_stand, monkeypatch
):
    filename = os.getenv("PROFILE_STORAGE_RUNTIME")
    if not filename:
        pytest.skip("Set PROFILE_STORAGE_RUNTIME for separate isolated owner processes")
    runtime = json.loads(Path(filename).read_text(encoding="utf-8"))
    for owner in ("transaction", "analysis"):
        with grpc.insecure_channel(f"127.0.0.1:{runtime[owner + 'Port']}") as channel:
            grpc.channel_ready_future(channel).result(timeout=15)
    monkeypatch.setenv("SERVICE_TOKEN", runtime["token"])
    monkeypatch.setenv("TRANSACTION_GRPC", f"127.0.0.1:{runtime['transactionPort']}")
    params = {k: storage_stand[k] for k in ("host", "port", "user", "password")}
    client, account, subject = str(uuid4()), str(uuid4()), str(uuid4())
    t = datetime.now(UTC) - timedelta(minutes=1)
    with psycopg.connect(
        **params,
        dbname=runtime.get("databases", {}).get(
            "transaction", "st2_transaction_runtime"
        ),
    ) as c:
        c.execute(
            "INSERT INTO bank_clients VALUES (%s,%s,'RPC audit',NULL,true)",
            (client, client),
        )
        c.execute(
            "INSERT INTO accounts VALUES (%s,%s,%s,'BYN','OPEN')",
            (account, client, account),
        )
        for d in range(21):
            identifier = subject if d == 0 else str(uuid4())
            c.execute(
                """INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at)
                VALUES (%s,'rpc-audit',%s,%s,%s,'BYN','OUT',%s,%s)""",
                (
                    identifier,
                    identifier,
                    account,
                    "1000.00" if d == 0 else "100.00",
                    t - timedelta(days=d),
                    t - timedelta(seconds=10),
                ),
            )
    actor = SimpleNamespace(
        id=str(uuid4()), login="audit-manager", role="MANAGER", is_active=True
    )
    transaction = EventTransactionClient()
    request = dict(
        requestKey="rpc:" + subject,
        clientId=client,
        currency="BYN",
        direction="OUT",
        windowStart=(t - timedelta(days=90)).isoformat(),
        asOf=t.isoformat(),
        ingestedBefore=t.isoformat(),
    )
    snap = transaction.prepare_behavior_history(request)
    assert snap["itemCount"] == 20
    assert transaction.prepare_behavior_history(request) == snap
    legacy = transaction.get_history(
        dict(
            clientId=client,
            currency="BYN",
            direction="OUT",
            windowStart=request["windowStart"],
            beforeAt=request["asOf"],
            ingestedBefore=request["ingestedBefore"],
            limit=100,
        )
    )
    assert len(legacy["items"]) == 20  # Previously published RPC remains usable.
    public_actor_client = RemoteClient(
        "transaction", actor, address=f"127.0.0.1:{runtime['transactionPort']}"
    )
    with pytest.raises(DomainError) as denied:
        public_actor_client._invoke("prepare_behavior_history", {"parameters": request})
    assert denied.value.code == "FORBIDDEN"
    analysis = RemoteClient(
        "analysis", actor, address=f"127.0.0.1:{runtime['analysisPort']}"
    )
    arguments = dict(transactionId=subject, idempotencyKey="rpc-audit:" + subject)
    first = analysis._invoke("analyze_transaction", arguments)
    assert analysis._invoke("analyze_transaction", arguments) == first
    context = analysis._invoke("get_detection_context", dict(transactionId=subject))
    assert context["contextSchemaVersion"] == 2
    assert context["profileAlgorithmVersion"]=="behavior-profile-v3.2"
    assert context["assessmentAlgorithmVersion"]=="behavior-assessment-v3.0"
    assert context["baseline"]["metricsSchemaVersion"]==4
    assert context["baseline"]["operationsPerDay"] is None
    assert context["historySnapshot"]["itemCount"] == 20
    assert len(context["profileVersionIds"]) == 1
    assert context["primaryProfileVersionId"] == context["profileVersionIds"][0]
    assert len(context["profiles"]) == 3
    with psycopg.connect(
        **params,
        dbname=runtime.get("databases", {}).get("analysis", "st2_analysis_runtime"),
    ) as c:
        result_count = c.execute(
            "SELECT count(*) FROM analysis_results WHERE transaction_id=%s", (subject,)
        ).fetchone()[0]
        assert result_count == 1
        link_count = c.execute(
            "SELECT count(*) FROM analysis_result_profiles WHERE result_id=%s",
            (context["resultId"],),
        ).fetchone()[0]
        assert link_count == 1
