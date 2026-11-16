from datetime import datetime, UTC
from typing import Any


def activity(db, owner):
    """Aggregates only the current owner's tables; never expose error payloads."""
    with db.connect() as c:
        c.execute("SET LOCAL statement_timeout='500ms'")
        outbox = c.execute(
            "SELECT count(*) FILTER (WHERE published_at IS NULL) AS pending,max(published_at) AS last_success FROM outbox_events"
        ).fetchone()
        failures = (
            c.execute(
                "SELECT status,count(*) AS count FROM event_failures GROUP BY status"
            ).fetchall()
            if owner != "transaction"
            else []
        )
        result: dict[str, Any] = dict(
            pendingOutbox=outbox["pending"],
            lastPublishedAt=outbox["last_success"],
            eventFailures={r["status"]: r["count"] for r in failures},
        )
        if owner == "analysis":
            jobs = c.execute(
                "SELECT status,count(*) AS count FROM profile_refresh_jobs GROUP BY status"
            ).fetchall()
            result["refreshJobs"] = {r["status"]: r["count"] for r in jobs}
            result["lastRefreshAt"] = c.execute(
                "SELECT max(completed_at) AS at FROM profile_refresh_jobs WHERE status='COMPLETED'"
            ).fetchone()["at"]
        result["measuredAt"] = datetime.now(UTC).isoformat()
        return {
            k: v.isoformat() if isinstance(v, datetime) else v
            for k, v in result.items()
        }
