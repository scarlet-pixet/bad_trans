"""Stable public errors shared by the real gRPC transports of LR6 and LR7."""
from functools import wraps
import logging

import grpc
import psycopg

from backend.common.data import DomainError


DOMAIN_STATUS = {
    "BAD_INPUT": grpc.StatusCode.INVALID_ARGUMENT,
    "CONFLICT": grpc.StatusCode.FAILED_PRECONDITION,
    "NOT_FOUND": grpc.StatusCode.NOT_FOUND,
    "FORBIDDEN": grpc.StatusCode.PERMISSION_DENIED,
    "UNAUTHENTICATED": grpc.StatusCode.UNAUTHENTICATED,
    "DEPENDENCY_UNAVAILABLE": grpc.StatusCode.UNAVAILABLE,
    "DATA_ACCESS_ERROR": grpc.StatusCode.INTERNAL,
}
STATUS_DOMAIN = {
    grpc.StatusCode.INVALID_ARGUMENT: "BAD_INPUT",
    grpc.StatusCode.FAILED_PRECONDITION: "CONFLICT",
    grpc.StatusCode.ALREADY_EXISTS: "CONFLICT",
    grpc.StatusCode.NOT_FOUND: "NOT_FOUND",
    grpc.StatusCode.PERMISSION_DENIED: "FORBIDDEN",
    grpc.StatusCode.UNAUTHENTICATED: "UNAUTHENTICATED",
    grpc.StatusCode.INTERNAL: "DATA_ACCESS_ERROR",
}


def abort_error(context, error):
    """Do not expose SQL, connection strings or internal Python diagnostics."""
    if isinstance(error, DomainError):
        context.abort(DOMAIN_STATUS.get(error.code, grpc.StatusCode.INTERNAL), str(error))
    if isinstance(error, psycopg.errors.UniqueViolation):
        context.abort(grpc.StatusCode.ALREADY_EXISTS, "Объект уже существует")
    if isinstance(error, psycopg.OperationalError):
        context.abort(grpc.StatusCode.UNAVAILABLE, "База данных недоступна")
    if isinstance(error, psycopg.Error):
        logging.error("gRPC storage failure: %s", type(error).__name__)
        context.abort(grpc.StatusCode.INTERNAL, "Ошибка хранения данных")
    if isinstance(error, (ValueError, TypeError, KeyError, AttributeError)):
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Некорректные параметры запроса")
    logging.error("gRPC operation failed: %s", type(error).__name__)
    context.abort(grpc.StatusCode.INTERNAL, "Ошибка выполнения операции")


def rpc_guard(handler):
    """Guard a synchronous unary servicer method, including input decoding."""
    @wraps(handler)
    def guarded(self, request, context):
        try:
            return handler(self, request, context)
        except Exception as error:
            # context.abort already selected the public error status.
            if context.code() is not None:
                raise
            abort_error(context, error)
    return guarded


def client_error(error):
    code = STATUS_DOMAIN.get(error.code(), "DEPENDENCY_UNAVAILABLE")
    message = error.details() or error.code().name
    return DomainError(code, message)
