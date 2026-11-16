"""Validated selectors and authenticated, canonical report snapshots."""

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import hmac
import json
import os
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from backend.common.data import DomainError, period, uuid

MAX_ROWS = 2000
MAX_BYTES = 2 * 1024 * 1024
SCHEMA = json.loads((Path(__file__).parents[1] / "contracts/report.schema.json").read_text(encoding="utf-8"))


def wire(value):
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    return value


def canonical(value):
    return json.dumps(wire(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def checked(name, value):
    validator = Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]}, format_checker=FormatChecker())
    if next(validator.iter_errors(value), None) is not None:
        raise DomainError("BAD_INPUT", "Некорректный контракт отчёта: " + name)
    return value


def normalize_request(value):
    checked("Request", value)
    result = dict(value)
    for field in ("profileVersionId", "transactionId", "resultId", "clientId"):
        if field in result:
            result[field] = uuid(result[field])
    if result["reportType"] == "PERIOD":
        start, end = period(result["period"])
        if (end - start).total_seconds() > 366 * 86400:
            raise DomainError("BAD_INPUT", "Период отчёта не более 366 дней")
        result["period"] = wire(dict(**{"from": start, "to": end}))
        result.setdefault("windowDays", 30)
    return result


def principal(actor):
    return {k: getattr(actor, k) for k in ("id", "login", "role")}


def signature_bytes(actor, request, snapshot):
    return canonical(dict(protocol="report-snapshot-v1", actor=principal(actor), request=request, snapshot=snapshot))


def sign(actor, request, snapshot):
    key = os.getenv("REPORT_SIGNING_KEY", "")
    if len(key) < 32:
        raise DomainError("DEPENDENCY_UNAVAILABLE", "Ключ сборки отчётов не настроен")
    return hmac.new(key.encode(), signature_bytes(actor, request, snapshot), hashlib.sha256).hexdigest()


def verify(actor, request, snapshot, signature):
    if not isinstance(signature, str) or not hmac.compare_digest(sign(actor, request, snapshot), signature):
        raise DomainError("FORBIDDEN", "Подпись серверного снимка недействительна")


def cutoff_time(value):
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if at.tzinfo is None or at > datetime.now(UTC):
            raise ValueError()
        return at.astimezone(UTC)
    except (TypeError, ValueError, AttributeError):
        raise DomainError("BAD_INPUT", "Срез требует дату с часовым поясом, не в будущем") from None


def cut(owner, captured, cutoff, references):
    return wire(dict(owner=owner, capturedAt=captured, cutoff=cutoff, referencesHash=digest(references)))


def validate_snapshot(actor, request, value):
    checked("Snapshot", value)
    if len(canonical(value)) > MAX_BYTES:
        raise DomainError("BAD_INPUT", "Отчёт превышает 2 MiB; сузьте период")
    filters = {k: v for k, v in request.items() if k != "idempotencyKey"}
    if value["author"] != principal(actor) or value["filters"] != filters or value["reportType"] != request["reportType"]:
        raise DomainError("FORBIDDEN", "Автор или селекторы не совпадают со снимком")
    data = value["data"]
    expected = {"PROFILE": {"client", "profile"}, "OPERATION": {"transaction", "assessment", "profiles", "review"}, "PERIOD": {"transactions", "analysis", "cases"}}
    if set(data) != expected[request["reportType"]] or {c["owner"] for c in value["sourceCuts"]} != ({"transaction", "analysis"} if request["reportType"] == "PROFILE" else {"transaction", "analysis", "case"}):
        raise DomainError("BAD_INPUT", "Несогласованные разделы отчёта")
    if any(c["cutoff"] != value["cutoff"] for c in value["sourceCuts"]):
        raise DomainError("BAD_INPUT", "Несогласованные срезы владельцев")
    if request["reportType"] == "PROFILE" and data["profile"]["metadata"]["id"] != request["profileVersionId"]:
        raise DomainError("BAD_INPUT", "Другая версия профиля")
    if request["reportType"] == "OPERATION" and (data["transaction"]["id"] != request["transactionId"] or data["assessment"]["resultId"] != request["resultId"]):
        raise DomainError("BAD_INPUT", "Другой результат операции")
    cutoff_time(value["cutoff"])
    return value
