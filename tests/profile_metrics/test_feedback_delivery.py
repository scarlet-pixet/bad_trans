import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.infrastructure import delivery
from tests.profile_storage.test_feedback import event


class Stop(BaseException):
    pass


def module_for(e):
    calls = []
    raw = json.dumps(e).encode()

    class Handler:
        def __init__(self, kind):
            self.kind = kind

        def process(self, c, event):
            calls.append(self.kind)

    class Cursor:
        def __init__(self, value=None):
            self.value = value

        def fetchone(self):
            return self.value

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def transaction(self):
            return nullcontext()

        def execute(self, sql, args=None):
            if "FROM event_failures" in sql:
                return Cursor(dict(id="failure", topic="case.feedback.v1", payload=raw))
            return Cursor()

    def stop(_):
        raise Stop()

    m = SimpleNamespace(
        SERVICE="analysis",
        ROOT=Path(__file__).resolve().parents[2],
        AnalysisEventService=lambda _: Handler("transaction"),
        FeedbackEventService=lambda: Handler("feedback"),
        EventTransactionClient=lambda: None,
        connect=Connection,
        log=lambda *a, **k: None,
        time=SimpleNamespace(sleep=stop),
        os=SimpleNamespace(environ={"KAFKA_BOOTSTRAP": "unused"}),
    )
    m.__name__ = "backend.infrastructure.feedback_test"
    return m, calls


def test_retry_dispatches_saved_topic_instead_of_primary_transaction():
    m, calls = module_for(event())
    with pytest.raises(Stop):
        delivery.retry_loop(m)
    assert calls == ["feedback"]


def test_consumer_subscribes_both_and_dispatches_message_topic():
    e = event()
    m, calls = module_for(e)

    class Message:
        def value(self):
            return json.dumps(e).encode()

        def topic(self):
            return "case.feedback.v1"

        def offset(self):
            return 1

        def partition(self):
            return 0

        def error(self):
            return None

    class Consumer:
        count = 0

        def subscribe(self, topics):
            assert set(topics) == {"transaction.registered.v1", "case.feedback.v1"}

        def poll(self, *a):
            self.count += 1
            if self.count > 1:
                raise Stop()
            return Message()

        def commit(self, **kw):
            pass

    m.Consumer = lambda _: Consumer()
    with pytest.raises(Stop):
        delivery.consume_loop(m)
    assert calls == ["feedback"]


def test_old_single_route_adapter_remains_supported():
    m, _ = module_for(event())
    del m.FeedbackEventService
    assert list(delivery.routes(m)) == ["transaction.registered.v1"]
