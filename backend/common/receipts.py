import hashlib
import json
from psycopg.types.json import Jsonb
from backend.common.data import DomainError
from backend.common.commands import required_text
from backend.common.events import ident
from backend.infrastructure.resilience import log

def receipt(c, actor, name, key, payload, perform):
    key = required_text(key, 'idempotencyKey', 128)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'{actor.id}:{name}:{key}',))
    old = c.execute('SELECT * FROM command_receipts WHERE actor_id=%s AND command=%s AND idempotency_key=%s', (actor.id, name, key)).fetchone()
    if old:
        if old['request_hash'] != digest:
            raise DomainError('CONFLICT', 'Ключ уже использован с другим содержимым')
        return old['response']
    result = perform(c)
    c.execute('INSERT INTO command_receipts VALUES (%s,%s,%s,%s,%s,%s,now())', (ident(), actor.id, name, key, digest, Jsonb(result)))
    log('business.completed', operation=name, result=result)
    return result
