"""Hand-calculated feature expectations, independent of implementation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backend.services.analysis.profiles.metrics import build_metrics

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
CLIENT = "client-one"


def row(identifier, amount="100.00", *, age=1, counterparty="A", **extra):
    return dict(
        id=str(identifier),
        clientId=CLIENT,
        amount=amount,
        currency="BYN",
        direction="OUT",
        occurredAt=(NOW - timedelta(days=age)).isoformat(),
        ingestedAt=(NOW - timedelta(minutes=1)).isoformat(),
        counterpartyId=counterparty,
        **extra,
    )


def coverage(*intervals, mode="DECLARED_COMPLETE"):
    return dict(
        mode=mode,
        revision="test-coverage-v1",
        declaredBySource="test-complete-stream",
        intervals=[{"from": a.isoformat(), "to": b.isoformat()} for a, b in intervals],
    )


def calculate(rows, days=30, **kwargs):
    return build_metrics(rows, NOW, NOW, "BYN", "OUT", days, client_id=CLIENT, **kwargs)


def test_amount_statistics_and_units_are_hand_calculated():
    result = calculate(
        [
            row(i, str(value), age=i + 1)
            for i, value in enumerate([80, 90, 100, 110, 120])
        ]
    )
    assert result["metricsSchemaVersion"] == 4
    expected = dict(
        totalAmount=500,
        meanAmount=100,
        medianAmount=100,
        madAmount=10,
        q25=90,
        q75=110,
        iqrAmount=20,
    )
    for key, value in expected.items():
        assert Decimal(result[key]) == value
    assert result["activeDays"] == 5
    assert Decimal(result["interarrivalMedianSeconds"]) == 86400
    assert result["units"]["amount"] == "BYN"
    assert result["quality"]["eligibility"]["amount"]["eligible"] is False
    assert "SAMPLE_SIZE_LT_20" in result["quality"]["eligibility"]["amount"]["reasons"]
    assert result["observed"]["totalAmount"] == result["baseline"]["totalAmount"]
    assert result["observedCount"] == result["includedCount"] == 5
    assert result["excludedCount"] == 0


@pytest.mark.parametrize(
    "values,median,mad,iqr",
    [
        ([], None, None, None),
        ([100], 100, 0, 0),
        ([100, 100, 100], 100, 0, 0),
        ([80, 100], 90, 10, 10),
    ],
)
def test_empty_singleton_constant_and_even_quantiles(values, median, mad, iqr):
    result = calculate([row(i, str(a)) for i, a in enumerate(values)])
    for name, expected in [
        ("medianAmount", median),
        ("madAmount", mad),
        ("iqrAmount", iqr),
    ]:
        assert (Decimal(result[name]) if result[name] is not None else None) == expected
    assert (
        result["meanAmount"] is None if not values else result["meanAmount"] is not None
    )
    assert (
        result["interarrivalMedianSeconds"] is None
        if len(values) < 2
        else Decimal(result["interarrivalMedianSeconds"]) == 0
    )
    assert (
        result["counterparties"]["knownFraction"] is None
        if not values
        else Decimal(result["counterparties"]["knownFraction"]) == 1
    )


def test_unknown_observation_is_not_thirty_zero_days_or_a_daily_rate():
    result = calculate([row("one")])
    assert (
        result["operationsPerDay"]
        is result["amountPerDay"]
        is result["dailyAverage"]
        is None
    )
    assert result["observation"]["coverageMode"] == "UNKNOWN"
    assert Decimal(result["observation"]["coveredSeconds"]) == 0
    assert result["observation"]["fullWindowCovered"] is False
    assert all(bin["count"] is None for bin in result["calendarActivity"])
    assert sum(bin["observedCount"] for bin in result["calendarActivity"]) == 1
    assert Decimal(result["sampleFillRatio"]) == Decimal("0.03333333")
    assert result["confidenceDeprecated"] is True


def test_complete_coverage_and_overlap_use_explicit_calendar_denominators():
    start = NOW - timedelta(days=30)
    metadata = coverage(
        (start, NOW - timedelta(days=10)), (NOW - timedelta(days=20), NOW)
    )
    result = calculate([row("one")], coverage=metadata)
    assert Decimal(result["observation"]["coveredSeconds"]) == 30 * 86400
    assert Decimal(result["observation"]["coverageFraction"]) == 1
    assert result["observation"]["fullWindowCovered"] is True
    assert Decimal(result["operationsPerDay"]) == Decimal("0.03333333")
    assert Decimal(result["amountPerDay"]) == Decimal("3.33333333")
    assert result["rateDenominatorDays"] == 30
    assert sum(b["count"] for b in result["calendarActivity"]) == 1
    assert (
        sum(b["count"] == 0 for b in result["calendarActivity"])
        == len(result["calendarActivity"]) - 1
    )


@pytest.mark.parametrize("mode", ["FIRST_SEEN_ONLY", "UNKNOWN"])
def test_first_seen_or_unknown_does_not_become_declared_coverage(mode):
    result = calculate(
        [row("one")], coverage=coverage((NOW - timedelta(days=30), NOW), mode=mode)
    )
    assert result["operationsPerDay"] is None
    assert Decimal(result["observation"]["coveredSeconds"]) == 0


def test_partial_coverage_preserves_gaps_and_clips_outside_intervals():
    result = calculate(
        [row("one")],
        coverage=coverage((NOW - timedelta(days=2), NOW + timedelta(days=3))),
    )
    assert Decimal(result["observation"]["coveredSeconds"]) == 2 * 86400
    assert result["operationsPerDay"] is None
    assert any(b["count"] == 0 for b in result["calendarActivity"])
    assert any(b["count"] is None for b in result["calendarActivity"])


def test_counterparty_shares_include_unknown_and_concentration_is_conditional():
    result = calculate(
        [
            row(1, "40", counterparty="A"),
            row(2, "40", counterparty="A"),
            row(3, "20", counterparty="B"),
            row(4, "10", counterparty=None),
        ]
    )
    cp = result["counterparties"]
    assert cp["knownCount"] == 2 and cp["unknownCount"] == 1
    assert Decimal(cp["knownFraction"]) == Decimal("0.75")
    assert Decimal(cp["knownHHI"]) == Decimal("0.55555556")
    buckets = {item["id"]: item for item in cp["items"]}
    assert Decimal(buckets["A"]["countShare"]) == Decimal("0.5")
    assert Decimal(buckets["A"]["totalAmount"]) == 80
    assert Decimal(buckets[None]["amountShare"]) == Decimal("0.09090909")
    assert sum(item["count"] for item in cp["items"]) == 4
    assert not result["quality"]["eligibility"]["counterparty"]["eligible"]


def test_top_five_and_other_unknown_buckets_reconcile_with_totals():
    rows = [row(i, str(i + 1), counterparty=chr(65 + i)) for i in range(7)] + [
        row(9, "10", counterparty=None)
    ]
    result = calculate(rows)
    top = result["counterparties"]["top"]
    assert sum(i["kind"] == "KNOWN" for i in top) == 5
    assert {i["kind"] for i in top} == {"KNOWN", "OTHER_KNOWN", "UNKNOWN"}
    assert sum(i["count"] for i in top) == 8
    assert sum(Decimal(i["totalAmount"]) for i in top) == 38


def test_profile_is_deterministic_and_preserves_strict_time_group_boundaries():
    rows = [row(1, "80", age=30), row(2, "100"), row(3, "120", age=2)]
    rows += [
        dict(row(4), occurredAt=NOW.isoformat()),
        dict(row(5), ingestedAt=NOW.isoformat()),
        dict(row(6), currency="USD"),
        dict(row(7), direction="IN"),
        dict(row(8), clientId="other"),
    ]
    expected = calculate(rows, excluded_id="3")
    assert expected == calculate(list(reversed(rows)), excluded_id="3")
    assert expected["sampleSize"] == 2
    assert expected["history_ids"] == ["1", "2"]
    assert Decimal(expected["totalAmount"]) == 180


def test_timezone_is_normalized_before_histograms_and_calendar_dates():
    result = calculate([dict(row(1), occurredAt="2026-11-04T11:00:00+03:00")])
    assert result["hourCountsUtc"]["8"] == 1
    assert result["weekdayCountsUtc"]["2"] == 1
    assert result["asOf"].endswith("+00:00")


def test_many_operations_in_one_hour_do_not_make_weekly_habits_eligible():
    result = calculate([row(i, age=1) for i in range(120)])
    assert result["activeDays"] == 1
    flags = result["quality"]["eligibility"]
    assert not flags["time"]["eligible"]
    assert not flags["frequency"]["eligible"]
    assert not flags["amount"]["eligible"]


def test_zero_activity_requires_known_observation_and_has_null_counterparty_shares():
    result = calculate([], coverage=coverage((NOW - timedelta(days=30), NOW)))
    assert Decimal(result["operationsPerDay"]) == 0
    assert Decimal(result["totalAmount"]) == 0
    assert result["counterparties"]["knownFraction"] is None
    assert result["quality"]["eligibility"]["frequency"]["eligible"] is True


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "0"])
def test_invalid_money_does_not_become_an_empty_profile(bad):
    with pytest.raises(ValueError):
        calculate([row(1, bad)])


def test_duplicate_ids_and_invalid_coverage_are_explicit_errors():
    with pytest.raises(ValueError):
        calculate([row(1), row(1)])
    with pytest.raises(ValueError):
        calculate([], coverage=coverage((NOW, NOW - timedelta(days=1))))


def test_eligibility_is_separate_for_each_family_and_has_positive_cases():
    rows = [row(i, age=1 + i % 29) for i in range(120)]
    unknown = calculate(rows)
    assert unknown["quality"]["eligibility"]["amount"]["eligible"]
    assert unknown["quality"]["eligibility"]["counterparty"]["eligible"]
    assert not unknown["quality"]["eligibility"]["time"]["eligible"]
    complete = calculate(rows, coverage=coverage((NOW - timedelta(days=30), NOW)))
    assert all(flag["eligible"] for flag in complete["quality"]["eligibility"].values())


def test_decimal_precision_survives_large_amounts_and_json_serialization():
    import json

    result = calculate([row(1, "9999999999999999.99"), row(2, "9999999999999999.98")])
    assert result["totalAmount"] == "19999999999999999.97"
    assert Decimal(result["medianAmount"]) == Decimal("9999999999999999.985")
    assert Decimal(result["madAmount"]) == Decimal("0.005")
    assert (
        json.loads(json.dumps(result, allow_nan=False))["totalAmount"]
        == result["totalAmount"]
    )


@pytest.mark.parametrize("days,expected", [(7, 1), (30, 2), (90, 3)])
def test_windows_have_independent_samples_and_preserve_required_legacy_fields(
    days, expected
):
    result = calculate([row(1, age=2), row(2, age=15), row(3, age=45)], days=days)
    assert result["sampleSize"] == expected
    required = {
        "medianAmount",
        "madAmount",
        "q25",
        "q75",
        "dailyAverage",
        "dailyCounts",
        "hourCountsUtc",
        "weekdayCountsUtc",
        "knownCounterparties",
        "dataStatus",
        "confidence",
        "history_ids",
    }
    assert required <= result.keys()
    assert result == calculate(
        [row(3, age=45), row(2, age=15), row(1, age=2)], days=days
    )


@pytest.mark.parametrize("bad", ["10000000000000000.00", "0.001", "1e100"])
def test_money_outside_source_precision_is_rejected(bad):
    with pytest.raises(ValueError):
        calculate([row(1, bad)])
