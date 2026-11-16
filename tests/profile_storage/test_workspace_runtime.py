"""Real owner RPC authorization, assignment revocation and pinned results."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, UTC, timedelta
from uuid import uuid4
from unittest.mock import Mock

import grpc
import psycopg
import pytest
from psycopg.rows import dict_row

from backend.common.data import DomainError
from backend.infrastructure.clients import RemoteClient


def test_independent_owners_enforce_scope_and_historical_results(
    storage_stand, monkeypatch
):
    filename = os.getenv("PROFILE_STORAGE_RUNTIME")
    if not filename:
        pytest.skip("Explicit isolated owner runtime required")
    r = json.loads(Path(filename).read_text())
    monkeypatch.setenv("SERVICE_TOKEN", r["token"])
    for owner in ["transaction", "analysis", "case"]:
        with grpc.insecure_channel(f"127.0.0.1:{r[owner + 'Port']}") as channel:
            grpc.channel_ready_future(channel).result(timeout=15)
    manager = SimpleNamespace(
        id=str(uuid4()), role="MANAGER", login="manager", is_active=True
    )
    analyst = SimpleNamespace(
        id=str(uuid4()), role="ANALYST", login="analyst", is_active=True
    )
    params = {k: storage_stand[k] for k in ["host", "port", "user", "password"]}

    def query(owner, sql, args=()):
        with psycopg.connect(
            **params,
            dbname=r["databases"][owner],
            row_factory=dict_row,
            connect_timeout=3,
        ) as c:
            return c.execute(sql, args).fetchone()

    def remote(owner, actor):
        return RemoteClient(owner, actor, address=f"127.0.0.1:{r[owner + 'Port']}")

    am = remote("analysis", manager)
    aa = remote("analysis", analyst)
    ta = remote("transaction", analyst)
    ca = remote("case", analyst)
    with pytest.raises(DomainError) as denied_write:
        ta.register_transaction(analyst,{})
    assert denied_write.value.code=='FORBIDDEN'
    client, account, subject = (str(uuid4()) for _ in range(3))
    t = datetime.now(UTC) - timedelta(minutes=1)
    with psycopg.connect(**params, dbname=r["databases"]["transaction"]) as c:
        c.execute(
            "INSERT INTO bank_clients VALUES (%s,%s,'Workspace runtime',NULL,true)",
            (client, client),
        )
        c.execute(
            "INSERT INTO accounts VALUES (%s,%s,%s,'BYN','OPEN')",
            (account, client, account),
        )
        for d in range(21):
            key = subject if d == 0 else str(uuid4())
            c.execute(
                "INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at) VALUES (%s,'workspace-runtime',%s,%s,%s,'BYN','OUT',%s,%s)",
                (
                    key,
                    key,
                    account,
                    "1000.00" if d == 0 else "100.00",
                    t - timedelta(days=d),
                    t - timedelta(seconds=10),
                ),
            )

    def analyze():
        am._invoke(
            "analyze_transaction",
            dict(transactionId=subject, idempotencyKey="workspace:" + uuid4().hex),
        )
        return am._invoke("get_detection_context", dict(transactionId=subject))

    first = analyze()
    event = query(
        "analysis",
        "SELECT payload FROM outbox_events WHERE payload->'payload'->>'result_id'=%s",
        (first["resultId"],),
    )["payload"]
    from backend.services.case.events.analysis import CaseEventService

    with psycopg.connect(
        **params, dbname=r["databases"]["case"], row_factory=dict_row
    ) as c:
        CaseEventService().process(c, event)
    case = query(
        "case", "SELECT * FROM review_cases WHERE transaction_id=%s", (subject,)
    )
    assert case
    ca._invoke(
        "take_case",
        dict(
            caseId=str(case["id"]),
            expectedVersion=case["row_version"],
            idempotencyKey="take:" + uuid4().hex,
        ),
    )
    assert ta._invoke("get_transaction", dict(id=subject))["id"] == subject
    assert (
        aa._invoke(
            "get_detection_context",
            dict(transactionId=subject, resultId=first["resultId"]),
        )
        == first
    )
    profile = aa._invoke(
        "get_profile_version", dict(id=first["primaryProfileVersionId"])
    )
    assert profile["metadata"]["id"] == first["primaryProfileVersionId"]
    second = analyze()
    history = aa._invoke("get_result_history", dict(transactionId=subject, limit=1))
    assert (
        history["items"][0]["resultId"] == second["resultId"]
        and history["nextPageToken"]
    )
    older = aa._invoke(
        "get_result_history",
        dict(transactionId=subject, limit=1, pageToken=history["nextPageToken"]),
    )
    assert older["items"][0]["resultId"] == first["resultId"]
    assert (
        aa._invoke(
            "get_detection_context",
            dict(transactionId=subject, resultId=first["resultId"]),
        )
        == first
    )
    legacy = query(
        "analysis",
        "SELECT profile_id,run_id FROM analysis_results WHERE id=%s",
        (first["resultId"],),
    )
    assert aa._invoke("get_profile", dict(id=str(legacy["profile_id"])))
    for owner, method, args in [
        ("analysis", "get_behavior_profiles", dict(clientId=client)),
        ("analysis", "get_behavior_profile_page", {}),
        ("analysis", "get_run", dict(id=str(legacy["run_id"]))),
        ("transaction", "search_transactions", {}),
    ]:
        with pytest.raises(DomainError) as error:
            remote(owner, analyst)._invoke(method, args)
        assert error.value.code == "FORBIDDEN"
    # A cached client must still invoke the owner after reassignment.
    from backend.infrastructure.cache import CachedClient

    cached = CachedClient.__new__(CachedClient)
    RemoteClient.__init__(
        cached, "analysis", analyst, address=f"127.0.0.1:{r['analysisPort']}"
    )
    cached.cache = Mock()
    assert cached.get_result(id=first["resultId"])
    with psycopg.connect(**params, dbname=r["databases"]["case"]) as c:
        c.execute(
            "UPDATE review_cases SET status='CLOSED',assignee_id=%s,row_version=row_version+1 WHERE id=%s",
            (str(uuid4()), case["id"]),
        )
    for method, args in [
        ("get_result", dict(id=first["resultId"])),
        ("get_profile", dict(id=str(legacy["profile_id"]))),
    ]:
        with pytest.raises(DomainError) as error:
            getattr(cached, method)(**args)
        assert error.value.code == "FORBIDDEN"
    cached.cache.get.assert_not_called()
    for obj, method, args in [
        (ta, "get_transaction", dict(id=subject)),
        (
            aa,
            "get_detection_context",
            dict(transactionId=subject, resultId=first["resultId"]),
        ),
        (aa, "get_result_history", dict(transactionId=subject)),
        (aa, "get_profile_version", dict(id=first["primaryProfileVersionId"])),
        (
            aa,
            "analyze_transaction",
            dict(transactionId=subject, idempotencyKey=uuid4().hex),
        ),
    ]:
        with pytest.raises(DomainError) as error:
            obj._invoke(method, args)
        assert error.value.code == "FORBIDDEN"
    assert (
        am._invoke(
            "get_detection_context",
            dict(transactionId=subject, resultId=first["resultId"]),
        )
        == first
    )

    # Simulate an actual outage of this audit Case process only. Other owners
    # remain reachable, but analyst reads must fail closed rather than use cache.
    import subprocess

    wrapper = Path(r["processes"]["case"]["wrapper"]).resolve()
    assert (
        wrapper.is_relative_to(Path(__file__).resolve().parents[2] / ".local")
        and wrapper.name == "native-case.py"
    )
    env = os.environ.copy()
    env["AUDIT_CASE_WRAPPER"] = str(wrapper)
    command = """$p=Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {$_.CommandLine -and $_.CommandLine.Contains($env:AUDIT_CASE_WRAPPER)}; $p | ForEach-Object {Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue}"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        env=env,
        capture_output=True,
        check=True,
        timeout=12,
    )
    with pytest.raises(DomainError) as unavailable:
        cached.get_result(id=first["resultId"])
    assert unavailable.value.code == "DEPENDENCY_UNAVAILABLE"
    assert (
        am._invoke(
            "get_detection_context",
            dict(transactionId=subject, resultId=first["resultId"]),
        )
        == first
    )
