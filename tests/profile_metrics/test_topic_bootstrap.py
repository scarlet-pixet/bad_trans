import importlib

import pytest


def test_topic_bootstrap_retries_broker_outage_without_stopping_service(monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "transaction")
    monkeypatch.setenv("SERVICE_TOKEN", "test-only")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused:unused@127.0.0.1/unused")
    module = importlib.import_module("backend.infrastructure.server")
    calls = []

    def topics():
        calls.append("attempt")
        if len(calls) == 1:
            raise RuntimeError("broker down")

    class Stop(BaseException):
        pass

    def sleep(seconds):
        if len(calls) == 2:
            raise Stop()

    monkeypatch.setattr(module, "topics", topics)
    import time

    monkeypatch.setattr(time, "sleep", sleep)
    with pytest.raises(Stop):
        module.topics_loop()
    assert calls == ["attempt", "attempt"]
