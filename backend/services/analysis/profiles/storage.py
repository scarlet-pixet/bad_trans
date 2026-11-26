"""One metric calculation shared by canonical and legacy profile projections."""

from contextlib import nullcontext
from uuid import uuid4

from psycopg.types.json import Jsonb

from backend.common.behavior_contract import (
    MAX_HISTORY_PAGE,
    canonical,
    digest,
    digest_array,
    validate,
)
from backend.common.data import DomainError
from backend.services.analysis.profiles.metrics import ALGORITHM, METRICS_SCHEMA_VERSION, WINDOWS, build_metrics, timestamp

from backend.services.analysis.profiles.history_policy import VERSION as HISTORY_POLICY, HASH as POLICY_HASH, LEGACY_VERSION, LEGACY_HASH, validate_reference
from backend.services.analysis.profiles.metrics import LEGACY_ALGORITHM, LEGACY_METRICS_SCHEMA_VERSION


class HistoryBatch(list):
    def __init__(self, rows, snapshot):
        super().__init__(rows)
        self.snapshot = validate("Snapshot", snapshot)


def prepare_snapshot(client, tx, cutoff, watermark):
    from datetime import timedelta

    parameters = dict(
        clientId=str(tx["clientId"]),
        currency=tx["currency"],
        direction=tx["direction"],
        windowStart=(timestamp(cutoff) - timedelta(days=90)).isoformat(),
        asOf=timestamp(cutoff).isoformat(),
        ingestedBefore=timestamp(watermark).isoformat(),
    )
    return validate(
        "Snapshot",
        client.prepare_behavior_history(
            dict(parameters, requestKey="history:" + digest(parameters))
        ),
    )


def read_snapshot_history(client, snapshot, *, deadline=None):
    import time

    snapshot = validate("Snapshot", snapshot)
    rows, token, seen_tokens = [], None, set()
    page_size = MAX_HISTORY_PAGE if snapshot["itemCount"] > 10000 else 100
    open_reader = getattr(client, "history_page_reader", None)
    reader = open_reader() if open_reader else nullcontext(client.get_behavior_history_page)
    with reader as read_page:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Profile history read budget exhausted")
            page = validate(
                "Page", read_page({"snapshotId": snapshot["snapshotId"], "pageToken": token, "limit": page_size})
            )
            if any(
                page[key] != snapshot[key] for key in ("snapshotId", "digest", "itemCount")
            ) or len(page["items"]) > page_size:
                raise DomainError("DATA_ACCESS_ERROR", "Inconsistent history snapshot page")
            rows.extend(page["items"])
            if len(rows) > snapshot["itemCount"]:
                raise DomainError("DATA_ACCESS_ERROR", "History snapshot count mismatch")
            token = page["nextPageToken"]
            if not token:
                break
            if token in seen_tokens or not page["items"]:
                raise DomainError("DATA_ACCESS_ERROR", "History pagination did not advance")
            seen_tokens.add(token)
    if (
        len(rows) != snapshot["itemCount"]
        or len({r["id"] for r in rows}) != len(rows)
        or digest_array(canonical(row).encode("utf-8") for row in rows) != snapshot["digest"]
    ):
        raise DomainError("DATA_ACCESS_ERROR", "History snapshot digest/count mismatch")
    return HistoryBatch(rows, snapshot)


def fetch_snapshot_history(client, tx, cutoff, watermark):
    return read_snapshot_history(client, prepare_snapshot(client, tx, cutoff, watermark))


def version_wire(row):
    # Relational identity/version metadata is authoritative; metric payloads
    # may carry the same schema version without duplicate keyword arguments.
    return {
        **row["metrics"],
        "id": str(row["id"]),
        "version": row["version"],
        "calculationId": str(row["calculation_id"]),
        "metricsSchemaVersion": row["metrics_schema_version"],
        "historyPolicyVersion": row["history_policy_version"],
    }


def save_profile_versions(
    conn, tx, rows, cutoff, watermark, origin="EVENT", *, job_key=None, feedback_ref=None
):
    """Publish exactly one complete set per fixed context, in caller's txn.

    The durable scheduler supplies its persisted request key; this layer rejects
    reuse with another context and atomically publishes all three windows.
    """
    if origin not in ("EVENT", "MANUAL", "SCHEDULED") or not isinstance(
        rows, HistoryBatch
    ):
        raise DomainError(
            "BAD_INPUT", "Profile requires origin and immutable owner history"
        )
    if (origin == "EVENT") != bool(tx.get("id")):
        raise DomainError(
            "BAD_INPUT", "Only EVENT profiles require a subject transaction"
        )
    snapshot = rows.snapshot
    if feedback_ref is not None:
        validate_reference(feedback_ref)
        from backend.services.analysis.feedback.events import selection
        decisions=selection(conn,feedback_ref,rows)
    else:
        decisions=[]
    algorithm=ALGORITHM if feedback_ref is not None else LEGACY_ALGORITHM
    schema=METRICS_SCHEMA_VERSION if feedback_ref is not None else LEGACY_METRICS_SCHEMA_VERSION
    history_policy=HISTORY_POLICY if feedback_ref is not None else LEGACY_VERSION
    policy_hash=POLICY_HASH if feedback_ref is not None else LEGACY_HASH
    feedback_revision=feedback_ref['revision'] if feedback_ref is not None else 0
    selection = snapshot["selection"]
    expected = dict(
        clientId=str(tx["clientId"]),
        currency=tx["currency"],
        direction=tx["direction"],
        asOf=timestamp(cutoff).isoformat(),
        ingestedBefore=timestamp(watermark).isoformat(),
    )
    from datetime import timedelta

    expected["windowStart"] = (timestamp(cutoff) - timedelta(days=90)).isoformat()
    if any(selection[k] != v for k, v in expected.items()):
        raise DomainError(
            "CONFLICT", "History selection does not match calculation context"
        )
    context = dict(
        clientId=str(tx["clientId"]),
        currency=tx["currency"],
        direction=tx["direction"],
        asOf=timestamp(cutoff).isoformat(),
        ingestedBefore=timestamp(watermark).isoformat(),
        cutoffTransactionId=str(tx["id"]) if tx.get("id") else None,
        origin=origin,
        algorithmVersion=algorithm,
        metricsSchemaVersion=schema,
        historyPolicyVersion=history_policy,
        historyPolicyHash=policy_hash,
        timeZone="UTC",
        feedbackRevision=feedback_revision,
        coverageRevision=snapshot["coverage"]["revision"],
        historySnapshotId=snapshot["snapshotId"],
        historyDigest=snapshot["digest"],
        snapshotCapturedAt=snapshot["capturedAt"],
    )
    if feedback_ref is not None:context['feedbackReference']=feedback_ref
    context_hash = digest(context)
    job_key = job_key or "profile:" + context_hash
    if not isinstance(job_key, str) or not 1 <= len(job_key) <= 128:
        raise DomainError("BAD_INPUT", "Invalid calculation job key")
    # Group serializes version numbers; job lock also detects a key reused for
    # another group without leaking a UniqueViolation or leaving a partial set.
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("profile-job:" + job_key,),
    )
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"profile-group:{tx['clientId']}:{tx['currency']}:{tx['direction']}",),
    )
    existing = conn.execute(
        "SELECT * FROM profile_calculations WHERE job_key=%s", (job_key,)
    ).fetchone()
    if existing:
        if existing["context_hash"] != context_hash or existing["context"] != context:
            raise DomainError("CONFLICT", "Calculation job has a different context")
        if existing["status"] != "COMPLETED":
            raise DomainError("CONFLICT", "Calculation is not completed")
        return [
            version_wire(r)
            for r in conn.execute(
                "SELECT * FROM profile_versions WHERE calculation_id=%s ORDER BY window_days",
                (existing["id"],),
            )
        ]
    calculation = str(uuid4())
    conn.execute(
        """INSERT INTO profile_calculations(id,job_key,context,context_hash,history_snapshot_id,
        history_digest,snapshot_captured_at,status,attempts) VALUES (%s,%s,%s,%s,%s,%s,%s,'RUNNING',1)""",
        (
            calculation,
            job_key,
            Jsonb(context),
            context_hash,
            snapshot["snapshotId"],
            snapshot["digest"],
            snapshot["capturedAt"],
        ),
    )
    versions = []
    for days in WINDOWS:
        metrics = build_metrics(
            rows,
            timestamp(cutoff),
            timestamp(watermark),
            tx["currency"],
            tx["direction"],
            days,
            tx.get("id"),
            coverage=snapshot["coverage"],
            client_id=str(tx["clientId"]),
            feedback=dict(reference=feedback_ref,decisions=decisions) if feedback_ref is not None else None,
            legacy=feedback_ref is None,
        )
        version = conn.execute(
            """SELECT coalesce(max(version),0)+1 AS v FROM profile_versions
            WHERE client_id=%s AND currency=%s AND direction=%s AND window_days=%s""",
            (tx["clientId"], tx["currency"], tx["direction"], days),
        ).fetchone()["v"]
        identifier = str(uuid4())
        row = conn.execute(
            """INSERT INTO profile_versions(id,client_id,currency,direction,window_days,version,
            as_of,ingested_before,cutoff_transaction_id,origin,metrics,calculation_id,metrics_schema_version,
            algorithm_version,history_policy_version,history_policy_hash,time_zone,feedback_revision,coverage_revision)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'UTC',%s,%s) RETURNING *""",
            (
                identifier,
                tx["clientId"],
                tx["currency"],
                tx["direction"],
                days,
                version,
                cutoff,
                watermark,
                tx.get("id"),
                origin,
                Jsonb(metrics),
                calculation,
                schema,
                algorithm,
                history_policy,
                policy_hash,
                feedback_revision,
                context["coverageRevision"],
            ),
        ).fetchone()
        versions.append(version_wire(row))
    conn.execute(
        "UPDATE profile_calculations SET status='COMPLETED' WHERE id=%s", (calculation,)
    )
    return versions
