import base64
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.common.data import DomainError
from backend.common.import_csv import REQUIRED
from backend.infrastructure.product_migrations import migrate
from backend.services.transaction.ingest.bulk_import import BulkImport


@pytest.fixture
def job_store(database):
    db = database("transaction")
    migrate(db, "transaction")
    with db.connect() as c:
        client = str(uuid4())
        account = str(uuid4())
        c.execute("INSERT INTO bank_clients VALUES (%s,'import-test','Import test',NULL,true)", (client,))
        c.execute("INSERT INTO accounts VALUES (%s,%s,'import-test','BYN','OPEN')", (account, client))
    return db, BulkImport(db), account


def principal():
    return SimpleNamespace(id=str(uuid4()), role="ANALYST", is_active=True)


def csv_file(account, names):
    lines = [",".join(REQUIRED)]
    lines.extend(f"bulk-test,{name},{account},120.00,BYN,OUT,2026-11-01T09:00:00Z" for name in names)
    return ("\n".join(lines) + "\n").encode()


def enqueue(store, actor, raw, key):
    return store.start(actor, base64.b64encode(raw).decode(), sha256(raw).hexdigest(), key)["id"]


def test_chunk_rollback_keeps_first_committed_rows_and_outbox(job_store, monkeypatch):
    db, store, account = job_store
    monkeypatch.setattr("backend.services.transaction.ingest.bulk_import.PORTION", 2)
    actor = principal()
    raw = csv_file(account, ["first", "second", "third", "fourth"])
    identifier = enqueue(store, actor, raw, "request")
    store.process_portion()
    assert store.get(actor, identifier)["added"] == 2
    resume = store.register

    def interrupted(connection, service, actor, item):
        if item["externalId"] == "fourth":
            raise KeyboardInterrupt()
        return resume(connection, service, actor, item)

    monkeypatch.setattr(store, "register", interrupted)
    with pytest.raises(KeyboardInterrupt):
        store.process_portion()
    assert store.get(actor, identifier)["pending"] == 2
    assert db.one("SELECT count(*) AS n FROM outbox_events")["n"] == 2
    assert BulkImport(db).process_portion()
    done = store.get(actor, identifier)
    assert (done["status"], done["added"], done["duplicates"], done["failed"]) == ("COMPLETED", 4, 0, 0)
    assert db.one("SELECT count(*) AS n FROM outbox_events")["n"] == 4


def test_hash_replay_and_role_scoped_errors(job_store):
    db, store, account = job_store
    actor = principal()
    raw = csv_file(account, ["valid", "bad amount"]).replace(b"bad amount," + account.encode() + b",120.00",
                                                            b"bad amount," + account.encode() + b",-1")
    identifier = enqueue(store, actor, raw, "key1")
    assert enqueue(store, actor, raw, "key2") == identifier
    with pytest.raises(DomainError, match="Ключ уже использован"):
        enqueue(store, actor, raw + b"\n", "key2")
    store.process_portion()
    assert store.get(actor, identifier)["status"] == "PARTIAL"
    assert "3,BAD_INPUT," in store.errors(actor, identifier)
    other = principal()
    assert not store.list(other)["items"]
    for read in (store.get, store.errors):
        with pytest.raises(DomainError, match="не найдена"):
            read(other, identifier)
