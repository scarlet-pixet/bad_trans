"""Normalize an exact instant or a calendar date with an explicit precision marker."""

from datetime import UTC, date, datetime, time

from backend.common.data import DomainError


def observation_time(source, value, precision=None):
    try:
        if isinstance(value, str) and len(value) == 10:
            value = datetime.combine(date.fromisoformat(value), time.min, UTC)
            if precision not in (None, "DATE"):
                raise ValueError()
            precision = "DATE"
        elif isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError()
        if source == "banksim":
            if precision == "EXACT":
                raise ValueError()
            precision = "DATE"
        if precision == "DATE":
            value = value.astimezone(UTC)
        if precision not in (None, "EXACT", "DATE") or (
            precision == "DATE" and value.time() != time.min
        ):
            raise ValueError()
        return value, precision
    except (ValueError, OverflowError):
        raise DomainError("BAD_INPUT", "Укажите дату без времени либо момент с часовым поясом") from None
