"""Authenticated GraphQL boundary for the complete transaction analysis system."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4, UUID
import os
import json
import time
import requests
import redis
import strawberry
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from strawberry.fastapi import GraphQLRouter
from strawberry.schema.config import StrawberryConfig
from strawberry.types import Info
from backend.services.auth.persistence.models import User as Principal
from backend.gateway.types import ImportBatchInput, ImportResult, ProfileInput, AnalysisRun, ReanalysisInput, RuleVersionInput, RuleVersion, DistributionInput, DistributionResult, ReportInput, Report
from backend.gateway.operations import invoke
from backend.gateway.product import Query, Mutation as SecuredMutation
from backend.gateway.security import context, MUTATION_ROLES
from backend.gateway.auth_client import AuthAPIClient
from backend.common.principal import PrincipalClient
from backend.infrastructure.cache import CachedClient
from backend.infrastructure.clients import RemoteClient
from backend.infrastructure.kafka_lag import LagMonitor
from backend.infrastructure.resilience import configure_logging, log, trace_id
configure_logging()
MUTATION_ROLES['MANAGER'] |= {'importTransactions', 'buildProfile', 'analyzeTransaction', 'publishRuleVersion', 'reanalyzePeriod', 'distributeCases', 'createReport'}
MUTATION_ROLES['ANALYST'] |= {'importTransactions', 'buildProfile', 'analyzeTransaction', 'createReport'}
MUTATION_ROLES['ANALYST'] -= {'buildProfile','registerTransaction'}

@strawberry.type
class Mutation(SecuredMutation):

    @strawberry.mutation
    async def importTransactions(self, info: Info, *, input: ImportBatchInput) -> ImportResult:
        return await invoke('transaction', 'import_transactions', ImportResult, info, {'input': input})

    @strawberry.mutation
    async def buildProfile(self, info: Info, *, input: ProfileInput) -> AnalysisRun:
        return await invoke('analysis', 'build_profile', AnalysisRun, info, {'input': input})

    @strawberry.mutation
    async def analyzeTransaction(self, info: Info, *, transactionId: strawberry.ID, idempotencyKey: str) -> AnalysisRun:
        return await invoke('analysis', 'analyze_transaction', AnalysisRun, info, {'transactionId': transactionId, 'idempotencyKey': idempotencyKey})

    @strawberry.mutation
    async def publishRuleVersion(self, info: Info, *, input: RuleVersionInput) -> RuleVersion:
        return await invoke('analysis', 'publish_rule_version', RuleVersion, info, {'input': input})

    @strawberry.mutation
    async def reanalyzePeriod(self, info: Info, *, input: ReanalysisInput) -> AnalysisRun:
        return await invoke('analysis', 'reanalyze_period', AnalysisRun, info, {'input': input})

    @strawberry.mutation
    async def distributeCases(self, info: Info, *, input: DistributionInput) -> DistributionResult:
        return await invoke('case', 'distribute_cases', DistributionResult, info, {'input': input})

    @strawberry.mutation
    async def createReport(self, info: Info, *, input: ReportInput) -> Report:
        return await invoke('case', 'create_report', Report, info, {'input': input})
schema = strawberry.Schema(query=Query, mutation=Mutation, config=StrawberryConfig(auto_camel_case=False))

class Container:

    def clients(self, verified):
        actor = Principal(id=verified['id'], login=verified['login'], role=verified['role'], is_active=verified['isActive'])
        return SimpleNamespace(auth=PrincipalClient(actor), transaction=CachedClient('transaction', actor), analysis=CachedClient('analysis', actor), case=CachedClient('case', actor))

class ReliableAuth(AuthAPIClient):

    def request(self, method, path, token=None, body=None):
        from backend.common.data import DomainError
        attempts = 3 if method == 'GET' else 1
        for attempt in range(1, attempts + 1):
            try:
                result = super().request(method, path, token, body)
                log('auth.request', method=method, path=path, attempt=attempt, status='OK')
                return result
            except DomainError as exc:
                log('auth.request.error', path=path, attempt=attempt, code=exc.code)
                if exc.code != 'DEPENDENCY_UNAVAILABLE' or attempt == attempts:
                    raise
                time.sleep(0.1 * attempt)

@asynccontextmanager
async def lifespan(app):
    app.state.container = Container()
    app.state.auth_api = ReliableAuth(os.environ['AUTH_URL'])
    app.state.kafka_lag = LagMonitor(os.getenv("KAFKA_BOOTSTRAP"))
    app.state.kafka_lag.start()
    try:
        yield
    finally:
        app.state.kafka_lag.close()
app = FastAPI(title='Bad Trans — анализ банковских транзакций', lifespan=lifespan)

@app.middleware('http')
async def correlated(request, call_next):
    try:
        correlation = str(UUID(request.headers.get('X-Correlation-ID', '')))
    except ValueError:
        correlation = str(uuid4())
    token = trace_id.set(correlation)
    start = time.monotonic()
    try:
        response = await call_next(request)
        response.headers['X-Correlation-ID'] = correlation
        log('graphql.request', path=request.url.path, status=response.status_code, elapsedMs=round((time.monotonic() - start) * 1000))
        return response
    finally:
        trace_id.reset(token)
app.include_router(GraphQLRouter(schema, context_getter=context), prefix='/graphql')

@app.get('/schema.graphql', response_class=PlainTextResponse)
def sdl():
    return schema.as_str()

@app.get('/health')
def health():
    from backend.gateway.health import collect_health
    value = collect_health(len(schema._schema.query_type.fields), len(schema._schema.mutation_type.fields))
    return JSONResponse(value, status_code=200 if value['allReady'] else 503)

@app.get('/livez')
def livez():
    return {'application':'bad_trans','alive':True}


from backend.gateway.downloads import download_report
app.add_api_route('/report-files/{report_id}', download_report, methods=['POST'])
