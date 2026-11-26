"""Load a reproducible cohort from the published BankSim payments file."""
import argparse
from collections import Counter
import csv
from datetime import date, timedelta
from decimal import Decimal
from getpass import getpass
from hashlib import file_digest
from io import StringIO, TextIOWrapper
import json
import os
from pathlib import Path
from zipfile import ZipFile

from tools.import_tabformer import FIELDS, Gateway, submit_csv

SOURCE = "banksim"
MEMBER = "bs140513_032310.csv"
DEFAULT_START = date(2026, 5, 22)


def read_rows(archive):
    with TextIOWrapper(archive.open(MEMBER), encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, quotechar="'")
        if not {"step", "customer", "merchant", "amount"} <= set(reader.fieldnames or []):
            raise ValueError("Unexpected BankSim CSV header")
        for number, row in enumerate(reader, start=1):
            amount = Decimal(row["amount"])
            if amount.is_finite() and amount > 0:
                yield number, int(row["step"]), row["customer"], row["merchant"], amount


def select_cohort(archive, size):
    totals, late = Counter(), Counter()
    first_days, merchants = {}, {}
    last_day = 0
    for _, day, customer, merchant, _ in read_rows(archive):
        if day < 0:
            raise ValueError("Negative BankSim day")
        totals[customer] += 1
        first_days[customer] = min(day, first_days.get(customer, day))
        merchants.setdefault(customer, set()).add(merchant)
        if day >= 90:
            late[customer] += 1
        last_day = max(last_day, day)
    candidates = [
        customer for customer, total in totals.items()
        if 75 <= total <= 125 and late[customer] >= 30
        and first_days[customer] <= 30 and len(merchants[customer]) >= 3
    ]
    cohort = sorted(candidates, key=lambda customer: (abs(totals[customer] - 100), customer))[:size]
    if len(cohort) != size:
        raise ValueError(f"Only {len(cohort)} customers match the cohort criteria")
    keys = set(cohort)
    rows = sorted((row for row in read_rows(archive) if row[2] in keys), key=lambda row: (row[1], row[0]))
    return rows, last_day


def build_payload(api, rows, start):
    clients, accounts, merchants = {}, {}, {}
    for customer in sorted({row[2] for row in rows}):
        clients[customer] = api.create(
            "createClient", "ClientInput", f"BS-U-{customer}",
            displayName=f"BankSim · клиент {customer}", segment="BANKSIM",
        )
        accounts[customer] = api.create(
            "openAccount", "AccountInput", f"BS-A-{customer}",
            clientId=clients[customer], currency="EUR",
        )
    for merchant in sorted({row[3] for row in rows}):
        merchants[merchant] = api.create(
            "registerCounterparty", "CounterpartyInput", f"BS-M-{merchant}",
            displayName=f"BankSim · продавец {merchant}", category="BANKSIM",
        )
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    for number, day, customer, merchant, amount in rows:
        stamp = start + timedelta(days=day)
        writer.writerow(dict(
            source=SOURCE, externalId=f"BS-{number}", accountId=accounts[customer],
            amount=format(amount, "f"), currency="EUR", direction="OUT",
            occurredAt=stamp.isoformat(), counterpartyId=merchants[merchant],
        ))
    return buffer.getvalue().encode(), clients, len(merchants)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--clients", type=int, default=10)
    parser.add_argument("--url", default="http://127.0.0.1:18100/graphql")
    parser.add_argument("--ca", type=Path)
    parser.add_argument("--login", default="manager")
    parser.add_argument("--output", type=Path, default=Path(".local/coursework-banksim"))
    args = parser.parse_args()
    if not 1 <= args.clients <= 80:
        parser.error("--clients must be in 1–80 (at most 10,000 transaction rows)")
    with ZipFile(args.archive) as archive:
        rows, last_day = select_cohort(archive, args.clients)
    with args.archive.open("rb") as stream:
        input_hash = file_digest(stream, "sha256").hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    api = Gateway(args.url, args.ca)
    password = os.environ.get("BANKSIM_GATEWAY_PASSWORD") or getpass("Gateway password: ")
    try:
        api.token = api.call(
            "mutation($l:String!,$p:String!){signIn(login:$l,password:$p){accessToken}}",
            l=args.login, p=password,
        )["signIn"]["accessToken"]
        contents, client_ids, merchant_count = build_payload(api, rows, DEFAULT_START)
        (args.output / "banksim-import.csv").write_bytes(contents)
        job, file_hash = submit_csv(api, contents)
    finally:
        try:
            if api.token:
                api.call("mutation{signOut}")
        finally:
            api.session.close()
    report = {
        "dataset": "BankSim",
        "archiveSha256": input_hash,
        "syntheticDateOfStepZero": DEFAULT_START.isoformat(),
        "sourceLastDay": last_day,
        "selected": len(rows),
        "byCustomer": dict(Counter(row[2] for row in rows)),
        "clients": client_ids,
        "merchants": merchant_count,
        "fileHash": file_hash,
        "job": job,
    }
    (args.output / "banksim-import.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"BankSim job {job['id']}: {job['added']} added, {job['duplicates']} duplicates, {job['failed']} errors")


if __name__ == "__main__":
    main()
