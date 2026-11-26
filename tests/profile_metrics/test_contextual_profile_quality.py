from datetime import datetime, timedelta

import pytest

from backend.services.analysis.assessments.calculation import default_rules
from tools.contextual_profile_quality import compare, manifest, run


def test_paired_holdout_separates_missed_reviews_and_unavailable(tmp_path):
    report = run(tmp_path)
    assert report["threshold"] == 40
    general, contextual = (report["overall"][method] for method in ("general", "contextual"))
    assert general["falseNegative"] == 24 and contextual["falseNegative"] == 0
    assert general["falsePositive"] == 0 and contextual["falsePositive"] == 12
    assert general["positiveUnavailable"] == contextual["positiveUnavailable"] == 12
    by_scenario = report["byScenario"]
    assert by_scenario["approved-one-off-purchase"]["contextual"]["falsePositive"] == 12
    assert by_scenario["unexpected-large-purchase"]["contextual"]["truePositive"] == 12


def test_holdout_rejects_history_arriving_after_the_subject():
    inputs = manifest()["cases"][0]["inputs"]
    cutoff = datetime.fromisoformat(inputs["transaction"]["occurredAt"])
    inputs["history"][0]["ingestedAt"] = (cutoff + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="available before the evaluation cut"):
        compare(inputs, default_rules())
