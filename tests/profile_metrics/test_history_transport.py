import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend.common.behavior_contract import digest
from backend.common.data import DomainError
from backend.infrastructure import clients
from backend.services.analysis.profiles.storage import read_snapshot_history


@pytest.mark.parametrize("corrupt", [False, True])
def test_history_pages_share_channel_and_close_after_validation(monkeypatch, corrupt):
    timestamp = "2026-11-25T10:00:00+00:00"
    facts = [
        {
            "id": f"transaction-{index}", "clientId": "client", "accountId": "account", "counterpartyId": None,
            "currency": "BYN", "direction": "OUT", "amount": "100.00", "occurredAt": timestamp,
            "ingestedAt": timestamp,
        }
        for index in (1, 2)
    ]
    shared = {"snapshotId": "snapshot", "digest": digest(facts), "itemCount": 2}
    snapshot = {
        **shared, "capturedAt": timestamp,
        "coverage": {"mode": "UNKNOWN", "revision": "unknown", "intervals": [], "declaredBySource": None},
        "selection": {
            "requestKey": "test", "clientId": "client", "currency": "BYN", "direction": "OUT",
            "windowStart": timestamp, "asOf": timestamp, "ingestedBefore": timestamp,
        },
    }
    progress = []

    @contextmanager
    def channel(address, owner):
        assert (address, owner) == ("transaction:50051", "transaction")
        progress.append("opened")
        try:
            yield "test channel"
        finally:
            progress.append("closed")

    def stub(transport):
        assert transport == "test channel"

        def invoke(request, **_):
            assert request.method == "get_behavior_history_page"
            token = json.loads(request.arguments_json)["parameters"]["pageToken"]
            progress.append(token)
            index = int(token is not None)
            page = {**shared, "items": [facts[index]], "nextPageToken": None if token else "page-2"}
            if token and corrupt:
                page["digest"] = "0" * 64
            return SimpleNamespace(error_code="", result_json=json.dumps(page).encode())

        return SimpleNamespace(Invoke=invoke)

    monkeypatch.setenv("TRANSACTION_GRPC", "transaction:50051")
    monkeypatch.setenv("SERVICE_TOKEN", "test-token")
    monkeypatch.setattr(clients, "channel_for", channel)
    monkeypatch.setattr(clients.rpc, "BusinessServiceStub", stub)
    client = clients.EventTransactionClient()
    if corrupt:
        with pytest.raises(DomainError, match="Inconsistent history snapshot page"):
            read_snapshot_history(client, snapshot)
    else:
        assert read_snapshot_history(client, snapshot) == facts
    assert progress == ["opened", None, "page-2", "closed"]
