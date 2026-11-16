from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.common.data import DomainError


def profile(**changes):
    metadata = dict(
        id=str(uuid4()),
        clientId="client",
        currency="BYN",
        direction="OUT",
        windowDays=30,
        version=1,
        origin="SCHEDULED",
        asOf="2026-11-01T00:00:00+00:00",
    )
    metrics = dict(
        algorithmVersion="behavior-profile-v3.2",
        metricsSchemaVersion=4,
        historyPolicyVersion="policy",
        medianAmount="100",
        sampleSize=20,
        operationsPerDay="2",
        units=dict(activityRate="operations/24h"),
        quality=dict(status="PARTIAL"),
        knownCounterparties=["a"],
        counterpartyCount=1,
        history_ids=["tx-a"],
    )
    metrics.update(changes)
    return dict(metadata=metadata, metrics=metrics)


def compare(a, b):
    from backend.services.analysis.profiles.comparison import compare_profiles

    return compare_profiles(a, b)


def test_manual_money_frequency_status_and_saved_membership():
    a = profile()
    b = profile(
        medianAmount="150",
        operationsPerDay="3",
        sampleSize=30,
        quality=dict(status="READY"),
        knownCounterparties=["b"],
        history_ids=["tx-b"],
    )
    original = deepcopy((a, b))
    value = compare(a, b)
    rows = {r["key"]: r for r in value["rows"]}
    assert rows["medianAmount"]["delta"] == "50"
    assert rows["medianAmount"]["percentChange"] == "50"
    assert rows["operationsPerDay"]["delta"] == "1"
    assert rows["operationsPerDay"]["unit"] == "operations/24h"
    assert (
        rows["qualityStatus"]["before"] == "PARTIAL"
        and rows["qualityStatus"]["after"] == "READY"
    )
    assert value["counterparties"]["added"] == ["b"]
    assert value["counterparties"]["removed"] == ["a"]
    assert value["operations"]["added"] == ["tx-b"]
    assert (a, b) == original
    assert compare(a, b) == value


def test_zero_baseline_same_version_empty_and_legacy():
    a = profile(medianAmount="0", operationsPerDay="0", sampleSize=0)
    b = profile(medianAmount="10", operationsPerDay="1")
    assert compare(a, b)["rows"][0]["percentChange"] is None
    assert compare(a, a)["rows"][0]["delta"] == "0"
    empty = profile(
        medianAmount=None,
        operationsPerDay=None,
        sampleSize=0,
        history_ids=[],
        knownCounterparties=[],
    )
    assert compare(empty, empty)["rows"][0]["reason"] == "MISSING_VALUE"
    legacy = profile()
    legacy["metrics"] = dict(sampleSize=5, topCounterparties=[dict(id="a")])
    value = compare(legacy, b)
    assert value["rows"][0]["delta"] is None
    assert not value["counterparties"]["complete"]
    assert not value["operations"]["available"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("clientId", "other"),
        ("currency", "USD"),
        ("direction", "IN"),
        ("windowDays", 7),
    ],
)
def test_incompatible_groups_are_rejected(field, value):
    a, b = profile(), profile()
    b["metadata"][field] = value
    with pytest.raises(DomainError, match="групп"):
        compare(a, b)


@pytest.mark.parametrize(
    "changes",
    [
        dict(algorithmVersion="other"),
        dict(metricsSchemaVersion=1),
        dict(historyPolicyVersion="other"),
    ],
)
def test_method_change_disables_numeric_deltas(changes):
    value = compare(profile(), profile(**changes))
    assert not value["methodComparable"]
    assert all(r["delta"] is None for r in value["rows"])


@pytest.mark.parametrize("role", ["ANALYST", "ADMIN"])
def test_other_roles_rejected_before_read(role):
    from backend.services.analysis.profiles.workspace import WorkspaceService

    db = Mock()
    with pytest.raises(DomainError):
        WorkspaceService(db).compare_profile_versions(
            SimpleNamespace(role=role, is_active=True), str(uuid4()), str(uuid4())
        )
    db.one.assert_not_called()


def test_unknown_profile_rejected():
    from backend.services.analysis.profiles.workspace import WorkspaceService

    db = Mock()
    db.one.return_value = None
    with pytest.raises(DomainError, match="не найдена"):
        WorkspaceService(db).compare_profile_versions(
            SimpleNamespace(role="MANAGER", is_active=True), str(uuid4()), str(uuid4())
        )


def test_fixed_history_metrics_have_hand_checked_money_frequency_and_status():
    from tests.profile_metrics.test_metrics import NOW, row, coverage, calculate
    from datetime import timedelta

    a, b = profile(), profile()
    a["metrics"] = calculate(
        [row(i, "100", age=i + 1) for i in range(20)],
        coverage=coverage((NOW - timedelta(days=30), NOW)),
    )
    b["metrics"] = calculate(
        [row(i, "150", age=(i % 29) + 0.5) for i in range(100)],
        coverage=coverage((NOW - timedelta(days=30), NOW)),
    )
    value = compare(a, b)
    rows = {r["key"]: r for r in value["rows"]}
    assert rows["medianAmount"]["delta"] == "50"
    assert rows["operationsPerDay"]["before"] == "0.66666667"
    assert rows["operationsPerDay"]["after"] == "3.33333333"
    assert rows["operationsPerDay"]["delta"] == "2.66666666"
    assert rows["qualityStatus"]["before"] == "PARTIAL"
    assert rows["qualityStatus"]["after"] == "READY"


def test_absent_membership_is_unknown_and_long_saved_lists_are_explicitly_limited():
    a, b = profile(), profile()
    a["metrics"].pop("knownCounterparties")
    a["metrics"].pop("history_ids")
    assert not compare(a, b)["counterparties"]["available"]
    b["metrics"]["history_ids"] = [str(i) for i in range(210)]
    a = profile(history_ids=[])
    delta = compare(a, b)["operations"]
    assert (
        delta["addedCount"] == 210
        and len(delta["added"]) == 200
        and delta["outputTruncated"]
    )
