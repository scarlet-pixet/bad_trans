"""Three ownership outcomes on an isolated GraphQL/gRPC/PostgreSQL stand."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import requests

REGISTER = "mutation($i:RegisterTransferInput!){registerTransfer(input:$i){id}}"
READ = "query($id:ID!){operationTransfer(transactionId:$id)}"


def test_transfer_owners_and_linked_entries():
    api = os.environ["GRAPHQL_URL"]
    evidence = Path(os.environ["BAD_TRANS_EVIDENCE_DIR"])
    with requests.Session() as session:
        session.trust_env = False

        def request(query, variables=None, expected=None):
            response = session.post(api, json={"query": query, "variables": variables or {}}, timeout=25)
            body = response.json()
            if expected:
                code = body["errors"][0]["extensions"]["code"] if "errors" in body else body["detail"]["code"]
                assert code == expected
                return {}
            assert response.ok and not body.get("errors"), body
            return body["data"]

        def login(login_name):
            session.headers.pop("Authorization", None)
            data = request('mutation($l:String!){signIn(login:$l,password:"BadTrans-demo-2026!"){accessToken}}',
                           {"l": login_name})
            session.headers["Authorization"] = "Bearer " + data["signIn"]["accessToken"]

        login("manager")
        prefix = "transfer-test-" + uuid4().hex[:8]
        at = (datetime.now(UTC) - timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()

        def create(field, kind, key, **values):
            return request(f"mutation($v:{kind}!){{{field}(input:$v){{id}}}}",
                           {"v": {"idempotencyKey": key, "externalId": key, **values}})[field]["id"]

        first = create("createClient", "ClientInput", prefix+"-one", displayName="Одинаковое имя")
        second = create("createClient", "ClientInput", prefix+"-two", displayName="Одинаковое имя")
        left = create("openAccount", "AccountInput", prefix+"-a", clientId=first, currency="BYN")
        own = create("openAccount", "AccountInput", prefix+"-b", clientId=first, currency="BYN")
        other = create("openAccount", "AccountInput", prefix+"-c", clientId=second, currency="BYN")

        def input_for(label, credit, account=left, direction="OUT"):
            reference = prefix+"-"+label
            return {
                "transferNamespace": "synthetic-bank",
                "transferRef": reference,
                "debit": {"accountId": left},
                "credit": credit,
                "transaction": {"source": prefix, "externalId": label+"-"+direction, "accountId": account,
                                "amount": "100.00", "currency": "BYN", "direction": direction,
                                "occurredAt": at, "idempotencyKey": reference+"-"+direction},
            }

        def register(input, error=None):
            data = request(REGISTER, {"i": input}, expected=error)
            return data["registerTransfer"]["id"] if data else None

        def read(identifier):
            return request(READ, {"id": identifier})["operationTransfer"]

        records = {}
        for label, party, relation in (
            ("own", {"accountId": own}, "SAME_OWNER"),
            ("another", {"accountId": other}, "DIFFERENT_OWNERS"),
            ("outside", {"externalAccountId": "external-0007", "externalNamespace": "test-clearing"}, "UNKNOWN"),
        ):
            payload = input_for(label, party)
            identifier = register(payload)
            assert register(payload) == identifier
            value = read(identifier)
            assert value["ownerRelation"] == relation
            assert value["ownerEvidence"] == (None if relation == "UNKNOWN" else "BANK_ACCOUNT_DIRECTORY")
            records[label] = {"transactionId": identifier, **value}

        opposite = register(input_for("own", {"accountId": own}, account=own, direction="IN"))
        assert opposite != records["own"]["transactionId"]
        assert read(opposite)["transferId"] == records["own"]["transferId"]
        wrong_side = input_for("wrong-side", {"accountId": other}, account=own)
        register(wrong_side, "BAD_INPUT")
        conflicting = input_for("own", {"accountId": other}, account=other, direction="IN")
        conflicting["transaction"]["idempotencyKey"] += "-conflict"
        conflicting["transaction"]["externalId"] += "-conflict"
        register(conflicting, "CONFLICT")
        same_leg = input_for("own", {"accountId": own})
        same_leg["transaction"]["idempotencyKey"] += "-new"
        same_leg["transaction"]["externalId"] += "-new"
        register(same_leg, "CONFLICT")
        request("mutation{signOut}")
        login("analyst")
        request(READ, {"id": records["own"]["transactionId"]}, expected="FORBIDDEN")
        request("mutation{signOut}")
    (evidence/"transfer-parties.json").write_text(
        json.dumps({"payments": records, "oppositeTransactionId": opposite, "at": at, "amount": "100.00",
                    "roles": {"manager": "read and create", "analystWithoutCase": "forbidden"}}, indent=2),
        encoding="utf-8",
    )
