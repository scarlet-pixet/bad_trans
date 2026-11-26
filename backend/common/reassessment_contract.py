"""Selection criteria shared between Analysis and the Transaction owner."""
from datetime import UTC

from backend.common.data import DomainError, period, uuid

MAX_OPERATIONS = 10_000


def selection(period_value, client_id=None):
    start, end = period(period_value)
    return {
        "period": {"from": start.astimezone(UTC).isoformat(), "to": end.astimezone(UTC).isoformat()},
        "clientId": uuid(client_id) if client_id is not None else None,
    }


def page_bounds(offset, limit):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise DomainError("BAD_INPUT", "Некорректные границы страницы")
