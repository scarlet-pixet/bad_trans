"""Real GraphQL acceptance of the lifecycle and the customer's posted expenses."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import requests

RECORD = "mutation($v:OperationEventInput!){recordOperationEvent(input:$v)}"
SPENDING = "query($id:ID!,$p:PeriodInput!){clientSpending(clientId:$id,currencyCode:\"BYN\",period:$p)}"
DETAIL = "query($id:ID!){operationLifecycle(transactionId:$id)}"


def test_purchase_posting_partial_refund_and_own_transfer():
    gateway = os.environ["GRAPHQL_URL"]
    output = Path(os.environ["BAD_TRANS_EVIDENCE_DIR"])
    with requests.Session() as session:
        session.trust_env = False

        def call(query, variables=None, error=None):
            response = session.post(gateway, json={"query": query, "variables": variables or {}}, timeout=35)
            body = response.json()
            if error:
                code = body["errors"][0]["extensions"]["code"] if "errors" in body else body["detail"]["code"]
                assert code == error, body
                return {}
            assert response.ok and not body.get("errors"), body
            return body["data"]

        def login(name):
            session.headers.pop("Authorization", None)
            payload = call('mutation($n:String!){signIn(login:$n,password:"BadTrans-demo-2026!"){accessToken}}',
                           {"n": name})
            session.headers["Authorization"] = "Bearer " + payload["signIn"]["accessToken"]

        login("manager")
        source = "cycle-" + uuid4().hex[:9]
        now = (datetime.now(UTC) - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        label = "Расходы 70 " + source
        client = call("mutation($v:ClientInput!){createClient(input:$v){id}}", {"v": {
            "externalId": source, "idempotencyKey": source, "displayName": label,
        }})["createClient"]["id"]

        def account(suffix):
            reference = source + suffix
            result = call("mutation($v:AccountInput!){openAccount(input:$v){id}}", {"v": {
                "externalId": reference, "idempotencyKey": reference, "clientId": client, "currency": "BYN",
            }})
            return result["openAccount"]["id"]

        debit, credit = account("-debit"), account("-credit")
        window = {"from": (now - timedelta(hours=1)).isoformat(), "to": (now + timedelta(hours=2)).isoformat()}

        def event(reference, status, minute, **attrs):
            return {"source": source, "operationRef": reference, "eventRef": reference + "-" + status,
                    "kind": "PURCHASE", "channel": "ONLINE", "accountId": debit,
                    "direction": "OUT", "currency": "BYN", "amount": "100.00", "status": status,
                    "occurredAt": (now + timedelta(minutes=minute)).isoformat(), **attrs}

        def record(item, error=None):
            return call(RECORD, {"v": item}, error=error).get("recordOperationEvent", {})

        def spending():
            return call(SPENDING, {"id": client, "p": window})["clientSpending"]

        authorization = event("purchase", "AUTHORIZED", 0)
        original = record(authorization)
        assert original["transactionId"] is None and spending()["netSpending"] == "0.00"
        posted_event = event("purchase", "POSTED", 1)
        posted = record(posted_event)
        assert posted["operationId"] == original["operationId"]
        assert record(posted_event) == posted and record(authorization) == original
        record({**posted_event, "amount": "101.00"}, "CONFLICT")
        record(event("purchase", "CANCELLED", 2), "CONFLICT")

        record(event("void", "AUTHORIZED", 3))
        assert record(event("void", "CANCELLED", 4))["transactionId"] is None
        record(event("void", "POSTED", 5), "CONFLICT")
        refund_event = event("refund", "POSTED", 10, kind="REFUND", direction="IN", amount="30.00",
                             originalTransactionId=posted["transactionId"])
        refund = record(refund_event)
        assert record(refund_event) == refund
        record(event("too-big", "POSTED", 11, kind="REFUND", direction="IN", amount="70.01",
                     originalTransactionId=posted["transactionId"]), "CONFLICT")
        record(event("wrong-account", "POSTED", 12, kind="REFUND", direction="IN", accountId=credit,
                     originalTransactionId=posted["transactionId"]), "BAD_INPUT")
        record(event("reverse-rest", "POSTED", 12, kind="REVERSAL", direction="IN", amount="70.00",
                     originalTransactionId=posted["transactionId"]), "CONFLICT")
        record(event("other-purchase", "POSTED", 8, kind="WITHDRAWAL", channel="ATM", amount="10.00"))
        record(event("deposit", "POSTED", 9, kind="DEPOSIT", channel="BANK_API", direction="IN", amount="500.00"))

        parties = {"transferNamespace": source, "transferRef": "own",
                   "debit": {"accountId": debit}, "credit": {"accountId": credit}}
        outgoing = record(event("own-out", "POSTED", 13, kind="TRANSFER", amount="250.00",
                                channel="BANK_API", transfer=parties))
        incoming = record(event("own-in", "POSTED", 13, kind="TRANSFER", amount="250.00",
                                channel="BANK_API", direction="IN", accountId=credit, transfer=parties))
        transfer = "query($id:ID!){operationTransfer(transactionId:$id)}"
        transfer_out = call(transfer, {"id": outgoing["transactionId"]})["operationTransfer"]
        transfer_in = call(transfer, {"id": incoming["transactionId"]})["operationTransfer"]
        assert transfer_out["ownerRelation"] == "SAME_OWNER" and transfer_out["transferId"] == transfer_in["transferId"]

        purchase_detail = call(DETAIL, {"id": posted["transactionId"]})["operationLifecycle"]
        assert purchase_detail["kind"] == "PURCHASE" and purchase_detail["channel"] == "ONLINE"
        assert [item["status"] for item in purchase_detail["events"]] == ["AUTHORIZED", "POSTED"]
        refund_detail = call(DETAIL, {"id": refund["transactionId"]})["operationLifecycle"]
        assert refund_detail["originalTransactionId"] == posted["transactionId"]
        totals = spending()
        assert totals == {"purchases": "100.00", "adjustments": "30.00", "refunds": "30.00", "reversals": "0.00",
                          "ownTransfersOut": "250.00", "ownTransfersIn": "250.00", "unclassifiedOut": "0.00",
                          "netSpending": "70.00", "currency": "BYN"}
        call("mutation{signOut}")
        login("analyst")
        call(DETAIL, {"id": posted["transactionId"]}, error="FORBIDDEN")
        call(SPENDING, {"id": client, "p": window}, error="FORBIDDEN")
        call("mutation{signOut}")
    (output / "operation-lifecycle.json").write_text(json.dumps({
        "client": client, "clientLabel": label, "source": source, "purchase": posted, "refund": refund,
        "purchaseDetail": purchase_detail, "refundDetail": refund_detail, "spending": totals,
        "ownTransfer": transfer_out["transferId"], "window": window,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
