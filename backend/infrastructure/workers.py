import sys
from backend.infrastructure import delivery
"""Bounded processing attempts and durable parking of failed messages."""
from pathlib import Path
import base64
import json
import os
import threading
import time
import psycopg
from psycopg.rows import dict_row
from confluent_kafka import Producer, Consumer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from backend.common.data import DomainError
from backend.services.analysis.events.transactions import AnalysisEventService
from backend.services.case.events.analysis import CaseEventService
from backend.services.analysis.feedback.events import FeedbackEventService
from backend.common.events import utc
from backend.infrastructure.clients import EventTransactionClient
from backend.services.case.notifications import notification_job, recipients
from backend.infrastructure.resilience import log, trace_id
SERVICE=os.environ['SERVICE_NAME']
ROOT=Path(__file__).resolve().parents[2]

def connect():
    return psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=2,
                          options='-c statement_timeout=10000')

def initialize():
    delivery.initialize(sys.modules[__name__])
    with connect() as c:
        if SERVICE in ('analysis','case'):
            c.execute('''CREATE TABLE IF NOT EXISTS event_failures (
                id UUID PRIMARY KEY,topic TEXT NOT NULL,partition_id INT NOT NULL,offset_id BIGINT NOT NULL,
                payload BYTEA NOT NULL,error TEXT NOT NULL,attempts INT NOT NULL,status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),UNIQUE(topic,partition_id,offset_id))''')
        if SERVICE=='case':
            c.execute('''CREATE TABLE IF NOT EXISTS notification_jobs (
                id UUID PRIMARY KEY,case_id UUID NOT NULL REFERENCES review_cases(id),type TEXT NOT NULL,
                assignee_id UUID,created_at TIMESTAMPTZ NOT NULL,published_at TIMESTAMPTZ)''')
            c.execute('ALTER TABLE notification_jobs ADD COLUMN IF NOT EXISTS correlation_id UUID')

def producer():
    return Producer({'bootstrap.servers':os.environ['KAFKA_BOOTSTRAP'],'enable.idempotence':True,
                     'message.timeout.ms':1800,'socket.timeout.ms':1000})

def send(p,topic,key,payload):
    errors=[]
    p.produce(topic,key=key,value=json.dumps(payload,default=str).encode(),
              on_delivery=lambda error,message:errors.append(str(error)) if error else None)
    if p.flush(2.2) or errors:raise RuntimeError('Kafka delivery unavailable')

def outbox_loop():
    p=producer()
    while True:
        try:
            with connect() as c:
                rows=c.execute('SELECT * FROM outbox_events WHERE published_at IS NULL ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 10').fetchall()
                for row in rows:
                    event=row['payload'];token=trace_id.set(event['correlation_id'])
                    try:
                        c.execute('UPDATE outbox_events SET attempts=attempts+1 WHERE id=%s',(row['id'],))
                        send(p,row['event_type'],str(row['aggregate_id']),event)
                        c.execute('UPDATE outbox_events SET published_at=now() WHERE id=%s',(row['id'],))
                        log('kafka.published',eventId=str(row['id']),topic=row['event_type'])
                    finally:trace_id.reset(token)
        except Exception as exc:
            # A new bounded delivery cycle retries durable pending records after recovery.
            log('kafka.outbox.error',error=type(exc).__name__,action='keep durable outbox pending')
        time.sleep(0.5)

def parking_loop():
    p=producer()
    while True:
        try:
            admin=AdminClient({'bootstrap.servers':os.environ['KAFKA_BOOTSTRAP']})
            for future in admin.create_topics([NewTopic('bank.dead-letter.v1',1,1)],request_timeout=2).values():
                try:future.result(timeout=3)
                except Exception:pass
            with connect() as c:
                for row in c.execute("SELECT * FROM event_failures WHERE status='PENDING' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 10").fetchall():
                    payload={'failureId':str(row['id']),'sourceTopic':row['topic'],'partition':row['partition_id'],
                             'offset':row['offset_id'],'attempts':row['attempts'],'error':row['error'],
                             'originalBase64':base64.b64encode(bytes(row['payload'])).decode()}
                    send(p,'bank.dead-letter.v1',str(row['id']),payload)
                    c.execute("UPDATE event_failures SET status='PUBLISHED' WHERE id=%s",(row['id'],))
                    log('kafka.dead_letter',failureId=str(row['id']),topic=row['topic'],attempts=row['attempts'])
        except Exception as exc:log('kafka.parking.error',error=type(exc).__name__)
        time.sleep(2)

def consume_loop():
    delivery.consume_loop(sys.modules[__name__])


def retry_loop():
    delivery.retry_loop(sys.modules[__name__])


def notifications_loop():
    while True:
        try:
            users=recipients()
            with connect() as c:
                jobs=c.execute('SELECT * FROM notification_jobs WHERE published_at IS NULL ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 20').fetchall()
                for job in jobs:
                    token=trace_id.set(str(job['correlation_id']) if job.get('correlation_id') else str(job['id']))
                    targets={u['id'] for u in users if u['role']=='MANAGER' or u['id']==str(job['assignee_id'])}
                    for user in targets:
                        c.execute('INSERT INTO notifications VALUES (%s,%s,%s,%s,%s,NULL,now()) ON CONFLICT (recipient_id,source_event_id,type) DO NOTHING',
                                  (str(__import__('uuid').uuid4()),job['case_id'],user,job['type'],job['id']))
                    c.execute('UPDATE notification_jobs SET published_at=now() WHERE id=%s',(job['id'],))
                    log('notification.delivered',caseId=str(job['case_id']),type=job['type'],recipientCount=len(targets))
                    trace_id.reset(token)
        except Exception as exc:log('notification.pending',error=type(exc).__name__)
        time.sleep(1)

def after_process(conn, event):
    if SERVICE == 'case':
        row = conn.execute('SELECT id,assignee_id FROM review_cases WHERE transaction_id=%s',
                           (event['payload']['transaction_id'],)).fetchone()
        if row:
            notification_job(conn, str(row['id']), 'ANALYSIS_COMPLETED', event['event_id'],
                             str(row['assignee_id']) if row['assignee_id'] else None)


def feedback_bootstrap_loop():
    from backend.common.commands import WritableDatabase
    from backend.services.case.lifecycle.feedback import CaseFeedback
    db=WritableDatabase(os.environ['DATABASE_URL'],'case')
    while True:
        try:
            count=CaseFeedback.bootstrap(db)
            if count:log('feedback.bootstrap',staged=count)
        except Exception as exc:log('feedback.bootstrap.retry',error=type(exc).__name__)
        time.sleep(2)
