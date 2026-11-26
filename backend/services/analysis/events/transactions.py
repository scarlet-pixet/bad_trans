"""Orchestrate pinned owner history, pure assessment, and atomic publication."""

from datetime import timedelta
import logging
from psycopg.types.json import Jsonb

from backend.common.events import utc, ident, make_event, add_outbox
from backend.common.data import DomainError
from backend.services.analysis.assessments.calculation import assess
from backend.services.analysis.assessments.comparison import COMPARISON_POLICY, select_amount_basis
from backend.services.analysis.assessments.requests import (
    freeze,
    history as request_history,
)
from backend.services.analysis.profiles.observation import timestamp
from backend.services.analysis.profiles.storage import save_profile_versions

LABELS = {
    "AMOUNT_DEVIATION": "Сумма отличается от индивидуального диапазона",
    "PROFILE_FREQUENCY": "Превышена обычная частота операций за час",
    "UNUSUAL_TIME": "Нетипичное время операции",
    "COUNTERPARTY_NOVELTY": "Контрагент отсутствует в базовой истории",
    "ACTIVITY_CHANGE": "Изменилась интенсивность активности",
}


class AnalysisEventService:
    def __init__(self, transaction_client):
        self.transaction_client = transaction_client

    def process(
        self,
        c,
        event,
        run_id=None,
        rule_ids=None,
        initiated_by=None,
        ingested_before=None,
        request_key=None,
        frozen_context=None,
    ):
        event = dict(event)
        if ingested_before is not None:
            event["occurred_at"] = timestamp(ingested_before).isoformat()
        request = freeze(
            c,
            self.transaction_client,
            event,
            rule_ids,
            request_key or "event:" + event["event_id"],
            knowledge_mode='WITH_CURRENT_KNOWLEDGE' if initiated_by else 'FIRST_REQUEST_KNOWLEDGE',
            frozen_context=frozen_context,
        )
        context = request["context"]
        tx = context["transaction"]
        rules = context["rules"]
        cutoff, watermark = (
            timestamp(context["asOf"]),
            timestamp(context["ingestedBefore"]),
        )
        rows = request_history(c, self.transaction_client, request)
        versions = save_profile_versions(c, tx, rows, cutoff, watermark,feedback_ref=context.get('feedbackReference'))
        baseline = next(v for v in versions if v["windowDays"] == 30)
        ids = set(baseline.get('observedHistoryIds',baseline["history_ids"]))
        selected = [r for r in rows if r["id"] in ids]
        comparison = context.get("comparisonPolicy")
        if comparison is not None and comparison != COMPARISON_POLICY:
            raise DomainError("CONFLICT", "Версия выбора базы оценки не поддерживается")
        basis, amount_profile = select_amount_basis(tx, baseline, rows, rules) if comparison else (None, None)
        assessment = assess(tx, baseline, selected, rules, amount_profile=amount_profile,
                            amount_basis_id=basis["id"] if basis else None)
        score = assessment["assessmentScore"]
        legacy_score = score if score is not None else 0
        status = (
            "SUFFICIENT"
            if assessment["assessmentStatus"] == "COMPLETE"
            else "INSUFFICIENT"
        )
        missing = [
            ch["code"] + ": " + reason
            for ch in assessment["checks"]
            if ch["status"] == "INSUFFICIENT_DATA"
            for reason in ch["dataReasons"]
        ]
        run, profile, result = run_id or ident(), ident(), ident()
        now = utc()
        calculation = baseline["calculationId"]
        c.execute(
            "INSERT INTO analysis_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
            (
                run,
                "MANUAL" if initiated_by else "AUTO",
                Jsonb({"transaction_id": tx["id"]}),
                cutoff,
                "COMPLETED",
                initiated_by,
                Jsonb({"processed": 1}),
                watermark,
                now,
            ),
        )
        for r in rules:
            c.execute(
                "INSERT INTO run_rule_versions VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (run, r["id"]),
            )
        c.execute(
            """INSERT INTO behavior_profiles(id,run_id,client_id,currency,direction,window_start,window_end,cutoff_transaction_id,ingested_before,sample_size,median_amount,mad_amount,metrics,algorithm_version,data_status)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                profile,
                run,
                tx["clientId"],
                tx["currency"],
                tx["direction"],
                cutoff - timedelta(days=30),
                cutoff,
                tx["id"],
                watermark,
                baseline["sampleSize"],
                baseline["medianAmount"],
                baseline["madAmount"],
                Jsonb(baseline),
                baseline["algorithmVersion"],
                status,
            ),
        )
        c.execute(
            """INSERT INTO analysis_results(id,transaction_id,run_id,profile_id,score,threshold,data_status,missing_reasons,calculated_at,calculation_id,
          assessment_score,assessment_status,assessment_algorithm_version,assessment_policy_version,assessment_policy_hash,review_required,assessment_request_key)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                result,
                tx["id"],
                run,
                profile,
                legacy_score,
                assessment["threshold"],
                status,
                Jsonb(missing),
                now,
                calculation,
                score,
                assessment["assessmentStatus"],
                assessment["assessmentAlgorithmVersion"],
                assessment["assessmentPolicyVersion"],
                assessment["assessmentPolicyHash"],
                assessment["reviewRequired"],
                request["request_key"],
            ),
        )
        c.execute(
            "INSERT INTO analysis_result_profiles VALUES (%s,30,%s)",
            (result, baseline["id"]),
        )
        explanations = []
        for ch in assessment["checks"]:
            if ch["status"] == "TRIGGERED":
                c.execute(
                    "INSERT INTO rule_hits VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        ident(),
                        result,
                        ch["ruleVersionId"],
                        Jsonb(ch["observed"]),
                        Jsonb(ch["baseline"]),
                        ch["contribution"],
                    ),
                )
                if ch["contribution"]:
                    explanations.append(dict(ch, text=LABELS[ch["code"]]))
        summary = (
            "Оценка недоступна: недостаточно данных"
            if score is None
            else "Обнаружено отклонение от профиля"
            if assessment["reviewRequired"]
            else "По выполненным проверкам порог не достигнут"
        )
        if assessment["assessmentStatus"] == "PARTIAL":
            summary += "; оценка неполная"
        details = dict(
            assessment,
            contextSchemaVersion=2,
            calculationId=calculation,
            primaryProfileVersionId=baseline["id"],
            profileVersionIds=[baseline["id"]],
            historySnapshot=rows.snapshot,
            algorithmVersion=assessment["assessmentAlgorithmVersion"],
            profileAlgorithmVersion=baseline["algorithmVersion"],
            profiles=versions,
            baseline=baseline,
            explanations=explanations,
            behavioralSignals=[],
            **({'amountBasis': basis} if basis else {}),
            summary=summary,
            scoreIsCompatibilityProjection=score is None,
            feedbackReference=context.get('feedbackReference'),
            knowledgeMode=(context.get('feedbackReference') or {}).get('knowledgeMode','LEGACY_FROZEN_ALL_OBSERVED'),
        )
        c.execute(
            "INSERT INTO detection_context(result_id,transaction_id,details,context_schema_version,calculation_id) VALUES (%s,%s,%s,2,%s)",
            (result, tx["id"], Jsonb(details), calculation),
        )
        output = make_event(
            dict(
                result_id=result,
                transaction_id=tx["id"],
                client_id=tx["clientId"],
                run_id=run,
                score=legacy_score,
                threshold=assessment["threshold"],
                data_status=status,
                calculated_at=now.isoformat(),
                assessment_score=score,
                assessment_status=assessment["assessmentStatus"],
                assessment_policy_version=assessment["assessmentPolicyVersion"],
                review_required=assessment["reviewRequired"],
            )
        )
        output["correlation_id"] = event["correlation_id"]
        add_outbox(c, "analysis.completed.v1", tx["id"], output)
        logging.info(
            "ANALYZED transaction=%s assessment=%s score=%s run=%s",
            tx["id"],
            assessment["assessmentStatus"],
            score,
            run,
        )
