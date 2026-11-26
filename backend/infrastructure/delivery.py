"""Durable at-least-once delivery shared by the two laboratory stages.

An offset is acknowledged only after the business transaction or a durable retry
record commits. Inbox and business changes share a transaction and event lock.
"""
import json
from pathlib import Path
from uuid import uuid4

import grpc
import psycopg
from confluent_kafka import TopicPartition
from jsonschema import Draft202012Validator, FormatChecker

from backend.common.data import DomainError

WORKER_TOPICS = {
    "analysis": ("transaction.registered.v1", "case.feedback.v1"),
    "case": ("analysis.completed.v1",),
}


def consumer_group_id(service, module_name="backend.infrastructure.workers"):
    if module_name.startswith("backend.infrastructure"):
        return f"bad_trans-{service}"
    return f"bank-{service}-v1"


def transient(error):
    if isinstance(error, DomainError):
        return error.code == 'DEPENDENCY_UNAVAILABLE'
    if isinstance(error, grpc.RpcError):
        return error.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED)
    return isinstance(error, (psycopg.OperationalError,
                              psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected))


def initialize(module):
    if module.SERVICE not in ('analysis', 'case'):
        return
    with module.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS event_failures (
            id UUID PRIMARY KEY, topic TEXT NOT NULL, partition_id INT NOT NULL,
            offset_id BIGINT NOT NULL, payload BYTEA NOT NULL, error TEXT NOT NULL,
            attempts INT NOT NULL, status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(topic,partition_id,offset_id))''')
        conn.execute('ALTER TABLE event_failures ADD COLUMN IF NOT EXISTS '
                     'next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now()')


def inputs(module):
    handler = (module.AnalysisEventService(module.EventTransactionClient())
               if module.SERVICE == 'analysis' else module.CaseEventService())
    topic = WORKER_TOPICS[module.SERVICE][0]
    schema = Path(module.ROOT) / 'backend/contracts/events' / f'{topic}.schema.json'
    validator = Draft202012Validator(json.loads(schema.read_text()), format_checker=FormatChecker())
    return handler, topic, validator


def routes(module):
    """Optional multi-topic routing; historical single-topic adapters still work."""
    handler,topic,validator=inputs(module)
    result={topic:(handler,validator)}
    if module.SERVICE=='analysis' and hasattr(module,'FeedbackEventService'):
        feedback = WORKER_TOPICS["analysis"][1]
        path = Path(module.ROOT) / 'backend/contracts/events' / f'{feedback}.schema.json'
        result[feedback]=(module.FeedbackEventService(),Draft202012Validator(json.loads(path.read_text(encoding='utf-8')),format_checker=FormatChecker()))
    return result


def process(module, conn, handler, topic, validator, raw):
    event = json.loads(raw)
    validator.validate(event)
    trace = getattr(module, 'trace_id', None)
    token = trace.set(event['correlation_id']) if trace else None
    try:
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (event['event_id'],))
        old = conn.execute('SELECT 1 FROM inbox_events WHERE event_id=%s', (event['event_id'],)).fetchone()
        if not old:
            handler.process(conn, event)
            if hasattr(module, 'after_process'):
                module.after_process(conn, event)
            conn.execute('INSERT INTO inbox_events VALUES (%s,%s,now())', (event['event_id'], topic))
        elif hasattr(handler,'verify_duplicate'):
            handler.verify_duplicate(conn,event)
        module.log('kafka.consumed', topic=topic, eventId=event['event_id'], duplicate=bool(old))
        return event
    finally:
        if trace:
            trace.reset(token)


def consume_loop(module):
    dispatch=routes(module);primary=next(iter(dispatch))
    consumer = module.Consumer({
        'bootstrap.servers': module.os.environ['KAFKA_BOOTSTRAP'],
        'group.id': consumer_group_id(module.SERVICE, module.__name__),
        'auto.offset.reset': 'earliest', 'enable.auto.commit': False,
        'max.poll.interval.ms': 300000,
    })
    consumer.subscribe(list(dispatch))
    while True:
        try:
            msg = consumer.poll(1)
        except Exception as error:
            module.log('kafka.poll.error', error=type(error).__name__)
            module.time.sleep(1)
            continue
        if msg is None:
            continue
        if msg.error():
            module.log('kafka.broker.error', error=str(msg.error()))
            continue
        error = None
        topic=msg.topic() if hasattr(msg,'topic') else primary
        handler,validator=dispatch[topic]
        attempt = 0
        for attempt in range(1, 4):
            try:
                with module.connect() as conn:
                    process(module, conn, handler, topic, validator, msg.value())
                error = None
                break
            except Exception as caught:
                error = caught
                module.log('kafka.processing.error', topic=topic, offset=msg.offset(),
                           attempt=attempt, error=type(caught).__name__)
                if not transient(caught):
                    break
                module.time.sleep(0.15 * attempt)
        try:
            if error is not None:
                status = 'RETRY_PENDING' if transient(error) else 'PENDING'
                with module.connect() as conn:
                    conn.execute('''INSERT INTO event_failures
                        (id,topic,partition_id,offset_id,payload,error,attempts,status,next_attempt_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()+interval '2 seconds')
                        ON CONFLICT (topic,partition_id,offset_id) DO NOTHING''',
                        (str(uuid4()), topic, msg.partition(), msg.offset(), msg.value(),
                         type(error).__name__ + ': ' + str(error)[:300], attempt, status))
                module.log('kafka.parked', topic=topic, offset=msg.offset(), status=status)
            consumer.commit(message=msg, asynchronous=False)
        except Exception as caught:
            module.log('kafka.commit.error', error=type(caught).__name__)
            # A polled but uncommitted message must not be skipped in this process.
            try:
                consumer.seek(TopicPartition(topic, msg.partition(), msg.offset()))
            except Exception:
                consumer.assign([TopicPartition(topic, msg.partition(), msg.offset())])
            module.time.sleep(1)


def retry_loop(module):
    dispatch=routes(module);primary=next(iter(dispatch))
    while True:
        try:
            # Process at most one record per transaction/cycle. Transient outages
            # retain durable state without a tight infinite retry on one partition.
            if getattr(module, 'manual_failure_reviews', False):
                from backend.infrastructure.failure_review import retry_one
                with module.connect() as connection:
                    retry_one(module, connection, dispatch, process)
            with module.connect() as conn:
                row = conn.execute("SELECT * FROM event_failures WHERE status='RETRY_PENDING' "
                                   'AND next_attempt_at<=now() ORDER BY next_attempt_at '
                                   'FOR UPDATE SKIP LOCKED LIMIT 1').fetchone()
                if row:
                    try:
                        topic=row.get('topic',primary)
                        if topic not in dispatch:raise DomainError('BAD_INPUT','Unsupported saved event topic')
                        handler,validator=dispatch[topic]
                        with conn.transaction():
                            event = process(module, conn, handler, topic, validator, bytes(row['payload']))
                        conn.execute("UPDATE event_failures SET status='RESOLVED', attempts=attempts+1 "
                                     'WHERE id=%s', (row['id'],))
                        module.log('kafka.recovered', failureId=str(row['id']), topic=topic,
                                   correlationId=event['correlation_id'])
                    except Exception as error:
                        status = 'RETRY_PENDING' if transient(error) else 'PENDING'
                        conn.execute("UPDATE event_failures SET status=%s, attempts=attempts+1, "
                                     "error=%s,next_attempt_at=now()+interval '5 seconds' WHERE id=%s",
                                     (status, type(error).__name__ + ': ' + str(error)[:300], row['id']))
        except Exception as error:
            module.log('kafka.retry.error', error=type(error).__name__)
        module.time.sleep(1)
