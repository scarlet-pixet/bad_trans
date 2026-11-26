"""Resolve confirmed intervals across all accounts and observed or declared feeds."""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from backend.common.behavior_contract import digest
from backend.common.commands import require_role, required_text
from backend.common.data import DomainError, period, uuid


def merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(end, merged[-1][1])
        else:
            merged.append((start, end))
    return merged


def intersection(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        a, b = left[i]
        c, d = right[j]
        if max(a, c) < min(b, d):
            result.append((max(a, c), min(b, d)))
        if b < d:
            i += 1
        else:
            j += 1
    return result


def wire(intervals):
    return [{"from": start.astimezone(UTC).isoformat(), "to": end.astimezone(UTC).isoformat()} for start, end in intervals]


def declare(db, actor, input):
    require_role(actor, "ADMIN")
    account = uuid(input["accountId"])
    source = required_text(input["source"], "source", 32)
    revision = required_text(input["revision"], "revision", 100)
    channel, direction, feature = (input[field] for field in ("channel", "direction", "feature"))
    if (channel not in ("POS","ONLINE","ATM","BRANCH","BANK_API","UNKNOWN")
            or direction not in ("IN","OUT") or feature not in ("DAY","HOUR")):
        raise DomainError("BAD_INPUT", "Некорректный канал, направление или признак")
    if not isinstance(input["intervals"], list) or len(input["intervals"]) > 100:
        raise DomainError("BAD_INPUT", "Разрешено до 100 интервалов")
    if source == "banksim" and feature == "HOUR" and input["intervals"]:
        raise DomainError("BAD_INPUT", "BankSim передаёт даты и не подтверждает покрытие по часам")
    intervals = wire(merge([period(item) for item in input["intervals"]]))
    key = (account,source,channel,direction,feature,revision)
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM accounts WHERE id=%s", (account,)).fetchone():
            raise DomainError("NOT_FOUND", "Счёт покрытия не найден")
        c.execute(
            """INSERT INTO source_observation_declarations
            (account_id,source,channel,direction,feature,revision,id,intervals)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (account_id,source,channel,direction,feature,revision) DO NOTHING""",
            (*key, uuid4(), Jsonb(intervals)),
        )
        row = c.execute(
            """SELECT id,intervals FROM source_observation_declarations
            WHERE (account_id,source,channel,direction,feature,revision)=(%s,%s,%s,%s,%s,%s)""",key,
        ).fetchone()
        if row["intervals"] != intervals:
            raise DomainError("CONFLICT", "Эта версия уже содержит другие интервалы")
        return {"id": str(row["id"])}


def resolve_scopes(
    accounts: Sequence[str], observations: Iterable[tuple[str, str, str]],
    declarations: Sequence[dict], start: datetime, end: datetime,
):
    if not declarations and len(accounts) < 2:
        return None
    values = {
        (str(row["account_id"]),row["source"],row["channel"],row["feature"]): row
        for row in declarations
    }
    scopes = {(account,source,channel) for account,source,channel,_ in values} | set(observations)
    absent = set(accounts) - {account for account,_,_ in scopes}
    undeclared = set(accounts) - {account for account,_,_,_ in values}
    intervals = {feature: ([] if absent else [(start,end)]) for feature in ("DAY","HOUR")}
    gaps = {feature: len(absent) for feature in intervals}
    for account, source, channel in sorted(scopes):
        for feature, current in intervals.items():
            item = values.get((account, source, channel, feature))
            declared = merge([period(item) for item in item["intervals"]]) if item else []
            clipped = intersection([(start,end)], declared)
            if clipped != [(start,end)]:
                gaps[feature] += 1
            intervals[feature] = intersection(current, clipped)
    return {
        "mode": "DECLARED_COMPLETE" if intervals["DAY"] else "UNKNOWN",
        "revision": "scoped:" + digest({
            "declarations": sorted(str(x["id"]) for x in declarations),
            "scopes": sorted(scopes), "missingAccounts": sorted(undeclared),
        }),
        "declaredBySource": None,
        "intervals": wire(intervals["DAY"]),
        "hourIntervals": wire(intersection(intervals["DAY"],intervals["HOUR"])),
        "gaps": {"accountsWithoutScope": len(undeclared), "day": gaps["DAY"], "hour": gaps["HOUR"]},
    }


def scoped_observation(connection, parameters, observed_scopes, start, end, known_before):
    client, currency, direction = (parameters[field] for field in ("clientId","currency","direction"))
    declarations = connection.execute(
        """SELECT DISTINCT ON (d.account_id,d.source,d.channel,d.feature) d.*
        FROM source_observation_declarations d JOIN accounts a ON a.id=d.account_id
        WHERE a.client_id=%s AND a.currency=%s AND d.direction=%s AND d.known_at<%s
        ORDER BY d.account_id,d.source,d.channel,d.feature,d.known_at DESC,d.id DESC""",
        (client,currency,direction,known_before),
    ).fetchall()
    accounts = connection.execute(
        "SELECT id FROM accounts WHERE client_id=%s AND currency=%s", (client,currency),
    ).fetchall()
    return resolve_scopes(
        [str(row["id"]) for row in accounts],
        observed_scopes,
        declarations,start,end,
    )
