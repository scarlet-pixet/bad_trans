"""Real GraphQL/gRPC/Kafka acceptance; opt in to the dedicated product stand."""

import json
import os
import subprocess
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
API = os.getenv("PRODUCT_API")
pytestmark = pytest.mark.skipif(
    not API, reason="Set PRODUCT_API for a real isolated product stand"
)


def graphql(query, variables=None, token=None, expected=200):
    headers = {"Authorization": "Bearer " + token} if token else {}
    r = requests.post(
        API,
        json={"query": query, "variables": variables or {}},
        headers=headers,
        timeout=40,
    )
    assert r.status_code == expected, r.text[:600]
    result = r.json()
    if expected == 200:
        assert not result.get("errors"), result.get("errors")
        return result["data"]
    return result


def login(role):
    return graphql(
        'mutation($l:String!){signIn(login:$l,password:"BadTrans-demo-2026!"){accessToken}}',
        {"l": role},
    )["signIn"]["accessToken"]


def wait_for(callback, seconds=180):
    until = monotonic() + seconds
    last = None
    while monotonic() < until:
        last = callback()
        if last:
            return last
        sleep(0.5)
    raise AssertionError(f"Timed out: {last}")


def test_end_to_end_source_profiles_explanations_queue_and_quality():
    manager, analyst = login("manager"), login("analyst")
    request_key = "acceptance-" + uuid4().hex
    mutation = (
        "mutation($k:String!){startSource(idempotencyKey:$k,intervalSeconds:0.1)}"
    )
    run = graphql(mutation, {"k": request_key}, manager)["startSource"]
    assert (
        graphql(mutation, {"k": request_key}, manager)["startSource"]["id"] == run["id"]
    )
    graphql(mutation, {"k": "forbidden"}, analyst, expected=403)
    pause = "mutation($id:ID!,$p:Boolean!){setSourceState(runId:$id,paused:$p)}"
    paused = graphql(pause, {"id": run["id"], "p": True}, manager)["setSourceState"]
    assert paused["status"] == "PAUSED"
    unknown_result = None
    if os.getenv('PRODUCT_TRANSACTION_DB_PORT'):
        settings = dict(line.split('=',1) for line in (ROOT/'.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
        with psycopg.connect(host='127.0.0.1',port=int(os.environ['PRODUCT_TRANSACTION_DB_PORT']),dbname='bank_transaction',user='bank_transaction',password=settings['DB_PASSWORD'],row_factory=dict_row) as conn:
            pending = conn.execute('SELECT payload FROM source_records WHERE run_id=%s AND transaction_id IS NULL ORDER BY sequence LIMIT 1',(run['id'],)).fetchone()
        # Reproduce a committed registration with a missing source checkpoint.
        unknown_result = graphql('mutation($x:TransactionInput!){registerTransaction(input:$x){id}}',{'x':pending['payload']},manager)['registerTransaction']['id']
    compose = os.getenv("PRODUCT_COMPOSE")
    if compose:
        subprocess.run(
            ["docker", "compose", "-f", compose, "restart", "transaction-service"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        wait_for(
            lambda: (
                requests.get(API.removesuffix("/graphql") + "/health", timeout=10)
                .json()
                .get("allReady")
            )
        )
        current = next(
            r
            for r in graphql("query{sources}", token=manager)["sources"]
            if r["id"] == run["id"]
        )
        assert (
            current["status"] == "PAUSED"
            and current["processed"] == paused["processed"]
        )
    graphql(pause, {"id": run["id"], "p": False}, manager)

    def completed():
        r = next(
            r
            for r in graphql("query{sources}", token=manager)["sources"]
            if r["id"] == run["id"]
        )
        return r if r["status"] == "COMPLETED" else None

    finished = wait_for(completed)
    assert finished["processed"] == finished["total"] == 105
    if unknown_result:
        with psycopg.connect(host='127.0.0.1',port=int(os.environ['PRODUCT_TRANSACTION_DB_PORT']),dbname='bank_transaction',user='bank_transaction',password=settings['DB_PASSWORD'],row_factory=dict_row) as conn:
            found=conn.execute('SELECT count(*) AS n FROM source_records WHERE run_id=%s AND transaction_id=%s',(run['id'],unknown_result)).fetchone()
            total=conn.execute('SELECT count(DISTINCT transaction_id) AS n FROM source_records WHERE run_id=%s',(run['id'],)).fetchone()
        assert found['n']==1 and total['n']==105

    def quality_done():
        q = graphql(
            "query($id:ID!){qualityReport(runId:$id)}", {"id": run["id"]}, manager
        )["qualityReport"]
        return q if q["pending"] == 0 else None

    quality = wait_for(quality_done)
    assert quality["warmup"] == 72 and quality["evaluated"] == 33
    assert quality["unavailable"] == 0
    assert quality["truePositive"] == 6 and quality["falsePositive"] == 0
    # Frequency requires confirmed coverage. The source does not declare it.
    assert quality["byScenario"]["frequency_burst"]["falseNegative"] == 6
    assert quality["falseNegative"] == 6
    high = next(x for x in quality["examples"] if x["expected"] and x["predicted"])
    detection = graphql(
        "query($tx:ID!){detectionContext(transactionId:$tx)}",
        {"tx": high["transactionId"]},
        manager,
    )["detectionContext"]
    assert detection["score"] >= detection["threshold"]
    assert detection["explanations"] and len(detection["profiles"]) == 3
    assert all(
        high["transactionId"] not in p["history_ids"] for p in detection["profiles"]
    )
    case_page = graphql(
        "query{reviewCases(page:{limit:100}){items{id transactionId status rowVersion}}}",
        token=manager,
    )["reviewCases"]["items"]
    case = wait_for(
        lambda: next(
            (
                x
                for x in graphql(
                    "query{reviewCases(page:{limit:100}){items{id transactionId status rowVersion}}}",
                    token=manager,
                )["reviewCases"]["items"]
                if x["transactionId"] == high["transactionId"]
            ),
            None,
        )
    )
    context = graphql(
        "query($id:ID!){caseContext(caseId:$id)}", {"id": case["id"]}, analyst
    )["caseContext"]
    assert context["transaction"]["id"] == high["transactionId"]
    distribution = graphql(
        "mutation($x:DistributionInput!){distributeCases(input:$x){assignments}}",
        {"x": {"limit": 100, "idempotencyKey": uuid4().hex}}, manager,
    )["distributeCases"]
    assert any(row["caseId"] == case["id"] for row in distribution["assignments"])
    case = graphql("query($id:ID!){reviewCase(id:$id){id status rowVersion}}",
                   {"id": case["id"]}, manager)["reviewCase"]
    assert case["status"] == "ASSIGNED"
    taken = graphql(
        "mutation($id:ID!,$v:Int!,$k:String!){takeCase(caseId:$id,expectedVersion:$v,idempotencyKey:$k){id status rowVersion}}",
        {"id": case["id"], "v": case["rowVersion"], "k": uuid4().hex},
        analyst,
    )["takeCase"]
    closed = graphql(
        "mutation($x:CaseDecisionInput!){closeCase(input:$x){id status rowVersion}}",
        {
            "x": {
                "caseId": case["id"],
                "expectedVersion": taken["rowVersion"],
                "outcome": "UNUSUAL_CONFIRMED",
                "reason": "Synthetic acceptance investigation",
                "idempotencyKey": uuid4().hex,
            }
        },
        analyst,
    )["closeCase"]
    assert closed["status"] == "CLOSED"
    profiles = graphql("query{behaviorProfiles(windowDays:30)}", token=manager)[
        "behaviorProfiles"
    ]
    assert profiles
    def scheduled_after_feedback():
        response = graphql(
            "query($c:ID!){behaviorProfiles(clientId:$c,windowDays:30)}",
            {"c": context["transaction"]["clientId"]}, manager,
        )
        return next((profile for profile in response["behaviorProfiles"]
                     if profile["origin"] == "SCHEDULED" and profile.get("excludedCount") == 1), None)

    scheduled = wait_for(scheduled_after_feedback, seconds=75)
    assert scheduled["observedCount"] == 35
    assert scheduled["sampleSize"] == 34
    assert context['detection']['resultId']==context['case']['latestResultId']
    dash = graphql("query{managerDashboard}", token=manager)["managerDashboard"]
    assert (
        not dash["partialErrors"] and dash["components"]["analysis"]["analyzed"] >= 105
    )
    out = ROOT / "artifacts/product-20261105/live-proof.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "source": finished,
                "scheduledProfile": scheduled,
                "uncertainCommitDeduplicated": bool(unknown_result),
                "quality": quality,
                "detection": detection,
                "closedCase": closed,
                "dashboard": dash,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    graphql("mutation{signOut}", token=analyst)
    graphql("mutation{signOut}", token=manager)


def test_product_roles_and_staff_auth_boundary():
    admin, analyst, manager = login("admin"), login("analyst"), login("manager")
    users = graphql("query{staffUsers}", token=admin)["staffUsers"]
    assert users and all(
        "password" not in u and "password_hash" not in u for u in users
    )
    graphql("query{staffUsers}", token=manager, expected=403)
    graphql("query{managerDashboard}", token=analyst, expected=403)
    graphql("query{behaviorProfiles}", token=admin, expected=403)
    graphql("query{sources}", token=analyst, expected=403)
    graphql("query{sources}", expected=401)
    for token in (admin, analyst, manager):
        graphql("mutation{signOut}", token=token)
