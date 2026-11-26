"""Case-owned access scope, checked at every owner RPC boundary."""

from backend.common.data import DomainError, uuid
from backend.infrastructure.clients import RemoteClient
import os


def transaction_access(actor, transaction_id):
    if actor.role not in ("SYSTEM", "ADMIN", "MANAGER", "ANALYST"):
        raise DomainError("FORBIDDEN", "Требуется principal")
    if actor.role == "ANALYST":
        grant = RemoteClient(
            "case", actor, address=os.getenv("CASE_GRPC", "case-service:50051")
        )._invoke("authorize_transaction", dict(transactionId=uuid(transaction_id)))
        if grant.get("allowed") is not True:
            raise DomainError("FORBIDDEN", "Нет доступа к операции случая")


def authorize(owner, db, actor, method, arguments):
    if actor.role != "ANALYST" or owner == "case":
        return
    if owner == "transaction" and method in {
        "import_transactions", "start_bulk_import", "get_bulk_import",
        "list_bulk_imports", "get_bulk_import_errors",
    }:
        return  # Transaction checks the write role; no case-owned data is returned.
    if owner == "analysis" and method in {"get_profile_version", "get_result_history", "get_operation_processing"}:
        return  # These methods enforce Case scope themselves before returning data.
    tx = None
    if owner == "transaction" and method in {"get_transaction", "report_transaction_part", "get_operation_delivery", "get_operation_transfer", "get_operation_lifecycle"}:
        tx = arguments["id"] if method == "get_transaction" else arguments["transactionId"]
    elif owner == "analysis":
        if method in {
            "get_detection_context",
            "report_assessment_part",
            "get_result_history",
            "analyze_transaction",
        }:
            tx = arguments["transactionId"]
        elif method == "get_result":
            row = db.one(
                "SELECT transaction_id FROM analysis_results WHERE id=%s",
                (uuid(arguments["id"]),),
            )
            tx = str(row["transaction_id"]) if row else None
        elif method == "get_profile":
            row = db.one(
                "SELECT cutoff_transaction_id FROM behavior_profiles WHERE id=%s",
                (uuid(arguments["id"]),),
            )
            tx = (
                str(row["cutoff_transaction_id"])
                if row and row["cutoff_transaction_id"]
                else None
            )
    if not tx:
        raise DomainError(
            "FORBIDDEN",
            "Глобальный обзор доступен руководителю; аналитику — данные доступного случая",
        )
    transaction_access(actor, tx)
