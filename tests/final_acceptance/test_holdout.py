from decimal import Decimal
from tools.coursework_holdout import run


def test_fixed_holdout_hand_calculations_and_evaluation_denominators():
    result = run()
    assert result["profiles"]["narrow"]["sampleSize"] == 145
    assert Decimal(result["profiles"]["narrow"]["totalAmount"]) == Decimal(
        "14500.00000000"
    )
    assert Decimal(result["profiles"]["narrow"]["medianAmount"]) == Decimal(
        "100.00000000"
    )
    assert Decimal(result["profiles"]["narrow"]["madAmount"]) == Decimal("0.00000000")
    assert Decimal(result["profiles"]["wide"]["madAmount"]) == 40
    rows = {r["scenario"]: r for r in result["detections"]}
    assert rows["narrow-high"]["predicted"] is True
    assert rows["wide-high"]["predicted"] is False
    assert result["quality"]["truePositive"] == 2
    assert result["quality"]["trueNegative"] == 3
    assert result["quality"]["falseNegative"] == 2
    assert result["quality"]["evaluated"] == 7
    assert result["excluded"] == dict(pending=1, warmup=1, unavailable=1)
    assert result["emptyDenominators"]["precision"] is None
    assert result["emptyDenominators"]["recall"] is None
    assert result == run()
