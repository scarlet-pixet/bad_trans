"""Broker observations run outside synchronous owner health RPCs."""

from datetime import datetime, UTC
import os
import time
from confluent_kafka.admin import AdminClient

_observation = (None, None, 0.0)


def observation():
    ready, measured, monotonic = _observation
    return (ready if time.monotonic() - monotonic <= 20 else None), measured


def loop():
    global _observation
    client = None
    while True:
        try:
            if client is None:
                client = AdminClient(
                    {"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP"]}
                )
            ready = bool(client.list_topics(timeout=0.5).brokers)
        except Exception:
            ready = False
        if not ready:
            client = None
        _observation = (ready, datetime.now(UTC).isoformat(), time.monotonic())
        time.sleep(2)
