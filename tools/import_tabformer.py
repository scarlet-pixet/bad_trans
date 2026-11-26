"""Import selected, dated IBM TabFormer card activity through the public API."""
import argparse
import base64
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import UTC, date, datetime, time as clock
from decimal import Decimal
from getpass import getpass
from hashlib import sha256
from io import StringIO, TextIOWrapper
import json
import os
from pathlib import Path
import tarfile
import time
import zipfile

import requests


SOURCE = "ibm-tabformer"
FIELDS = ("source", "externalId", "accountId", "amount", "currency", "direction", "occurredAt", "counterpartyId")


@contextmanager
def open_source(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError("Select one transaction CSV archive member")
            with TextIOWrapper(archive.open(names[0]), encoding="utf-8-sig", newline="") as stream:
                yield stream
    elif path.name.endswith((".tgz", ".tar.gz")):
        with tarfile.open(path, "r:gz") as archive:
            member = next((member for member in archive if member.isfile() and member.name.endswith(".csv")), None)
            if member is None:
                raise ValueError("No CSV in the transaction archive")
            with TextIOWrapper(archive.extractfile(member), encoding="utf-8-sig", newline="") as stream:
                yield stream
    else:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            yield stream


def read_transactions(path, users, start, finish, limit):
    output, counts, duplicates = [], Counter(), Counter()
    required = {"User", "Card", "Year", "Month", "Day", "Time", "Amount", "Merchant Name", "Errors?", "Is Fraud?"}
    with open_source(path) as stream:
        reader = csv.DictReader(stream)
        if not required <= set(reader.fieldnames or []):
            raise ValueError("The file does not contain the IBM TabFormer fields")
        for raw in reader:
            user = raw["User"]
            if user not in users:
                continue
            day = date(int(raw["Year"]), int(raw["Month"]), int(raw["Day"]))
            if not start <= day < finish:
                continue
            if raw["Errors?"].strip():
                counts["declined"] += 1
                continue
            amount = Decimal(raw["Amount"].strip().removeprefix("$"))
            if not amount.is_finite() or amount <= 0:
                counts["nonPositive"] += 1
                continue
            stamp = datetime.combine(day, clock.fromisoformat(raw["Time"]), tzinfo=UTC)
            key = (user, raw["Card"], stamp.isoformat(), str(amount), raw["Merchant Name"])
            duplicates[key] += 1
            external = sha256(json.dumps([key, duplicates[key]], separators=(",", ":")).encode()).hexdigest()
            output.append(dict(user=user, card=raw["Card"], merchant=raw["Merchant Name"], amount=format(amount,"f"),
                               occurredAt=stamp.isoformat(), externalId=external, label=raw["Is Fraud?"]))
            if len(output) > limit:
                raise ValueError(f"More than {limit} eligible transactions. Narrow the period or select fewer users.")
    missing = set(users) - {row["user"] for row in output}
    if missing:
        raise ValueError(f"No eligible transactions for selected users: {', '.join(sorted(missing))}")
    return sorted(output, key=lambda row: (row["occurredAt"],row["externalId"])), counts


class Gateway:
    def __init__(self, url, ca):
        self.url = url
        self.session = requests.Session()
        if ca:
            self.session.verify = str(ca)
        self.token = None

    def call(self, query, **variables):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        reply = self.session.post(self.url, json=dict(query=query, variables=variables), headers=headers, timeout=30)
        reply.raise_for_status()
        data = reply.json()
        if data.get("errors"):
            raise RuntimeError(f"GraphQL: {data['errors'][0]['message']}")
        return data["data"]

    def create(self, field, type_name, external, **values):
        result = self.call(f"mutation($i:{type_name}!){{{field}(input:$i){{id}}}}",
                           i=dict(values, externalId=external, idempotencyKey=external))
        return result[field]["id"]


def submit(api, rows, output):
    users, accounts, merchants = {}, {}, {}
    for user, card in sorted({(row["user"],row["card"]) for row in rows}):
        if user not in users:
            users[user] = api.create("createClient","ClientInput",f"TF-U-{user}",
                                     displayName=f"TabFormer · клиент {user}",segment="IBM_TABFORMER")
        accounts[user,card] = api.create("openAccount","AccountInput",f"TF-A-{user}-{card}",
                                        clientId=users[user],currency="USD")
    for merchant in sorted({row["merchant"] for row in rows}):
        merchants[merchant] = api.create("registerCounterparty","CounterpartyInput",f"TF-M-{merchant}",
                                         displayName=f"TabFormer · продавец {merchant}",category="IBM_TABFORMER")
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(dict(source=SOURCE, externalId=row["externalId"], accountId=accounts[row["user"],row["card"]],
                             amount=row["amount"],currency="USD",direction="OUT",occurredAt=row["occurredAt"],
                             counterpartyId=merchants[row["merchant"]]))
    contents = buffer.getvalue().encode()
    (output/"tabformer-import.csv").write_bytes(contents)
    job, file_hash = submit_csv(api, contents)
    return users, accounts, merchants, job, file_hash


def submit_csv(api, contents):
    file_hash = sha256(contents).hexdigest()
    job_id = api.call("mutation($b:String!,$h:String!){startBulkImport(fileBase64:$b,fileHash:$h,idempotencyKey:$h)}",
                      b=base64.b64encode(contents).decode(),h=file_hash)["startBulkImport"]["id"]
    job = api.call("query($id:ID!){bulkImport(id:$id)}",id=job_id)["bulkImport"]
    until = time.monotonic()+180
    while job["status"] in ("QUEUED","RUNNING"):
        if time.monotonic()>until:
            raise RuntimeError(f"Import still running: {job['id']}")
        time.sleep(.75)
        job = api.call("query($id:ID!){bulkImport(id:$id)}",id=job["id"])["bulkImport"]
    if job["status"]!="COMPLETED":
        raise RuntimeError(f"Import {job['id']} has status {job['status']}")
    return job, file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file",type=Path)
    parser.add_argument("--users",nargs="+",default=["0"])
    parser.add_argument("--from",dest="start",type=date.fromisoformat,required=True)
    parser.add_argument("--to",dest="finish",type=date.fromisoformat,required=True)
    parser.add_argument("--limit",type=int,default=3000)
    parser.add_argument("--url",default="http://127.0.0.1:18100/graphql")
    parser.add_argument("--ca",type=Path)
    parser.add_argument("--login",default="manager")
    parser.add_argument("--output",type=Path,default=Path(".local/tabformer-import"))
    args=parser.parse_args()
    if args.start>=args.finish or not 1<=args.limit<=10_000 or not all(x.isdecimal() for x in args.users):
        parser.error("Expected a nonempty period, numeric user IDs and a limit of 1–10000")
    rows, counts = read_transactions(args.file,set(args.users),args.start,args.finish,args.limit)
    args.output.mkdir(parents=True,exist_ok=True)
    api=Gateway(args.url,args.ca)
    password=os.getenv("TABFORMER_GATEWAY_PASSWORD") or getpass("Gateway password: ")
    api.token=api.call("mutation($l:String!,$p:String!){signIn(login:$l,password:$p){accessToken}}",
                       l=args.login,p=password)["signIn"]["accessToken"]
    try:
        users,accounts,merchants,job,file_hash=submit(api,rows,args.output)
    finally:
        api.call("mutation{signOut}")
        api.session.close()
    with (args.output/"tabformer-labels.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow(("externalId","sourceFraudLabel"))
        writer.writerows((row["externalId"],row["label"]) for row in rows)
    report=dict(source="IBM TabFormer",firstDate=rows[0]["occurredAt"],lastDate=rows[-1]["occurredAt"],
                clients=users,accounts=len(accounts),merchants=len(merchants),selected=len(rows),
                skipped=dict(counts),fileHash=file_hash,job=job)
    (args.output/"tabformer-import.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))


if __name__=="__main__":
    main()
