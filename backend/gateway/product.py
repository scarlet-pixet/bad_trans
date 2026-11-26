"""Product API composition. All domain data is obtained over owner service RPC."""

from datetime import datetime
from decimal import Decimal
from enum import Enum

import strawberry
from typing import cast
from graphql import GraphQLError
from starlette.concurrency import run_in_threadpool
from strawberry.scalars import JSON
from strawberry.types import Info

from backend.common.data import DomainError
from backend.gateway.operations import Query as ExistingQuery
from backend.gateway.security import MUTATION_ROLES, QUERY_ROLES
from backend.gateway.security import Mutation as ExistingMutation
from backend.gateway.types import PeriodInput, ReanalysisInput, Transaction, TransactionInput
from backend.gateway.dispatch import hydrate, plain


@strawberry.input
class TransferPartyInput:
    accountId: strawberry.ID | None = None
    externalAccountId: str | None = None
    externalNamespace: str | None = None


@strawberry.input
class RegisterTransferInput:
    transaction: TransactionInput
    transferNamespace: str
    transferRef: str
    debit: TransferPartyInput
    credit: TransferPartyInput


@strawberry.input
class OperationTransferInput:
    transferNamespace: str
    transferRef: str
    debit: TransferPartyInput
    credit: TransferPartyInput


@strawberry.enum
class LocationKind(Enum):
    POINT_OF_SALE = "POINT_OF_SALE"
    ATM_DEVICE = "ATM_DEVICE"
    MERCHANT_REGISTRATION = "MERCHANT_REGISTRATION"


@strawberry.input
class OperationLocationInput:
    kind: LocationKind
    countryCode: str
    city: str | None = None
    placeRef: str | None = None


@strawberry.input
class OperationEventInput:
    source: str
    operationRef: str
    eventRef: str
    kind: str
    channel: str
    status: str
    accountId: strawberry.ID
    currency: str
    direction: str
    amount: Decimal
    occurredAt: datetime
    counterpartyId: strawberry.ID | None = None
    originalTransactionId: strawberry.ID | None = None
    transfer: OperationTransferInput | None = None
    timePrecision: str | None = None
    location: OperationLocationInput | None = None


@strawberry.input
class SourceCoverageInput:
    accountId: strawberry.ID
    source: str
    channel: str
    direction: str
    feature: str
    revision: str
    intervals: list[PeriodInput]


async def remote(info, owner, method, **arguments):
    try:
        client = getattr(info.context["clients"], owner)
        return await run_in_threadpool(client._invoke, method, arguments)
    except DomainError as exc:
        raise GraphQLError(
            str(exc), extensions={"code": exc.code, "service": owner}
        ) from None


def failure_owner(owner):
    if owner not in ("analysis", "case"):
        raise GraphQLError("Неизвестный владелец журнала ошибок", extensions={"code": "BAD_INPUT"})
    return owner


@strawberry.enum
class OperationProcessingState(Enum):
    PENDING = "PENDING"
    SCORED = "SCORED"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@strawberry.type
class OperationProcessing:
    state: OperationProcessingState
    resultId: strawberry.ID | None


def analysis_read_unavailable(error):
    return error.extensions.get("code") in ("DEPENDENCY_UNAVAILABLE", "DATA_ACCESS_ERROR", "INTERNAL")


@strawberry.type
class Query(ExistingQuery):
    @strawberry.field
    async def failureReviews(self, info: Info, owner: str, state: str = "ACTIVE", offset: int = 0) -> JSON:
        return await remote(info, failure_owner(owner), "list_failure_reviews", state=state, offset=offset)

    @strawberry.field
    async def failureReview(self, info: Info, owner: str, id: strawberry.ID) -> JSON:
        return await remote(info, failure_owner(owner), "get_failure_review", id=str(id))

    @strawberry.field
    async def bulkImport(self, info: Info, id: strawberry.ID) -> JSON:
        return await remote(info, "transaction", "get_bulk_import", id=str(id))

    @strawberry.field
    async def bulkImports(self, info: Info, offset: int = 0) -> JSON:
        return await remote(info, "transaction", "list_bulk_imports", offset=offset)

    @strawberry.field
    async def bulkImportErrors(self, info: Info, id: strawberry.ID) -> str:
        return await remote(info, "transaction", "get_bulk_import_errors", id=str(id))

    @strawberry.field
    def kafkaLag(self, info: Info) -> JSON:
        return info.context["request"].app.state.kafka_lag.snapshot()

    @strawberry.field
    async def clientLabels(self, info: Info, ids: list[strawberry.ID]) -> JSON:
        return await remote(info, "transaction", "get_client_labels", ids=[str(identifier) for identifier in ids])

    @strawberry.field
    async def notificationById(self, info: Info, id: strawberry.ID) -> JSON | None:
        return await remote(info, "case", "get_notification", id=str(id))

    @strawberry.field
    async def ruleCatalog(self, info: Info) -> JSON:
        return await remote(info, "analysis", "get_rule_catalog")

    @strawberry.field
    async def periodRunResults(
        self, info: Info, runId: strawberry.ID, offset: int = 0, limit: int = 100,
    ) -> JSON | None:
        return await remote(info, "analysis", "get_period_run_results", runId=str(runId), offset=offset, limit=limit)

    @strawberry.field
    async def comparePeriodRuns(
        self, info: Info, firstId: strawberry.ID, secondId: strawberry.ID, offset: int = 0, limit: int = 50,
    ) -> JSON:
        return await remote(info, "analysis", "compare_period_runs", firstId=str(firstId), secondId=str(secondId),
                            offset=offset, limit=limit)

    @strawberry.field
    async def compareProfileRuns(
        self, info: Info, firstId: strawberry.ID, secondId: strawberry.ID,
        offset: int = 0, limit: int = 20,
    ) -> JSON:
        return await remote(info, "analysis", "compare_profile_runs",
                            firstId=str(firstId), secondId=str(secondId),
                            offset=offset, limit=limit)

    @strawberry.field
    async def profileRun(self, info: Info, id: strawberry.ID, offset: int = 0, limit: int = 50) -> JSON:
        return await remote(info, 'analysis', 'get_profile_run', id=str(id), offset=offset, limit=limit)

    @strawberry.field
    async def profileRuns(self, info: Info, limit: int = 20, offset: int = 0) -> JSON:
        return await remote(info, 'analysis', 'list_profile_runs', limit=limit, offset=offset)

    @strawberry.field
    async def exportSnapshotReport(self, info: Info, id: strawberry.ID, format: str, table: str | None = None) -> JSON:
        return await remote(info, 'case', 'export_snapshot_report', id=str(id), format=format, table=table)

    @strawberry.field
    async def snapshotReport(self, info: Info, id: strawberry.ID) -> JSON:
        return await remote(info, 'case', 'get_snapshot_report', id=str(id))

    @strawberry.field
    async def snapshotReports(self, info: Info, limit: int = 20, offset: int = 0) -> JSON:
        return await remote(info, 'case', 'list_snapshot_reports', limit=limit, offset=offset)

    @strawberry.field
    async def profileVersion(self,info: Info,id: strawberry.ID) -> JSON:
        return await remote(info,'analysis','get_profile_version',id=str(id))

    @strawberry.field
    async def compareProfileVersions(self, info: Info, firstId: strawberry.ID, secondId: strawberry.ID) -> JSON:
        return await remote(info, 'analysis', 'compare_profile_versions', firstId=str(firstId), secondId=str(secondId))

    @strawberry.field
    async def operationLifecycle(self, info: Info, transactionId: strawberry.ID) -> JSON | None:
        return await remote(info, "transaction", "get_operation_lifecycle", transactionId=str(transactionId))

    @strawberry.field
    async def clientSpending(self, info: Info, clientId: strawberry.ID, currencyCode: str, period: PeriodInput) -> JSON:
        return await remote(info, "transaction", "get_client_spending", clientId=str(clientId),
                            currencyCode=currencyCode, window=plain(period))

    @strawberry.field
    async def operationTransfer(self, info: Info, transactionId: strawberry.ID) -> JSON | None:
        return await remote(info, "transaction", "get_operation_transfer", transactionId=str(transactionId))

    @strawberry.field
    async def operationProcessing(self, info: Info, transactionId: strawberry.ID) -> OperationProcessing:
        tx = str(transactionId)
        delivery = await remote(info, "transaction", "get_operation_delivery", transactionId=tx)
        try:
            evidence = await remote(info, "analysis", "get_operation_processing",
                                    transactionId=tx, eventId=delivery["eventId"])
        except GraphQLError as error:
            if not analysis_read_unavailable(error):
                raise
            evidence = {"state": "PENDING" if delivery["queued"] else "UNKNOWN", "resultId": None}
        return OperationProcessing(
            state=OperationProcessingState(evidence["state"]), resultId=evidence["resultId"]
        )

    @strawberry.field
    async def resultHistory(self,info: Info,transactionId: strawberry.ID,limit: int=20,pageToken: str | None=None) -> JSON:
        return await remote(info,'analysis','get_result_history',transactionId=str(transactionId),limit=limit,pageToken=pageToken)

    @strawberry.field
    async def feedbackStatus(self,info: Info) -> JSON:
        return await remote(info,'analysis','get_feedback_status')

    @strawberry.field
    async def behaviorProfilePage(
        self, info: Info, clientId: strawberry.ID | None = None,
        windowDays: int = 30, history: bool = False, currency: str | None = None,
        direction: str | None = None, origin: str | None = None,
        limit: int = 50, pageToken: str | None = None,
    ) -> JSON:
        return await remote(info, "analysis", "get_behavior_profile_page", clientId=clientId,
                            windowDays=windowDays,history=history,currency=currency,
                            direction=direction,origin=origin,limit=limit,pageToken=pageToken)

    @strawberry.field
    async def profileRefreshStatus(self, info: Info) -> JSON:
        return await remote(info, "analysis", "get_profile_refresh_status")

    @strawberry.field
    async def counterparties(self, info: Info) -> JSON:
        return await remote(info, 'transaction', 'get_counterparties')

    @strawberry.field
    async def staffUsers(self, info: Info) -> JSON:
        try:
            return await run_in_threadpool(
                info.context["auth_api"].request, "GET", "/staff", info.context["token"]
            )
        except DomainError as exc:
            raise GraphQLError(
                str(exc), extensions={"code": exc.code, "service": "auth"}
            ) from None

    @strawberry.field
    async def behaviorProfiles(
        self,
        info: Info,
        clientId: strawberry.ID | None = None,
        windowDays: int = 30,
        history: bool = False,
    ) -> JSON:
        return await remote(
            info,
            "analysis",
            "get_behavior_profiles",
            clientId=clientId,
            windowDays=windowDays,
            history=history,
        )

    @strawberry.field
    async def detections(
        self, info: Info, clientId: strawberry.ID | None = None
    ) -> JSON:
        return await remote(info, "analysis", "get_detections", clientId=clientId)

    @strawberry.field
    async def detectionContext(self, info: Info, transactionId: strawberry.ID,resultId: strawberry.ID | None=None) -> JSON | None:
        return await remote(
            info, "analysis", "get_detection_context", transactionId=transactionId,resultId=resultId
        )

    @strawberry.field
    async def sources(self, info: Info) -> JSON:
        return await remote(info, "transaction", "get_sources")

    @strawberry.field
    async def qualityReport(self, info: Info, runId: strawberry.ID) -> JSON:
        return await remote(info, "analysis", "get_quality", runId=runId)

    @strawberry.field
    async def caseContext(self, info: Info, caseId: strawberry.ID) -> JSON:
        clients = info.context["clients"]
        # Reuse the Case owner's row access rules before reading related data.
        case = await run_in_threadpool(clients.case.get_case, id=str(caseId))
        if not case:
            raise GraphQLError("Случай недоступен", extensions={"code": "NOT_FOUND"})
        tx = await run_in_threadpool(
            clients.transaction.get_transaction, id=case["transactionId"]
        )
        try:
            detection = await remote(
                info, "analysis", "get_detection_context",
                transactionId=case["transactionId"], resultId=case["latestResultId"],
            )
        except GraphQLError as error:
            if not analysis_read_unavailable(error):
                raise
            detection = None
        return cast(JSON, {"case": case, "transaction": tx, "detection": detection})

    @strawberry.field
    async def managerDashboard(self, info: Info) -> JSON:
        components, errors = {}, []
        for owner, method in [
            ("transaction", "get_ingestion_metrics"),
            ("analysis", "get_analysis_metrics"),
            ("case", "get_case_metrics"),
        ]:
            try:
                components[owner] = await remote(info, owner, method)
            except GraphQLError as exc:
                errors.append(
                    {
                        "service": owner,
                        "code": (exc.extensions or {}).get("code"),
                        "message": exc.message,
                    }
                )
        return cast(JSON, {"components": components, "partialErrors": errors})


@strawberry.type
class Mutation(ExistingMutation):
    @strawberry.mutation
    async def declareSourceCoverage(self, info: Info, input: SourceCoverageInput) -> JSON:
        return await remote(info, "transaction", "declare_source_coverage", input=plain(input))

    @strawberry.mutation
    async def recordOperationEvent(self, info: Info, input: OperationEventInput) -> JSON:
        return await remote(info, "transaction", "record_operation_event", input=plain(input))

    @strawberry.mutation
    async def registerTransfer(self, info: Info, input: RegisterTransferInput) -> Transaction:
        value = await remote(info, "transaction", "register_transfer", input=plain(input))
        return cast(Transaction, hydrate(Transaction, value))


    @strawberry.mutation
    async def changeFailureReview(self, info: Info, owner: str, id: strawberry.ID, action: str,
                                  idempotencyKey: str, reason: str | None = None) -> JSON:
        return await remote(info, failure_owner(owner), "change_failure_review", id=str(id),
                            action=action, reason=reason, idempotencyKey=idempotencyKey)

    @strawberry.mutation
    async def startPeriodReassessment(self, info: Info, input: ReanalysisInput) -> JSON:
        arguments = dict(
            period={"from": input.period.from_.isoformat(), "to": input.period.to.isoformat()},
            clientId=str(input.clientId) if input.clientId is not None else None,
            ruleVersionIds=[str(identifier) for identifier in input.ruleVersionIds],
            idempotencyKey=input.idempotencyKey,
        )
        return await remote(info, "analysis", "start_period_reassessment", input=arguments)

    @strawberry.mutation
    async def startBulkImport(self, info: Info, fileBase64: str, fileHash: str, idempotencyKey: str) -> JSON:
        return await remote(info, "transaction", "start_bulk_import",
                            fileBase64=fileBase64, fileHash=fileHash, idempotencyKey=idempotencyKey)

    @strawberry.mutation
    async def startProfileRun(self, info: Info, selection: JSON, idempotencyKey: str) -> JSON:
        return await remote(info, 'analysis', 'start_profile_run', selection=selection, idempotencyKey=idempotencyKey)

    @strawberry.mutation
    async def createSnapshotReport(self, info: Info, input: JSON) -> JSON:
        from backend.gateway.reporting import create_snapshot, info_actor
        try:
            return await run_in_threadpool(create_snapshot, info.context['clients'], info_actor(info.context['actor']), input)
        except DomainError as exc:
            raise GraphQLError(str(exc), extensions={'code': exc.code}) from None

    @strawberry.mutation
    async def startSource(
        self, info: Info, idempotencyKey: str, intervalSeconds: float = 1.0
    ) -> JSON:
        return await remote(
            info,
            "transaction",
            "start_source",
            idempotencyKey=idempotencyKey,
            intervalSeconds=intervalSeconds,
        )

    @strawberry.mutation
    async def setSourceState(
        self, info: Info, runId: strawberry.ID, paused: bool
    ) -> JSON:
        return await remote(
            info, "transaction", "set_source_state", runId=runId, paused=paused
        )


for role in ("MANAGER", "ANALYST"):
    QUERY_ROLES[role] |= {
        "behaviorProfiles",
        "behaviorProfilePage",
        "detections",
        "detectionContext",
        "operationProcessing",
        "operationTransfer",
        "operationLifecycle",
        "caseContext",
    }
QUERY_ROLES["MANAGER"] |= {"sources", "qualityReport", "managerDashboard"}
QUERY_ROLES["MANAGER"] |= {"profileRefreshStatus"}
QUERY_ROLES["ADMIN"] |= {"profileRefreshStatus"}
QUERY_ROLES['ADMIN'] |= {'feedbackStatus'}
QUERY_ROLES['MANAGER'] |= {'feedbackStatus'}
QUERY_ROLES["ADMIN"] |= {"sources", "staffUsers", "counterparties", "transactions", "transaction"}
QUERY_ROLES['MANAGER'] |= {'profileVersion','resultHistory'}
QUERY_ROLES['MANAGER'].add('compareProfileVersions')
QUERY_ROLES['ANALYST'] |= {'profileVersion','resultHistory'}
QUERY_ROLES['ANALYST'] -= {'clients','transactions','clientActivity','analysisRun','behaviorProfiles','behaviorProfilePage','detections'}
QUERY_ROLES['ADMIN'].update({'operationTransfer', 'operationLifecycle'})
QUERY_ROLES['MANAGER'].add('clientSpending')
for role in ("MANAGER", "ADMIN"):
    MUTATION_ROLES[role].add('recordOperationEvent')
MUTATION_ROLES['ADMIN'] |= {'unblockStaff', 'registerTransaction', 'registerTransfer'}
MUTATION_ROLES['MANAGER'].add('registerTransfer')
MUTATION_ROLES['ADMIN'].add('declareSourceCoverage')
for role in ("MANAGER", "ADMIN"):
    MUTATION_ROLES[role] |= {"startSource", "setSourceState"}

for role in ('MANAGER', 'ANALYST'):
    QUERY_ROLES[role] |= {'snapshotReport', 'snapshotReports'}
    MUTATION_ROLES[role] |= {'createSnapshotReport'}

for role in ('MANAGER', 'ANALYST'):
    QUERY_ROLES[role] |= {'exportSnapshotReport'}

QUERY_ROLES['MANAGER'] |= {'profileRun','profileRuns','compareProfileRuns'}
QUERY_ROLES['MANAGER'] |= {'ruleCatalog', 'periodRunResults', 'comparePeriodRuns'}
MUTATION_ROLES['MANAGER'].add('startPeriodReassessment')
QUERY_ROLES['MANAGER'].update({'clientLabels', 'notificationById'})
QUERY_ROLES['ANALYST'].add('notificationById')
QUERY_ROLES['ADMIN'].update({'kafkaLag', 'failureReviews', 'failureReview'})
MUTATION_ROLES['ADMIN'].add('changeFailureReview')
for role in ("MANAGER", "ANALYST"):
    QUERY_ROLES[role].update({"bulkImport", "bulkImports", "bulkImportErrors"})
    MUTATION_ROLES[role].add("startBulkImport")
MUTATION_ROLES['MANAGER'].add('startProfileRun')
