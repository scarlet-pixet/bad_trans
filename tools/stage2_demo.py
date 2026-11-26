"""Rebuild the stage 2 demonstration CSVs against the local Bad Trans instance."""
import argparse
import csv
from datetime import UTC, datetime, timedelta
from getpass import getpass
from hashlib import sha256
import json
from pathlib import Path

import requests


def gql(query, variables=None, token=None):
    with requests.Session() as client:
        client.trust_env = False
        response = client.post("http://127.0.0.1:18100/graphql",
                               json={"query": query, "variables": variables or {}},
                               headers={"Authorization": "Bearer " + token} if token else {},
                               timeout=20)
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["data"]


def signout(token):
    gql("mutation{signOut}", token=token)


def prepare(output):
    output.mkdir(parents=True, exist_ok=True)
    anchor = datetime(2026, 11, 18, 16, tzinfo=UTC)
    prefix = "demo-20261118-v1"
    clients = {}
    token = gql(
        'mutation($p:String!){signIn(login:"manager",password:$p){accessToken}}',
        {"p": getpass("Пароль manager: ")},
    )["signIn"]["accessToken"]
    try:
        for suffix, label in (
            ("stable", "ДЕМО · Стабильный"),
            ("amount", "ДЕМО · Изменение суммы"),
            ("short", "ДЕМО · Короткая история"),
        ):
            external_id = f"{prefix}-{suffix}"
            client = gql(
                "mutation($i:ClientInput!){createClient(input:$i){id externalId}}",
                dict(i=dict(externalId=external_id, displayName=label, segment=prefix,
                            idempotencyKey=external_id)),
                token,
            )["createClient"]
            account = gql(
                "mutation($i:AccountInput!){openAccount(input:$i){id externalId}}",
                dict(i=dict(clientId=client["id"], externalId=external_id, currency="BYN",
                            idempotencyKey=external_id)),
                token,
            )["openAccount"]
            clients[suffix] = dict(id=client["id"], externalId=external_id,
                                   account=account["id"], title=label)
    finally:
        signout(token)
    rows = []
    checks = []
    for suffix, client in clients.items():
        if suffix == "short":
            dates = [anchor - timedelta(days=2), anchor - timedelta(days=1)]
        else:
            dates = [anchor - timedelta(days=day) for day in (86, 75, 64, 53, 42, 35, 30, 26)]
            dates.extend(anchor - timedelta(days=day, hours=hour)
                         for day in range(12, 0, -1) for hour in (4, 3, 2))
        for index, day in enumerate(dates):
            rows.append((prefix, f"{prefix}-{suffix}-h-{index}", client["account"],
                         "100.00", "BYN", "OUT", day.isoformat()))
        amount = "900.00" if suffix == "amount" else "100.00"
        checks.append((prefix, f"{prefix}-{suffix}-check", client["account"],
                       amount, "BYN", "OUT", (anchor - timedelta(hours=1)).isoformat()))
    files = {}
    for name, records in (("history.csv", rows), ("control.csv", checks)):
        path = output / name
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, lineterminator="\n")
            writer.writerow(("source", "externalId", "accountId", "amount", "currency", "direction", "occurredAt"))
            writer.writerows(records)
        fingerprint = sha256(path.read_bytes()).hexdigest()
        files[name] = dict(rows=len(records), sha256=fingerprint, gatewayKey="csv:" + fingerprint)
    manifest = dict(prefix=prefix, clients=clients, files=files, anchor=anchor.isoformat(),
                    period={"from": (anchor - timedelta(days=90)).isoformat(), "to": anchor.isoformat()})
    expectations = dict(
        stable="score below threshold",
        amount="AMOUNT_DEVIATION; score reaches stored threshold",
        short="UNAVAILABLE; score null",
        feedback="one exclusion after confirmation",
    )
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "expectations.json").write_text(json.dumps(expectations, indent=2), encoding="utf-8")
    print("Prepared", len(rows), "+", len(checks), "rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args().output)
