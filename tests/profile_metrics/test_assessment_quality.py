from backend.services.analysis.assessments.quality import comparison_example, summarize
from frontend.quality_view import reason_rows


def test_quality_distinguishes_unavailable_pending_and_historical_threshold():
    base = {"scenario": "control", "transaction_id": "operation", "expected": True}
    unavailable = dict(assessment_status="UNAVAILABLE", missing_reasons=[
        "AMOUNT_DEVIATION: INSUFFICIENT_SAMPLE", "UNUSUAL_TIME: INSUFFICIENT_SAMPLE",
    ])
    partial = dict(assessment_status="PARTIAL", assessment_score=35, score=35, threshold=30)
    legacy = dict(assessment_status=None, score=35, threshold=40)
    rows = [
        comparison_example(base, unavailable),
        comparison_example(base, None),
        comparison_example(base, partial),
        comparison_example(dict(base, expected=False), legacy),
        comparison_example(dict(base, expected=None), unavailable),
    ]
    stats = summarize(rows)
    assert stats["evaluated"] == 2 and stats["availability"] == 0.5
    assert stats["unavailable"] == stats["pending"] == stats["warmup"] == 1
    assert stats["truePositive"] == stats["trueNegative"] == 1
    assert stats["falsePositive"] == stats["falseNegative"] == 0
    assert stats["unavailableByReason"] == {"INSUFFICIENT_SAMPLE": 1}
    assert reason_rows(stats["unavailableByReason"]) == [
        {"Причина": "Мало операций в истории клиента", "Операций": 1}
    ]
