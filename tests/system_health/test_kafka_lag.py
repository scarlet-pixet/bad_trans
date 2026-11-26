from concurrent.futures import Future
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from confluent_kafka import TopicPartition
from streamlit.testing.v1 import AppTest

from backend.infrastructure.kafka_lag import LagMonitor, sample


def resolved(value):
    future = Future()
    future.set_result(value)
    return future


class Broker:
    def list_topics(self, *, timeout):
        topic = SimpleNamespace(error=None, partitions={i: object() for i in range(2)})
        return SimpleNamespace(brokers={1: object()}, topics={"test": topic})

    def list_consumer_group_offsets(self, groups, *, request_timeout):
        group = groups[0].group_id
        commits = {
            "ready": [TopicPartition("test", 0, 6), TopicPartition("test", 1, 8)],
            "no-commit": [TopicPartition("test", 0, 6), TopicPartition("test", 1, -1001)],
            "unknown": [],
        }
        return {group: resolved(SimpleNamespace(topic_partitions=commits[group]))}

    def list_offsets(self, offsets, *, request_timeout):
        return {part: resolved(SimpleNamespace(offset=8)) for part in offsets}


def test_missing_commit_never_counts_as_zero_or_completes_the_group():
    value = sample(Broker(), {group: ("test",) for group in ("ready", "no-commit", "unknown")})
    ready, partial, unknown = value["groups"]
    assert ready["lag"] == 2 and ready["partitions"][1]["lag"] == 0
    assert partial["status"] == "PARTIAL" and partial["lag"] is None
    assert partial["knownPartitions"] == 1 and partial["partitions"][1]["lag"] is None
    assert unknown["status"] == "NO_DATA" and unknown["lag"] is None


def test_stale_sample_discards_old_offsets_and_renders_unknown_measurement():
    monitor = LagMonitor(None)
    with patch("backend.infrastructure.kafka_lag.time.monotonic", return_value=100):
        monitor.last = (70, "2026-11-18T08:00:00+00:00",
                        dict(status="FRESH", brokerReachable=True, groups=[{"lag": 300}]))
        snapshot = monitor.snapshot()
    assert snapshot["status"] == "STALE" and snapshot["groups"] == []
    app = AppTest.from_string("from frontend.system_health import render_kafka_lag\n"
                              f"render_kafka_lag({snapshot!r})").run()
    assert not app.exception and not app.dataframe
    assert any("устарело" in item.value for item in app.warning)


def test_failed_native_worker_does_not_stop_subsequent_sampling():
    monitor = LagMonitor("isolated-broker")
    healthy = SimpleNamespace(stdout=b'{"status":"FRESH","brokerReachable":true,"groups":[]}')
    statuses = []

    def after_iteration(seconds):
        statuses.append(monitor.snapshot()["status"])
        if len(statuses) == 2:
            monitor.stop_event.set()

    with patch("backend.infrastructure.kafka_lag.subprocess.run",
               side_effect=[subprocess.CalledProcessError(139, "poller"), healthy]), patch.object(
                   monitor.stop_event, "wait", side_effect=after_iteration
               ):
        monitor.run()
    assert statuses == ["NO_DATA", "FRESH"]
