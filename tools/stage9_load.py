"""Measure real TLS GraphQL reads with ten independently authenticated users."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from statistics import median, quantiles
from threading import Barrier
import time

import requests

ROUTES = {
    "clients": 'query{clients(filter:{segment:"stage9-load"},page:{limit:20,sortBy:"displayName"}){items{id externalId} totalCount}}',
    "transactions": 'query{transactions(page:{limit:20,sortBy:"occurredAt",sortDirection:"DESC"}){items{id amount occurredAt} totalCount}}',
    "transaction": 'query($id:ID!){transaction(id:$id){id amount occurredAt}}',
    "behaviorProfilePage": 'query($id:ID!){behaviorProfilePage(clientId:$id,windowDays:30,history:true,limit:20)}',
    "profileVersion": 'query($id:ID!){profileVersion(id:$id)}',
    "resultHistory": 'query($id:ID!){resultHistory(transactionId:$id,limit:20)}',
    "detectionContext": 'query($id:ID!,$r:ID!){detectionContext(transactionId:$id,resultId:$r)}',
    "reviewCases": 'query{reviewCases(page:{limit:20,sortBy:"createdAt",sortDirection:"DESC"}){items{id latestScore} totalCount}}',
    "reviewCase": 'query($id:ID!){reviewCase(id:$id){id latestScore status}}',
    "snapshotReport": 'query($id:ID!){snapshotReport(id:$id)}',
}


def benchmark(fixture, credentials, ca, seconds):
    if len(credentials) != 10 or len({item["login"] for item in credentials}) != 10:
        raise ValueError("Provide ten distinct test accounts")
    url = "https://127.0.0.1:18444/graphql"
    rendezvous = Barrier(len(credentials), timeout=60)
    data = {
        "transaction": {"id": fixture["transactionId"]},
        "behaviorProfilePage": {"id": fixture["clientId"]},
        "profileVersion": {"id": fixture["profileId"]},
        "resultHistory": {"id": fixture["transactionId"]},
        "detectionContext": {"id": fixture["transactionId"], "r": fixture["assessmentId"]},
        "reviewCase": {"id": fixture["caseId"]},
        "snapshotReport": {"id": fixture["reportId"]},
    }
    routes = list(ROUTES)

    def measure_user(index, credential):
        with requests.Session() as session:
            session.trust_env = False
            session.verify = ca

            def execute(query, variables=None):
                response = session.post(url, json={"query": query, "variables": variables or {}}, timeout=15)
                response.raise_for_status()
                body = response.json()
                if body.get("errors"):
                    codes = [
                        error.get("extensions", {}).get("code", "GRAPHQL")
                        for error in body["errors"]
                    ]
                    raise ValueError(",".join(codes))
                return body["data"]

            def read(route):
                value = execute(ROUTES[route], data.get(route))[route]
                if value is None or (isinstance(value, dict) and "items" in value and not value["items"]):
                    raise ValueError("EMPTY_READ")
                if route == "transactions" and value["totalCount"] != 100_000:
                    raise ValueError("UNEXPECTED_DATASET_SIZE")

            token = execute(
                'mutation($l:String!,$p:String!){signIn(login:$l,password:$p){accessToken}}',
                dict(l=credential["login"], p=credential["password"]),
            )["signIn"]["accessToken"]
            session.headers["Authorization"] = "Bearer " + token
            try:
                order = routes[index:] + routes[:index]
                for route in order:
                    read(route)
                rendezvous.wait()
                until = time.monotonic() + seconds
                results = []
                while time.monotonic() < until:
                    for route in order:
                        if time.monotonic() >= until:
                            break
                        started = time.monotonic()
                        error = None
                        try:
                            read(route)
                        except requests.RequestException as exc:
                            error = type(exc).__name__
                        except ValueError as exc:
                            error = str(exc)
                        results.append(dict(route=route, seconds=time.monotonic()-started, error=error))
                        time.sleep(0.05)
                return results
            finally:
                execute("mutation{signOut}")

    with ThreadPoolExecutor(max_workers=len(credentials)) as pool:
        workers = [pool.submit(measure_user, index, account)
                   for index, account in enumerate(credentials)]
        results = [row for worker in workers for row in worker.result()]
    stats = {}
    for route in routes:
        rows = [row for row in results if row["route"] == route]
        valid = [row["seconds"] for row in rows if not row["error"]]
        stats[route] = dict(
            requests=len(rows), errors=len(rows)-len(valid),
            p50Ms=round(median(valid)*1000, 1) if valid else None,
            p95Ms=round(quantiles(valid, n=100, method="inclusive")[94]*1000, 1)
            if len(valid) > 1 else None,
            maxMs=round(max(valid)*1000, 1) if valid else None,
            overTwoSeconds=sum(row["seconds"] > 2 for row in rows),
        )
    return dict(
        userCount=len(credentials), durationSeconds=seconds, warmupRequestsPerUser=len(routes),
        pauseMs=50, transport="HTTPS, validated local CA, internal TLS gRPC", timing="from request to decoding",
        stats=stats, samples=results,
        criterionMet=all(s["requests"] and not s["errors"] and not s["overTwoSeconds"] for s in stats.values()),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("fixture", "accounts", "ca", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=35)
    args = parser.parse_args()
    result = benchmark(json.loads(args.fixture.read_text(encoding="utf-8")),
                       json.loads(args.accounts.read_text(encoding="utf-8")),
                       str(args.ca), args.seconds)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(dict(result=result["criterionMet"], routes=result["stats"]), indent=2))
