from backend.common.data import uuid, period, currency, money, DomainError
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from backend.common.data import DomainError, currency, money, period, uuid
from backend.common.commands import require_role, required_text
from backend.common.time_precision import observation_time

from backend.services.transaction.history.queries import TransactionQueries
from backend.services.transaction.ingest.transfer import normalize_transfer, save_transfer

class TransactionService(TransactionQueries):

    def __init__(self, repository):
        self.repository = repository





    def get_operation_delivery(self, actor, transactionId):
        require_role(actor, "MANAGER", "ANALYST")
        tx = uuid(transactionId)
        rows = self.repository.db.query(
            """SELECT t.id AS transaction_id,o.id,o.published_at
            FROM transactions t LEFT JOIN outbox_events o
            ON o.event_type='transaction.registered.v1'
            AND o.payload #>> '{payload,transaction_id}'=%s
            AND o.payload->>'event_id'=o.id::text
            WHERE t.id=%s LIMIT 2""",
            (tx, tx),
        )
        if not rows:
            raise DomainError("NOT_FOUND", "Операция не найдена")
        if len(rows) != 1 or rows[0]["id"] is None:
            return {"eventId": None, "queued": False}
        return {"eventId": str(rows[0]["id"]), "queued": rows[0]["published_at"] is None}

    def create_client(self, actor, input):
        require_role(actor, 'MANAGER', 'ADMIN')
        external = required_text(input['externalId'], 'externalId', 64)
        display = required_text(input['displayName'], 'displayName', 160)
        segment = input.get('segment')
        if segment is not None:
            segment = required_text(segment, 'segment', 50)
        payload = {'externalId': external, 'displayName': display, 'segment': segment}

        def perform(conn):
            value = str(uuid4())
            conn.execute('INSERT INTO bank_clients(id,external_id,display_name,segment,is_active) VALUES (%s,%s,%s,%s,true)', (value, external, display, segment))
            return value
        result = self.repository.execute(actor, 'createClient', input['idempotencyKey'], payload, perform)
        return self.repository.client(result)

    def open_account(self, actor, input):
        require_role(actor, 'MANAGER', 'ADMIN')
        client_id = uuid(input['clientId'])
        external = required_text(input['externalId'], 'externalId', 64)
        code = currency(input['currency'])
        payload = {'clientId': client_id, 'externalId': external, 'currency': code}

        def perform(conn):
            client = conn.execute('SELECT is_active FROM bank_clients WHERE id=%s', (client_id,)).fetchone()
            if not client:
                raise DomainError('NOT_FOUND', 'Клиент не найден')
            if not client['is_active']:
                raise DomainError('CONFLICT', 'Клиент заблокирован')
            value = str(uuid4())
            conn.execute('INSERT INTO accounts(id,client_id,external_id,currency,status) VALUES (%s,%s,%s,%s,%s)', (value, client_id, external, code, 'OPEN'))
            return value
        result = self.repository.execute(actor, 'openAccount', input['idempotencyKey'], payload, perform)
        return self.repository.account(result)

    def register_counterparty(self, actor, input):
        require_role(actor, 'MANAGER', 'ADMIN')
        external = required_text(input['externalId'], 'externalId', 64)
        display = required_text(input['displayName'], 'displayName', 160)
        category = input.get('category')
        if category is not None:
            category = required_text(category, 'category', 50)
        payload = {'externalId': external, 'displayName': display, 'category': category}

        def perform(conn):
            value = str(uuid4())
            conn.execute('INSERT INTO counterparties(id,external_id,display_name,category) VALUES (%s,%s,%s,%s)', (value, external, display, category))
            return value
        result = self.repository.execute(actor, 'registerCounterparty', input['idempotencyKey'], payload, perform)
        return self.repository.counterparty(result)

    def get_operation_transfer(self, actor, transactionId):
        require_role(actor, "MANAGER", "ANALYST", "ADMIN")
        tx = uuid(transactionId)
        row = self.repository.db.one(
            """SELECT p.id,p.namespace,p.external_ref,p.parties,p.owner_relation,p.owner_evidence
            FROM operation_transfer_entries e JOIN operation_transfers p ON p.id=e.transfer_id
            WHERE e.transaction_id=%s""", (tx,),
        )
        if not row:
            return None
        return {
            "transferId": str(row["id"]),
            "transferNamespace": row["namespace"],
            "transferRef": row["external_ref"],
            "debit": row["parties"]["debit"],
            "credit": row["parties"]["credit"],
            "ownerRelation": row["owner_relation"],
            "ownerEvidence": row["owner_evidence"],
        }

    def register_transfer(self, actor, input):
        require_role(actor, "MANAGER", "ADMIN")
        return self.register_transaction(actor, input["transaction"], transfer=input)

    def register_transaction(self, actor, input, *, transfer=None):
        require_role(actor, 'MANAGER', 'ANALYST', 'ADMIN')
        account_id = uuid(input['accountId'])
        counterparty_id = uuid(input['counterpartyId']) if input.get('counterpartyId') else None
        source = required_text(input['source'], 'source', 32)
        external = required_text(input['externalId'], 'externalId', 100)
        code = currency(input['currency'])
        direction = input['direction']
        if direction not in ('IN', 'OUT'):
            raise DomainError('BAD_INPUT', 'Некорректное направление')
        try:
            amount = Decimal(str(input['amount']))
            if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2 or (amount > Decimal('9999999999999999.99')):
                raise ValueError()
        except (ValueError, InvalidOperation):
            raise DomainError('BAD_INPUT', 'Сумма должна быть положительной с точностью до копеек') from None
        occurred, precision = observation_time(source, input['occurredAt'], input.get('timePrecision'))
        payload = {'source': source, 'externalId': external, 'accountId': account_id, 'counterpartyId': counterparty_id, 'amount': str(amount), 'currency': code, 'direction': direction, 'occurredAt': occurred.isoformat()}

        if input.get("timePrecision") or (isinstance(input["occurredAt"], str) and len(input["occurredAt"]) == 10):
            payload["timePrecision"] = precision
        if transfer is not None:
            transfer = normalize_transfer(transfer, account_id, direction)
            payload["transfer"] = transfer

        def perform(conn):
            account = conn.execute('SELECT a.currency,a.status,c.is_active FROM accounts a JOIN bank_clients c ON c.id=a.client_id WHERE a.id=%s', (account_id,)).fetchone()
            if not account:
                raise DomainError('NOT_FOUND', 'Счёт не найден')
            if account['status'] != 'OPEN' or not account['is_active']:
                raise DomainError('CONFLICT', 'Счёт или клиент недоступен')
            if account['currency'] != code:
                raise DomainError('BAD_INPUT', 'Валюта операции не совпадает с валютой счёта')
            if counterparty_id and (not conn.execute('SELECT 1 FROM counterparties WHERE id=%s', (counterparty_id,)).fetchone()):
                raise DomainError('NOT_FOUND', 'Контрагент не найден')
            value = str(uuid4())
            conn.execute('INSERT INTO transactions (id,source,external_id,account_id,counterparty_id,batch_id,amount,currency,direction,occurred_at,ingested_at,time_precision) VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,now(),%s)', (value, source, external, account_id, counterparty_id, amount, code, direction, occurred, precision))
            if transfer is not None:
                save_transfer(conn, transfer, value, direction)
            return value
        result = self.repository.execute(actor, 'registerTransaction', input['idempotencyKey'], payload, perform)
        return self.repository.transaction(result)
