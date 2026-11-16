"""Transport regressions without database or broker dependencies."""
import importlib
from types import SimpleNamespace

import grpc
import psycopg
import pytest

from backend.generated import bank_pb2 as bank
from backend.common.data import DomainError


class Aborted(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail


class Context:
    def __init__(self):
        self.status = None

    def invocation_metadata(self):
        return (("x-service-token", "test-internal-token"),('x-actor-role','SYSTEM'))

    def code(self):
        return self.status

    def abort(self, status, detail):
        self.status = status
        raise Aborted(status, detail)


@pytest.mark.parametrize('method',['GetTransaction','GetHistory'])
def test_internal_token_without_principal_cannot_read_history(server,method):
    class MissingPrincipal(Context):
        def invocation_metadata(self):
            return (('x-service-token','test-internal-token'),)
    request = bank.GetTransactionRequest(id='missing') if method=='GetTransaction' else bank.GetHistoryRequest()
    with pytest.raises(Aborted) as error:
        getattr(server.TypedTransaction(),method)(request,MissingPrincipal())
    assert error.value.status == grpc.StatusCode.PERMISSION_DENIED


@pytest.fixture(params=(7,))
def server(request, monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "transaction")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@localhost/unused")
    monkeypatch.setenv("SERVICE_TOKEN", "test-internal-token")
    module = importlib.import_module("backend.infrastructure.server")
    monkeypatch.setattr(module, "TOKEN", "test-internal-token")
    return module


def test_get_transaction_maps_dependency_to_unavailable(server):
    def fail(_):
        raise DomainError("DEPENDENCY_UNAVAILABLE", "Database is unavailable")

    handler = server.TypedTransaction()
    handler.service = SimpleNamespace(get_transaction=fail)
    with pytest.raises(Aborted) as error:
        handler.GetTransaction(bank.GetTransactionRequest(id="missing"), Context())
    assert error.value.status == grpc.StatusCode.UNAVAILABLE


def test_register_transaction_maps_database_outage(server):
    def fail(*_):
        raise psycopg.OperationalError("private connection diagnostic")

    handler = server.TypedTransaction()
    handler.service = SimpleNamespace(register_transaction=fail)
    with pytest.raises(Aborted) as error:
        handler.RegisterTransaction(bank.RegisterTransactionRequest(), Context())
    assert error.value.status == grpc.StatusCode.UNAVAILABLE
    assert "private" not in error.value.detail


def test_history_rejects_invalid_cursor_as_bad_input(server):
    with pytest.raises(Aborted) as error:
        server.TypedTransaction().GetHistory(bank.GetHistoryRequest(page_token="invalid"), Context())
    assert error.value.status == grpc.StatusCode.INVALID_ARGUMENT


def test_gateway_maps_internal_storage_error(monkeypatch):
    from backend.infrastructure import clients as network

    class RpcFailure(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.INTERNAL

        def details(self):
            return "Ошибка хранения данных"

    class Channel:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    def fail(*_, **__):
        raise RpcFailure()

    monkeypatch.setenv("SERVICE_TOKEN", "test-internal-token")
    monkeypatch.setattr(network, "channel_for", lambda *_: Channel())
    monkeypatch.setattr(network.bank_rpc, "TransactionServiceStub", lambda _: SimpleNamespace(SearchClients=fail))
    client = network.RemoteClient("transaction", {"id": "test", "login": "test", "role": "MANAGER"}, "unused")
    with pytest.raises(DomainError) as error:
        client.search_clients()
    assert error.value.code == "DATA_ACCESS_ERROR"


def test_retry_does_not_replay_unkeyed_mutation(monkeypatch):
    from backend.infrastructure.resilience import Details, Retry

    monkeypatch.setenv("RPC_RETRY_ATTEMPTS", "3")
    calls = []

    def unavailable(*_):
        calls.append(True)
        return SimpleNamespace(code=lambda: grpc.StatusCode.UNAVAILABLE)

    details = Details("/bank.v1.TransactionService/CreateClient", 8, [], None, False, None)
    Retry("transaction").intercept_unary_unary(unavailable, details, bank.CreateClientRequest())
    assert len(calls) == 1
