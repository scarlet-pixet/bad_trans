from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

T = datetime(2026, 11, 9, 12, tzinfo=UTC)


def row(days, amount="100.00"):
    return dict(
        id=str(uuid4()),
        occurredAt=(T - timedelta(days=days)).isoformat(),
        amount=amount,
    )


def compare(rows, days=30, complete=True):
    from backend.services.analysis.profiles.dynamics import compare_periods

    intervals = [(T - timedelta(days=days), T)] if complete else []
    return compare_periods(rows, T, days, intervals)


def test_rates_use_disjoint_periods_and_equal_time_units():
    result = compare([row(d) for d in range(1, 31)])
    assert result["short"]["days"] == 7 and result["prior"]["days"] == 23
    assert result["short"]["count"] == 7 and result["prior"]["count"] == 23
    assert result["status"] == "AVAILABLE"
    assert Decimal(result["countRateRatio"]) == 1
    assert Decimal(result["amountRateRatio"]) == 1
    assert result["overlappingPeriods"] is False


@pytest.mark.parametrize("days,prior", [(30, 23), (90, 83)])
def test_zero_baseline_and_no_activity_are_explicit(days, prior):
    result = compare([row(1)], days)
    assert result["prior"]["days"] == prior
    assert result["status"] == "ZERO_BASELINE" and result["countRateRatio"] is None
    assert compare([], days)["status"] == "NO_ACTIVITY"
    assert compare([row(10)], days)["countRateRatio"] == "0.00000000"


def test_unknown_coverage_is_not_zero_and_not_infinite():
    r = compare([row(1), row(10)], complete=False)
    assert r["status"] == "UNKNOWN_COVERAGE"
    assert r["short"]["count"] == 1 and r["short"]["operationsPerDay"] is None
    assert r["countRateRatio"] is None
    assert r["short"]["medianAmount"] == "100.00"


def test_boundary_7_is_short_lower_bound_and_old_rows_do_not_leak():
    r = compare([row(0), row(7), row(30), row(31)])
    assert r["short"]["count"] == 1 and r["prior"]["count"] == 1
    assert compare([], 7)["status"] == "WINDOW_TOO_SHORT"


def test_large_money_is_decimal_and_dynamics_does_not_score():
    r = compare([row(1, "9999999999999999.99"), row(10, "0.01")])
    assert r["short"]["totalAmount"] == "9999999999999999.99"
    assert "score" not in r
