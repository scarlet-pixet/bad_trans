"""Durable refresh requests, short DB transactions, one session lock per job.

The lock connection is also the publishing connection: a worker that loses its
lock cannot publish on a replacement connection. Process death releases the
session lock; persisted RUNNING work is picked up with the same context.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from backend.common.data import DomainError, currency, uuid
from backend.services.analysis.profiles.metrics import ALGORITHM, METRICS_SCHEMA_VERSION
from backend.services.analysis.profiles.storage import (
    HISTORY_POLICY,
    POLICY_HASH,
    prepare_snapshot,
    read_snapshot_history,
    save_profile_versions,
)


class ProfileRefresh:
    def __init__(self, db, client):
        self.db, self.client = db, client

    def discover(self, limit=100):
        # The short discovery lock prevents two workers advancing the durable
        # cursor out of order. No work/job locks are acquired in this section.
        with self.db.connect() as c:
            c.autocommit = True
            lock = "profile-group-discovery"
            if not c.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok", (lock,)
            ).fetchone()["ok"]:
                return 0
            try:
                state = c.execute(
                    "SELECT discovery_cursor FROM profile_scheduler_state WHERE id=1"
                ).fetchone()
                page = self.client.get_behavior_groups(
                    pageToken=state["discovery_cursor"], limit=limit
                )
                with c.transaction():
                    for group in page["items"]:
                        client = uuid(group["clientId"])
                        curr = currency(group["currency"])
                        if group["direction"] not in ("IN", "OUT"):
                            raise DomainError(
                                "DATA_ACCESS_ERROR", "Invalid owner group"
                            )
                        c.execute(
                            """INSERT INTO profile_refresh_groups(client_id,currency,direction)
                            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (client, curr, group["direction"]),
                        )
                    c.execute(
                        "UPDATE profile_scheduler_state SET discovery_cursor=%s,last_discovered_at=now(),last_error=NULL WHERE id=1",
                        (page["nextPageToken"],),
                    )
                return len(page["items"])
            except Exception as exc:
                c.execute(
                    "UPDATE profile_scheduler_state SET last_error=%s WHERE id=1",
                    (type(exc).__name__,),
                )
                raise
            finally:
                c.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lock,))

    def enqueue(self, *, now=None, refresh_seconds=300, batch=10):
        now = now or datetime.now(UTC)
        if (
            now.tzinfo is None
            or type(refresh_seconds) is not int
            or refresh_seconds < 1
            or type(batch) is not int
            or not 1 <= batch <= 100
        ):
            raise ValueError("Invalid scheduler configuration")
        now = now.astimezone(UTC)
        cutoff = datetime.fromtimestamp(
            int(now.timestamp()) // refresh_seconds * refresh_seconds, UTC
        )
        count = 0
        with self.db.connect() as c:
            groups = c.execute(
            """SELECT g.* FROM profile_refresh_groups g WHERE (next_due_at<=%s OR EXISTS(
                SELECT 1 FROM feedback_journal j WHERE j.client_id=g.client_id AND j.applied
                AND j.knowledge_revision>coalesce((SELECT max(p.feedback_revision) FROM profile_versions p
                   WHERE p.client_id=g.client_id AND p.currency=g.currency AND p.direction=g.direction
                   AND p.origin='SCHEDULED' AND p.as_of<=now()),0)))
                AND NOT EXISTS(SELECT 1 FROM profile_refresh_jobs j WHERE j.client_id=g.client_id
                  AND j.currency=g.currency AND j.direction=g.direction AND j.status IN ('PENDING','RUNNING','RETRY'))
                ORDER BY next_due_at,client_id,currency,direction LIMIT %s FOR UPDATE SKIP LOCKED""",
                (now, batch),
            ).fetchall()
            for g in groups:
                identifier = str(uuid4())
                context: dict = dict(
                    clientId=str(g["client_id"]),
                    currency=g["currency"],
                    direction=g["direction"],
                    asOf=cutoff.isoformat(),
                    ingestedBefore=now.isoformat(),
                    algorithmVersion=ALGORITHM,
                    metricsSchemaVersion=METRICS_SCHEMA_VERSION,
                    historyPolicyVersion=HISTORY_POLICY,
                    historyPolicyHash=POLICY_HASH,
                )
                from backend.services.analysis.feedback.events import capture
                context['feedbackReference']=capture(c,'SCHEDULED')
                inserted = c.execute(
                    """INSERT INTO profile_refresh_jobs(id,client_id,currency,direction,as_of,algorithm_version,context,feedback_revision)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                    (
                        identifier,
                        g["client_id"],
                        g["currency"],
                        g["direction"],
                        cutoff,
                        ALGORITHM,
                        Jsonb(context),
                        context['feedbackReference']['revision'],
                    ),
                ).fetchone()
                count += bool(inserted)
                c.execute(
                    "UPDATE profile_refresh_groups SET next_due_at=%s WHERE client_id=%s AND currency=%s AND direction=%s",
                    (
                        now + timedelta(seconds=refresh_seconds),
                        g["client_id"],
                        g["currency"],
                        g["direction"],
                    ),
                )
        return count

    def drain(self, *, batch=2, budget_seconds: float = 20):
        if (
            type(batch) is not int
            or not 1 <= batch <= 100
            or not 0 < budget_seconds <= 300
        ):
            raise ValueError("Invalid worker limits")
        deadline = time.monotonic() + budget_seconds
        candidates = self.db.query("""SELECT id FROM profile_refresh_jobs
            WHERE status IN ('PENDING','RUNNING','RETRY') AND available_at<=now()
            ORDER BY available_at,created_at,id LIMIT 256""")
        completed, attempted = 0, 0
        for item in candidates:
            if attempted >= batch or time.monotonic() >= deadline:
                break
            with self.db.connect() as c:
                c.autocommit = True
                lock = "profile-refresh:" + str(item["id"])
                if not c.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok", (lock,)
                ).fetchone()["ok"]:
                    continue
                job = None
                try:
                    job = c.execute(
                        "SELECT * FROM profile_refresh_jobs WHERE id=%s AND status IN ('PENDING','RUNNING','RETRY') AND available_at<=now()",
                        (item["id"],),
                    ).fetchone()
                    if not job:
                        continue
                    attempted += 1
                    c.execute(
                        "UPDATE profile_refresh_jobs SET status='RUNNING',attempts=attempts+1 WHERE id=%s",
                        (job["id"],),
                    )
                    p = job["context"]
                    if (
                        p["algorithmVersion"],
                        p["metricsSchemaVersion"],
                        p["historyPolicyVersion"],
                        p["historyPolicyHash"],
                    ) != (
                        ALGORITHM,
                        METRICS_SCHEMA_VERSION,
                        HISTORY_POLICY,
                        POLICY_HASH,
                    ):
                        c.execute(
                            "UPDATE profile_refresh_jobs SET status='BLOCKED',last_error='VERSION_MISMATCH' WHERE id=%s",
                            (job["id"],),
                        )
                        c.execute('UPDATE profile_refresh_groups SET next_due_at=least(next_due_at,clock_timestamp()) WHERE client_id=%s AND currency=%s AND direction=%s',
                                  (job['client_id'],job['currency'],job['direction']))
                        continue
                    from backend.services.analysis.profiles.observation import timestamp

                    cutoff, watermark = (
                        timestamp(p["asOf"]),
                        timestamp(p["ingestedBefore"]),
                    )
                    snapshot = job["snapshot"]
                    if snapshot is None:
                        snapshot = prepare_snapshot(self.client, p, cutoff, watermark)
                        c.execute(
                            "UPDATE profile_refresh_jobs SET snapshot=%s WHERE id=%s",
                            (Jsonb(snapshot), job["id"]),
                        )
                    rows = read_snapshot_history(
                        self.client, snapshot, deadline=deadline
                    )
                    with c.transaction():
                        versions = save_profile_versions(
                            c,
                            p,
                            rows,
                            cutoff,
                            watermark,
                            "SCHEDULED",
                            job_key="refresh:" + str(job["id"]),
                            feedback_ref=p.get('feedbackReference'),
                        )
                        c.execute(
                            "UPDATE profile_refresh_jobs SET status='COMPLETED',calculation_id=%s,completed_at=now(),last_error=NULL WHERE id=%s",
                            (versions[0]["calculationId"], job["id"]),
                        )
                    completed += 1
                except Exception as exc:
                    # Never log exception text/DSNs. If this connection died,
                    # leave durable RUNNING for recovery rather than reconnect.
                    if not c.closed and job is not None:
                        c.execute(
                            "UPDATE profile_refresh_jobs SET status='RETRY',available_at=now()+%s*interval '1 second',last_error=%s WHERE id=%s",
                            (
                                min(300, 2 ** min(job["attempts"] + 1, 8)),
                                type(exc).__name__,
                                item["id"],
                            ),
                        )
                    logging.warning("profiles.refresh.retry %s", type(exc).__name__)
                finally:
                    if not c.closed:
                        c.execute(
                            "SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lock,)
                        )
        return completed

    def cleanup_pages(self):
        with self.db.connect() as c:
            c.execute(
                "DELETE FROM profile_page_selections WHERE id IN (SELECT id FROM profile_page_selections WHERE expires_at<=now() ORDER BY expires_at LIMIT 20)"
            )
