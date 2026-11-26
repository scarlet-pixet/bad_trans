"""Canonical selection shared by owner discovery and bulk-profile commands."""
from datetime import UTC, datetime

from backend.common.commands import required_text
from backend.common.data import DomainError, currency, period, uuid


def normalize_selection(value, *, now=None):
    allowed = {"period", "clientIds", "segment", "currency", "direction"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise DomainError("BAD_INPUT", "Неизвестные параметры отбора")
    start, end = period(value.get("period"))
    if end > (now or datetime.now(UTC)):
        raise DomainError("BAD_INPUT", "Конец периода не может быть в будущем")
    clients = value.get("clientIds")
    if clients is not None:
        if not isinstance(clients, list) or not 1 <= len(clients) <= 1000:
            raise DomainError("BAD_INPUT", "Выберите 1–1000 клиентов либо всех клиентов")
        clients = sorted({uuid(item) for item in clients})
    segment = value.get("segment")
    if segment is not None:
        segment = required_text(segment, "segment", 50)
    if clients is not None and segment is not None:
        raise DomainError("BAD_INPUT", "Выберите клиентов или сегмент")
    direct = value.get("direction")
    if direct not in (None, "IN", "OUT"):
        raise DomainError("BAD_INPUT", "Направление: IN или OUT")
    return dict(period={"from": start.astimezone(UTC).isoformat(),
                        "to": end.astimezone(UTC).isoformat()},
                clientIds=clients, segment=segment,
                currency=currency(value["currency"]) if value.get("currency") is not None else None,
                direction=direct)
