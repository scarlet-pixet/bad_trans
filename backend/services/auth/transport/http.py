"""Independent Auth REST and gRPC adapters sharing one security service."""
from contextlib import asynccontextmanager
import os
import psycopg
import redis
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from backend.common.data import DomainError
from backend.services.auth.persistence.repository import AuthRepository
from backend.services.auth.service import AuthFailure, AuthService, public_user
from concurrent.futures import ThreadPoolExecutor
import grpc
from backend.generated import bank_pb2_grpc as rpc
from backend.services.auth.transport.grpc import AuthGrpcService
from backend.infrastructure.resilience import ServerLogging, configure_logging
from fastapi import Request, HTTPException

class Credentials(BaseModel):
    login: str
    password: str

class StaffPayload(BaseModel):
    login: str
    password: str
    roleCode: str
    idempotencyKey: str

class CommandKey(BaseModel):
    idempotencyKey: str

class RolePayload(CommandKey):
    roleCode: str

@asynccontextmanager
async def lifespan(app):
    configure_logging()
    repository = AuthRepository(os.environ['AUTH_DATABASE_URL'])
    app.state.security = AuthService(repository, os.environ['SESSION_REDIS_URL'], os.environ['JWT_SECRET'], int(os.getenv('SESSION_TTL', '1800')))
    server = grpc.server(ThreadPoolExecutor(max_workers=4), interceptors=[ServerLogging()])
    rpc.add_AuthServiceServicer_to_server(AuthGrpcService(app.state.security), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    try:
        yield
    finally:
        server.stop(2)
        app.state.security.redis.close()
app = FastAPI(title='Bad Trans — Auth Service', lifespan=lifespan)

@app.exception_handler(AuthFailure)
async def auth_error(_request, exc):
    return JSONResponse({'code': exc.code, 'message': str(exc)}, status_code=exc.status)

@app.exception_handler(DomainError)
async def domain_error(_request, exc):
    return JSONResponse({'code': exc.code, 'message': str(exc)}, status_code={'BAD_INPUT': 400, 'NOT_FOUND': 404, 'FORBIDDEN': 403, 'CONFLICT': 409, 'DEPENDENCY_UNAVAILABLE': 503, 'DATA_ACCESS_ERROR': 500}.get(exc.code, 400))

@app.exception_handler(psycopg.errors.UniqueViolation)
async def duplicate(_request, _exc):
    return JSONResponse({'code': 'CONFLICT', 'message': 'Логин уже существует'}, status_code=409)

@app.exception_handler(redis.RedisError)
async def redis_unavailable(_request, _exc):
    return JSONResponse({'code': 'DEPENDENCY_UNAVAILABLE', 'message': 'Хранилище сессий недоступно'}, status_code=503)

@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(_request, _exc):
    return JSONResponse({'code': 'DEPENDENCY_UNAVAILABLE', 'message': 'База Auth недоступна'}, status_code=503)

def bearer(authorization):
    if not authorization or not authorization.lower().startswith('bearer '):
        raise AuthFailure('UNAUTHENTICATED', 'Требуется Bearer JWT')
    token = authorization[7:].strip()
    if not token:
        raise AuthFailure('UNAUTHENTICATED', 'Требуется Bearer JWT')
    return token

async def principal(request: Request, authorization: str | None):
    token = bearer(authorization)
    actor, _ = await run_in_threadpool(request.app.state.security.validate, token)
    return (actor, token)

@app.get('/health')
async def health(request: Request):
    from redis.retry import Retry
    from redis.backoff import NoBackoff
    database = None
    redis_ready = False
    try:
        database = await run_in_threadpool(request.app.state.security.repository.health)
    except psycopg.Error:
        pass
    try:
        with redis.Redis.from_url(os.environ['SESSION_REDIS_URL'], socket_connect_timeout=.2,
                                  socket_timeout=.2, retry=Retry(NoBackoff(), 0)) as probe:
            redis_ready = bool(await run_in_threadpool(probe.ping))
    except redis.RedisError:
        pass
    ready = database is not None and redis_ready
    return JSONResponse({'service':'auth','database': database['database'] if database else None,
                         'redisReady':redis_ready,'sessionTtlSeconds':request.app.state.security.ttl,
                         'ready':ready}, status_code=200 if ready else 503)

@app.post('/login')
async def login(values: Credentials, request: Request):
    return await run_in_threadpool(request.app.state.security.sign_in, values.login, values.password)

@app.get('/validate')
async def validate(request: Request, authorization: str | None=Header(default=None)):
    actor, _ = await principal(request, authorization)
    return public_user(actor)

@app.post('/logout')
async def logout(request: Request, authorization: str | None=Header(default=None)):
    _, token = await principal(request, authorization)
    return {'signedOut': await run_in_threadpool(request.app.state.security.sign_out, token)}

@app.post('/staff')
async def register_staff(values: StaffPayload, request: Request, authorization: str | None=Header(default=None)):
    actor, _ = await principal(request, authorization)
    return await run_in_threadpool(request.app.state.security.register_staff, actor, values.model_dump())

@app.get('/staff')
async def list_staff(request: Request, authorization: str | None=Header(default=None)):
    actor, _ = await principal(request, authorization)
    if actor['role'] != 'ADMIN':
        raise DomainError('FORBIDDEN', 'Список сотрудников доступен администратору')
    return await run_in_threadpool(request.app.state.security.repository.db.query,
        'SELECT u.id::text AS id,u.login,u.is_active AS "isActive",r.code AS role FROM users u JOIN roles r ON r.id=u.role_id ORDER BY u.login LIMIT 200')

@app.post('/staff/{user_id}/block')
async def block_staff(user_id: str, values: CommandKey, request: Request, authorization: str | None=Header(default=None)):
    actor, _ = await principal(request, authorization)
    return await run_in_threadpool(request.app.state.security.block_staff, actor, user_id, values.idempotencyKey)

@app.post('/staff/{user_id}/unblock')
async def unblock_staff(user_id: str, values: CommandKey, request: Request, authorization: str | None=Header(default=None)):
    actor, _ = await principal(request, authorization)
    return await run_in_threadpool(request.app.state.security.unblock_staff, actor, user_id, values.idempotencyKey)

@app.post('/staff/{user_id}/role')
async def change_staff_role(user_id: str, values: RolePayload, request: Request, authorization: str | None=Header(default=None)):
    actor, _ = await principal(request, authorization)
    return await run_in_threadpool(request.app.state.security.change_staff_role, actor, user_id, values.roleCode, values.idempotencyKey)

@app.get('/internal/recipients')
def internal_recipients(request: Request):
    if request.headers.get('X-Service-Token') != os.environ['SERVICE_TOKEN']:
        raise HTTPException(status_code=403, detail='Internal service token required')
    rows = request.app.state.security.repository.db.query('SELECT u.id::text AS id,r.code AS role FROM users u JOIN roles r ON r.id=u.role_id WHERE u.is_active ORDER BY u.id')
    return {'users': rows}
