"""Registration and outbox on the connection that owns the import row."""

from datetime import datetime
from decimal import Decimal

from backend.common.data import DomainError
from backend.common.events import add_outbox, make_event
from backend.infrastructure.resilience import current_trace


def existing_transaction(connection, item):
    old = connection.execute(
        "SELECT * FROM transactions WHERE source=%s AND external_id=%s",
        (item["source"], item["externalId"]),
    ).fetchone()
    if old is None:
        return None
    changed = (
        str(old["account_id"]) != item["accountId"]
        or str(old["counterparty_id"] or "") != str(item.get("counterpartyId") or "")
        or old["amount"] != Decimal(str(item["amount"]))
        or old["currency"] != item["currency"]
        or old["direction"] != item["direction"]
        or (old.get("time_precision") or "EXACT") != (item.get("timePrecision") or "EXACT")
        or old["occurred_at"] != datetime.fromisoformat(str(item["occurredAt"]).replace("Z", "+00:00"))
    )
    if changed:
        raise DomainError("CONFLICT", "Внешний ID уже имеет другое содержимое")
    return str(old["id"])


class ImportRegistration:
    def __init__(self, connection, batch_id=None):
        self.connection = connection
        self.batch_id = batch_id

    def execute(self, actor, name, key, payload, action):
        c = self.connection
        transaction = action(c)
        if self.batch_id is not None:
            c.execute("UPDATE transactions SET batch_id=%s WHERE id=%s", (self.batch_id, transaction))
        client = c.execute("SELECT client_id FROM accounts WHERE id=%s",
                           (payload["accountId"],)).fetchone()
        event = make_event(dict(transaction_id=transaction, client_id=str(client["client_id"])))
        event["correlation_id"] = current_trace()
        add_outbox(c, "transaction.registered.v1", str(client["client_id"]), event)
        return transaction

    def transaction(self, identifier):
        return identifier
