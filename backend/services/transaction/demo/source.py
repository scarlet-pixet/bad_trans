"""Durable synthetic source, independently progressing without an open UI."""

import logging
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from uuid import UUID, uuid5

from psycopg.types.json import Jsonb

from backend.common.commands import require_role, required_text
from backend.common.data import DomainError, uuid
from backend.services.transaction.ingest.commands import TransactionCommands
from backend.services.transaction.persistence.repository import TransactionRepository
from backend.services.transaction.demo.scenarios import scenario_records


from backend.services.transaction.history.workspace import HistoryWorkspace

class SourceService(HistoryWorkspace):
    def __init__(self, db):
        self.db = db
        self.commands = TransactionCommands(TransactionRepository(db))





    def start_source(self, actor, idempotencyKey, intervalSeconds=1.0):
        require_role(actor, "MANAGER", "ADMIN")
        key = required_text(idempotencyKey, "idempotencyKey", 128)
        try:
            interval = Decimal(str(intervalSeconds))
        except InvalidOperation:
            raise DomainError("BAD_INPUT", "Интервал должен быть числом") from None
        if not interval.is_finite() or not Decimal("0.05") <= interval <= 30:
            raise DomainError("BAD_INPUT", "Интервал источника: 0.05–30 секунд")
        run = str(uuid5(UUID(actor.id), "source:" + key))
        with self.db.connect() as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (run,))
            old = c.execute("SELECT * FROM source_runs WHERE id=%s", (run,)).fetchone()
            if old:
                if old["interval_seconds"] != interval:
                    raise DomainError(
                        "CONFLICT", "Ключ источника использован с другим интервалом"
                    )
            else:
                prefix = run[:8]
                accounts = []
                for i, name in enumerate(("Анна", "Борис", "Вера")):
                    client = self.commands.create_client(
                        actor,
                        {
                            "externalId": f"DEMO-{prefix}-{i}",
                            "displayName": f"{name} · поток {prefix}",
                            "segment": "synthetic",
                            "idempotencyKey": f"{run}:client:{i}",
                        },
                    )
                    account = self.commands.open_account(
                        actor,
                        {
                            "clientId": client.id,
                            "externalId": f"DEMO-ACC-{prefix}-{i}",
                            "currency": "BYN",
                            "idempotencyKey": f"{run}:account:{i}",
                        },
                    )
                    accounts.append(account.id)
                counterparties = {}
                for recipient, name in [
                    ("usual", "Обычный получатель"),
                    ("new", "Новый получатель"),
                ]:
                    result = self.commands.register_counterparty(
                        actor,
                        {
                            "externalId": f"{prefix}-{recipient}",
                            "displayName": name,
                            "idempotencyKey": f"{run}:recipient:{recipient}",
                        },
                    )
                    counterparties[recipient] = result["id"]
                c.execute(
                    "INSERT INTO source_runs(id,requested_by,request_key,interval_seconds,status) VALUES (%s,%s,%s,%s,'RUNNING')",
                    (run, actor.id, key, interval),
                )
                for seq, record in enumerate(scenario_records(run, datetime.now(UTC))):
                    payload = {
                        **record["input"],
                        "source": "synthetic",
                        "externalId": f"{run}:{seq}",
                        "accountId": accounts[record["client"]],
                        "counterpartyId": counterparties[record["recipient"]],
                        "currency": "BYN",
                        "direction": "OUT",
                        "idempotencyKey": f"{run}:tx:{seq}",
                    }
                    c.execute(
                        "INSERT INTO source_records(run_id,sequence,payload,expected,scenario,phase) VALUES (%s,%s,%s,%s,%s,%s)",
                        (
                            run,
                            seq,
                            Jsonb(payload),
                            record["expected"],
                            record["scenario"],
                            record["phase"],
                        ),
                    )
        return self.get_sources(actor, runId=run)[0]

    def get_sources(self, actor, runId=None):
        require_role(actor, "MANAGER", "ADMIN")
        rows = self.db.query(
            """SELECT r.id,r.status,r.created_at,r.interval_seconds,r.last_error,
            count(*)::int AS total,count(transaction_id)::int AS processed,
            count(*) FILTER(WHERE phase='HISTORY' AND transaction_id IS NULL)::int AS history_pending
            FROM source_runs r JOIN source_records s ON s.run_id=r.id
            WHERE (%s::uuid IS NULL OR r.id=%s) GROUP BY r.id ORDER BY r.created_at DESC LIMIT 30""",
            (uuid(runId) if runId else None, uuid(runId) if runId else None),
        )
        return [
            {
                **dict(row),
                "id": str(row["id"]),
                "created_at": row["created_at"].isoformat(),
                "interval_seconds": float(row["interval_seconds"]),
            }
            for row in rows
        ]

    def set_source_state(self, actor, runId, paused):
        require_role(actor, "MANAGER", "ADMIN")
        if not isinstance(paused, bool):
            raise DomainError("BAD_INPUT", "paused должен быть boolean")
        with self.db.connect() as c:
            row = c.execute(
                "SELECT status FROM source_runs WHERE id=%s FOR UPDATE", (uuid(runId),)
            ).fetchone()
            if not row:
                raise DomainError("NOT_FOUND", "Источник не найден")
            if row["status"] != "COMPLETED":
                c.execute(
                    "UPDATE source_runs SET status=%s,next_at=now() WHERE id=%s",
                    ("PAUSED" if paused else "RUNNING", runId),
                )
        return self.get_sources(actor, runId)[0]

    def get_source_labels(self, actor, runId):
        require_role(actor, "MANAGER", "SYSTEM")
        if not self.db.one('SELECT 1 FROM source_runs WHERE id=%s', (uuid(runId),)):
            raise DomainError('NOT_FOUND', 'Источник не найден')
        return [
            dict(
                r,
                transaction_id=str(r["transaction_id"])
                if r["transaction_id"]
                else None,
            )
            for r in self.db.query(
                "SELECT sequence,transaction_id,expected,scenario FROM source_records WHERE run_id=%s ORDER BY sequence",
                (uuid(runId),),
            )
        ]

    def get_ingestion_metrics(self, actor):
        require_role(actor, "MANAGER")
        rows = self.db.query(
            "SELECT currency,count(*)::int AS count,sum(amount)::text AS total FROM transactions GROUP BY currency ORDER BY currency"
        )
        trend = self.db.query(
            "SELECT ingested_at::date AS day,count(*)::int AS count FROM transactions WHERE ingested_at>=now()-interval '14 days' GROUP BY 1 ORDER BY 1"
        )
        pending = self.db.one(
            "SELECT count(*)::int AS count FROM outbox_events WHERE published_at IS NULL"
        )["count"]
        return {
            "currencies": [dict(r) for r in rows],
            "pendingOutbox": pending,
            "trend": [
                {"day": r["day"].isoformat(), "count": r["count"]} for r in trend
            ],
        }

    def tick(self) -> bool:
        run = None
        try:
            with self.db.connect() as c:
                run = c.execute(
                    "SELECT * FROM source_runs WHERE status='RUNNING' AND next_at<=now() ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                ).fetchone()
                if not run:
                    return False
                actor = SimpleNamespace(
                    id=str(run["requested_by"]),
                    login="synthetic-source",
                    role="MANAGER",
                    is_active=True,
                )
                # Registration is idempotent across the separate checkpoint commit:
                # after a crash the same payload/key returns the same transaction ID.
                records = c.execute(
                    "SELECT * FROM source_records WHERE run_id=%s AND transaction_id IS NULL ORDER BY sequence LIMIT 8",
                    (run["id"],),
                ).fetchall()
                for record in records:
                    result = self.commands.register_transaction(
                        actor, record["payload"]
                    )
                    c.execute(
                        "UPDATE source_records SET transaction_id=%s WHERE run_id=%s AND sequence=%s",
                        (result.id, run["id"], record["sequence"]),
                    )
                    if record["phase"] == "LIVE":
                        break
                pending = c.execute(
                    "SELECT 1 FROM source_records WHERE run_id=%s AND transaction_id IS NULL LIMIT 1",
                    (run["id"],),
                ).fetchone()
                c.execute(
                    "UPDATE source_runs SET status=%s,next_at=now()+(interval '1 second'*interval_seconds),last_error=NULL WHERE id=%s",
                    ("RUNNING" if pending else "COMPLETED", run["id"]),
                )
            return True
        except DomainError as exc:
            if run is None:
                raise
            permanent = exc.code not in ("DEPENDENCY_UNAVAILABLE", "DATA_ACCESS_ERROR")
            with self.db.connect() as c:
                c.execute(
                    "UPDATE source_runs SET status=%s,last_error=%s,next_at=now()+interval '2 seconds' WHERE id=%s",
                    (
                        "PAUSED" if permanent else "RUNNING",
                        f"{exc.code}: {exc}"[:240],
                        run["id"],
                    ),
                )
            logging.warning(
                "source.delivery %s %s", exc.code, "paused" if permanent else "retry"
            )
            return False

    def loop(self):
        while True:
            try:
                self.tick()
            except Exception as exc:
                logging.warning("source.retry %s", type(exc).__name__)
            time.sleep(0.2)
