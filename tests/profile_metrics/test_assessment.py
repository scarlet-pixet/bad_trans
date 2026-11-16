from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from backend.common.data import DomainError
from backend.services.analysis.profiles.metrics import build_metrics

T = datetime(2026, 11, 9, 12, tzinfo=UTC)


def fixture(amount="100.00", complete=True):
    cp = str(uuid4())
    client = str(uuid4())
    rows = [
        dict(
            id=str(uuid4()),
            clientId=client,
            currency="BYN",
            direction="OUT",
            amount="100.00",
            counterpartyId=cp,
            occurredAt=(T - timedelta(days=d, minutes=5 + i)).isoformat(),
            ingestedAt=(T - timedelta(days=d)).isoformat(),
        )
        for d in range(1, 31)
        for i in range(5)
    ]
    coverage = dict(
        mode="DECLARED_COMPLETE" if complete else "UNKNOWN",
        revision="test",
        intervals=[{"from": (T - timedelta(days=90)).isoformat(), "to": T.isoformat()}],
    )
    p = build_metrics(rows, T, T, "BYN", "OUT", 30, coverage=coverage, client_id=client)
    p["id"] = str(uuid4())
    tx = dict(
        id=str(uuid4()),
        clientId=client,
        currency="BYN",
        direction="OUT",
        amount=amount,
        counterpartyId=cp,
        occurredAt=T.isoformat(),
    )
    return tx, p, rows


def evaluate(tx, p, rows, rules=None):
    from backend.services.analysis.assessments.calculation import assess, default_rules

    return assess(tx, p, rows, default_rules() if rules is None else rules)


def test_full_records_normal_and_amount_exact_sum():
    tx, p, rows = fixture("1000.00")
    r = evaluate(tx, p, rows)
    assert (
        len(r["checks"]) == 5
        and r["assessmentScore"] == 40
        and r["reviewRequired"] is True
    )
    assert sum(x["contribution"] for x in r["checks"]) == r["assessmentScore"]
    amount = next(x for x in r["checks"] if x["code"] == "AMOUNT_DEVIATION")
    assert (
        amount["status"] == "TRIGGERED"
        and amount["deviation"]["method"] == "CONSTANT_FRACTION"
    )
    assert (
        amount["baseline"]["lower"] == "75.00000000"
        and amount["baseline"]["upper"] == "125.00000000"
    )
    tx["amount"] = "125.00"
    assert evaluate(tx, p, rows)["assessmentScore"] == 0
    tx["amount"] = "10.00"
    assert evaluate(tx, p, rows)["assessmentScore"] == 40


@pytest.mark.parametrize(
    "code",
    [
        "AMOUNT_DEVIATION",
        "PROFILE_FREQUENCY",
        "UNUSUAL_TIME",
        "COUNTERPARTY_NOVELTY",
        "ACTIVITY_CHANGE",
    ],
)
def test_disabled_is_not_applicable_and_insufficient_is_explicit(code):
    from backend.services.analysis.assessments.calculation import default_rules

    tx, p, rows = fixture()
    rule = next(x for x in default_rules() if x["code"] == code)
    rule["parameters"]["enabled"] = False
    r = evaluate(tx, p, rows, [rule])
    assert r["checks"][0]["status"] == "NOT_APPLICABLE"
    rule["parameters"]["enabled"] = True
    p["sampleSize"] = 0
    p["activeDays"] = 0
    p["calendarActivity"] = []
    p["referenceHours"] = []
    p["observation"]["fullWindowCovered"] = False
    p["observation"]["confirmedIntervals"] = []
    p["dynamics"]["status"] = "UNKNOWN_COVERAGE"
    r = evaluate(tx, p, [], [rule])
    assert r["assessmentScore"] is None and r["assessmentStatus"] == "UNAVAILABLE"
    assert (
        r["reviewRequired"] is None and r["checks"][0]["status"] == "INSUFFICIENT_DATA"
    )


def test_partial_amount_does_not_require_fabricated_coverage():
    tx, p, rows = fixture("1000.00", complete=False)
    r = evaluate(tx, p, rows)
    assert r["assessmentStatus"] == "PARTIAL" and r["assessmentScore"] == 40
    assert (
        next(x for x in r["checks"] if x["code"] == "PROFILE_FREQUENCY")["status"]
        == "INSUFFICIENT_DATA"
    )


def test_correlated_activity_signals_are_preserved_but_not_added():
    tx, p, rows = fixture()
    rows += [
        dict(
            rows[0], id=str(uuid4()), occurredAt=(T - timedelta(minutes=1)).isoformat()
        )
        for _ in range(20)
    ]
    p["dynamics"]["countRateRatio"] = "8.00000000"
    p["dynamics"]["short"]["count"] = 300
    r = evaluate(tx, p, rows)
    f = {x["code"]: x for x in r["checks"]}
    assert (
        f["PROFILE_FREQUENCY"]["status"]
        == f["ACTIVITY_CHANGE"]["status"]
        == "TRIGGERED"
    )
    assert f["PROFILE_FREQUENCY"]["contribution"] == 40
    assert (
        f["ACTIVITY_CHANGE"]["contribution"] == 0
        and f["ACTIVITY_CHANGE"]["suppressedBy"] == "PROFILE_FREQUENCY"
    )
    assert r["assessmentScore"] == 40


def test_circular_time_smoothing_and_missing_counterparty():
    tx, p, rows = fixture()
    p["hourCountsUtc"] = {str(h): (150 if h == 23 else 0) for h in range(24)}
    tx["occurredAt"] = T.replace(hour=0).isoformat()
    p["asOf"] = tx["occurredAt"]
    tx["counterpartyId"] = None
    r = evaluate(tx, p, rows)
    assert (
        next(x for x in r["checks"] if x["code"] == "UNUSUAL_TIME")["status"]
        == "NOT_TRIGGERED"
    )
    assert (
        next(x for x in r["checks"] if x["code"] == "COUNTERPARTY_NOVELTY")["status"]
        == "INSUFFICIENT_DATA"
    )


@pytest.mark.parametrize(
    "change", ["unknown", "duplicate", "weight", "nan", "extra", "empty"]
)
def test_configuration_errors_are_not_silent(change):
    from backend.services.analysis.assessments.calculation import default_rules

    tx, p, rows = fixture()
    rules = deepcopy(default_rules())
    if change == "unknown":
        rules[0]["code"] = "UNKNOWN"
    elif change == "duplicate":
        rules.append(deepcopy(rules[0]))
    elif change == "weight":
        rules[0]["weight"] = -1
    elif change == "nan":
        rules[0]["parameters"]["mad_scale"] = "NaN"
    elif change == "extra":
        rules[0]["parameters"]["hidden"] = "ignored?"
    else:
        rules = []
    with pytest.raises(DomainError):
        evaluate(tx, p, rows, rules)
