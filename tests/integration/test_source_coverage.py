"""Source declarations, frozen coverage and date-only input on the isolated product."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import requests

REGISTER = "mutation($v:TransactionInput!){registerTransaction(input:$v){id timePrecision}}"
DECLARE = "mutation($v:SourceCoverageInput!){declareSourceCoverage(input:$v)}"
READ = "query($id:ID!){detectionContext(transactionId:$id)}"


def test_date_only_and_missing_account_coverage():
    gateway = os.environ["GRAPHQL_URL"]
    output = Path(os.environ["BAD_TRANS_EVIDENCE_DIR"])
    with requests.Session() as session:
        session.trust_env = False

        def call(query, values=None, error=None):
            response = session.post(gateway, json={"query": query, "variables": values or {}}, timeout=25)
            body = response.json()
            if error:
                code = body["errors"][0]["extensions"]["code"] if "errors" in body else body["detail"]["code"]
                assert code == error, body
                return {}
            assert response.ok and not body.get("errors"), body
            return body["data"]

        def login(name):
            session.headers.pop("Authorization", None)
            payload = call('mutation($n:String!){signIn(login:$n,password:"BadTrans-demo-2026!"){accessToken}}', {"n": name})
            session.headers["Authorization"] = "Bearer " + payload["signIn"]["accessToken"]

        login("manager")
        prefix = "source-stage-" + uuid4().hex[:8]
        anchor = (datetime.now(UTC)-timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        client = call("mutation($v:ClientInput!){createClient(input:$v){id}}", {"v": {
            "externalId": prefix, "displayName": "Контроль покрытия "+prefix, "idempotencyKey": prefix,
        }})["createClient"]["id"]

        def account(name):
            return call("mutation($v:AccountInput!){openAccount(input:$v){id}}", {"v": {
                "externalId": prefix+name, "currency": "BYN", "idempotencyKey": prefix+name, "clientId": client,
            }})["openAccount"]["id"]

        first, second = account("-first"), account("-second")
        known = {"from": (anchor-timedelta(days=100)).isoformat(), "to": (anchor+timedelta(days=3)).isoformat()}

        def declaration(account_id, feature, revision="initial", intervals=None):
            return {"v": {"accountId": account_id, "source": prefix, "channel": "UNKNOWN",
                          "direction": "OUT", "feature": feature, "revision": revision,
                          "intervals": [known] if intervals is None else intervals}}

        call(DECLARE, declaration(first, "DAY"), error="FORBIDDEN")
        call("mutation{signOut}")
        login("admin")
        reserved = declaration(first, "HOUR")
        reserved["v"]["source"] = "banksim"
        call(DECLARE, reserved, error="BAD_INPUT")
        for feature in ("DAY","HOUR"):
            values = declaration(first, feature)
            assert call(DECLARE, values) == call(DECLARE, values)
            call(DECLARE, declaration(first, feature, intervals=[]), error="CONFLICT")
        call("mutation{signOut}")
        login("manager")

        def register(label, at, amount, precision=None):
            values = {"source": prefix, "externalId": label, "accountId": first,
                      "amount": amount, "currency": "BYN", "direction": "OUT",
                      "occurredAt": at.isoformat(), "idempotencyKey": prefix+label}
            if precision:
                values["timePrecision"] = precision
            return call(REGISTER, {"v": values})["registerTransaction"]

        for ago in range(1, 11):
            for hour in (10, 12, 14):
                register(f"history-{ago}-{hour}", anchor-timedelta(days=ago)+timedelta(hours=hour), "100.00")
        date = register("date-only", anchor, "900.00", "DATE")
        assert date["timePrecision"] == "DATE"

        def result(identifier):
            deadline = monotonic()+150
            while monotonic()<deadline:
                row = call(READ, {"id": identifier})["detectionContext"]
                if row:
                    return row
                sleep(.8)
            raise AssertionError("Analysis did not save the result")

        original = result(date["id"])
        assert original["assessmentScore"] == 40
        checks = {c["code"]: c for c in original["checks"]}
        for key in ("UNUSUAL_TIME","PROFILE_FREQUENCY"):
            assert checks[key]["status"] == "INSUFFICIENT_DATA"
            assert checks[key]["dataReasons"] == ["SUBJECT_TIME_UNKNOWN"]
        assert not original["baseline"]["observation"]["fullWindowCovered"]
        assert original["baseline"]["observation"]["gaps"]["accountsWithoutScope"] == 1

        call("mutation{signOut}")
        login("admin")
        call(DECLARE, declaration(second, "DAY"))
        # Daily coverage is confirmed for both accounts while the second
        # account's hourly coverage is still unknown.
        call("mutation{signOut}")
        login("manager")
        exact = register("exact-after-daily-coverage", anchor+timedelta(days=1,hours=12), "900.00", "EXACT")
        updated = result(exact["id"])
        coverage = updated["baseline"]["observation"]
        assert coverage["fullWindowCovered"] and coverage["gaps"]["hour"] == 1
        assert coverage["unknownTimeCount"] == 1 and updated["baseline"]["hourCountsUtc"]["0"] == 0
        assert call(READ, {"id": date["id"]})["detectionContext"]["baseline"]["observation"] == original["baseline"]["observation"]
        entry = call("mutation($v:OperationEventInput!){recordOperationEvent(input:$v)}", {"v": {
            "source": prefix, "eventRef": "lifecycle-date", "operationRef": "lifecycle-date",
            "accountId": first, "currency": "BYN", "direction": "OUT", "amount": "100.00",
            "kind": "PURCHASE", "channel": "UNKNOWN", "status": "POSTED", "timePrecision": "DATE",
            "occurredAt": (anchor+timedelta(days=2)).isoformat(),
        }})["recordOperationEvent"]["transactionId"]
        lifecycle = call("query($id:ID!){operationLifecycle(transactionId:$id)}",{"id":entry})["operationLifecycle"]
        assert lifecycle["events"][0]["timePrecision"] == "DATE"
        call("mutation{signOut}")
    (output/"source-coverage.json").write_text(json.dumps({
        "source":prefix, "subject":date, "before":original, "after":updated, "second":second,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
