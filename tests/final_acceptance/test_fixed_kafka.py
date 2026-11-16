"""Fixed business inputs delivered by real Transaction outbox and audit Kafka."""

import os
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from types import SimpleNamespace
from decimal import Decimal
import pytest
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from tools.coursework_holdout import AT, dataset, identifier
from backend.infrastructure.clients import RemoteClient


def test_fixed_seed_automatic_pipeline(storage_stand, monkeypatch):
    filename = os.getenv("PROFILE_STORAGE_RUNTIME")
    if not filename:
        pytest.skip("Explicit isolated owners and Kafka required")
    r = json.loads(Path(filename).read_text())
    assert r["kafkaContainer"].startswith("bad-trans-final11-")
    monkeypatch.setenv("SERVICE_TOKEN", r["token"])
    params = {k: storage_stand[k] for k in ["host", "port", "user", "password"]}

    def connect(owner):
        return psycopg.connect(
            **params,
            dbname=r["databases"][owner],
            row_factory=dict_row,
            connect_timeout=4,
        )

    actor = SimpleNamespace(
        id=identifier("rpc-manager"), login="fixed-seed-manager", role="MANAGER"
    )
    txn = RemoteClient(
        "transaction", actor, address="127.0.0.1:" + str(r["transactionPort"])
    )
    evidence = {}
    base = AT - timedelta(days=1)
    cp = identifier("server-known")
    newcp = identifier("server-new")
    specs = [
        ("narrow-high", False, "200", 40, "BYN", "OUT"),
        ("wide-high", True, "200", 0, "BYN", "OUT"),
        ("normal", False, "100", 0, "BYN", "OUT"),
        ("narrow-low", False, "5", 40, "BYN", "OUT"),
        ("novel", False, "100", 10, "BYN", "OUT"),
        ("burst", False, "100", 40, "BYN", "OUT"),
        ("usd-in", False, "100", 0, "USD", "IN"),
    ]
    for name, wide, amount, expected, currency, direction in specs:
        client = identifier(
            "server-client:" + ("narrow-high" if name == "usd-in" else name)
        )
        account = identifier("server-account:" + name)
        rows = dataset("server:" + name, wide)
        for row in rows:
            row["occurredAt"] = (
                datetime.fromisoformat(row["occurredAt"]) - timedelta(days=1)
            ).isoformat()
        if name == "burst":
            for i in range(20):
                rows.append(
                    dict(
                        id=identifier("burst:" + str(i)),
                        amount="100",
                        occurredAt=(base - timedelta(minutes=5, seconds=i)).isoformat(),
                    )
                )
        with connect("transaction") as c:
            c.execute(
                "INSERT INTO bank_clients VALUES (%s,%s,%s,NULL,true) ON CONFLICT DO NOTHING",
                (client, client, "Fixed " + name),
            )
            c.execute(
                "INSERT INTO accounts VALUES (%s,%s,%s,%s,'OPEN')",
                (account, client, account, currency),
            )
            for key in [cp, newcp]:
                c.execute(
                    "INSERT INTO counterparties VALUES (%s,%s,'Fixed recipient',NULL) ON CONFLICT DO NOTHING",
                    (key, key),
                )
            for row in rows:
                c.execute(
                    "INSERT INTO transactions(id,source,external_id,account_id,counterparty_id,amount,currency,direction,occurred_at,ingested_at) VALUES (%s,'stage11-history',%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        row["id"],
                        row["id"],
                        account,
                        cp,
                        row["amount"],
                        currency,
                        direction,
                        row["occurredAt"],
                        base - timedelta(minutes=1),
                    ),
                )
            c.execute(
                "INSERT INTO behavior_observation_coverage VALUES (%s,%s,%s,%s,'DECLARED_COMPLETE',%s,'stage11-fixed',now()-interval '2 seconds',%s)",
                (
                    identifier("coverage:" + name),
                    client,
                    currency,
                    direction,
                    Jsonb(
                        [
                            {
                                "from": (base - timedelta(days=90)).isoformat(),
                                "to": base.isoformat(),
                            }
                        ]
                    ),
                    "stage11:" + name,
                ),
            )
        values = dict(
            input=dict(
                idempotencyKey="stage11:" + name,
                source="stage11-fixed",
                externalId="stage11:" + name,
                accountId=account,
                counterpartyId=newcp if name == "novel" else cp,
                amount=amount,
                currency=currency,
                direction=direction,
                occurredAt=base.isoformat(),
            )
        )
        tx = txn._invoke("register_transaction", values)
        assert tx == txn._invoke("register_transaction", values)
        deadline = time.monotonic() + 40
        result = None
        while time.monotonic() < deadline:
            with connect("analysis") as c:
                result = c.execute(
                    "SELECT id,assessment_score,assessment_status,calculation_id FROM analysis_results WHERE transaction_id=%s",
                    (tx["id"],),
                ).fetchone()
            if result:
                break
            time.sleep(0.4)
        assert result is not None, name
        assert result["assessment_score"] == expected, (name, result)
        with connect("analysis") as c:
            profile = c.execute(
                "SELECT p.metrics FROM profile_versions p JOIN analysis_result_profiles l ON l.profile_version_id=p.id WHERE l.result_id=%s AND l.window_days=30",
                (result["id"],),
            ).fetchone()["metrics"]
            assert profile["sampleSize"] == len(rows)
            assert Decimal(profile["medianAmount"]) == 100
            assert Decimal(profile["madAmount"]) == (40 if wide else 0)
        case = None
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            with connect("case") as c:
                case = c.execute(
                    "SELECT id FROM review_cases WHERE transaction_id=%s", (tx["id"],)
                ).fetchone()
            if expected < 40 or case:
                break
            time.sleep(0.3)
        assert bool(case) == (expected >= 40), name
        evidence[name] = dict(
            clientId=client,
            transactionId=tx["id"],
            resultId=str(result["id"]),
            score=expected,
            status=result["assessment_status"],
            profileCount=len(rows),
            currency=currency,
            direction=direction,
            caseId=str(case["id"]) if case else None,
        )
    output = os.getenv("FINAL_ACCEPTANCE_ARTIFACTS")
    if output:
        Path(output, "fixed-kafka-pipeline.json").write_text(
            json.dumps(
                dict(
                    seed="coursework-holdout-20261111-v1",
                    businessTime=base.isoformat(),
                    scenarios=evidence,
                    mainWrites=False,
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
