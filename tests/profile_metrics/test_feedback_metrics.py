from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.services.analysis.profiles.metrics import build_metrics


def test_observed_preserved_baseline_exclusion_and_no_score_feedback_loop():
    t = datetime(2026, 11, 9, 12, tzinfo=UTC)
    client = str(uuid4())
    outlier = str(uuid4())
    cp = str(uuid4())
    rows = [
        dict(
            id=outlier if d == 1 else str(uuid4()),
            clientId=client,
            amount="1000.00" if d == 1 else "100.00",
            currency="BYN",
            direction="OUT",
            counterpartyId=cp,
            occurredAt=(t - timedelta(days=d)).isoformat(),
            ingestedAt=(t - timedelta(days=d) + timedelta(minutes=1)).isoformat(),
        )
        for d in range(1, 5)
    ]
    from backend.services.analysis.profiles.history_policy import reference

    ref = reference(1, t, "WITH_CURRENT_KNOWLEDGE")
    feedback = dict(
        reference=ref,
        decisions=[
            dict(
                transactionId=outlier,
                caseId=str(uuid4()),
                decisionId=str(uuid4()),
                outcome="UNUSUAL_CONFIRMED",
                decidedAt=(t - timedelta(minutes=1)).isoformat(),
                knowledgeRevision=1,
                caseVersion=2,
                learnedAt=t.isoformat(),
            )
        ],
    )
    p = build_metrics(rows, t, t, "BYN", "OUT", 30, client_id=client, feedback=feedback)
    assert (
        p["observedCount"] == 4 and p["includedCount"] == 3 and p["excludedCount"] == 1
    )
    assert (
        p["observed"]["totalAmount"] == "1300.00"
        and p["baseline"]["totalAmount"] == "300.00"
    )
    assert outlier in p["observedHistoryIds"] and outlier not in p["history_ids"]
    assert p["exclusions"][0]["decisionId"] == feedback["decisions"][0]["decisionId"]
    unchanged = build_metrics(rows, t, t, "BYN", "OUT", 30, client_id=client)
    assert unchanged["sampleSize"] == 4


def test_excluded_observations_still_count_in_current_hour_and_counterparty_context():
    from tests.profile_metrics.test_assessment import fixture, evaluate, T
    from backend.services.analysis.profiles.history_policy import reference

    tx, old, rows = fixture()
    unusual_cp = str(uuid4())
    tx["counterpartyId"] = unusual_cp
    recent = dict(
        rows[0],
        id=str(uuid4()),
        counterpartyId=unusual_cp,
        occurredAt=(T - timedelta(minutes=1)).isoformat(),
        ingestedAt=(T - timedelta(minutes=1)).isoformat(),
    )
    rows.append(recent)
    ref = reference(1, T, "WITH_CURRENT_KNOWLEDGE")
    decision = dict(
        transactionId=recent["id"],
        caseId=str(uuid4()),
        decisionId=str(uuid4()),
        outcome="UNUSUAL_CONFIRMED",
        decidedAt=T.isoformat(),
        knowledgeRevision=1,
        caseVersion=2,
        learnedAt=T.isoformat(),
    )
    p = build_metrics(
        rows,
        T,
        T,
        "BYN",
        "OUT",
        30,
        client_id=tx["clientId"],
        coverage=dict(
            mode="DECLARED_COMPLETE",
            revision="test",
            intervals=old["observation"]["confirmedIntervals"],
        ),
        feedback=dict(reference=ref, decisions=[decision]),
    )
    p["id"] = "profile"
    checks = {c["code"]: c for c in evaluate(tx, p, rows)["checks"]}
    assert (
        p["observedCount"] == old["sampleSize"] + 1
        and p["includedCount"] == old["sampleSize"]
    )
    assert checks["PROFILE_FREQUENCY"]["observed"]["count"] == 2
    assert checks["COUNTERPARTY_NOVELTY"]["status"] == "TRIGGERED"
    assert checks["COUNTERPARTY_NOVELTY"]["baseline"]["seenInObservedHistory"] is True


def test_excluding_all_history_is_explicitly_empty_baseline():
    from tests.profile_metrics.test_assessment import fixture, evaluate, T
    from backend.services.analysis.profiles.history_policy import reference

    tx, _, rows = fixture()
    rows = rows[:1]
    ref = reference(1, T, "WITH_CURRENT_KNOWLEDGE")
    d = dict(
        transactionId=rows[0]["id"],
        caseId=str(uuid4()),
        decisionId=str(uuid4()),
        outcome="UNUSUAL_CONFIRMED",
        decidedAt=T.isoformat(),
        knowledgeRevision=1,
        caseVersion=2,
        learnedAt=T.isoformat(),
    )
    p = build_metrics(
        rows,
        T,
        T,
        "BYN",
        "OUT",
        30,
        client_id=tx["clientId"],
        feedback=dict(reference=ref, decisions=[d]),
    )
    p["id"] = "profile"
    assert p["quality"]["status"] == "BASELINE_EMPTY"
    assert p["observedCount"] == 1 and p["includedCount"] == 0
    assert (
        p["observed"]["totalAmount"] == "100.00"
        and p["baseline"]["totalAmount"] == "0.00"
    )
    assert evaluate(tx, p, rows)["assessmentScore"] is None
