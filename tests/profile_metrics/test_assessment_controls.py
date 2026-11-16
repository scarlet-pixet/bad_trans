from copy import deepcopy
from datetime import UTC, datetime

import pytest

from backend.common.data import DomainError
from backend.services.analysis.assessments.calculation import assess, default_rules
from backend.services.analysis.assessments.publication import validate_controls
from tools.assessment_evaluation import example, evaluate


@pytest.mark.parametrize(
    "name,code",
    [
        ("amount_upper", "AMOUNT_DEVIATION"),
        ("burst", "PROFILE_FREQUENCY"),
        ("rare_time", "UNUSUAL_TIME"),
        ("new_counterparty", "COUNTERPARTY_NOVELTY"),
        ("change", "ACTIVITY_CHANGE"),
    ],
)
def test_each_registry_check_triggers_and_has_both_publication_controls(name, code):
    t = datetime(2026, 11, 8, 12, tzinfo=UTC)
    positive, p = example(name, t)
    normal, np = example("normal", t)
    rule = next(r for r in default_rules() if r["code"] == code)
    assert (
        assess(positive["transaction"], p, positive["history"], [rule])["checks"][0][
            "status"
        ]
        == "TRIGGERED"
    )
    assert (
        assess(normal["transaction"], np, normal["history"], [rule])["checks"][0][
            "status"
        ]
        == "NOT_TRIGGERED"
    )
    examples = [dict(positive, expected=True), dict(normal, expected=False)]
    before = deepcopy(examples)
    cfg = deepcopy(rule["parameters"])
    validate_controls(code, cfg, rule["weight"], examples)
    assert examples == before and cfg == rule["parameters"]
    examples[0]["expected"] = False
    with pytest.raises(DomainError):
        validate_controls(code, cfg, rule["weight"], examples)


@pytest.mark.parametrize(
    "mad,q1,q3,method", [("10", "90", "110", "SCALED_MAD"), ("0", "90", "110", "IQR")]
)
def test_amount_zero_mad_fallback_and_boundary(mad, q1, q3, method):
    e, p = example("amount_upper", datetime(2026, 11, 8, 12, tzinfo=UTC))
    p.update(madAmount=mad, q25=q1, q75=q3)
    r = assess(e["transaction"], p, e["history"], default_rules())
    assert r["checks"][0]["deviation"]["method"] == method


def test_separate_tuning_holdout_frozen_and_legacy_comparison():
    r = evaluate()
    assert r["chosenThreshold"] == 40
    assert all(
        t["historyDigest"] != h["historyDigest"]
        for t, h in zip(r["tuning"], r["holdout"])
        if t["scenario"] != "unknown"
    )
    assert (
        next(x for x in r["holdout"] if x["scenario"] == "amount_lower")["legacyScore"]
        == 0
    )
    assert (
        next(x for x in r["holdout"] if x["scenario"] == "amount_lower")["score"] == 40
    )


def test_all_triggered_checks_sum_to_group_budgets_not_raw_140():
    from datetime import timedelta
    from uuid import uuid4
    from backend.services.analysis.profiles.metrics import build_metrics

    e, _ = example("change", datetime(2026, 11, 8, 12, tzinfo=UTC))
    tx = e["transaction"]
    cutoff = datetime(2026, 11, 8, 3, tzinfo=UTC)
    tx.update(
        occurredAt=cutoff.isoformat(), amount="1000.00", counterpartyId=str(uuid4())
    )
    rows = e["history"] + [
        dict(
            e["history"][0],
            id=str(uuid4()),
            occurredAt=(cutoff - timedelta(minutes=1)).isoformat(),
            ingestedAt=(cutoff - timedelta(seconds=10)).isoformat(),
        )
        for _ in range(8)
    ]
    p = build_metrics(
        rows,
        cutoff,
        cutoff,
        "BYN",
        "OUT",
        30,
        tx["id"],
        coverage=e["coverage"],
        client_id=tx["clientId"],
    )
    p["id"] = "fixed-profile"
    r = assess(tx, p, rows, default_rules())
    assert all(x["status"] == "TRIGGERED" for x in r["checks"])
    assert sum(x["candidateContribution"] for x in r["checks"]) == 140
    assert sum(x["contribution"] for x in r["checks"]) == r["assessmentScore"] == 100
