"""Periodic, read-only observation of the application's Kafka consumer offsets."""

from datetime import UTC, datetime
import json
import os
import subprocess
import sys
from threading import Event, Thread
import time

from confluent_kafka import ConsumerGroupTopicPartitions, KafkaException, TopicPartition
from confluent_kafka.admin import AdminClient, OffsetSpec

from backend.infrastructure.delivery import WORKER_TOPICS, consumer_group_id

REQUEST_TIMEOUT = 1.5
MAX_AGE_SECONDS = 20
POLL_INTERVAL_SECONDS = 3
PROCESS_TIMEOUT = 7


def futures_result(future, deadline):
    try:
        return future.result(timeout=max(0, deadline - time.monotonic()))
    except (KafkaException, TimeoutError):
        return None


def sample(client, groups):
    """Read commits first, then the log ends. Neither call joins a consumer group."""
    try:
        metadata = client.list_topics(timeout=REQUEST_TIMEOUT)
    except KafkaException:
        return dict(brokerReachable=False, status="NO_DATA", groups=[])
    if not metadata.brokers:
        return dict(brokerReachable=False, status="NO_DATA", groups=[])
    topics = {
        name: sorted(topic.partitions)
        for name, topic in metadata.topics.items()
        if not topic.error
    }
    parts = {
        TopicPartition(topic, number)
        for names in groups.values() for topic in names for number in topics.get(topic, [])
    }
    commits = {
        group: client.list_consumer_group_offsets(
            [ConsumerGroupTopicPartitions(group)], request_timeout=REQUEST_TIMEOUT,
        )[group]
        for group in groups
    }
    deadline = time.monotonic() + REQUEST_TIMEOUT
    offsets = {group: futures_result(future, deadline) for group, future in commits.items()}
    ends = client.list_offsets(
        {part: OffsetSpec.latest() for part in parts}, request_timeout=REQUEST_TIMEOUT,
    ) if parts else {}
    deadline = time.monotonic() + REQUEST_TIMEOUT
    high = {}
    for part, future in ends.items():
        result = futures_result(future, deadline)
        high[part.topic, part.partition] = result.offset if result and result.offset >= 0 else None

    groups_result = []
    for name, names in groups.items():
        offsets_for_group = offsets[name]
        committed = {
            (part.topic, part.partition): part.offset
            for part in offsets_for_group.topic_partitions or []
            if not part.error and part.offset >= 0
        } if offsets_for_group is not None else {}
        missing = [topic for topic in names if not topics.get(topic)]
        partitions = []
        for topic in names:
            for number in topics.get(topic, []):
                end, commit = high.get((topic, number)), committed.get((topic, number))
                lag = end - commit if end is not None and commit is not None and end >= commit else None
                partitions.append(dict(topic=topic, partition=number, logEndOffset=end,
                                       committedOffset=commit, lag=lag))
        known = [row["lag"] for row in partitions if row["lag"] is not None]
        complete = bool(partitions) and not missing and len(known) == len(partitions)
        groups_result.append(dict(
            group=name, topics=list(names), missingTopics=missing, partitions=partitions,
            knownPartitions=len(known), totalPartitions=len(partitions),
            lag=sum(known) if complete else None,
            status="COMPLETE" if complete else "PARTIAL" if known else "NO_DATA",
        ))
    return dict(brokerReachable=True, status="FRESH", groups=groups_result)


def worker_groups():
    return {consumer_group_id(service): topics for service, topics in WORKER_TOPICS.items()}


class LagMonitor:
    def __init__(self, bootstrap):
        self.bootstrap = bootstrap
        self.stop_event = Event()
        self.thread = None
        self.last = None

    def start(self):
        if self.bootstrap:
            self.thread = Thread(target=self.run, name="kafka-lag", daemon=True)
            self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=PROCESS_TIMEOUT + 1)

    def run(self):
        while not self.stop_event.is_set():
            try:
                # Keep native Kafka failures during reconnection outside the HTTP process.
                result = subprocess.run(
                    [sys.executable, "-m", "backend.infrastructure.kafka_lag"],
                    env={**os.environ, "KAFKA_BOOTSTRAP": self.bootstrap},
                    timeout=PROCESS_TIMEOUT, capture_output=True, check=True,
                )
                value = json.loads(result.stdout)
            except (OSError, subprocess.SubprocessError, ValueError):
                value = dict(brokerReachable=None, status="NO_DATA", groups=[])
            self.last = time.monotonic(), datetime.now(UTC).isoformat(), value
            self.stop_event.wait(POLL_INTERVAL_SECONDS)

    def snapshot(self):
        last = self.last
        if last is None:
            return dict(status="NO_DATA", brokerReachable=None, measuredAt=None,
                        groups=[], maxAgeSeconds=MAX_AGE_SECONDS)
        sampled, timestamp, value = last
        age = time.monotonic() - sampled
        if age > MAX_AGE_SECONDS:
            value = dict(status="STALE", brokerReachable=None, groups=[])
        return dict(value, measuredAt=timestamp, ageSeconds=round(age, 2),
                    maxAgeSeconds=MAX_AGE_SECONDS)


if __name__ == "__main__":
    client = AdminClient({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP"]})
    print(json.dumps(sample(client, worker_groups())), flush=True)
