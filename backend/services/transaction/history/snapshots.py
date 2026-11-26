"""Immutable history materialized in the Transaction-owned database."""

import base64
import json
from datetime import UTC, datetime
from tempfile import SpooledTemporaryFile
from uuid import uuid4

from psycopg.types.json import Jsonb

from backend.common.behavior_contract import (
    MAX_HISTORY_ITEMS,
    canonical,
    digest,
    digest_array,
    validate,
)
from backend.common.commands import required_text
from backend.common.data import DomainError, period, uuid
from backend.services.transaction.history.source_coverage import scoped_observation


class HistorySnapshots:
    def __init__(self, db):
        self.db = db

    def prepare(self, parameters):
        p = dict(validate("PrepareRequest", parameters))
        p["clientId"] = uuid(p["clientId"])
        p["requestKey"] = required_text(p["requestKey"], "requestKey", 128)
        start, cutoff = period({"from": p["windowStart"], "to": p["asOf"]})
        # Validate a timezone-aware timestamp without imposing w>=event-time.
        try:
            watermark = datetime.fromisoformat(
                p["ingestedBefore"].replace("Z", "+00:00")
            )
            if watermark.tzinfo is None:
                raise ValueError("Timezone required")
            watermark = watermark.astimezone(UTC)
        except (ValueError, TypeError, AttributeError):
            raise DomainError("BAD_INPUT", "Invalid ingestion watermark") from None
        if (cutoff - start).total_seconds() > 90 * 86400:
            raise DomainError("BAD_INPUT", "History snapshot exceeds 90 days")
        p.update(
            windowStart=start.astimezone(watermark.tzinfo).isoformat(),
            asOf=cutoff.astimezone(watermark.tzinfo).isoformat(),
            ingestedBefore=watermark.isoformat(),
        )
        request_hash = digest(p)
        with self.db.connect() as c:
            # Acquire outside RR, so a waiter begins its MVCC snapshot AFTER
            # the prior request's commit. A transaction lock acquired inside RR
            # could leave that waiter unable to see the winner.
            c.autocommit = True
            lock = "behavior-history:" + p["requestKey"]
            c.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (lock,))
            try:
                with c.transaction(), SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as staged:
                    c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    c.execute("SET LOCAL statement_timeout = '25s'")
                    existing = c.execute(
                        "SELECT * FROM behavior_history_snapshots WHERE request_key=%s",
                        (p["requestKey"],),
                    ).fetchone()
                    if existing:
                        if existing["request_hash"] != request_hash:
                            raise DomainError(
                                "CONFLICT",
                                "History request key has a different context",
                            )
                        return self.wire(existing)
                    captured = c.execute("SELECT clock_timestamp() AS at").fetchone()[
                        "at"
                    ]
                    if not c.execute(
                        "SELECT 1 FROM bank_clients WHERE id=%s", (p["clientId"],)
                    ).fetchone():
                        raise DomainError("NOT_FOUND", "Client does not exist")
                    item_count, facts_digest, scopes = self.capture(c, p, start, cutoff, watermark, staged)
                    coverage_row = c.execute(
                        """SELECT * FROM behavior_observation_coverage WHERE client_id=%s
                        AND currency=%s AND direction=%s AND known_at<%s ORDER BY known_at DESC,id DESC LIMIT 1""",
                        (p["clientId"], p["currency"], p["direction"], min(captured,watermark)),
                    ).fetchone()
                    coverage: dict = dict(
                        mode="UNKNOWN",
                        revision="unknown-v1",
                        intervals=[],
                        declaredBySource=None,
                    )
                    if coverage_row:
                        coverage = dict(
                            mode=coverage_row["mode"],
                            revision=coverage_row["revision"],
                            intervals=coverage_row["intervals"],
                            declaredBySource=coverage_row["declared_by_source"],
                        )
                    scoped = scoped_observation(c, p, scopes, start, cutoff, min(captured, watermark))
                    if scoped is not None:
                        coverage = scoped
                    identifier = str(uuid4())
                    result = dict(
                        snapshotId=identifier,
                        digest=facts_digest,
                        itemCount=item_count,
                        capturedAt=captured.isoformat(),
                        coverage=coverage,
                        selection=p,
                    )
                    validate("Snapshot", result)
                    for interval in coverage["intervals"]:
                        period(interval)
                    c.execute(
                        """INSERT INTO behavior_history_snapshots(id,request_key,request_hash,client_id,currency,
                        direction,window_start,as_of,ingested_before,captured_at,item_count,digest,coverage)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            identifier,
                            p["requestKey"],
                            request_hash,
                            p["clientId"],
                            p["currency"],
                            p["direction"],
                            start,
                            cutoff,
                            watermark,
                            captured,
                            item_count,
                            result["digest"],
                            Jsonb(coverage),
                        ),
                    )
                    staged.seek(0)
                    with c.cursor() as cursor, cursor.copy(
                        """COPY behavior_history_snapshot_items(snapshot_id,ordinal,transaction_id,facts)
                           FROM STDIN"""
                    ) as copy:
                        for ordinal, line in enumerate(staged):
                            transaction_id, raw = line.decode("utf-8").rstrip("\n").split(" ", 1)
                            copy.write_row((identifier, ordinal, transaction_id, raw))
                    return result
            finally:
                c.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lock,))

    def capture(self, connection, parameters, start, cutoff, watermark, staged):
        scopes = set()
        count = 0
        with connection.cursor(name="behavior_snapshot_rows") as cursor:
            cursor.itersize = 1000
            cursor.execute(
                """SELECT t.*,a.client_id,coalesce(m.channel,'UNKNOWN') AS channel,m.kind AS operation_kind
                FROM transactions t JOIN accounts a ON a.id=t.account_id
                LEFT JOIN managed_operations m ON m.transaction_id=t.id
                WHERE a.client_id=%s AND t.currency=%s AND t.direction=%s
                AND t.occurred_at>=%s AND t.occurred_at<%s AND t.ingested_at<%s
                ORDER BY t.occurred_at,t.id LIMIT %s""",
                (parameters["clientId"], parameters["currency"], parameters["direction"],
                 start, cutoff, watermark, MAX_HISTORY_ITEMS + 1),
            )

            def serialized_items():
                nonlocal count
                for count, row in enumerate(cursor, 1):
                    if count > MAX_HISTORY_ITEMS:
                        raise DomainError(
                            "BAD_INPUT", f"HISTORY_LIMIT: more than {MAX_HISTORY_ITEMS} operations"
                        )
                    item = self.item(row)
                    encoded = canonical(item).encode("utf-8")
                    staged.write(str(row["id"]).encode("ascii") + b" " + encoded + b"\n")
                    scopes.add((item["accountId"], row["source"], row["channel"]))
                    yield encoded

            facts_digest = digest_array(serialized_items())
        return count, facts_digest, scopes

    def page(self, parameters):
        p = validate("PageRequest", parameters)
        identifier = uuid(p["snapshotId"])
        offset = 0
        if p.get("pageToken"):
            try:
                context = json.loads(
                    base64.b64decode(p["pageToken"], altchars=b"-_", validate=True)
                )
                if (
                    set(context) != {"snapshotId", "offset"}
                    or context["snapshotId"] != identifier
                    or type(context["offset"]) is not int
                    or context["offset"] < 0
                ):
                    raise ValueError()
                offset = context["offset"]
            except (ValueError, TypeError, KeyError):
                raise DomainError("BAD_INPUT", "Invalid snapshot cursor") from None
        row = self.db.one(
            "SELECT * FROM behavior_history_snapshots WHERE id=%s", (identifier,)
        )
        if not row:
            raise DomainError("NOT_FOUND", "History snapshot does not exist")
        if offset > row["item_count"]:
            raise DomainError("BAD_INPUT", "Snapshot cursor exceeds item count")
        limit = p.get("limit", 100)
        rows = self.db.query(
            """SELECT facts FROM behavior_history_snapshot_items
            WHERE snapshot_id=%s AND ordinal>=%s ORDER BY ordinal LIMIT %s""",
            (identifier, offset, limit),
        )
        following = offset + len(rows)
        token = (
            base64.urlsafe_b64encode(
                json.dumps(dict(snapshotId=identifier, offset=following)).encode()
            ).decode()
            if following < row["item_count"]
            else None
        )
        return validate(
            "Page",
            dict(
                snapshotId=identifier,
                digest=row["digest"],
                itemCount=row["item_count"],
                items=[r["facts"] for r in rows],
                nextPageToken=token,
            ),
        )

    @staticmethod
    def wire(row):
        return validate(
            "Snapshot",
            dict(
                snapshotId=str(row["id"]),
                digest=row["digest"],
                itemCount=row["item_count"],
                capturedAt=row["captured_at"].isoformat(),
                coverage=row["coverage"],
                selection=dict(
                    requestKey=row["request_key"],
                    clientId=str(row["client_id"]),
                    currency=row["currency"],
                    direction=row["direction"],
                    windowStart=row["window_start"].isoformat(),
                    asOf=row["as_of"].isoformat(),
                    ingestedBefore=row["ingested_before"].isoformat(),
                ),
            ),
        )

    @staticmethod
    def item(row):
        return validate(
            "Item",
            dict(
                id=str(row["id"]),
                clientId=str(row["client_id"]),
                accountId=str(row["account_id"]),
                counterpartyId=str(row["counterparty_id"])
                if row["counterparty_id"]
                else None,
                amount=str(row["amount"]),
                currency=row["currency"],
                direction=row["direction"],
                occurredAt=row["occurred_at"].isoformat(),
                ingestedAt=row["ingested_at"].isoformat(),
                **({"timePrecision": row["time_precision"]} if row.get("time_precision") else {}),
                **({"operationKind": row["operation_kind"], "operationChannel": row["channel"]}
                   if row.get("operation_kind") else {}),
            ),
        )
