"""Project source labels and saved automatic assessments into quality metrics."""
from collections import Counter

from backend.services.analysis.assessments.behavior import evaluate_quality


def comparison_example(label, result):
    status = "PENDING"
    prediction = None
    reasons = []
    if result is not None:
        status = result["assessment_status"] or "LEGACY"
        if status == "UNAVAILABLE":
            reasons = sorted({reason.rsplit(": ", 1)[-1] for reason in result["missing_reasons"]})
            if not reasons:
                reasons = ["NO_RECORDED_REASON"]
        else:
            score = result["score"] if status == "LEGACY" else result["assessment_score"]
            prediction = score >= result["threshold"]
    return dict(scenario=label["scenario"], transactionId=label["transaction_id"],
                expected=label["expected"], predicted=prediction, assessmentStatus=status,
                unavailableReasons=reasons)


def summarize(examples):
    labeled = [row for row in examples if row["expected"] is not None]
    unavailable = [row for row in labeled if row["assessmentStatus"] == "UNAVAILABLE"]
    metrics = evaluate_quality([row for row in examples
                                if row["expected"] is None or row["assessmentStatus"] != "UNAVAILABLE"])
    return dict(
        **metrics, unavailable=len(unavailable), labeled=len(labeled),
        availability=round(metrics["evaluated"] / len(labeled), 4) if labeled else None,
        unavailableByReason=dict(Counter(reason for row in unavailable for reason in row["unavailableReasons"])),
    )
