"""Server-only report assembly. No database connection or client snapshot input."""

from datetime import UTC, datetime
from types import SimpleNamespace

from backend.common.commands import require_role
from backend.common.report_contract import normalize_request, principal, sign, validate_snapshot


def create_snapshot(clients, actor, input):
    request = normalize_request(input)
    require_role(actor, "MANAGER", "ANALYST")
    if actor.role == "ANALYST":
        require_role(actor, "MANAGER" if request["reportType"] != "OPERATION" else "ANALYST")
    old = clients.case._invoke("find_snapshot_report", dict(input=request))
    if old:
        return old
    cutoff = datetime.now(UTC).isoformat()
    kind = request["reportType"]
    if kind == "PROFILE":
        analysis = clients.analysis._invoke("report_profile_part", dict(profileVersionId=request["profileVersionId"], cutoff=cutoff))
        transaction = clients.transaction._invoke("report_client_part", dict(clientId=analysis["data"]["metadata"]["clientId"], cutoff=cutoff))
        data = dict(client=transaction["data"], profile=analysis["data"])
        cuts = [transaction["cut"], analysis["cut"]]
    elif kind == "OPERATION":
        txid = request["transactionId"]
        transaction = clients.transaction._invoke("report_transaction_part", dict(transactionId=txid, cutoff=cutoff))
        analysis = clients.analysis._invoke("report_assessment_part", dict(transactionId=txid, resultId=request["resultId"], cutoff=cutoff))
        case = clients.case._invoke("report_case_part", dict(transactionId=txid, cutoff=cutoff))
        data = dict(transaction=transaction["data"], **analysis["data"], review=case["data"])
        cuts = [transaction["cut"], analysis["cut"], case["cut"]]
    else:
        transaction = clients.transaction._invoke("report_transactions_part", dict(input=request, cutoff=cutoff))
        cohort = transaction["data"]["cohort"]
        analysis = clients.analysis._invoke("report_analysis_part", dict(cohort=cohort, windowDays=request["windowDays"], periodFilter=request["period"], cutoff=cutoff))
        case = clients.case._invoke("report_cases_part", dict(transactionIds=[r["id"] for r in cohort], periodFilter=request["period"], cutoff=cutoff))
        data = dict(transactions=transaction["data"], analysis=analysis["data"], cases=case["data"])
        cuts = [transaction["cut"], analysis["cut"], case["cut"]]
    snapshot = dict(reportSchemaVersion=1, reportType=kind, author=principal(actor),
                    filters={k: v for k, v in request.items() if k != "idempotencyKey"},
                    assembledAt=datetime.now(UTC).isoformat(), cutoff=cutoff,
                    completeness="COMPLETE", consistency="OWNER_SNAPSHOTS_NOT_GLOBAL_ATOMIC", sourceCuts=cuts, data=data)
    validate_snapshot(actor, request, snapshot)
    signature = sign(actor, request, snapshot)
    return clients.case._invoke("store_snapshot_report", dict(input=request, snapshot=snapshot, signature=signature))


def info_actor(value):
    return SimpleNamespace(id=value["id"], login=value["login"], role=value["role"], is_active=value["isActive"])
