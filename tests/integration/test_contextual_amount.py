"""Verify contextual amount baselines and frozen evidence over the isolated product."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import requests

RECORD = "mutation($v:OperationEventInput!){recordOperationEvent(input:$v)}"
DETECTION = "query($id:ID!,$r:ID){detectionContext(transactionId:$id,resultId:$r)}"


def test_amount_baselines_by_kind_channel_and_general_fallback():
    api = os.environ["GRAPHQL_URL"]
    out = Path(os.environ["BAD_TRANS_EVIDENCE_DIR"])
    with requests.Session() as session:
        session.trust_env = False

        def call(query, variables=None):
            response = session.post(api, json={"query": query, "variables": variables or {}}, timeout=30)
            body = response.json()
            assert response.ok and not body.get("errors"), body
            return body["data"]

        token = call('mutation{signIn(login:"manager",password:"BadTrans-demo-2026!"){accessToken}}')["signIn"]["accessToken"]
        session.headers["Authorization"] = "Bearer " + token
        prefix = "context-" + uuid4().hex[:10]
        base = (datetime.now(UTC)-timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)

        def create(field, kind, suffix, **attrs):
            key = prefix + suffix
            return call(f"mutation($v:{kind}!){{{field}(input:$v){{id}}}}",
                        {"v": {"externalId": key, "idempotencyKey": key, **attrs}})[field]["id"]

        client = create("createClient", "ClientInput", "-client", displayName="Контекстный профиль "+prefix)
        account = create("openAccount", "AccountInput", "-account", clientId=client, currency="BYN")

        def register(label, at, kind, channel, amount):
            values = {"source":prefix,"operationRef":label,"eventRef":label+"-posted","kind":kind,
                      "accountId":account,"currency":"BYN","direction":"OUT","amount":amount,
                      "occurredAt":at.isoformat(),"status":"POSTED","channel":channel}
            return call(RECORD,{"v":values})["recordOperationEvent"]["transactionId"]

        for kind, channel, amount in (("PURCHASE","POS","100.00"),("TRANSFER","BANK_API","1000.00")):
            for index in range(20):
                register(f"{kind}-{index}",base-timedelta(days=1+index//2,minutes=index%2),kind,channel,amount)

        samples = {
            "purchase": (register("purchase",base,"PURCHASE","POS","400.00"),"KIND","PURCHASE","100.00",40),
            "transfer": (register("transfer",base+timedelta(minutes=1),"TRANSFER","BANK_API","1000.00"),
                         "KIND","TRANSFER","1000.00",0),
            "channel": (register("channel",base+timedelta(minutes=2),"WITHDRAWAL","BANK_API","1000.00"),
                        "CHANNEL","BANK_API","1000.00",0),
            "general": (register("general",base+timedelta(minutes=3),"WITHDRAWAL","ATM","3000.00"),
                        "GENERAL",None,None,40),
        }

        def result(tx_id):
            deadline=monotonic()+140
            while monotonic()<deadline:
                info=call(DETECTION,{"id":tx_id})["detectionContext"]
                if info:
                    return info
                sleep(.7)
            raise AssertionError(f"No assessment for {tx_id}")

        proof = {}
        for label,(tx_id,scope,value,center,score) in samples.items():
            info=result(tx_id)
            basis=info["amountBasis"]
            amount=next(c for c in info["checks"] if c["code"]=="AMOUNT_DEVIATION")
            assert (basis["scope"],basis["value"])==(scope,value)
            if center is not None:
                assert float(basis["statistics"]["medianAmount"])==float(center)
            assert amount["contribution"]==score
            assert amount["baseline"]["comparisonBasisId"]==basis["id"]
            assert basis["generalProfileVersionId"]==info["primaryProfileVersionId"]
            assert info["threshold"]==40 and basis["windowDays"]==30
            assert basis["generalCoverage"]["mode"]=="UNKNOWN"
            assert call(DETECTION,{"id":tx_id,"r":info["resultId"]})["detectionContext"]["amountBasis"]==basis
            proof[label]={"transactionId":tx_id,"resultId":info["resultId"],"basis":basis,
                          "amount":amount,"score":info["assessmentScore"],"assessmentStatus":info["assessmentStatus"]}

        assert proof["purchase"]["basis"]["sampleSize"]==20
        assert proof["general"]["basis"]["sampleSize"]==43
        assert [c["sampleSize"] for c in proof["general"]["basis"]["candidates"]]==[1,0]
        assert all(c["reason"]=="INSUFFICIENT_CONTEXT_HISTORY" for c in proof["general"]["basis"]["candidates"])
        call("mutation{signOut}")

    (out/"contextual-amount.json").write_text(json.dumps({"clientId":client,"source":prefix,"samples":proof},
                                                       ensure_ascii=False,indent=2),encoding="utf-8")
