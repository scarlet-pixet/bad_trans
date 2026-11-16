"""Prove GraphQL -> gRPC -> PostgreSQL outbox -> Kafka -> Analysis -> Kafka -> Case."""
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4
import json
import os

import psycopg
import requests
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.getenv("BAD_TRANS_EVIDENCE_DIR",str(ROOT / "artifacts/verification"))) / "live-proof.json"
OUT.parent.mkdir(parents=True, exist_ok=True)
env = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines() if "=" in line)
password = env["DB_PASSWORD"]
api = os.getenv("BAD_TRANS_API","http://127.0.0.1:18100/graphql")
fixture = json.loads((ROOT / "infra/seed_ids.json").read_text())
run = uuid4().hex[:10].upper()


def graphql(query, variables=None, token=None, expected=200):
    headers = {"Authorization": "Bearer " + token} if token else {}
    response = requests.post(api, json={"query": query, "variables": variables},
                             headers=headers, timeout=20)
    assert response.status_code == expected, (response.status_code, response.text[:500])
    body = response.json()
    if expected == 200:
        assert "errors" not in body, body.get("errors")
        return body["data"]
    return body


def db(name, sql, params=()):
    defaults = {'transaction':57742,'analysis':57743,'case':57744}
    port = int(os.getenv('BAD_TRANS_'+name.upper()+'_DB_PORT',str(defaults[name])))
    with psycopg.connect(host='127.0.0.1',port=port,user='bank_'+name,password=password,
                         dbname='bank_'+name,row_factory=dict_row) as conn:
        return conn.execute(sql, params).fetchone()


graphql("query { me { login } }", expected=401)
login = graphql('mutation($login:String!,$password:String!){ signIn(login:$login,password:$password){ accessToken user { login role } } }',
                {"login": "manager", "password": "BadTrans-demo-2026!"})["signIn"]
assert login["user"] == {"login": "manager", "role": "MANAGER"}
token = login["accessToken"]
summary = graphql("query { transactionSummary { transactionCount totalAmount } }", token=token)["transactionSummary"]

client = graphql("mutation($input:ClientInput!){createClient(input:$input){id externalId}}",
                 {"input": {"externalId": "BAD-TRANS-CLIENT-" + run,
                            "displayName": "Проверка Bad Trans " + run,
                            "idempotencyKey": "client-" + run}}, token)["createClient"]
account = graphql("mutation($input:AccountInput!){openAccount(input:$input){id externalId}}",
                  {"input": {"clientId": client["id"], "externalId": "BAD-TRANS-ACCOUNT-" + run,
                             "currency": "BYN", "idempotencyKey": "account-" + run}}, token)["openAccount"]
assert db("transaction", "SELECT id FROM bank_clients WHERE id=%s", (client["id"],))
assert db("transaction", "SELECT id FROM accounts WHERE id=%s", (account["id"],))

input_value = {"source": "bad_trans", "externalId": "BAD-TRANS-TX-" + run,
               "accountId": fixture["account"], "counterpartyId": fixture["counterparty"],
               "amount": "10000.00", "currency": "BYN",
               "direction": "OUT", "occurredAt": "2026-09-30T11:30:00Z",
               "idempotencyKey": "tx-" + run}
transaction = graphql("mutation($input:TransactionInput!){registerTransaction(input:$input){id externalId amount clientId}}",
                      {"input": input_value}, token)["registerTransaction"]
assert transaction["clientId"] == fixture["client"]
repeated = graphql("mutation($input:TransactionInput!){registerTransaction(input:$input){id externalId amount clientId}}",
                   {"input": input_value}, token)["registerTransaction"]
assert repeated["id"] == transaction["id"]

deadline = monotonic() + 90
while monotonic() < deadline:
    outbox = db("transaction", "SELECT id,published_at FROM outbox_events WHERE payload->'payload'->>'transaction_id'=%s", (transaction["id"],))
    result = db("analysis", "SELECT id,run_id,score,threshold,data_status FROM analysis_results WHERE transaction_id=%s ORDER BY calculated_at DESC LIMIT 1", (transaction["id"],))
    case = db("case", "SELECT id,transaction_id,latest_score,status FROM review_cases WHERE transaction_id=%s", (transaction["id"],))
    if outbox and outbox["published_at"] and result and case:
        break
    sleep(1)
else:
    raise AssertionError({"outbox": str(outbox), "result": str(result), "case": str(case)})

assert result["score"] >= result["threshold"] and case["latest_score"] == result["score"]
outbox_count = db("transaction", "SELECT count(*)::int n FROM outbox_events WHERE payload->'payload'->>'transaction_id'=%s", (transaction["id"],))["n"]
assert outbox_count == 1
analysis_inbox = db("analysis", "SELECT count(*)::int n FROM inbox_events WHERE event_type='transaction.registered.v1'")
case_inbox = db("case", "SELECT count(*)::int n FROM inbox_events WHERE event_type='analysis.completed.v1'")
assert analysis_inbox["n"] >= 1 and case_inbox["n"] >= 1
observed = graphql("query($id:ID!){reviewCase(id:$id){id status latestScore transactionId}}",
                   {"id": str(case["id"])}, token)["reviewCase"]
assert observed["transactionId"] == transaction["id"]

analyst = graphql('mutation($login:String!,$password:String!){signIn(login:$login,password:$password){accessToken}}',
                  {"login": "analyst", "password": "BadTrans-demo-2026!"})["signIn"]["accessToken"]
graphql("query { transactionSummary { transactionCount } }", token=analyst, expected=403)

proof = {
    "run": run, "checkedAt": datetime.now(timezone.utc).isoformat(),
    "gateway": "GraphQL with JWT+Redis role validation",
    "transport": "gRPC bank.v1 typed methods for public contracts; bank.internal.v1.BusinessService extension for getClient, transactionSummary and health",
    "eventFlow": "transaction.registered.v1 -> Analysis -> analysis.completed.v1 -> Case",
    "managerSummaryBefore": summary,
    "client": client, "account": account,
    "transaction": transaction,
    "idempotentRepeatSameId": repeated["id"] == transaction["id"],
    "transactionOutboxCount": outbox_count,
    "transactionOutboxId": str(outbox["id"]), "transactionOutboxPublished": bool(outbox["published_at"]),
    "analysis": {"resultId": str(result["id"]), "runId": str(result["run_id"]),
                 "score": result["score"], "threshold": result["threshold"],
                 "dataStatus": result["data_status"], "inboxCount": analysis_inbox["n"]},
    "case": {"id": str(case["id"]), "transactionId": str(case["transaction_id"]),
             "latestScore": case["latest_score"], "status": case["status"],
             "inboxCount": case_inbox["n"]},
    "graphqlCase": observed,
    "anonymousHttp": 401, "analystSummaryHttp": 403,
}
OUT.write_text(json.dumps(proof, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps(proof, indent=2, ensure_ascii=False))
