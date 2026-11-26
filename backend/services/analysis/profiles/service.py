"""Automatic, versioned profiles and explainability in the Analysis-owned DB."""

import logging
import os
import time
from datetime import UTC, datetime


from backend.common.commands import require_role
from backend.common.data import DomainError, uuid
from backend.services.analysis.profiles.metrics import WINDOWS, timestamp
from backend.services.analysis.profiles.public_metrics import public_metrics




from backend.services.analysis.assessments.queries import AssessmentQueries

class ProfileService(AssessmentQueries):
    def compare_profile_runs(self, actor, firstId, secondId, offset=0, limit=20):
        from backend.services.analysis.profiles.run_comparison import compare_profile_runs
        return compare_profile_runs(self.db, actor, firstId, secondId, offset, limit)

    def start_profile_run(self, actor, selection, idempotencyKey):
        from backend.services.analysis.profiles.bulk import ProfileRuns
        return ProfileRuns(self.db, self.client).start(actor, selection, idempotencyKey)

    def get_profile_run(self, actor, id, offset=0, limit=50):
        from backend.services.analysis.profiles.bulk import ProfileRuns
        return ProfileRuns(self.db, self.client).get(actor, id, offset, limit)

    def list_profile_runs(self, actor, limit=20, offset=0):
        from backend.services.analysis.profiles.bulk import ProfileRuns
        return ProfileRuns(self.db, self.client).list(actor, limit, offset)

    def compare_profile_versions(self, actor, firstId, secondId):
        from backend.services.analysis.profiles.workspace import WorkspaceService
        return WorkspaceService(self.db).compare_profile_versions(actor, firstId, secondId)

    def get_operation_processing(self, actor, transactionId, eventId=None):
        from backend.services.analysis.assessments.processing import get_processing
        return get_processing(self.db, actor, transactionId, eventId)

    def get_profile_version(self,actor,id):
        from backend.services.analysis.profiles.workspace import WorkspaceService
        return WorkspaceService(self.db).get_profile_version(actor,id)


    def __init__(self, db, transaction_client):
        self.db, self.client = db, transaction_client

    def get_behavior_profiles(self, actor, clientId=None, windowDays=30, history=False):
        require_role(actor, "MANAGER")
        if type(windowDays) is not int or windowDays not in WINDOWS:
            raise DomainError("BAD_INPUT", "Окно профиля: 7, 30 или 90 дней")
        if type(history) is not bool:
            raise DomainError("BAD_INPUT", "history должен быть boolean")
        client = uuid(clientId) if clientId else None
        prefix = "" if history else "DISTINCT ON (client_id,currency,direction)"
        rows = self.db.query(
            f"""SELECT {prefix} * FROM profile_versions
            WHERE window_days=%s AND (%s::uuid IS NULL OR client_id=%s) AND as_of<=now()
            ORDER BY client_id,currency,direction,as_of DESC,version DESC LIMIT 100""",
            (windowDays, client, client),
        )
        return [self.profile_wire(r) for r in rows]

    def get_behavior_profile_page(self, actor, **options):
        from backend.services.analysis.profiles.pages import ProfilePages
        return ProfilePages(self.db).get(actor, **options)

    def get_profile_refresh_status(self, actor):
        require_role(actor, "MANAGER", "ADMIN")
        state = self.db.one("SELECT discovery_cursor,last_discovered_at,last_error FROM profile_scheduler_state WHERE id=1")
        counts = self.db.query("SELECT status,count(*) AS count FROM profile_refresh_jobs GROUP BY status")
        jobs = self.db.query("SELECT id,status,attempts,last_error,as_of,created_at,available_at,completed_at FROM profile_refresh_jobs ORDER BY created_at DESC,id DESC LIMIT 50")
        return dict(discovery=state, jobs=jobs, counts={r["status"]:r["count"] for r in counts},
                    groups=self.db.one("SELECT count(*) AS n FROM profile_refresh_groups")["n"])

    def get_feedback_status(self,actor):
        require_role(actor,'MANAGER','ADMIN')
        from backend.services.analysis.feedback.events import status
        return status(self.db)

    @staticmethod
    def profile_wire(row, served_at=None):
        served = timestamp(served_at or datetime.now(UTC))
        as_of = timestamp(row["as_of"])
        elapsed = (served-as_of).total_seconds()
        threshold = max(1, int(os.getenv("PROFILE_REFRESH_SECONDS", "300")))
        freshness = dict(servedAt=served.isoformat(),ageSeconds=max(0,elapsed),
                         clockSkewSeconds=max(0,-elapsed),thresholdSeconds=threshold,
                         status="FUTURE_CUTOFF" if elapsed<0 else "STALE" if elapsed>threshold else "FRESH")
        return {
            **public_metrics(row["metrics"]),
            "id": str(row["id"]),
            "clientId": str(row["client_id"]),
            "currency": row["currency"],
            "direction": row["direction"],
            "version": row["version"],
            "origin": row["origin"],
            "asOf": as_of.isoformat(),
            "windowDays": row["window_days"],
            "createdAt": row["created_at"].isoformat(),
            "freshness": freshness,
        }





    def refresh_once(self):
        from backend.services.analysis.profiles.refresh import ProfileRefresh
        worker = ProfileRefresh(self.db, self.client)
        try:
            worker.discover()
        except Exception as exc:
            # Discovery outage must not prevent recovery of already known jobs.
            logging.warning("profiles.discovery.retry %s", type(exc).__name__)
        batch = max(1,min(100,int(os.getenv("PROFILE_REFRESH_BATCH", "2"))))
        worker.enqueue(refresh_seconds=max(1,int(os.getenv("PROFILE_REFRESH_SECONDS","300"))),batch=batch)
        worker.drain(batch=batch,budget_seconds=max(1,min(300,float(os.getenv("PROFILE_REFRESH_BUDGET_SECONDS","45")))))
        worker.cleanup_pages()

    def loop(self):
        while True:
            try:
                self.refresh_once()
            except Exception as exc:
                logging.warning("profiles.refresh.retry %s", type(exc).__name__)
            time.sleep(max(0.5, float(os.getenv("PROFILE_POLL_SECONDS", "30"))))
