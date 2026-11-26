"""Bounded transport retries, deadlines and correlated JSON logs."""
from collections import namedtuple
from contextvars import ContextVar
import json
import logging
import os
import time
from uuid import uuid4

import grpc

trace_id = ContextVar('correlation_id', default=None)

def current_trace():
    return trace_id.get() or str(uuid4())

def configure_logging():
    logging.basicConfig(level=logging.INFO, format='%(message)s', force=True)

def log(event, **fields):
    logging.info(json.dumps({'event': event, 'service': os.getenv('SERVICE_NAME','gateway'),
                             'correlationId': current_trace(), **fields}, ensure_ascii=False, default=str))

Details = namedtuple('Details', 'method timeout metadata credentials wait_for_ready compression')
HISTORY_PREPARE_TIMEOUT = 30
HISTORY_CALCULATION_TIMEOUT = 60

def export_timeout():
    try:
        return max(5.0, min(45.0, float(os.getenv('REPORT_EXPORT_TIMEOUT_SECONDS', '30'))))
    except ValueError:
        return 30.0


class Retry(grpc.UnaryUnaryClientInterceptor):
    def __init__(self, owner):
        self.owner = owner

    def intercept_unary_unary(self, continuation, details, request):
        count = min(3, max(1, int(os.getenv('RPC_RETRY_ATTEMPTS','3'))))
        operation = details.method.rsplit('/',1)[-1]
        reads = operation.startswith(('Get','Search','List','Compare'))
        if operation == 'Invoke':
            reads = request.method == 'health' or request.method.startswith(
                ('get_', 'search_', 'list_', 'compare_', 'export_', 'transaction_summary'))
        # A timeout can arrive after a successful commit. Replay only commands
        # whose owner can identify the same operation, or an intrinsically safe update.
        keyed = bool(getattr(request, 'idempotency_key', '') or
                     getattr(getattr(request, 'input', None), 'idempotency_key', ''))
        if operation == 'Invoke' and not reads:
            try:
                arguments = json.loads(request.arguments_json or b'{}')
                keyed = bool(arguments.get('idempotencyKey') or
                             arguments.get('input', {}).get('idempotencyKey'))
            except (ValueError, TypeError, AttributeError):
                keyed = False
        if not reads and not keyed and operation != 'MarkNotificationRead':
            count = 1
        deadline = float(os.getenv('RPC_READ_TIMEOUT','0.8')) if reads else 8.0
        if operation == 'Invoke' and request.method == 'export_snapshot_report':
            deadline = export_timeout()
            count = 1
        if operation == 'Invoke' and request.method == 'prepare_behavior_history':
            deadline = HISTORY_PREPARE_TIMEOUT
        if operation in ('StartProfile', 'StartAnalysis'):
            deadline = HISTORY_CALCULATION_TIMEOUT
        deadline = min(deadline, details.timeout or deadline)
        metadata = list(details.metadata or [])
        if not any(k == 'x-correlation-id' for k,v in metadata):
            metadata.append(('x-correlation-id', current_trace()))
        changed = Details(details.method, deadline, metadata, details.credentials,
                          details.wait_for_ready, details.compression)
        for attempt in range(1,count+1):
            started=time.monotonic()
            response = continuation(changed, request)
            status = response.code()
            log('grpc.client', target=self.owner, method=operation, attempt=attempt,
                timeoutSeconds=deadline, elapsedMs=round((time.monotonic()-started)*1000), status=status.name,
                correlationId=dict(metadata)['x-correlation-id'])
            if status not in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED) or attempt == count:
                return response
            time.sleep(0.1 * 2**(attempt-1))

def channel_for(address, owner):
    from backend.infrastructure.tls import grpc_channel
    return grpc.intercept_channel(grpc_channel(address), Retry(owner))

class ServerLogging(grpc.ServerInterceptor):
    def __init__(self):
        self.once = set()

    def intercept_service(self, continuation, details):
        handler = continuation(details)
        if handler is None or handler.unary_unary is None:
            return handler
        def call(request, context):
            values = dict(context.invocation_metadata())
            correlation = values.get('x-correlation-id') or str(uuid4())
            token = trace_id.set(correlation)
            started=time.monotonic()
            method=details.method.rsplit('/',1)[-1]
            try:
                # Explicitly gated, authenticated fault injection for reproducible lab checks.
                if os.getenv('ENABLE_TEST_FAULTS') == '1' and values.get('x-service-token') == os.getenv('SERVICE_TOKEN'):
                    fault=values.get('x-test-fault')
                    if fault == 'unavailable-once' and correlation not in self.once:
                        self.once.add(correlation)
                        context.abort(grpc.StatusCode.UNAVAILABLE,'Injected transient fault')
                    if fault == 'delay':
                        time.sleep(1.2)
                        if not context.is_active():
                            context.abort(grpc.StatusCode.DEADLINE_EXCEEDED,'Injected slow dependency')
                result=handler.unary_unary(request, context)
                log('grpc.server', method=method, status='OK', elapsedMs=round((time.monotonic()-started)*1000))
                return result
            except Exception as exc:
                status=context.code()
                log('grpc.server.error', method=method, status=status.name if status else type(exc).__name__)
                raise
            finally:
                trace_id.reset(token)
        return grpc.unary_unary_rpc_method_handler(call, request_deserializer=handler.request_deserializer,
                                                   response_serializer=handler.response_serializer)
