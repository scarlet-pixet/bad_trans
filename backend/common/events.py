from datetime import datetime, timezone, timedelta
from decimal import Decimal
from statistics import median
from uuid import uuid4
import logging
from psycopg.types.json import Jsonb

def utc():
    return datetime.now(timezone.utc)

def ident():
    return str(uuid4())

def make_event(payload):
    return {'event_id': ident(), 'schema_version': 1, 'occurred_at': utc().isoformat(), 'correlation_id': ident(), 'payload': payload}

def add_outbox(conn, topic, key, event):
    conn.execute('INSERT INTO outbox_events VALUES (%s,%s,%s,%s,%s,NULL,0)', (event['event_id'], key, topic, Jsonb(event), utc()))
