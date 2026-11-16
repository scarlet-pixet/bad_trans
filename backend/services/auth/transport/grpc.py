"""The complete bank.v1 Auth adapter delegates to the same security service as REST."""
import os

import grpc
from google.protobuf.json_format import MessageToDict, ParseDict
import psycopg
import redis

from backend.common.data import DomainError
from backend.generated import bank_pb2 as bank, bank_pb2_grpc as rpc
from backend.services.auth.service import AuthFailure, public_user


class AuthGrpcService(rpc.AuthServiceServicer):
    def __init__(self, security):
        self.security = security

    def _execute(self, context, action):
        if dict(context.invocation_metadata()).get('x-service-token') != os.environ['SERVICE_TOKEN']:
            context.abort(grpc.StatusCode.PERMISSION_DENIED, 'Internal service token required')
        try:
            return action()
        except AuthFailure as exc:
            status = grpc.StatusCode.PERMISSION_DENIED if exc.code == 'FORBIDDEN' else grpc.StatusCode.UNAUTHENTICATED
            context.abort(status, str(exc))
        except DomainError as exc:
            status = {'BAD_INPUT': grpc.StatusCode.INVALID_ARGUMENT, 'NOT_FOUND': grpc.StatusCode.NOT_FOUND,
                      'FORBIDDEN': grpc.StatusCode.PERMISSION_DENIED,
                      'CONFLICT': grpc.StatusCode.FAILED_PRECONDITION}.get(exc.code, grpc.StatusCode.UNAVAILABLE)
            context.abort(status, str(exc))
        except psycopg.errors.UniqueViolation:
            context.abort(grpc.StatusCode.ALREADY_EXISTS, 'Staff login already exists')
        except (psycopg.Error, redis.RedisError):
            context.abort(grpc.StatusCode.UNAVAILABLE, 'Auth persistence is unavailable')

    def Login(self, request, context):
        return self._execute(context, lambda: ParseDict(
            {'data': self.security.sign_in(request.login, request.password)}, bank.LoginResponse()))

    def GetCurrentUser(self, request, context):
        return self._execute(context, lambda: ParseDict(
            {'data': public_user(self.security.validate(request.access_token)[0])}, bank.GetCurrentUserResponse()))

    def ValidateSession(self, request, context):
        return self._execute(context, lambda: ParseDict(
            {'data': public_user(self.security.validate(request.access_token)[0])}, bank.ValidateSessionResponse()))

    def Logout(self, request, context):
        return self._execute(context, lambda: bank.LogoutResponse(data=self.security.sign_out(request.access_token)))

    def RegisterStaff(self, request, context):
        def action():
            actor, _ = self.security.validate(request.access_token)
            values = MessageToDict(request.input, preserving_proto_field_name=False)
            return ParseDict({'data': self.security.register_staff(actor, values)}, bank.RegisterStaffResponse())
        return self._execute(context, action)

    def BlockStaff(self, request, context):
        def action():
            actor, _ = self.security.validate(request.access_token)
            return ParseDict({'data': self.security.block_staff(actor, request.user_id, request.idempotency_key)}, bank.BlockStaffResponse())
        return self._execute(context, action)

    def ChangeStaffRole(self, request, context):
        def action():
            actor, _ = self.security.validate(request.access_token)
            return ParseDict({'data': self.security.change_staff_role(actor, request.user_id, request.role_code, request.idempotency_key)}, bank.ChangeStaffRoleResponse())
        return self._execute(context, action)

    def ListActiveAnalysts(self, request, context):
        def action():
            rows = self.security.repository.db.query(
                "SELECT u.id::text AS id,u.login,r.code AS role,u.is_active FROM users u "
                "JOIN roles r ON r.id=u.role_id WHERE u.is_active AND r.code='ANALYST' ORDER BY u.id")
            return ParseDict({'data': rows}, bank.ListActiveAnalystsResponse())
        return self._execute(context, action)

    def Health(self, request, context):
        def action():
            database = self.security.repository.health()
            ready = self.security.redis.ping()
            return bank.HealthResponse(service='auth', database=database['database'],
                                       table_count=database['tables'], status='READY' if ready else 'UNAVAILABLE')
        return self._execute(context, action)
