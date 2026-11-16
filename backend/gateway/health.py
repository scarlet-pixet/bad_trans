from datetime import datetime, UTC
from concurrent.futures import ThreadPoolExecutor
import os
import requests
import redis
from backend.infrastructure.clients import RemoteClient


def collect_health(query_count, mutation_count):
    def owner(name):
        try:
            data = RemoteClient(
                name, {"id": "system", "login": "health", "role": "SYSTEM"}
            )._invoke("health", {})
            return dict(
                service=name,
                **data,
                status="READY" if data.get("ready") else "UNAVAILABLE",
                processAlive=True,
            )
        except Exception:
            return dict(
                service=name, status="UNAVAILABLE", processAlive=None, ready=False
            )

    with ThreadPoolExecutor(max_workers=3) as pool:
        services = list(pool.map(owner, ("transaction", "analysis", "case")))
    try:
        response = requests.get(os.environ["AUTH_URL"] + "/health", timeout=4)
        value = response.json()
        auth = {k: value.get(k) for k in ("ready", "redisReady", "database")}
        auth["processAlive"] = True
    except (requests.RequestException, ValueError, AttributeError):
        auth = dict(ready=False, redisReady=None, processAlive=None)
    try:
        with redis.Redis.from_url(
            os.environ["CACHE_URL"], socket_timeout=0.15, socket_connect_timeout=0.15
        ) as client:
            cache = dict(
                ready=bool(client.ping()),
                mode="enabled",
                entries=client.dbsize(),
                ttlSeconds=30,
            )
    except redis.RedisError:
        cache = dict(ready=False, mode="bypass")
    sync = bool(auth.get("ready")) and all(s.get("ready") for s in services)
    asynchronous = all(s.get("asynchronousReady") is True for s in services)
    return dict(
        application="bad_trans",
        transport="gRPC + Kafka",
        queryCount=query_count,
        mutationCount=mutation_count,
        services=services,
        auth=auth,
        cache=cache,
        gateway=dict(processAlive=True),
        measuredAt=datetime.now(UTC).isoformat(),
        synchronousReady=sync,
        asynchronousReady=asynchronous,
        allReady=sync and asynchronous,
        degraded=not cache["ready"],
        freshness="point-in-time observations; no global atomic snapshot",
    )
