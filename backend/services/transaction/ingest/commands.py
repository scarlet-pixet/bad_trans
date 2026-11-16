from datetime import datetime, timezone, timedelta
from decimal import Decimal
from statistics import median
from uuid import uuid4
import json
import re
import os
import psycopg
from psycopg.types.json import Jsonb
from backend.common.data import DomainError, uuid, period, currency
from backend.common.commands import require_role, required_text
from backend.common.events import utc, ident, make_event, add_outbox
from backend.common.receipts import receipt
from backend.infrastructure.resilience import current_trace, log, channel_for
from backend.services.transaction.ingest.registration import TransactionService

class TransactionCommands(TransactionService):

    def import_transactions(self, actor, input):
        require_role(actor, 'MANAGER', 'ANALYST')
        if not re.fullmatch('[0-9a-f]{64}', input['fileHash']) or not 1 <= len(input['items']) <= 100:
            raise DomainError('BAD_INPUT', 'Требуется SHA-256 и пакет из 1–100 строк')
        payload = {k: v for k, v in input.items() if k != 'idempotencyKey'}

        def perform(c):
            old = c.execute('SELECT * FROM import_batches WHERE file_hash=%s', (input['fileHash'],)).fetchone()
            if old:
                return {'id': str(old['id'])}
            batch = ident()
            c.execute("INSERT INTO import_batches VALUES (%s,%s,'ACCEPTED',%s,0,0,'[]',%s,now())", (batch, input['fileHash'], len(input['items']), actor.id))

            class BoundRepository:

                def execute(self, actor, name, key, payload, action):
                    tx = action(c)
                    c.execute('UPDATE transactions SET batch_id=%s WHERE id=%s', (batch, tx))
                    client = c.execute('SELECT client_id FROM accounts WHERE id=%s', (payload['accountId'],)).fetchone()
                    event = make_event({'transaction_id': tx, 'client_id': str(client['client_id'])})
                    event['correlation_id'] = current_trace()
                    add_outbox(c, 'transaction.registered.v1', str(client['client_id']), event)
                    return tx

                def transaction(self, id):
                    return id
            service = TransactionService(BoundRepository())
            errors = []
            new = 0
            duplicates = 0

            class Rejected(Exception):
                pass
            try:
                with c.transaction():
                    for index, item in enumerate(input['items'], 1):
                        try:
                            with c.transaction():
                                old = c.execute('SELECT * FROM transactions WHERE source=%s AND external_id=%s', (item['source'], item['externalId'])).fetchone()
                                if old:
                                    stamp = datetime.fromisoformat(str(item['occurredAt']).replace('Z', '+00:00'))
                                    if any([str(old['account_id']) != item['accountId'], str(old['counterparty_id'] or '') != str(item.get('counterpartyId') or ''), old['amount'] != Decimal(str(item['amount'])), old['currency'] != item['currency'], old['direction'] != item['direction'], old['occurred_at'] != stamp]):
                                        raise DomainError('CONFLICT', 'Внешний ID уже имеет другое содержимое')
                                    duplicates += 1
                                else:
                                    service.register_transaction(actor, item)
                                    new += 1
                        except (DomainError, ValueError, psycopg.IntegrityError) as exc:
                            errors.append({'row': index, 'code': getattr(exc, 'code', 'BAD_INPUT'), 'message': str(exc)[:180]})
                    if errors:
                        raise Rejected()
            except Rejected:
                new = duplicates = 0
            c.execute('UPDATE import_batches SET status=%s,new_count=%s,duplicate_count=%s,validation_errors=%s WHERE id=%s', ('REJECTED' if errors else 'ACCEPTED', new, duplicates, Jsonb(errors), batch))
            return {'id': batch}
        with self.repository.db.connect() as c:
            result = receipt(c, actor, 'importTransactions', input['idempotencyKey'], payload, perform)
            row = c.execute('SELECT * FROM import_batches WHERE id=%s', (result['id'],)).fetchone()
        return {'batchId': str(row['id']), 'status': row['status'], 'receivedCount': row['received_count'], 'newCount': row['new_count'], 'duplicateCount': row['duplicate_count'], 'errors': row['validation_errors']}
