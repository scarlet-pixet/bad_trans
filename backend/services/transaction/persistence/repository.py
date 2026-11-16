from backend.common.data import Repository, model
from backend.services.transaction.persistence.models import Account, Client, Transaction
TX = 'SELECT t.*,a.client_id FROM transactions t JOIN accounts a ON a.id=t.account_id'
from backend.common.data import model
from backend.services.transaction.persistence.models import Account
from backend.common.commands import command
from backend.common.events import make_event, add_outbox
from backend.infrastructure.resilience import current_trace

class TransactionRepository(Repository):

    def enrich_clients(self, rows):
        ids = [r['id'] for r in rows]
        accounts = self.db.query('SELECT * FROM accounts WHERE client_id=ANY(%s::uuid[]) ORDER BY id', (ids,)) if ids else []
        return [model(Client, r, accounts=[model(Account, a) for a in accounts if a['client_id'] == r['id']]) for r in rows]

    def client(self, id):
        rows = self.db.query('SELECT * FROM bank_clients WHERE id=%s', (id,))
        return self.enrich_clients(rows)[0] if rows else None

    def clients(self, filters, page):
        clauses, params = ([], [])
        for key, col in [('externalId', 'external_id'), ('segment', 'segment'), ('isActive', 'is_active')]:
            if key in filters:
                clauses.append(col + '=%s')
                params.append(filters[key])
        rows, total = self.page_rows('SELECT * FROM bank_clients', clauses, params, page)
        return (self.enrich_clients(rows), total)

    def transaction(self, id):
        return model(Transaction, self.db.one(TX + ' WHERE t.id=%s', (id,)))

    def transactions(self, filters, page):
        clauses, params = ([], [])
        for key, col in [('clientId', 'a.client_id'), ('currency', 't.currency'), ('direction', 't.direction')]:
            if key in filters:
                clauses.append(col + '=%s')
                params.append(filters[key])
        for key, op in [('amountFrom', '>='), ('amountTo', '<=')]:
            if key in filters:
                clauses.append('t.amount' + op + '%s')
                params.append(filters[key])
        if 'period' in filters:
            clauses.extend(['t.occurred_at >= %s', 't.occurred_at < %s'])
            params.extend(filters['period'])
        rows, total = self.page_rows(TX, clauses, params, page)
        return ([model(Transaction, r) for r in rows], total)

    def _execute_command(self, actor, name, key, payload, perform):
        with self.db.connect() as conn:
            return command(conn, actor=actor, name=name, key=key, payload=payload, perform=perform)

    def account(self, id):
        return model(Account, self.db.one('SELECT * FROM accounts WHERE id=%s', (id,)))

    def counterparty(self, id):
        row = self.db.one('SELECT id,external_id,display_name,category FROM counterparties WHERE id=%s', (id,))
        return {'id': str(row['id']), 'externalId': row['external_id'], 'displayName': row['display_name'], 'category': row['category']} if row else None

    def summary(self, filters):
        clauses, params = ([], [])
        for key, column in [('clientId', 'a.client_id'), ('currency', 't.currency'), ('direction', 't.direction')]:
            if key in filters:
                clauses.append(column + '=%s')
                params.append(filters[key])
        for key, op in [('amountFrom', '>='), ('amountTo', '<=')]:
            if key in filters:
                clauses.append('t.amount' + op + '%s')
                params.append(filters[key])
        if 'period' in filters:
            clauses.extend(['t.occurred_at >= %s', 't.occurred_at < %s'])
            params.extend(filters['period'])
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        row = self.db.one("SELECT count(*)::int AS transaction_count, count(DISTINCT t.currency)::int AS currency_count, round(coalesce(sum(t.amount),0),2) AS total_amount, round(avg(t.amount),2) AS average_amount, min(t.amount) AS minimum_amount, max(t.amount) AS maximum_amount, count(*) FILTER (WHERE t.direction='IN')::int AS incoming_count, count(*) FILTER (WHERE t.direction='OUT')::int AS outgoing_count FROM transactions t JOIN accounts a ON a.id=t.account_id" + where, params)
        return {name: row[column] for name, column in {'transactionCount': 'transaction_count', 'currencyCount': 'currency_count', 'totalAmount': 'total_amount', 'averageAmount': 'average_amount', 'minimumAmount': 'minimum_amount', 'maximumAmount': 'maximum_amount', 'incomingCount': 'incoming_count', 'outgoingCount': 'outgoing_count'}.items()}

    def execute(self, actor, name, key, payload, perform):
        if name != 'registerTransaction':
            return self._execute_command(actor, name, key, payload, perform)

        def traced(c):
            tx = perform(c)
            client = c.execute('SELECT a.client_id FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE t.id=%s', (tx,)).fetchone()
            event = make_event({'transaction_id': tx, 'client_id': str(client['client_id'])})
            event['correlation_id'] = current_trace()
            add_outbox(c, 'transaction.registered.v1', str(client['client_id']), event)
            return tx
        return self._execute_command(actor, name, key, payload, traced)
