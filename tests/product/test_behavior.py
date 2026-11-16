from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.services.analysis.assessments.behavior import evaluate_quality
from backend.services.analysis.profiles.metrics import build_metrics
from backend.services.transaction.demo.scenarios import scenario_records

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def row(amount, age=1, **extra):
    return dict(
        id=str(amount),
        amount=str(amount),
        currency="BYN",
        direction="OUT",
        occurredAt=(NOW - timedelta(days=age)).isoformat(),
        ingestedAt=(NOW - timedelta(minutes=1)).isoformat(),
        counterpartyId="known",
        **extra,
    )


def test_profile_excludes_self_future_and_wrong_dimensions():
    rows = [row(x) for x in [90, 100, 110, 120, 130]]
    rows += [row(9000, age=-1), row(8000, age=40)]
    rows += [dict(row(7000), currency="USD"), dict(row(6000), direction="IN")]
    rows += [dict(row(5000), ingestedAt=(NOW + timedelta(seconds=1)).isoformat())]
    rows += [dict(row(4000), id="self")]
    result = build_metrics(rows, NOW, NOW, "BYN", "OUT", 30, "self")
    assert result["sampleSize"] == 5
    assert Decimal(result["medianAmount"]) == 110
    assert Decimal(result["madAmount"]) == 10
    assert result["dataStatus"] == "SUFFICIENT"
    assert result["knownCounterparties"] == ["known"]


def test_windows_empty_and_warmup():
    assert build_metrics([], NOW, NOW, "BYN", "OUT", 7)["medianAmount"] is None
    result = build_metrics([row(100, 15)], NOW, NOW, "BYN", "OUT", 7)
    assert result["dataStatus"] == "INSUFFICIENT"
    assert result["sampleSize"] == 0
    assert build_metrics([row(100, 15)], NOW, NOW, "BYN", "OUT", 30)["sampleSize"] == 1


def test_quality_pending_is_never_a_true_negative():
    result = evaluate_quality(
        [
            {"expected": True, "predicted": True},
            {"expected": True, "predicted": False},
            {"expected": False, "predicted": True},
            {"expected": False, "predicted": False},
            {"expected": False, "predicted": None},
            {"expected": None, "predicted": False},
        ]
    )
    assert result["evaluated"] == 4
    assert result["pending"] == 1
    assert result["warmup"] == 1
    assert result["precision"] == result["recall"] == result["falsePositiveRate"] == 0.5


def test_scenario_is_reproducible_chronological_and_labels_are_separate():
    records = scenario_records("demo-one", NOW)
    assert records == scenario_records("demo-one", NOW)
    assert len(records) >= 40
    assert records == sorted(records, key=lambda x: x["input"]["occurredAt"])
    assert any(x["expected"] is True for x in records)
    assert any(x["expected"] is False for x in records)
    assert all("expected" not in x["input"] for x in records)
    assert all(datetime.fromisoformat(x["input"]["occurredAt"]) <= NOW for x in records)


def test_personal_frequency_detects_a_burst_without_amount_spike():
    from backend.services.analysis.assessments.behavior import frequency_signal

    history = [dict(row(100, age=d), id=str(d)) for d in range(1, 13)]
    burst = [
        dict(
            row(100),
            id="burst" + str(i),
            occurredAt=(NOW - timedelta(minutes=i + 1)).isoformat(),
        )
        for i in range(4)
    ]
    signal = frequency_signal(history + burst, NOW)
    assert signal["triggered"] and signal["observed"] == 5 and signal["baseline"] == 4
    assert not frequency_signal(history + burst[:3], NOW)["triggered"]
    assert not frequency_signal([], NOW)["triggered"]


def test_run_comparison_includes_explained_behavioral_hits():
    from types import SimpleNamespace
    from backend.services.analysis.persistence.repository import AnalysisRepository

    def query(sql, params):
        if 'JOIN rule_hits' in sql:
            return [{'transaction_id': 'tx', 'code': 'FREQUENCY'}]
        return [{'transaction_id': 'tx', 'details': {'behavioralSignals': [
            {'code': 'PROFILE_FREQUENCY', 'contribution': 35},
            {'code': 'INACTIVE', 'contribution': 0},
        ]}}]

    repository = AnalysisRepository(SimpleNamespace(query=query))
    assert repository.hit_codes('run') == {'tx': {'FREQUENCY', 'PROFILE_FREQUENCY'}}
