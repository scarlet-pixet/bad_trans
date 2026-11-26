"""Saved operation assessments, historical thresholds and quality queries."""
from backend.common.commands import require_role
from backend.common.data import DomainError, uuid
from backend.services.analysis.assessments.quality import comparison_example, summarize
from backend.services.analysis.profiles.metrics import timestamp
from backend.services.analysis.profiles.public_metrics import public_metrics

class AssessmentQueries:
    def get_rule_catalog(self, actor):
        require_role(actor, "MANAGER")
        rows = self.db.query("""SELECT r.id AS rule_id,r.code,v.id,v.version,v.weight,v.parameters
            FROM analysis_rules r LEFT JOIN rule_versions v
                ON v.rule_id=r.id AND v.status='PUBLISHED'
            ORDER BY r.code,v.version DESC""")
        catalog = {}
        for row in rows:
            rule_id = str(row["rule_id"])
            rule = catalog.setdefault(rule_id, dict(id=rule_id, code=row["code"], versions=[]))
            if row["id"] is not None:
                rule["versions"].append(dict(id=str(row["id"]), version=row["version"],
                                             weight=row["weight"], parameters=row["parameters"]))
        return list(catalog.values())

    def start_period_reassessment(self, actor, input):
        from backend.services.analysis.assessments.period_worker import PeriodReassessment
        return PeriodReassessment(self.db, self.client).start(actor, input)

    def compare_period_runs(self, actor, firstId, secondId, offset=0, limit=50):
        from backend.services.analysis.assessments.period_queries import compare
        return compare(self.db, actor, firstId, secondId, offset, limit)

    def get_period_run_results(self, actor, runId, offset=0, limit=100):
        from backend.services.analysis.assessments.period_queries import get
        return get(self.db, actor, runId, offset, limit)

    def get_result_history(self,actor,transactionId,limit=20,pageToken=None):
        from backend.services.analysis.profiles.workspace import WorkspaceService
        return WorkspaceService(self.db).get_result_history(actor,transactionId,limit,pageToken)

    def get_detection_context(self, actor, transactionId, resultId=None):
        require_role(actor, "MANAGER", "ANALYST")
        from backend.infrastructure.workspace_access import transaction_access
        transaction_access(actor,transactionId)
        row = self.db.one(
            """SELECT a.*,d.details FROM analysis_results a
            LEFT JOIN detection_context d ON d.result_id=a.id
            WHERE a.transaction_id=%s AND (%s::uuid IS NULL OR a.id=%s) ORDER BY a.calculated_at DESC,a.id DESC LIMIT 1""",
            (uuid(transactionId), uuid(resultId) if resultId else None, uuid(resultId) if resultId else None),
        )
        if not row:
            return None
        details: dict = dict(row["details"] or {"explanations": [], "profiles": [], "legacy": True})
        if isinstance(details.get("baseline"), dict):
            details["baseline"] = public_metrics(details["baseline"])
        if details.get("profiles"):
            details["profiles"] = [public_metrics(profile) for profile in details["profiles"]]
        if row.get("calculation_id"):
            links = self.db.query("""SELECT window_days,profile_version_id FROM analysis_result_profiles
                WHERE result_id=%s ORDER BY window_days""", (row["id"],))
            details.update(contextSchemaVersion=2, calculationId=str(row["calculation_id"]),
                           profileVersionIds=[str(r["profile_version_id"]) for r in links],
                           primaryProfileVersionId=next(str(r["profile_version_id"]) for r in links if r["window_days"]==30))
        return {
            **details,
            "resultId": str(row["id"]),
            "runId": str(row["run_id"]),
            "transactionId": str(row["transaction_id"]),
            "profileId": str(row["profile_id"]) if row["profile_id"] else None,
            "score": row["score"],
            "threshold": row["threshold"],
            "dataStatus": row["data_status"],
            "missingReasons": row["missing_reasons"],
            "calculatedAt": row["calculated_at"].isoformat(),
        }

    def get_detections(self, actor, clientId=None):
        require_role(actor, "MANAGER")
        client = uuid(clientId) if clientId else None
        rows = self.db.query(
            """SELECT DISTINCT ON(a.transaction_id) a.id,a.transaction_id,a.score,
            a.threshold,a.data_status,a.calculated_at,p.client_id,a.assessment_score,a.assessment_status,a.review_required
            FROM analysis_results a JOIN behavior_profiles p ON p.id=a.profile_id
            WHERE (%s::uuid IS NULL OR p.client_id=%s)
            ORDER BY a.transaction_id,a.calculated_at DESC,a.id DESC""",
            (client, client),
        )
        rows = sorted(rows, key=lambda r: r["calculated_at"], reverse=True)[:100]
        return [
            {
                "resultId": str(r["id"]),
                "transactionId": str(r["transaction_id"]),
                "clientId": str(r["client_id"]),
                "score": r["score"],
                "threshold": r["threshold"],
                "dataStatus": r["data_status"],
                "assessmentScore": r["assessment_score"],
                "assessmentStatus": r["assessment_status"],
                "reviewRequired": r["review_required"] if r["assessment_status"] else r["score"]>=r["threshold"],
                "calculatedAt": r["calculated_at"].isoformat(),
            }
            for r in rows
        ]

    def get_quality(self, actor, runId):
        require_role(actor, "MANAGER")
        # Labels arrive over the owner's API and never enter rule processing.
        labels = self.client._invoke("get_source_labels", {"runId": uuid(runId)})
        ids = [r["transaction_id"] for r in labels if r["transaction_id"]]
        rows = (
            self.db.query(
                """SELECT DISTINCT ON(a.transaction_id) a.transaction_id,a.score,a.threshold,a.assessment_status,a.assessment_score,a.missing_reasons
            FROM analysis_results a JOIN analysis_runs r ON r.id=a.run_id
            WHERE a.transaction_id=ANY(%s::uuid[]) AND r.mode='AUTO'
            ORDER BY a.transaction_id,a.calculated_at DESC,a.id DESC""",
                (ids,),
            )
            if ids
            else []
        )
        results = {str(row["transaction_id"]): row for row in rows}
        examples = [comparison_example(row, results.get(row["transaction_id"])) for row in labels]
        scenarios = sorted(
            {r["scenario"] for r in examples if r["expected"] is not None}
        )
        return {
            "runId": runId,
            **summarize(examples),
            "examples": examples,
            "byScenario": {
                name: summarize([r for r in examples if r["scenario"] == name])
                for name in scenarios
            },
            "scope": "synthetic chronological scenarios; not an estimate of real banking accuracy",
        }

    def get_analysis_metrics(self, actor):
        require_role(actor, "MANAGER")
        return dict(
            self.db.one("""SELECT count(*)::int AS analyzed,
            count(*) FILTER(WHERE score>=threshold)::int AS flagged,
            count(*) FILTER(WHERE data_status='INSUFFICIENT')::int AS insufficient
            FROM (SELECT DISTINCT ON(transaction_id) * FROM analysis_results ORDER BY transaction_id,calculated_at DESC) x""")
        )
