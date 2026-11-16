"""Actual gRPC clients for all LR3-LR5 business ports."""
import json
import os
from datetime import datetime
from types import SimpleNamespace

import grpc
from .resilience import channel_for, export_timeout
from google.protobuf.json_format import MessageToDict, ParseDict

from backend.generated import bank_pb2 as bank, bank_pb2_grpc as bank_rpc
from backend.generated import transport_pb2 as pb, transport_pb2_grpc as rpc
from backend.common.data import DomainError
from backend.infrastructure.transport import client_error

TYPED = {
    "transaction": {
        "import_transactions": ("ImportTransactions", "ImportTransactionsRequest"),
        "search_clients": ("SearchClients", "SearchClientsRequest"),
        "search_transactions": ("SearchTransactions", "SearchTransactionsRequest"),
        "get_transaction": ("GetTransaction", "GetTransactionRequest"),
        "create_client": ("CreateClient", "CreateClientRequest"),
        "open_account": ("OpenAccount", "OpenAccountRequest"),
        "register_counterparty": ("RegisterCounterparty", "RegisterCounterpartyRequest"),
    },
    "analysis": {
        "build_profile": ("StartProfile", "StartProfileRequest"),
        "analyze_transaction": ("StartAnalysis", "StartAnalysisRequest"),
        "publish_rule_version": ("PublishRuleVersion", "PublishRuleVersionRequest"),
        "reanalyze_period": ("ReanalyzePeriod", "ReanalyzePeriodRequest"),
        "get_client_activity": ("GetClientActivity", "GetClientActivityRequest"),
        "get_profile": ("GetProfile", "GetProfileRequest"),
        "get_result": ("GetResult", "GetResultRequest"),
        "get_run": ("GetRun", "GetRunRequest"),
        "compare_runs": ("CompareRuns", "CompareRunsRequest"),
        "list_rule_versions": ("ListRuleVersions", "ListRuleVersionsRequest"),
    },
    "case": {
        "distribute_cases": ("DistributeCases", "DistributeCasesRequest"),
        "create_report": ("CreateReport", "CreateReportRequest"),
        "search_cases": ("SearchCases", "SearchCasesRequest"),
        "get_case": ("GetCase", "GetCaseRequest"),
        "get_report": ("GetReport", "GetReportRequest"),
        "export_report": ("ExportReport", "ExportReportRequest"),
        "list_notifications": ("ListNotifications", "ListNotificationsRequest"),
        "take_case": ("TakeCase", "TakeCaseRequest"),
        "add_case_comment": ("AddCaseComment", "AddCaseCommentRequest"),
        "escalate_case": ("EscalateCase", "EscalateCaseRequest"),
        "close_case": ("CloseCase", "CloseCaseRequest"),
        "reopen_case": ("ReopenCase", "ReopenCaseRequest"),
        "mark_notification_read": ("MarkNotificationRead", "MarkNotificationReadRequest"),
    },
}


def actor_record(actor):
    if isinstance(actor, dict):
        return actor
    return {"id": actor.id, "login": actor.login, "role": actor.role}


class RemoteClient:
    def __init__(self, owner, actor, address=None):
        self.owner = owner
        self.actor = actor_record(actor)
        self.address = address or os.environ[f"{owner.upper()}_GRPC"]
        self.service_token = os.environ["SERVICE_TOKEN"]

    def _metadata(self):
        return (("x-service-token", self.service_token),)

    def _invoke(self, method, arguments):
        record = self.actor
        request = pb.InvocationRequest(
            method=method, actor_id=record["id"], actor_login=record["login"],
            actor_role=record["role"], arguments_json=json.dumps(arguments, default=str).encode(),
        )
        try:
            with channel_for(self.address, self.owner) as channel:
                response = rpc.BusinessServiceStub(channel).Invoke(request, timeout=export_timeout() if method == "export_snapshot_report" else 8,
                                                                    metadata=self._metadata())
        except grpc.RpcError as exc:
            raise DomainError("DEPENDENCY_UNAVAILABLE", f"gRPC {self.owner}: {exc.code().name}") from None
        if response.error_code:
            raise DomainError(response.error_code, response.error_message)
        return json.loads(response.result_json)

    def _typed(self, name, arguments):
        operation, request_name = TYPED[self.owner][name]
        values = json.loads(json.dumps(arguments,
            default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value)))
        request = ParseDict(values, getattr(bank, request_name)())
        actor = self.actor
        metadata = self._metadata() + (("x-actor-id", actor["id"]),
                                       ("x-actor-login", actor["login"]),
                                       ("x-actor-role", actor["role"]))
        stub_class = getattr(bank_rpc, self.owner.title() + "ServiceStub")
        try:
            with channel_for(self.address, self.owner) as channel:
                response = getattr(stub_class(channel), operation)(request, timeout=8,
                                                                    metadata=metadata)
        except grpc.RpcError as exc:
            raise client_error(exc) from None
        return MessageToDict(response, preserving_proto_field_name=False,
                             always_print_fields_with_no_presence=True).get("data")

    def register_transaction(self, actor, input):
        """The key business command uses the typed bank.v1 LR2 contract."""
        record = actor_record(actor)
        request = ParseDict({"input": json.loads(json.dumps(
            input, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value)))},
                            bank.RegisterTransactionRequest())
        metadata = self._metadata() + (("x-actor-id", record["id"]),
                                       ("x-actor-login", record["login"]),
                                       ("x-actor-role", record["role"]))
        try:
            with channel_for(self.address, self.owner) as channel:
                response = bank_rpc.TransactionServiceStub(channel).RegisterTransaction(
                    request, timeout=8, metadata=metadata)
        except grpc.RpcError as exc:
            raise client_error(exc) from None
        return MessageToDict(response.data, preserving_proto_field_name=False)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            if args:
                raise TypeError("Only named service arguments are supported")
            values = {key: value for key, value in kwargs.items() if key != "actor"}
            if name in TYPED.get(self.owner, {}):
                return self._typed(name, values)
            return self._invoke(name, values)
        return call


class AnalysisTransactionClient(RemoteClient):
    """Rehydrate the two dataclass fields used by AnalysisService."""
    def __init__(self):
        super().__init__("transaction", {"id": "system", "login": "analysis", "role": "SYSTEM"})

    def get_client(self, id):
        value = self._invoke("get_client", {"id": id})
        return SimpleNamespace(**value) if value else None

    def search_transactions(self, filter=None, page=None):
        value = self._typed("search_transactions", {"filter": filter, "page": page})
        value["items"] = [SimpleNamespace(**item) for item in value["items"]]
        value.setdefault("nextCursor", None)
        return value


class EventTransactionClient(AnalysisTransactionClient):
    """Typed LR2 contracts supplying the event service's analysis input."""
    def get_transaction(self, id):
        return self._typed("get_transaction", {"id": id})

    def get_history(self, parameters):
        request = ParseDict(parameters, bank.GetHistoryRequest())
        try:
            with channel_for(self.address, self.owner) as channel:
                response = bank_rpc.TransactionServiceStub(channel).GetHistory(
                    request, timeout=8, metadata=self._metadata() + (('x-actor-role', self.actor['role']),))
        except grpc.RpcError as exc:
            raise DomainError("DEPENDENCY_UNAVAILABLE", "История Transaction Service недоступна") from exc
        return MessageToDict(response, always_print_fields_with_no_presence=True)

    def prepare_behavior_history(self, parameters):
        from backend.common.behavior_contract import validate
        validate("PrepareRequest", parameters)
        return validate("Snapshot", self._invoke("prepare_behavior_history", {"parameters": parameters}))

    def get_behavior_history_page(self, parameters):
        from backend.common.behavior_contract import validate
        validate("PageRequest", parameters)
        return validate("Page", self._invoke("get_behavior_history_page", {"parameters": parameters}))
