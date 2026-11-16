"""Transaction-owned report cohort and exact decimal aggregates."""

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypedDict

from backend.common.commands import require_role
from backend.common.data import DomainError, period, uuid
from backend.common.report_contract import MAX_ROWS, cut, cutoff_time, normalize_request, wire
from backend.infrastructure.workspace_access import transaction_access


class Group(TypedDict):
    count: int
    amount: Decimal
    clients: set[str]


class ReportSlices:
    def __init__(self, db):
        self.db = db

    def report_client_part(self, actor, clientId, cutoff):
        require_role(actor, "MANAGER")
        at = cutoff_time(cutoff)
        row = self.db.one('SELECT id,external_id AS "externalId",display_name AS "displayName",segment FROM bank_clients WHERE id=%s', (uuid(clientId),))
        if not row:
            raise DomainError("NOT_FOUND", "Клиент профиля не найден")
        return dict(data=wire(row), cut=cut("transaction", datetime.now(UTC), at, [clientId]))

    def report_transaction_part(self, actor, transactionId, cutoff):
        require_role(actor, "MANAGER", "ANALYST")
        at = cutoff_time(cutoff)
        transaction_access(actor, transactionId)
        with self.db.connect() as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            row = c.execute('''SELECT t.*,a.client_id AS "clientId",a.external_id AS "accountExternalId",
                b.display_name AS "clientName",p.display_name AS "counterpartyName"
                FROM transactions t JOIN accounts a ON a.id=t.account_id
                JOIN bank_clients b ON b.id=a.client_id LEFT JOIN counterparties p ON p.id=t.counterparty_id
                WHERE t.id=%s AND t.ingested_at<=%s''', (uuid(transactionId), at)).fetchone()
            captured = c.execute("SELECT transaction_timestamp() AS at").fetchone()["at"]
        if not row:
            raise DomainError("NOT_FOUND", "Операция на срезе не найдена")
        return dict(data=wire(row), cut=cut("transaction", captured, at, [transactionId]))

    def report_transactions_part(self, actor, input, cutoff):
        require_role(actor, "MANAGER")
        request = normalize_request(input)
        if request["reportType"] != "PERIOD":
            raise DomainError("BAD_INPUT", "Требуется PERIOD")
        start, end = period(request["period"])
        at = cutoff_time(cutoff)
        with self.db.connect() as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            rows = c.execute('''SELECT t.id,a.client_id AS "clientId",t.currency,t.direction,t.amount,
                t.occurred_at AS "occurredAt",t.ingested_at AS "ingestedAt"
                FROM transactions t JOIN accounts a ON a.id=t.account_id
                WHERE t.occurred_at>=%s AND t.occurred_at<%s AND t.ingested_at<=%s
                AND (%s::uuid IS NULL OR a.client_id=%s) AND (%s::text IS NULL OR t.currency=%s)
                AND (%s::text IS NULL OR t.direction=%s) ORDER BY t.id LIMIT %s''',
                (start, end, at, request.get("clientId"), request.get("clientId"), request.get("currency"), request.get("currency"), request.get("direction"), request.get("direction"), MAX_ROWS + 1)).fetchall()
            captured = c.execute("SELECT transaction_timestamp() AS at").fetchone()["at"]
        if len(rows) > MAX_ROWS:
            raise DomainError("BAD_INPUT", "Более 2000 операций; сузьте период или фильтры")
        groups: defaultdict[tuple[str, str], Group] = defaultdict(lambda: Group(count=0, amount=Decimal(0), clients=set()))
        for row in rows:
            g = groups[(row["currency"], row["direction"])]
            g["count"] += 1
            g["amount"] += row["amount"]
            g["clients"].add(str(row["clientId"]))
        totals = [dict(currency=k[0], direction=k[1], transactionCount=g["count"], amount=str(g["amount"]), uniqueClients=len(g["clients"])) for k, g in sorted(groups.items())]
        data = wire(dict(transactionCount=len(rows), uniqueClients=len({r["clientId"] for r in rows}), groups=totals, cohort=rows, axis="occurred_at; ingested_at<=cutoff; [from,to)"))
        return dict(data=data, cut=cut("transaction", captured, at, [str(r["id"]) for r in rows]))
