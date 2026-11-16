from backend.common.data import uuid, period, currency, money, pagination, paged, DomainError
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from backend.common.data import DomainError, currency, money, period, uuid
from backend.common.commands import require_role, required_text


class TransactionQueries:
    def get_client(self, id):
        return self.repository.client(uuid(id))

    def get_transaction(self, id):
        return self.repository.transaction(uuid(id))

    def search_clients(self, filter=None, page=None):
        spec = pagination(page, {'id': 'id', 'displayName': 'display_name', 'externalId': 'external_id'}, 'id')
        rows, total = self.repository.clients(filter or {}, spec)
        return paged(rows, total, spec, page)

    def search_transactions(self, filter=None, page=None):
        filters = dict(filter or {})
        if 'clientId' in filters:
            filters['clientId'] = uuid(filters['clientId'])
        if 'currency' in filters:
            filters['currency'] = currency(filters['currency'])
        if 'period' in filters:
            filters['period'] = period(filters['period'])
        if 'direction' in filters and filters['direction'] not in ('IN', 'OUT'):
            raise DomainError('BAD_INPUT', 'Неизвестное направление')
        for key in ('amountFrom', 'amountTo'):
            if key in filters:
                filters[key] = money(filters[key])
        if 'amountFrom' in filters and 'amountTo' in filters and (filters['amountFrom'] > filters['amountTo']):
            raise DomainError('BAD_INPUT', 'amountFrom > amountTo')
        spec = pagination(page, {'id': 'id', 'occurredAt': 'occurred_at', 'amount': 'amount'}, 'occurredAt')
        rows, total = self.repository.transactions(filters, spec)
        return paged(rows, total, spec, page)

    def transaction_summary(self, filter=None):
        filters = dict(filter or {})
        if 'clientId' in filters:
            filters['clientId'] = uuid(filters['clientId'])
        if 'currency' in filters:
            filters['currency'] = currency(filters['currency'])
        if 'period' in filters:
            filters['period'] = period(filters['period'])
        if 'direction' in filters and filters['direction'] not in ('IN', 'OUT'):
            raise DomainError('BAD_INPUT', 'Неизвестное направление')
        for key in ('amountFrom', 'amountTo'):
            if key in filters:
                filters[key] = money(filters[key])
        if 'amountFrom' in filters and 'amountTo' in filters and (filters['amountFrom'] > filters['amountTo']):
            raise DomainError('BAD_INPUT', 'amountFrom > amountTo')
        result = self.repository.summary(filters)
        if result.pop('currencyCount') > 1:
            raise DomainError('BAD_INPUT', 'Для сводки нескольких валют укажите фильтр currency')
        return result
