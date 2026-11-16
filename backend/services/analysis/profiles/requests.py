"""Manual profile requests pin time and feedback just like scheduled jobs."""

from psycopg.types.json import Jsonb
from backend.common.behavior_contract import digest
from backend.common.commands import owned_connection
from backend.common.data import DomainError
from backend.services.analysis.feedback.events import capture
from backend.services.analysis.profiles.observation import timestamp
from backend.services.analysis.profiles.metrics import ALGORITHM


def prepare(c, tx, cutoff, actor_id, key):
    intent = digest(dict(group=tx, cutoff=timestamp(cutoff).isoformat()))
    request_key = "manual-profile:" + digest(dict(actor=actor_id, key=key))
    with owned_connection(c) as gate:
        gate.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (request_key,)
        )
        existing = gate.execute(
            "SELECT * FROM profile_build_requests WHERE request_key=%s", (request_key,)
        ).fetchone()
        if existing:
            if existing["intent_hash"] != intent:
                raise DomainError("CONFLICT", "Manual profile intent changed")
            if existing["context"]["algorithmVersion"] != ALGORITHM:
                raise DomainError("CONFLICT", "Manual profile algorithm unsupported")
            return existing
        clock = gate.execute("SELECT clock_timestamp() AS at").fetchone()
        if clock is None:
            raise RuntimeError("Database clock is unavailable")
        now = clock["at"]
        context = dict(
            transaction=tx,
            asOf=timestamp(cutoff).isoformat(),
            ingestedBefore=now.isoformat(),
            algorithmVersion=ALGORITHM,
            feedbackReference=capture(gate, "MANUAL_PROFILE"),
        )
        result = gate.execute(
            "INSERT INTO profile_build_requests(request_key,intent_hash,context) VALUES (%s,%s,%s) RETURNING *",
            (request_key, intent, Jsonb(context)),
        ).fetchone()
        if result is None:
            raise RuntimeError("Manual profile request was not saved")
        return result
