"""Product API composition. All domain data is obtained over owner service RPC."""

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


async def remote(info, owner, method, **arguments):
    try:
        client = getattr(info.context["clients"], owner)
        return await run_in_threadpool(client._invoke, method, arguments)
    except DomainError as exc:
        raise GraphQLError(
            str(exc), extensions={"code": exc.code, "service": owner}
        ) from None


@strawberry.type
class Query(ExistingQuery):
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
    async def detectionContext(self, info: Info, transactionId: strawberry.ID,resultId: strawberry.ID | None=None) -> JSON:
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
        detection = await remote(
            info,
            "analysis",
            "get_detection_context",
            transactionId=case["transactionId"], resultId=case["latestResultId"],
        )
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
MUTATION_ROLES['ADMIN'] |= {'unblockStaff', 'registerTransaction'}
for role in ("MANAGER", "ADMIN"):
    MUTATION_ROLES[role] |= {"startSource", "setSourceState"}

for role in ('MANAGER', 'ANALYST'):
    QUERY_ROLES[role] |= {'snapshotReport', 'snapshotReports'}
    MUTATION_ROLES[role] |= {'createSnapshotReport'}

for role in ('MANAGER', 'ANALYST'):
    QUERY_ROLES[role] |= {'exportSnapshotReport'}
