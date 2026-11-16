"""PostgreSQL regression tests; temporary tables are rolled back after each test.

SUMMARY_TEST_DSN can point to any disposable PostgreSQL database with TEMP privilege.
No persistent tables or existing lab data are changed.
"""
from decimal import Decimal
import os

import psycopg
from psycopg.rows import dict_row
import pytest

from backend.common.data import DomainError
from backend.services.transaction.persistence.repository import TransactionRepository
from backend.services.transaction.ingest.registration import TransactionService


@pytest.fixture
def summary_service():
    dsn = os.getenv('SUMMARY_TEST_DSN')
    if not dsn:
        pytest.skip('Set SUMMARY_TEST_DSN to the service database with TEMP privilege')
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute('CREATE TEMP TABLE accounts '
                     '(id uuid PRIMARY KEY, client_id uuid, currency char(3)) ON COMMIT DROP')
        conn.execute('CREATE TEMP TABLE transactions '
                     '(account_id uuid, amount numeric(18,2), currency char(3), '
                     'direction text, occurred_at timestamptz) ON COMMIT DROP')

        class Database:
            def one(self, query, params=()):
                return conn.execute(query, params).fetchone()

        service = TransactionService(TransactionRepository(Database()))
        yield service, conn
        conn.rollback()


def insert_transactions(conn, currency, amount, count=1):
    account = '00000000-0000-0000-0000-000000000001' if currency == 'BYN' else '00000000-0000-0000-0000-000000000002'
    conn.execute('INSERT INTO accounts VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                 (account, '00000000-0000-0000-0000-000000000003', currency))
    conn.execute("INSERT INTO transactions SELECT %s,%s,%s,'OUT',now() "
                 'FROM generate_series(1,%s)', (account, amount, currency, count))


def test_mixed_currency_requires_explicit_filter(summary_service):
    service, conn = summary_service
    insert_transactions(conn, 'BYN', Decimal('100.00'))
    insert_transactions(conn, 'USD', Decimal('10.00'))
    with pytest.raises(DomainError) as error:
        service.transaction_summary()
    assert error.value.code == 'BAD_INPUT'
    assert service.transaction_summary({'currency': 'BYN'})['totalAmount'] == Decimal('100.00')
    assert service.transaction_summary({'currency': 'USD'})['totalAmount'] == Decimal('10.00')


def test_empty_and_single_currency_keep_existing_contract(summary_service):
    service, conn = summary_service
    empty = service.transaction_summary()
    assert empty['transactionCount'] == 0
    assert empty['totalAmount'] == Decimal('0.00')
    assert empty['averageAmount'] is None and empty['minimumAmount'] is None
    assert empty['maximumAmount'] is None
    insert_transactions(conn, 'BYN', Decimal('123.45'))
    single = service.transaction_summary()
    assert single['transactionCount'] == 1
    assert single['totalAmount'] == single['averageAmount'] == Decimal('123.45')


def test_sum_does_not_overflow_for_valid_transaction_amounts(summary_service):
    service, conn = summary_service
    maximum = Decimal('9999999999999999.99')
    insert_transactions(conn, 'BYN', maximum, count=101)
    result = service.transaction_summary({'currency': 'BYN'})
    assert result['transactionCount'] == 101
    assert result['totalAmount'] == maximum * 101
    assert result['averageAmount'] == maximum
