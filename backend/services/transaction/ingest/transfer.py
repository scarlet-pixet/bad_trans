"""Register immutable transfer parties and resolve ownership from bank records."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from backend.common.commands import required_text
from backend.common.data import DomainError, uuid


def normalize_party(party):
    account = party.get("accountId")
    external = party.get("externalAccountId")
    namespace = party.get("externalNamespace")
    if account is not None and external is None and namespace is None:
        return {"accountId": uuid(account)}
    if account is None and external is not None and namespace is not None:
        return {
            "externalAccountId": required_text(external, "externalAccountId", 100),
            "externalNamespace": required_text(namespace, "externalNamespace", 64),
        }
    raise DomainError("BAD_INPUT", "Укажите внутренний счёт либо внешний ID и его источник")


def normalize_transfer(input, account_id, direction):
    debit = normalize_party(input["debit"])
    credit = normalize_party(input["credit"])
    current = debit if direction == "OUT" else credit
    if current.get("accountId") != account_id:
        raise DomainError("BAD_INPUT", "Счёт операции должен совпадать со стороной её направления")
    if debit == credit:
        raise DomainError("BAD_INPUT", "Укажите разные счета списания и зачисления")
    reference = required_text(input["transferRef"], "transferRef", 100)
    if reference.upper() == "NOTPROVIDED":
        raise DomainError("BAD_INPUT", "Нужен устойчивый идентификатор перевода")
    return {
        "namespace": required_text(input["transferNamespace"], "transferNamespace", 64),
        "reference": reference,
        "parties": {"debit": debit, "credit": credit},
    }


def owner_relation(connection, parties):
    owners = []
    for party in parties.values():
        account = party.get("accountId")
        if account:
            row = connection.execute("SELECT client_id FROM accounts WHERE id=%s", (account,)).fetchone()
            if not row:
                raise DomainError("NOT_FOUND", "Внутренний счёт стороны перевода не найден")
            owners.append(row["client_id"])
    if len(owners) < 2:
        return "UNKNOWN", None
    return ("SAME_OWNER" if owners[0] == owners[1] else "DIFFERENT_OWNERS"), "BANK_ACCOUNT_DIRECTORY"


def save_transfer(connection, transfer, transaction_id, direction):
    namespace, reference, parties = transfer["namespace"], transfer["reference"], transfer["parties"]
    relation, evidence = owner_relation(connection, parties)
    connection.execute(
        """INSERT INTO operation_transfers(id,namespace,external_ref,parties,owner_relation,owner_evidence)
        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (namespace,external_ref) DO NOTHING""",
        (uuid4(), namespace, reference, Jsonb(parties), relation, evidence),
    )
    row = connection.execute(
        "SELECT id,parties FROM operation_transfers WHERE namespace=%s AND external_ref=%s",
        (namespace, reference),
    ).fetchone()
    if row["parties"] != parties:
        raise DomainError("CONFLICT", "Ссылка перевода уже привязана к другим сторонам")
    entry = connection.execute(
        """INSERT INTO operation_transfer_entries(transaction_id,transfer_id,direction)
        VALUES (%s,%s,%s) ON CONFLICT (transfer_id,direction) DO NOTHING RETURNING transaction_id""",
        (transaction_id, row["id"], direction),
    ).fetchone()
    if entry is None:
        raise DomainError("CONFLICT", "Запись этого направления уже зарегистрирована для перевода")
