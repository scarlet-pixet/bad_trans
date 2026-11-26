from __future__ import annotations
from dataclasses import fields
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID
import re
import psycopg
from psycopg.rows import dict_row

class DomainError(Exception):

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

def uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise DomainError('BAD_INPUT', 'Идентификатор должен быть UUID') from None

def period(value):
    try:
        start, end = (value['from'], value['to'])
        start = datetime.fromisoformat(start.replace('Z', '+00:00')) if isinstance(start, str) else start
        end = datetime.fromisoformat(end.replace('Z', '+00:00')) if isinstance(end, str) else end
        if not start.tzinfo or not end.tzinfo or start >= end:
            raise ValueError()
        return (start, end)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise DomainError('BAD_INPUT', 'Период требует from < to и часовой пояс') from None

def currency(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Z]{3}', value):
        raise DomainError('BAD_INPUT', 'Валюта задаётся тремя заглавными латинскими буквами')
    return value

def money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise DomainError('BAD_INPUT', 'Некорректная граница суммы') from None

def model(cls, row, **extra):
    if row is None:
        return None
    values = {k: str(v) if isinstance(v, UUID) else v for k, v in row.items()}
    values.update(extra)
    return cls(**{f.name: values[f.name] for f in fields(cls) if f.name in values})

class Database:
    """Only a repository receives its owner's database handle."""

    def __init__(self, dsn: str, owner: str):
        self.dsn, self.owner = (dsn, owner)

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5, options='-c default_transaction_read_only=on -c statement_timeout=5000')

    def query(self, sql, params=()):
        try:
            with self.connect() as conn:
                return conn.execute(sql, params).fetchall()
        except psycopg.OperationalError:
            raise DomainError('DEPENDENCY_UNAVAILABLE', f'База {self.owner} недоступна') from None

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def health(self):
        return self.one("SELECT current_database() AS database, current_user AS user, count(*)::int AS tables FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")

class Repository:

    def __init__(self, db: Database):
        self.db = db
