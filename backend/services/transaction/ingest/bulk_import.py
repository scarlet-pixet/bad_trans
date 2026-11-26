"""Durable Transaction-owned CSV jobs. Each committed portion also commits its outbox."""

import base64
import binascii
import csv
from hashlib import sha256
from io import StringIO
import re
import time
from types import SimpleNamespace

import psycopg
from psycopg.types.json import Jsonb

from backend.common.commands import require_role
from backend.common.data import DomainError, uuid
from backend.common.events import ident
from backend.common.import_csv import MAX_BYTES, read_bulk
from backend.common.receipts import receipt
from backend.infrastructure.resilience import log
from backend.services.transaction.ingest.import_rows import ImportRegistration, existing_transaction
from backend.services.transaction.ingest.registration import TransactionService

PORTION = 100


class BulkImport:
    def __init__(self, db):
        self.db = db

    def start(self, actor, fileBase64, fileHash, idempotencyKey):
        require_role(actor, "MANAGER", "ANALYST")
        if (not isinstance(fileBase64, str) or len(fileBase64) > 4 * ((MAX_BYTES + 2) // 3)
                or not isinstance(fileHash, str) or not re.fullmatch(r"[0-9a-f]{64}", fileHash)):
            raise DomainError("BAD_INPUT", "Требуется файл до 2 МиБ и SHA-256")
        try:
            raw = base64.b64decode(fileBase64, validate=True)
        except (ValueError, binascii.Error):
            raise DomainError("BAD_INPUT", "Некорректная кодировка передачи файла") from None
        if len(raw) > MAX_BYTES or sha256(raw).hexdigest() != fileHash:
            raise DomainError("BAD_INPUT", "Проверьте размер файла и SHA-256")

        def prepare(connection):
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                               (f"bulk-file:{actor.id}:{fileHash}",))
            existing = connection.execute(
                "SELECT id FROM bulk_import_jobs WHERE author_id=%s AND file_hash=%s",
                (actor.id, fileHash),
            ).fetchone()
            if existing:
                return {"id": str(existing["id"])}
            rows = iter(read_bulk(raw, fileHash))
            columns = next(rows)
            identifier = ident()
            connection.execute(
                """INSERT INTO bulk_import_jobs(id,author_id,author_role,file_hash,columns,status)
                   VALUES (%s,%s,%s,%s,%s,'QUEUED')""",
                (identifier, actor.id, actor.role, fileHash, Jsonb(columns)),
            )
            with connection.cursor().copy(
                "COPY bulk_import_rows(job_id,line,payload,state,error_code,message) FROM STDIN"
            ) as copy:
                for line, item, error, message in rows:
                    copy.write_row((identifier, line, Jsonb(item) if item else None,
                                    "ERROR" if error else "PENDING", error, message))
            return {"id": identifier}

        with self.db.connect() as c:
            return receipt(c, actor, "startBulkImport", idempotencyKey, {"fileHash": fileHash}, prepare)

    @staticmethod
    def owned(c, actor, identifier):
        require_role(actor, "MANAGER", "ANALYST")
        row = c.execute("SELECT * FROM bulk_import_jobs WHERE id=%s AND author_id=%s",
                        (uuid(identifier), actor.id)).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", "Задача импорта не найдена")
        return row

    @staticmethod
    def counts(c, identifier):
        return {row["state"]: row["n"] for row in c.execute(
            "SELECT state,count(*) AS n FROM bulk_import_rows WHERE job_id=%s GROUP BY state",
            (identifier,),
        )}

    @staticmethod
    def summary(row):
        return dict(id=str(row["id"]), fileHash=row["file_hash"], status=row["status"],
                    createdAt=row["created_at"].isoformat(),
                    finishedAt=row["finished_at"].isoformat() if row["finished_at"] else None)

    def get(self, actor, id):
        with self.db.connect() as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = self.owned(c, actor, id)
            counts = self.counts(c, row["id"])
            errors = c.execute(
                """SELECT line,error_code AS code,message FROM bulk_import_rows
                   WHERE job_id=%s AND state='ERROR' ORDER BY line LIMIT 20""", (row["id"],),
            ).fetchall()
        return dict(self.summary(row), columns=row["columns"], received=sum(counts.values()),
                    pending=counts.get("PENDING", 0), added=counts.get("ADDED", 0),
                    duplicates=counts.get("DUPLICATE", 0), failed=counts.get("ERROR", 0),
                    errors=errors)

    def list(self, actor, offset=0):
        require_role(actor, "MANAGER", "ANALYST")
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise DomainError("BAD_INPUT", "Некорректная страница импорта")
        rows = self.db.query(
            """SELECT * FROM bulk_import_jobs WHERE author_id=%s
               ORDER BY created_at DESC,id DESC LIMIT 21 OFFSET %s""", (actor.id, offset),
        )
        return dict(items=[self.summary(row) for row in rows[:20]],
                    nextOffset=offset+20 if len(rows)>20 else None)

    def errors(self, actor, id):
        with self.db.connect() as c:
            row = self.owned(c, actor, id)
            if row["status"] in ("QUEUED", "RUNNING"):
                raise DomainError("CONFLICT", "Дождитесь завершения импорта")
            output = StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(("row", "code", "message"))
            writer.writerows((r["line"], r["error_code"], r["message"]) for r in c.execute(
                """SELECT line,error_code,message FROM bulk_import_rows
                   WHERE job_id=%s AND state='ERROR' ORDER BY line""", (row["id"],),
            ))
            return output.getvalue()

    @staticmethod
    def register(c, service, actor, item):
        try:
            with c.transaction():
                duplicate = existing_transaction(c, item)
                if duplicate:
                    return "DUPLICATE", duplicate
                return "ADDED", service.register_transaction(actor, item)
        except psycopg.errors.UniqueViolation:
            # A concurrent ordinary registration may have committed before our INSERT.
            duplicate = existing_transaction(c, item)
            if duplicate:
                return "DUPLICATE", duplicate
            raise

    def process_portion(self):
        with self.db.connect() as c:
            job = c.execute(
                """SELECT * FROM bulk_import_jobs WHERE status IN ('QUEUED','RUNNING')
                   ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED"""
            ).fetchone()
            if job is None:
                return False
            identifier = job["id"]
            actor = SimpleNamespace(id=str(job["author_id"]), role=job["author_role"], is_active=True)
            registration = TransactionService(ImportRegistration(c))
            rows = c.execute(
                """SELECT line,payload FROM bulk_import_rows
                   WHERE job_id=%s AND state='PENDING' ORDER BY line LIMIT %s""",
                (identifier, PORTION),
            ).fetchall()
            for row in rows:
                error = message = transaction = None
                try:
                    state, transaction = self.register(c, registration, actor, row["payload"])
                except DomainError as exc:
                    state, error, message = "ERROR", exc.code, str(exc)[:180]
                except psycopg.IntegrityError:
                    state, error, message = "ERROR", "BAD_INPUT", "Строка нарушает целостность данных"
                c.execute(
                    """UPDATE bulk_import_rows SET state=%s,transaction_id=%s,error_code=%s,
                       message=%s,payload=NULL WHERE job_id=%s AND line=%s""",
                    (state, transaction, error, message, identifier, row["line"]),
                )
            counts = self.counts(c, identifier)
            if counts.get("PENDING"):
                status = "RUNNING"
            elif not counts.get("ERROR"):
                status = "COMPLETED"
            else:
                status = "PARTIAL" if counts.get("ADDED", 0) + counts.get("DUPLICATE", 0) else "FAILED"
            c.execute(
                "UPDATE bulk_import_jobs SET status=%s,finished_at=CASE WHEN %s THEN now() END WHERE id=%s",
                (status, status != "RUNNING", identifier),
            )
            return True

    def loop(self):
        while True:
            try:
                if not self.process_portion():
                    time.sleep(0.5)
            except Exception as exc:
                log("bulk_import.retry", error=type(exc).__name__)
                time.sleep(1)
