"""Shared parsing and physical CSV line numbers for both import modes."""
import csv
from datetime import UTC
from decimal import Decimal, InvalidOperation
from io import BytesIO, TextIOWrapper
from uuid import UUID

from backend.common.data import DomainError, currency
from backend.common.time_precision import observation_time

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10_000
REQUIRED = ("source", "externalId", "accountId", "amount", "currency", "direction", "occurredAt")
OPTIONAL = "counterpartyId"
SAMPLE = (
    ",".join((*REQUIRED, OPTIONAL)) + "\n"
    "statement,bank-001,00000000-0000-0000-0000-000000000001,125.50,BYN,OUT,"
    "2026-11-01T12:00:00+03:00,\n"
)


def normalize_transaction(values, file_hash, row):
    item = {name: value.strip() for name, value in values.items()}
    if not 1 <= len(item["source"]) <= 32 or not 1 <= len(item["externalId"]) <= 100:
        raise ValueError("Укажите source (до 32 символов) и externalId (до 100).")
    for name in ("accountId", OPTIONAL):
        if name == "accountId" or item.get(name):
            try:
                item[name] = str(UUID(item[name]))
            except ValueError:
                raise ValueError(f"{name} должен быть UUID.") from None
    if not item.get(OPTIONAL):
        item.pop(OPTIONAL, None)
    try:
        amount = Decimal(item["amount"].replace(",", "."))
        if (not amount.is_finite() or not 0 < amount <= Decimal("9999999999999999.99")
                or amount.as_tuple().exponent < -2):
            raise InvalidOperation()
    except InvalidOperation:
        raise ValueError("Сумма должна быть положительной и содержать до двух десятичных знаков.") from None
    item["amount"] = format(amount, "f")
    item["currency"] = currency(item["currency"].upper())
    if item["direction"] not in {"IN", "OUT"}:
        raise ValueError("Допустимые направления — IN и OUT.")
    precision = item.pop("timePrecision", None) or None
    timestamp, precision = observation_time(item["source"], item["occurredAt"], precision)
    item["occurredAt"] = timestamp.astimezone(UTC).isoformat()
    if precision:
        item["timePrecision"] = precision
    item["idempotencyKey"] = f"csv:{file_hash}:{row}"
    return item


def read_bulk(raw, fingerprint):
    """Yield one normalized row or a row error without accumulating the file."""
    with TextIOWrapper(BytesIO(raw), encoding="utf-8-sig", errors="surrogateescape", newline="") as stream:
        first = stream.readline()
        stream.seek(0)
        reader = csv.reader(stream, delimiter=";" if ";" in first else ",", strict=True)
        line = 1
        try:
            columns = [field.strip() for field in next(reader, [])]
            if len(columns) != len(set(columns)) or not set(REQUIRED) <= set(columns) <= {*REQUIRED, OPTIONAL, 'timePrecision'}:
                raise DomainError("BAD_INPUT", "Строка 1: заголовок не соответствует образцу CSV UTF-8")
            yield columns
            count = 0
            while True:
                line = reader.line_num + 1
                fields = next(reader, None)
                if fields is None:
                    break
                if not fields:
                    continue
                count += 1
                if count > MAX_ROWS:
                    raise DomainError("BAD_INPUT", f"Строка {line}: превышен предел {MAX_ROWS} записей")
                try:
                    for value in fields:
                        if "\0" in value:
                            raise ValueError("Недопустимый нулевой символ")
                        value.encode("utf-8")
                    if len(fields) != len(columns):
                        raise ValueError("Число полей не совпадает с заголовком")
                    item = normalize_transaction(dict(zip(columns, fields)), fingerprint, line)
                except UnicodeEncodeError:
                    yield line, None, "INVALID_ENCODING", "Строка не в кодировке UTF-8"
                except (ValueError, DomainError) as exc:
                    yield line, None, "BAD_INPUT", str(exc)
                else:
                    yield line, item, None, None
            if not count:
                raise DomainError("BAD_INPUT", "Файл не содержит операций")
        except csv.Error as exc:
            raise DomainError("BAD_INPUT", f"Строка {line}: неверный формат CSV ({exc})") from None
