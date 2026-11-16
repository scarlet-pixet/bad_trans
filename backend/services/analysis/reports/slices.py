"""Analysis-owned exact results and period coverage; no foreign SQL."""

from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from backend.common.commands import require_role
from backend.common.data import DomainError, period, uuid
from backend.common.report_contract import MAX_ROWS, cut, cutoff_time, wire
from backend.infrastructure.workspace_access import transaction_access
from backend.services.analysis.profiles.service import ProfileService
from backend.services.analysis.profiles.workspace import WorkspaceService


def connection_db(c):
    return SimpleNamespace(one=lambda sql, args=(): c.execute(sql, args).fetchone(), query=lambda sql, args=(): c.execute(sql, args).fetchall())


class ReportSlices:
    def __init__(self, db):
        self.db = db

    def report_profile_part(self, actor, profileVersionId, cutoff):
        require_role(actor, "MANAGER")
        at = cutoff_time(cutoff)
        value = cast(dict, WorkspaceService(self.db).get_profile_version(actor, profileVersionId))
        if datetime.fromisoformat(value["metadata"]["asOf"]) > at:
            raise DomainError("BAD_INPUT", "Профиль позже среза отчёта")
        return dict(data=value, cut=cut("analysis", datetime.now(UTC), at, [profileVersionId]))

    def report_assessment_part(self, actor, transactionId, resultId, cutoff):
        require_role(actor, "MANAGER", "ANALYST")
        transaction_access(actor, transactionId)
        at = cutoff_time(cutoff)
        with self.db.connect() as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            db = connection_db(c)
            context = cast(dict, ProfileService(db, None).get_detection_context(actor, transactionId, resultId))
            row = db.one('SELECT id,run_id,calculated_at FROM analysis_results WHERE id=%s AND transaction_id=%s AND calculated_at<=%s', (uuid(resultId), uuid(transactionId), at))
            if not context or not row:
                raise DomainError("NOT_FOUND", "Выбранный результат на срезе не найден")
            context["runId"] = str(row["run_id"])
            linked = db.query("SELECT profile_version_id FROM analysis_result_profiles WHERE result_id=%s ORDER BY window_days", (uuid(resultId),))
            used_ids = {str(r["profile_version_id"]) for r in linked}
            profile_ids = list(dict.fromkeys([p["id"] for p in context.get("profiles", [])] + sorted(used_ids)))
            profiles = []
            for identifier in profile_ids:
                value = cast(dict, WorkspaceService(db).get_profile_version(actor, identifier))
                value["reportUsage"] = "ASSESSMENT_BASELINE" if identifier in used_ids else "CONTEXT_WINDOW"
                profiles.append(value)
            captured = c.execute("SELECT transaction_timestamp() AS at").fetchone()["at"]
        return dict(data=dict(assessment=wire(context), profiles=profiles), cut=cut("analysis", captured, at, [resultId, *[p["metadata"]["id"] for p in profiles]]))

    def report_analysis_part(self, actor, cohort, windowDays, periodFilter, cutoff):
        require_role(actor, "MANAGER")
        if not isinstance(cohort, list) or len(cohort) > MAX_ROWS or windowDays not in (7, 30, 90) or type(windowDays) is not int:
            raise DomainError("BAD_INPUT", "Некорректная подборка отчёта")
        ids = [uuid(r["id"]) for r in cohort]
        if len(ids) != len(set(ids)):
            raise DomainError("BAD_INPUT", "Дубли операций в подборке")
        groups = {(uuid(r["clientId"]), r["currency"], r["direction"]) for r in cohort}
        at = cutoff_time(cutoff)
        start, end = period(periodFilter)
        clients = list({g[0] for g in groups})
        with self.db.connect() as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            results = c.execute('''SELECT DISTINCT ON(transaction_id) id,transaction_id,run_id,calculated_at,
                assessment_status,assessment_score,data_status,score,review_required
                FROM analysis_results WHERE transaction_id=ANY(%s::uuid[]) AND calculated_at<=%s
                ORDER BY transaction_id,calculated_at DESC,id DESC''', (ids, at)).fetchall()
            profiles = c.execute('''SELECT DISTINCT ON(client_id,currency,direction) id,client_id,currency,direction,version,metrics
                FROM profile_versions WHERE client_id=ANY(%s::uuid[]) AND window_days=%s AND as_of<=%s
                ORDER BY client_id,currency,direction,as_of DESC,version DESC,id DESC''', (clients, windowDays, at)).fetchall()
            jobs = c.execute('''SELECT id,client_id,currency,direction,status FROM profile_refresh_jobs
                WHERE client_id=ANY(%s::uuid[]) AND created_at>=%s AND created_at<%s AND created_at<=%s''', (clients, start, end, at)).fetchall()
            failures = c.execute('''SELECT status,count(*) AS n FROM event_failures
                WHERE created_at>=%s AND created_at<%s AND created_at<=%s GROUP BY status''', (start, end, at)).fetchall()
            captured = c.execute("SELECT transaction_timestamp() AS at").fetchone()["at"]
        profiles = [r for r in profiles if (str(r["client_id"]), r["currency"], r["direction"]) in groups]
        jobs = [r for r in jobs if (str(r["client_id"]), r["currency"], r["direction"]) in groups]
        statuses = Counter(r["assessment_status"] or "LEGACY" for r in results)
        for name in ("LEGACY", "COMPLETE", "PARTIAL", "UNAVAILABLE"):
            statuses.setdefault(name, 0)
        statuses["NO_RESULT"] = len(ids) - len(results)
        usable = sum(r["assessment_status"] in ("COMPLETE", "PARTIAL") or (r["assessment_status"] is None and r["data_status"] == "SUFFICIENT") for r in results)
        ready = sum(r["metrics"].get("quality", {}).get("status") == "READY" for r in profiles)
        data = dict(denominator=len(ids), resultCount=len(results), usableAssessmentCount=usable, statuses=dict(statuses),
                    resultCoverage=len(results) / len(ids) if ids else None, usableAssessmentCoverage=usable / len(ids) if ids else None,
                    profileGroupDenominator=len(groups), profiledGroups=len(profiles), readyProfileGroups=ready, windowDays=windowDays,
                    resultRefs=wire(results), profileRefs=[dict(id=str(r["id"]), clientId=str(r["client_id"]), currency=r["currency"], direction=r["direction"], version=r["version"]) for r in profiles],
                    refreshJobsByStatus=dict(Counter(r["status"] for r in jobs)), eventFailuresAllClients={r["status"]: r["n"] for r in failures},
                    axes=dict(results="latest calculated_at<=cutoff per cohort transaction; UUID breaks ties", profiles="latest as_of<=cutoff per cohort group/window", diagnostics="current status at capturedAt; created_at in [from,to); event failures are service-wide, not cohort failures"))
        return dict(data=data, cut=cut("analysis", captured, at, dict(results=[str(r["id"]) for r in results], profiles=[str(r["id"]) for r in profiles], jobs=[str(r["id"]) for r in jobs])))
