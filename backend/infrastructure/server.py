"""One real gRPC process per owned database, with separate event business services."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
import json
import logging
import os
import threading
import grpc
import psycopg
from google.protobuf.json_format import MessageToDict, ParseDict
from confluent_kafka.admin import AdminClient, NewTopic
from backend.generated import bank_pb2 as bank, bank_pb2_grpc as bank_rpc
from backend.generated import transport_pb2 as pb, transport_pb2_grpc as rpc
from backend.services.auth.persistence.models import User
from backend.services.analysis.persistence.repository import AnalysisRepository
from backend.services.analysis.application.queries import AnalysisService
from backend.common.data import DomainError, uuid as valid_uuid, currency as valid_currency, period as valid_period
from backend.infrastructure.transport import abort_error, rpc_guard
from backend.common.commands import WritableDatabase
from backend.services.transaction.ingest.registration import TransactionService
from backend.services.case.persistence.repository import CaseRepository
from backend.services.case.lifecycle.service import CaseService
from backend.infrastructure.clients import AnalysisTransactionClient
from backend.services.transaction.ingest.commands import TransactionCommands
from backend.services.analysis.application.commands import AnalysisCommands
from backend.services.case.lifecycle.commands import CaseCommands
from backend.services.case.persistence.repository import CaseRepository as NotifyingCaseRepository
from backend.services.transaction.persistence.repository import TransactionRepository
from backend.infrastructure.resilience import ServerLogging, configure_logging
SERVICE = os.environ["SERVICE_NAME"]
TOKEN = os.environ["SERVICE_TOKEN"]
DB = WritableDatabase(os.environ["DATABASE_URL"], SERVICE)
WORKERS = []
configure_logging()

METHODS = {
    "transaction": {"get_client", "get_transaction", "search_clients", "search_transactions",
                    "transaction_summary", "create_client", "open_account",
                    "register_counterparty", "register_transaction", "import_transactions"},
    "analysis": {"get_profile", "get_result", "get_run", "list_rule_versions",
                 "compare_runs", "get_client_activity", "build_profile", "analyze_transaction", "publish_rule_version", "reanalyze_period"},
    "case": {"get_case", "search_cases", "get_report", "export_report",
             "list_notifications", "take_case", "add_case_comment", "escalate_case",
             "close_case", "reopen_case", "mark_notification_read", "distribute_cases", "create_report"},
}
WRITES = {"create_client", "open_account", "register_counterparty", "register_transaction",
          "take_case", "add_case_comment", "escalate_case", "close_case", "reopen_case",
          "mark_notification_read", "import_transactions", "build_profile", "analyze_transaction",
          "publish_rule_version", "reanalyze_period", "distribute_cases", "create_report"}


PRODUCT_METHODS = {
    'transaction': {'get_behavior_groups','prepare_behavior_history','get_behavior_history_page','start_source','get_sources','set_source_state','get_source_labels','get_ingestion_metrics','get_counterparties'},
    'analysis': {'get_profile_version','get_result_history','get_feedback_status','get_behavior_profile_page','get_profile_refresh_status','get_behavior_profiles','get_detection_context','get_detections','get_quality','get_analysis_metrics'},
    'case': {'get_case_metrics','authorize_transaction'},
}


PRODUCT_METHODS['analysis'].add('compare_profile_versions')


REPORT_METHODS = {
    'transaction': {'report_client_part', 'report_transaction_part', 'report_transactions_part'},
    'analysis': {'report_profile_part', 'report_assessment_part', 'report_analysis_part'},
    'case': {'export_snapshot_report', 'find_snapshot_report', 'store_snapshot_report', 'get_snapshot_report', 'list_snapshot_reports', 'report_case_part', 'report_cases_part'},
}


def report_service():
    if SERVICE == 'transaction':
        from backend.services.transaction.reports.slices import ReportSlices
        return ReportSlices(DB)
    if SERVICE == 'analysis':
        from backend.services.analysis.reports.slices import ReportSlices
        return ReportSlices(DB)
    from backend.services.case.reports.snapshots import ReportSnapshots
    return ReportSnapshots(DB)


def product_service():
    if SERVICE == 'transaction':
        from backend.services.transaction.demo.source import SourceService
        return SourceService(DB)
    if SERVICE == 'analysis':
        from backend.services.analysis.profiles.service import ProfileService
        from backend.infrastructure.clients import EventTransactionClient
        return ProfileService(DB, EventTransactionClient())
    from backend.services.case.reports.metrics import CaseMetrics
    return CaseMetrics(DB)


class PrincipalClient:
    def __init__(self, actor):
        self.actor = actor

    def get_current_user(self):
        return self.actor


def service_for(actor):
    if SERVICE == "transaction":
        return TransactionCommands(TransactionRepository(DB))
    if SERVICE == "analysis":
        return AnalysisCommands(AnalysisRepository(DB), AnalysisTransactionClient())
    return CaseCommands(NotifyingCaseRepository(DB), PrincipalClient(actor))


def wire(value):
    if is_dataclass(value):
        return {key.split("_")[0] + "".join(x.title() for x in key.split("_")[1:]): wire(item)
                for key, item in asdict(value).items()}
    if isinstance(value, SimpleNamespace):
        return wire(vars(value))
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [wire(item) for item in value]
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def metadata(context):
    values = dict(context.invocation_metadata())
    if values.get("x-service-token") != TOKEN:
        context.abort(grpc.StatusCode.PERMISSION_DENIED, "Internal service token required")
    return values


def actor_from(values):
    return User(id=values["id"], login=values["login"], role=values["role"], is_active=True)


class Business(rpc.BusinessServiceServicer):
    @rpc_guard
    def Invoke(self, request, context):
        metadata(context)
        if request.method == "health":
            return pb.InvocationResponse(result_json=json.dumps(readiness()).encode())
        if request.method not in METHODS[SERVICE] | PRODUCT_METHODS[SERVICE] | REPORT_METHODS[SERVICE]:
            return pb.InvocationResponse(error_code="BAD_INPUT", error_message="Unknown service method")
        actor = actor_from({"id": request.actor_id, "login": request.actor_login,
                            "role": request.actor_role})
        try:
            arguments = json.loads(request.arguments_json or b"{}")
            from backend.infrastructure.workspace_access import authorize
            authorize(SERVICE,DB,actor,request.method,arguments)
            product = request.method in PRODUCT_METHODS[SERVICE]
            reporting = request.method in REPORT_METHODS[SERVICE]
            if request.method in WRITES or product or reporting:
                arguments["actor"] = actor
            target = report_service() if reporting else product_service() if product else service_for(actor)
            result = getattr(target, request.method)(**arguments)
            return pb.InvocationResponse(result_json=json.dumps(wire(result), ensure_ascii=False).encode())
        except DomainError as exc:
            return pb.InvocationResponse(error_code=exc.code, error_message=str(exc))
        except (ValueError, TypeError, KeyError) as exc:
            return pb.InvocationResponse(error_code="BAD_INPUT", error_message=str(exc))
        except psycopg.errors.UniqueViolation:
            return pb.InvocationResponse(error_code="CONFLICT", error_message="Объект уже существует")
        except psycopg.OperationalError:
            return pb.InvocationResponse(error_code="DEPENDENCY_UNAVAILABLE", error_message="База данных недоступна")
        except psycopg.Error:
            logging.error("Database error in %s", SERVICE)
            return pb.InvocationResponse(error_code="DATA_ACCESS_ERROR", error_message="Ошибка хранения данных")


class TypedHealth:
    @rpc_guard
    def Health(self, request, context):
        metadata(context)
        data = readiness()
        return bank.HealthResponse(service=SERVICE, database=data['database'],
                                   table_count=data['tables'],
                                   status='READY' if data['ready'] else 'NOT_READY')


class TypedTransaction(TypedHealth, bank_rpc.TransactionServiceServicer):
    def __init__(self):
        self.service = TransactionCommands(TransactionRepository(DB))

    @rpc_guard
    def RegisterTransaction(self, request, context):
        values = metadata(context)
        actor = actor_from({"id": values.get("x-actor-id", ""),
                            "login": values.get("x-actor-login", ""),
                            "role": values.get("x-actor-role", "")})
        try:
            data = MessageToDict(request.input, preserving_proto_field_name=False)
            from backend.infrastructure.workspace_access import authorize
            authorize('transaction',DB,actor,'register_transaction',{'input':data})
            value = self.service.register_transaction(actor, data)
            return ParseDict({"data": wire(value)}, bank.RegisterTransactionResponse())
        except DomainError as exc:
            abort_error(context, exc)
        except psycopg.errors.UniqueViolation:
            context.abort(grpc.StatusCode.ALREADY_EXISTS, "External ID exists")

    @rpc_guard
    def GetTransaction(self, request, context):
        values=metadata(context)
        try:
            from backend.infrastructure.workspace_access import transaction_access
            principal=actor_from(dict(id=values.get('x-actor-id',''),login=values.get('x-actor-login',''),role=values.get('x-actor-role','')))
            transaction_access(principal,request.id)
            value = self.service.get_transaction(request.id)
            return ParseDict({"data": wire(value)} if value else {}, bank.GetTransactionResponse())
        except DomainError as exc:
            abort_error(context, exc)

    @rpc_guard
    def GetHistory(self, request, context):
        principal=metadata(context)
        if principal.get('x-actor-role') not in ('SYSTEM','MANAGER'):
            raise DomainError('FORBIDDEN','Глобальная история недоступна')
        values = MessageToDict(request, preserving_proto_field_name=False)
        valid_uuid(request.client_id)
        valid_currency(request.currency)
        if request.direction not in ("IN", "OUT"):
            raise DomainError("BAD_INPUT", "Недопустимое направление операции")
        valid_period({"from": values["windowStart"], "to": values["beforeAt"]})
        stamp = datetime.fromisoformat(values["ingestedBefore"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise DomainError("BAD_INPUT", "Требуется часовой пояс")
        if values.get("beforeId"):
            valid_uuid(values["beforeId"])
        limit = request.limit or 100
        offset = int(values.get("pageToken") or "0")
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
            raise DomainError("BAD_INPUT", "Некорректная пагинация истории")
        clauses = ["a.client_id=%s", "t.currency=%s", "t.direction=%s",
                   "t.occurred_at >= %s", "t.occurred_at < %s", "t.ingested_at < %s"]
        params = [request.client_id, request.currency, request.direction,
                  values["windowStart"], values["beforeAt"], values["ingestedBefore"]]
        if values.get("beforeId"):
            clauses[-2] = "(t.occurred_at < %s OR (t.occurred_at=%s AND t.id < %s))"
            params = [request.client_id, request.currency, request.direction,
                      values["windowStart"], values["beforeAt"], values["beforeAt"],
                      values["beforeId"], values["ingestedBefore"]]
        query = ("SELECT t.*,a.client_id FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE "
                 + " AND ".join(clauses) + " ORDER BY t.occurred_at,t.id LIMIT %s OFFSET %s")
        rows = DB.query(query, params + [limit + 1, offset])
        items = rows[:limit]
        result = {"items": [wire(row) for row in items]}
        # SQL rows use snake_case; bank.v1 protobuf input uses camelCase.
        columns = {"id", "external_id", "source", "client_id", "account_id",
                   "counterparty_id", "amount", "currency", "direction",
                   "occurred_at", "ingested_at"}
        result["items"] = [{key.split("_")[0] + "".join(x.title() for x in key.split("_")[1:]): value
                            for key, value in item.items() if key in columns and value is not None}
                           for item in result["items"]]
        if len(rows) > limit:
            result["nextPageToken"] = str(offset + limit)
        return ParseDict(result, bank.GetHistoryResponse())


def typed_execute(request, context, method, response_name):
    values = metadata(context)
    actor = actor_from({"id": values.get("x-actor-id", ""),
                        "login": values.get("x-actor-login", ""),
                        "role": values.get("x-actor-role", "")})
    arguments = MessageToDict(request, preserving_proto_field_name=False)
    arguments.pop("accessToken", None)
    if method in WRITES:
        arguments["actor"] = actor
    try:
        from backend.infrastructure.workspace_access import authorize
        authorize(SERVICE,DB,actor,method,arguments)
        result = getattr(service_for(actor), method)(**arguments)
        value = {"data": wire(result)} if result is not None else {}
        return ParseDict(value, getattr(bank, response_name)(), ignore_unknown_fields=True)
    except DomainError as exc:
        abort_error(context, exc)
    except psycopg.errors.UniqueViolation:
        context.abort(grpc.StatusCode.ALREADY_EXISTS, "Object already exists")
    except psycopg.Error as exc:
        abort_error(context, exc)


class TypedAnalysis(TypedHealth, bank_rpc.AnalysisServiceServicer):
    pass


class TypedCase(TypedHealth, bank_rpc.CaseServiceServicer):
    pass


def handler(method, operation):
    @rpc_guard
    def call(self, request, context):
        return typed_execute(request, context, method, operation + "Response")
    return call


for operation, method in {
    "SearchClients": "search_clients", "SearchTransactions": "search_transactions",
    "CreateClient": "create_client", "OpenAccount": "open_account",
    "RegisterCounterparty": "register_counterparty", "ImportTransactions": "import_transactions",
}.items():
    setattr(TypedTransaction, operation, handler(method, operation))

for operation, method in {
    "StartProfile": "build_profile", "StartAnalysis": "analyze_transaction",
    "PublishRuleVersion": "publish_rule_version", "ReanalyzePeriod": "reanalyze_period",
    "GetClientActivity": "get_client_activity", "GetProfile": "get_profile",
    "GetResult": "get_result", "GetRun": "get_run",
    "CompareRuns": "compare_runs", "ListRuleVersions": "list_rule_versions",
}.items():
    setattr(TypedAnalysis, operation, handler(method, operation))

for operation, method in {
    "DistributeCases": "distribute_cases", "CreateReport": "create_report",
    "SearchCases": "search_cases", "GetCase": "get_case", "GetReport": "get_report",
    "ExportReport": "export_report", "ListNotifications": "list_notifications",
    "TakeCase": "take_case", "AddCaseComment": "add_case_comment",
    "EscalateCase": "escalate_case", "CloseCase": "close_case",
    "ReopenCase": "reopen_case", "MarkNotificationRead": "mark_notification_read",
}.items():
    setattr(TypedCase, operation, handler(method, operation))


def topics():
    admin = AdminClient({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP"]})
    requested = (NewTopic(name, 1, 1) for name in
                 ("transaction.registered.v1", "analysis.completed.v1", "case.feedback.v1", "bank.dead-letter.v1"))
    for future in admin.create_topics(list(requested), request_timeout=2, operation_timeout=2).values():
        try:
            future.result(timeout=3)
        except Exception as exc:
            if "TOPIC_ALREADY_EXISTS" not in str(exc):
                raise


def topics_loop():
    """Create required topics asynchronously, including after broker recovery."""
    import time
    while True:
        try:
            topics()
        except Exception as exc:
            logging.warning('Kafka topic initialization pending: %s', type(exc).__name__)
            time.sleep(5)
        else:
            time.sleep(60)


def readiness():
    data = DB.health()
    workers = {worker.name: worker.is_alive() for worker in WORKERS}
    data['workers'] = workers
    data['ready'] = bool(data)
    data['workersReady'] = bool(workers) and all(workers.values())
    # Broker health is separate: pending outbox/retry records survive outages,
    # while independent synchronous reads remain available.
    from backend.infrastructure.broker_health import observation
    data['kafkaReady'], data['kafkaMeasuredAt'] = observation()
    data['asynchronousReady'] = data['ready'] and data['kafkaReady'] is True and data['workersReady']
    try:
        from backend.infrastructure.health_activity import activity
        data['activity'] = activity(DB, SERVICE)
    except Exception:
        data['activity'] = {'state': 'UNKNOWN'}
    return data


def start_worker(target, name):
    worker = threading.Thread(target=target, daemon=True, name=name)
    WORKERS.append(worker)
    worker.start()


def main():
    from . import workers as events  # Transport workers delegate to event_services.py.

    from backend.infrastructure.product_migrations import migrate
    migrate(DB, SERVICE)
    events.initialize()
    # Broker outages must not prevent synchronous gRPC service startup.
    server = grpc.server(ThreadPoolExecutor(max_workers=16), interceptors=[ServerLogging()])
    rpc.add_BusinessServiceServicer_to_server(Business(), server)
    if SERVICE == "transaction":
        bank_rpc.add_TransactionServiceServicer_to_server(TypedTransaction(), server)
    elif SERVICE == "analysis":
        bank_rpc.add_AnalysisServiceServicer_to_server(TypedAnalysis(), server)
    else:
        bank_rpc.add_CaseServiceServicer_to_server(TypedCase(), server)
    bind_address = os.getenv("GRPC_BIND", "[::]:50051")
    if server.add_insecure_port(bind_address) == 0:
        raise RuntimeError("Cannot bind the configured gRPC listener")
    server.start()
    from backend.infrastructure.broker_health import loop as broker_health_loop
    start_worker(broker_health_loop, 'kafka-health')
    start_worker(topics_loop, 'kafka-topics')
    if SERVICE in ("transaction", "analysis", "case"):
        start_worker(events.outbox_loop, "kafka-outbox")
    if SERVICE in ("analysis", "case"):
        start_worker(events.parking_loop, "kafka-parking")
        start_worker(events.consume_loop, "kafka-consumer")
        start_worker(events.retry_loop, "kafka-retry")
    if SERVICE == 'case':
        start_worker(events.notifications_loop, 'notification-delivery')
        start_worker(events.feedback_bootstrap_loop,'feedback-bootstrap')
    if SERVICE == 'transaction':
        start_worker(product_service().loop, 'source-ingestion')
    if SERVICE == 'analysis':
        start_worker(product_service().loop, 'profile-refresh')
    logging.info("Bad Trans %s gRPC running; Kafka workers active", SERVICE)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
