"""Exercise location semantics through the real isolated product."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import requests

RECORD = "mutation($v:OperationEventInput!){recordOperationEvent(input:$v)}"
DETAIL = "query($id:ID!){operationLifecycle(transactionId:$id)}"
DETECTION = "query($id:ID!){detectionContext(transactionId:$id)}"


def test_locations_preserve_merchant_identity_and_provenance():
    api = os.environ["GRAPHQL_URL"]
    output = Path(os.environ["BAD_TRANS_EVIDENCE_DIR"])
    with requests.Session() as session:
        session.trust_env = False

        def call(query, values=None, error=None):
            response = session.post(api, json={"query": query, "variables": values or {}}, timeout=25)
            body = response.json()
            if error:
                code = body["errors"][0]["extensions"]["code"] if "errors" in body else body["detail"]["code"]
                assert code == error, body
                return {}
            assert response.ok and not body.get("errors"), body
            return body["data"]

        def login(name):
            session.headers.pop("Authorization", None)
            token = call('mutation($n:String!){signIn(login:$n,password:"BadTrans-demo-2026!"){accessToken}}',
                         {"n": name})["signIn"]["accessToken"]
            session.headers["Authorization"] = "Bearer " + token

        login("manager")
        prefix = "geo-" + uuid4().hex[:10]
        at = (datetime.now(UTC) - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)

        def create(field, kind, suffix, **attrs):
            key = prefix + suffix
            return call(f"mutation($v:{kind}!){{{field}(input:$v){{id}}}}",
                        {"v": {"externalId": key, "idempotencyKey": key, **attrs}})[field]["id"]

        client = create("createClient", "ClientInput", "-client", displayName="Проверка географии "+prefix)
        account = create("openAccount", "AccountInput", "-account", clientId=client, currency="BYN")
        merchant = create("registerCounterparty", "CounterpartyInput", "-merchant", displayName="Тестовая сеть "+prefix)

        def event(label, minute, channel, location=None, *, kind="PURCHASE", counterparty=merchant):
            return {"source": prefix, "operationRef": label, "eventRef": label+"-posted",
                    "accountId": account, "currency": "BYN", "direction": "OUT", "amount": "100.00",
                    "status": "POSTED", "kind": kind, "channel": channel, "counterpartyId": counterparty,
                    "occurredAt": (at+timedelta(minutes=minute)).isoformat(), "location": location}

        examples = {
            "outlet-one": event("outlet-one",0,"POS",
                                {"kind":"POINT_OF_SALE","countryCode":"BY","city":"Минск","placeRef":"shop-minsk"}),
            "outlet-two": event("outlet-two",1,"POS",
                                {"kind":"POINT_OF_SALE","countryCode":"BY","city":"Брест","placeRef":"shop-brest"}),
            "online": event("online",2,"ONLINE",
                            {"kind":"MERCHANT_REGISTRATION","countryCode":"PL","city":"Варшава"}),
            "atm": event("atm",3,"ATM",
                         {"kind":"ATM_DEVICE","countryCode":"BY","city":"Гродно","placeRef":"atm-hrodna"},
                         kind="WITHDRAWAL",counterparty=None),
            "unknown": event("unknown",4,"POS"),
        }
        authorized = {**examples["unknown"], "status":"AUTHORIZED", "eventRef":"unknown-auth",
                      "occurredAt":(at+timedelta(minutes=3)).isoformat(), "location":examples["outlet-one"]["location"]}
        assert call(RECORD, {"v": authorized})["recordOperationEvent"]["transactionId"] is None
        proof = {}
        for label, payload in examples.items():
            receipt = call(RECORD, {"v": payload})["recordOperationEvent"]
            assert call(RECORD, {"v": payload})["recordOperationEvent"] == receipt
            detail = call(DETAIL, {"id": receipt["transactionId"]})["operationLifecycle"]
            assert detail["source"] == prefix
            posted = next(event for event in detail["events"] if event["status"] == "POSTED")
            assert posted["location"] == payload["location"]
            proof[label] = {"receipt": receipt, "detail": detail}

        for label in ("outlet-one","outlet-two","online"):
            assert proof[label]["detail"]["counterpartyId"] == merchant
        call(RECORD, {"v": {**examples["online"], "location": examples["outlet-one"]["location"]}}, error="BAD_INPUT")
        call(RECORD, {"v": {**examples["outlet-one"], "location": examples["outlet-two"]["location"]}}, error="CONFLICT")

        until = monotonic()+90
        tx = proof["online"]["receipt"]["transactionId"]
        while monotonic()<until:
            details = call(DETECTION, {"id":tx})["detectionContext"]
            if details:
                break
            sleep(.6)
        else:
            raise AssertionError("No Kafka assessment for the online purchase")
        baseline = details["baseline"]
        assert baseline["sampleSize"] == 2 and baseline["counterpartyCount"] == 1
        assert baseline["knownCounterparties"] == [merchant]

        call("mutation{signOut}")
        login("analyst")
        call(DETAIL, {"id": tx}, error="FORBIDDEN")
        call("mutation{signOut}")

    (output/"operation-location.json").write_text(json.dumps({
        "source":prefix, "merchantId":merchant, "cards":proof,
        "onlineBaseline":{"sampleSize":baseline["sampleSize"],"counterpartyCount":baseline["counterpartyCount"],
                          "resultId":details["resultId"]},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
