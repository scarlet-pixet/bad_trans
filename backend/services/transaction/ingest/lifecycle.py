"""Record source events and create a ledger operation only at posting."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from psycopg.types.json import Jsonb

from backend.common.behavior_contract import digest
from backend.common.commands import require_role, required_text
from backend.common.data import DomainError, currency, money, period, uuid
from backend.common.time_precision import observation_time
from backend.services.transaction.ingest.import_rows import ImportRegistration
from backend.services.transaction.ingest.location import normalize_location
from backend.services.transaction.ingest.transfer import normalize_transfer

DIRECTIONS = {"PURCHASE": "OUT", "WITHDRAWAL": "OUT", "DEPOSIT": "IN", "REFUND": "IN", "REVERSAL": "IN"}
CHANNELS = {"POS", "ONLINE", "ATM", "BRANCH", "BANK_API", "UNKNOWN"}
STATUSES = {"AUTHORIZED", "POSTED", "CANCELLED"}


def event_input(value):
    kind, status, channel, direction = (value[field] for field in ("kind", "status", "channel", "direction"))
    if (status not in STATUSES or channel not in CHANNELS or direction not in ("IN", "OUT")
            or (kind != "TRANSFER" and DIRECTIONS.get(kind) != direction)):
        raise DomainError("BAD_INPUT", "Недопустимый тип, канал, статус или направление операции")
    if kind != "PURCHASE" and status != "POSTED":
        raise DomainError("BAD_INPUT", "Авторизация и отмена до проведения применяются к покупке")
    original = uuid(value["originalTransactionId"]) if value.get("originalTransactionId") else None
    if (kind in ("REFUND", "REVERSAL")) != bool(original):
        raise DomainError("BAD_INPUT", "Укажите исходную покупку только для возврата или сторно")
    source = required_text(value["source"], "source", 32)
    at, precision = observation_time(source, value["occurredAt"], value.get("timePrecision"))
    amount = money(value["amount"])
    if amount <= 0 or amount >= 10**16 or amount != amount.quantize(Decimal("0.01")):
        raise DomainError("BAD_INPUT", "Сумма события должна быть положительной с точностью до копеек")
    account = uuid(value["accountId"])
    transfer = value.get("transfer")
    if transfer is not None and kind != "TRANSFER":
        raise DomainError("BAD_INPUT", "Стороны перевода применимы только к переводу")
    location = normalize_location(value.get("location"), channel)
    return {
        "source": source,
        **({"timePrecision": precision} if precision else {}),
        **({"location": location} if location is not None else {}),
        "operationRef": required_text(value["operationRef"], "operationRef", 100),
        "eventRef": required_text(value["eventRef"], "eventRef", 100),
        "kind": kind, "channel": channel, "status": status, "direction": direction,
        "accountId": account, "currency": currency(value["currency"]),
        "transfer": normalize_transfer(transfer, account, direction) if transfer is not None else None,
        "counterpartyId": uuid(value["counterpartyId"]) if value.get("counterpartyId") else None,
        "originalTransactionId": original, "amount": format(amount, ".2f"),
        "occurredAt": at.astimezone(UTC).isoformat(),
    }


def check_adjustment(connection, event):
    original = connection.execute(
        """SELECT m.account_id,m.currency,t.amount,t.occurred_at
        FROM managed_operations m JOIN transactions t ON t.id=m.transaction_id
        WHERE m.transaction_id=%s AND m.kind='PURCHASE' AND m.status='POSTED' FOR UPDATE OF m""",
        (event["originalTransactionId"],),
    ).fetchone()
    if not original:
        raise DomainError("NOT_FOUND", "Проведённая исходная покупка не найдена")
    if str(original["account_id"]) != event["accountId"] or original["currency"] != event["currency"]:
        raise DomainError("BAD_INPUT", "Возврат требует счёт и валюту исходной покупки")
    if datetime.fromisoformat(event["occurredAt"]) < original["occurred_at"]:
        raise DomainError("BAD_INPUT", "Возврат произошёл раньше проведения покупки")
    returned = connection.execute(
        """SELECT coalesce(sum(t.amount),0) AS amount
        FROM managed_operations m JOIN transactions t ON t.id=m.transaction_id
        WHERE m.original_transaction_id=%s AND m.status='POSTED'""",
        (event["originalTransactionId"],),
    ).fetchone()["amount"]
    amount = Decimal(event["amount"])
    if returned + amount > original["amount"] or (
        event["kind"] == "REVERSAL" and (returned or amount != original["amount"])
    ):
        raise DomainError("CONFLICT", "Сумма возврата или сторно превышает допустимую")


def receipt(operation_id, status, transaction_id):
    return {"operationId": str(operation_id), "status": status,
            "transactionId": str(transaction_id) if transaction_id else None}


class OperationLifecycle:
    def __init__(self, db):
        self.db = db

    def record(self, actor, input):
        require_role(actor, "MANAGER", "ADMIN")
        event = event_input(input)
        source, ref, event_ref = (event[field] for field in ("source", "operationRef", "eventRef"))
        intent = digest(event)
        with self.db.connect() as connection:
            for key in (f"operation-event:{source}:{event_ref}", f"operation-id:{source}:{ref}"):
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (key,))
            seen = connection.execute(
                """SELECT operation_id,status,intent_hash,transaction_id FROM managed_operation_events
                WHERE source=%s AND event_ref=%s""", (source, event_ref),
            ).fetchone()
            if seen:
                if seen["intent_hash"] != intent:
                    raise DomainError("CONFLICT", "Идентификатор события уже использован с другими данными")
                return receipt(seen["operation_id"], seen["status"], seen["transaction_id"])
            current = connection.execute(
                "SELECT * FROM managed_operations WHERE source=%s AND operation_ref=%s", (source, ref),
            ).fetchone()
            kind, channel, status, account, code, direction, original, at = (
                event[field] for field in ("kind", "channel", "status", "accountId", "currency",
                                           "direction", "originalTransactionId", "occurredAt")
            )
            account_row = connection.execute("SELECT currency FROM accounts WHERE id=%s", (account,)).fetchone()
            if not account_row:
                raise DomainError("NOT_FOUND", "Счёт операции не найден")
            if account_row["currency"] != code:
                raise DomainError("BAD_INPUT", "Валюта операции не совпадает с валютой счёта")
            if current:
                fixed = (current["kind"], current["channel"], str(current["account_id"]),
                         current["currency"], current["direction"])
                if fixed != (kind, channel, account, code, direction) or (
                    current["status"] != "AUTHORIZED" or status not in ("POSTED", "CANCELLED")
                    or datetime.fromisoformat(at) < current["last_event_at"]
                ):
                    raise DomainError("CONFLICT", "Переход состояния операции недопустим")
            elif status == "CANCELLED":
                raise DomainError("CONFLICT", "Отсутствует авторизация для отмены")
            if original:
                check_adjustment(connection, event)
            transaction_id = None
            if status == "POSTED":
                from backend.services.transaction.ingest.registration import (
                    TransactionService,
                )
                transaction_id = TransactionService(ImportRegistration(connection)).register_transaction(actor, {
                    "source": source, "externalId": ref, "accountId": account,
                    "counterpartyId": event["counterpartyId"], "amount": event["amount"],
                    "currency": code, "direction": direction, "occurredAt": at, "idempotencyKey": event_ref,
                    "timePrecision": event.get("timePrecision"),
                }, transfer=input.get("transfer"))
            operation_id = current["id"] if current else uuid4()
            connection.execute(
                """INSERT INTO managed_operations(id,source,operation_ref,kind,channel,status,account_id,
                    currency,direction,transaction_id,original_transaction_id,last_event_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET status=excluded.status, transaction_id=excluded.transaction_id,
                    last_event_at=excluded.last_event_at""",
                (operation_id, source, ref, kind, channel, status, account, code, direction,
                 transaction_id, original, at),
            )
            connection.execute(
                """INSERT INTO managed_operation_events(source,event_ref,operation_id,status,amount,occurred_at,
                    intent_hash,transaction_id,time_precision,location)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (source, event_ref, operation_id, status, event["amount"], at, intent,
                 transaction_id, event.get("timePrecision"),
                 Jsonb(event["location"]) if "location" in event else None),
            )
            return receipt(operation_id, status, transaction_id)

    def detail(self, actor, transactionId):
        require_role(actor, "MANAGER", "ANALYST", "ADMIN")
        row = self.db.one(
            """SELECT m.kind,m.channel,m.status,m.original_transaction_id AS "originalTransactionId",
                m.source,c.id AS "counterpartyId",c.display_name AS "counterpartyName",
                jsonb_agg(jsonb_build_object('status',e.status,'amount',e.amount::text,'occurredAt',e.occurred_at,
                    'timePrecision',e.time_precision,'location',e.location)
                    ORDER BY e.occurred_at, CASE WHEN e.status='AUTHORIZED' THEN 0 ELSE 1 END) AS events
            FROM managed_operations m JOIN managed_operation_events e ON e.operation_id=m.id
            JOIN transactions t ON t.id=m.transaction_id
            LEFT JOIN counterparties c ON c.id=t.counterparty_id
            WHERE m.transaction_id=%s GROUP BY m.id,c.id""", (uuid(transactionId),),
        )
        return row

    def spending(self, actor, clientId, currencyCode, window):
        require_role(actor, "MANAGER")
        client = uuid(clientId)
        code = currency(currencyCode)
        start, end = period(window)
        totals = self.db.one(
            """SELECT
              coalesce(sum(t.amount) FILTER (WHERE m.kind='PURCHASE'),0) AS "purchases",
              coalesce(sum(t.amount) FILTER (WHERE m.kind IN ('REFUND','REVERSAL')),0) AS "adjustments",
              coalesce(sum(t.amount) FILTER (WHERE m.kind='REFUND'),0) AS "refunds",
              coalesce(sum(t.amount) FILTER (WHERE m.kind='REVERSAL'),0) AS "reversals",
              coalesce(sum(t.amount) FILTER (WHERE p.owner_relation='SAME_OWNER' AND t.direction='OUT'),0) AS "ownTransfersOut",
              coalesce(sum(t.amount) FILTER (WHERE p.owner_relation='SAME_OWNER' AND t.direction='IN'),0) AS "ownTransfersIn",
              coalesce(sum(t.amount) FILTER (WHERE m.kind IS NULL AND p.id IS NULL AND t.direction='OUT'),0) AS "unclassifiedOut"
            FROM transactions t JOIN accounts a ON a.id=t.account_id
            LEFT JOIN managed_operations m ON m.transaction_id=t.id
            LEFT JOIN operation_transfer_entries e ON e.transaction_id=t.id
            LEFT JOIN operation_transfers p ON p.id=e.transfer_id
            WHERE a.client_id=%s AND t.currency=%s AND t.occurred_at>=%s AND t.occurred_at<%s""",
            (client, code, start, end),
        )
        totals["netSpending"] = totals["purchases"] - totals["adjustments"]
        return {**{key: format(amount, ".2f") for key, amount in totals.items()}, "currency": code}
