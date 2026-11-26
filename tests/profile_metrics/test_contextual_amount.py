from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.services.analysis.assessments.calculation import assess, default_rules
from backend.services.analysis.assessments.comparison import select_amount_basis
from backend.services.analysis.profiles.metrics import build_metrics

AT = datetime(2026, 11, 24, 12, tzinfo=UTC)


def example():
    client = str(uuid4())
    subject = {"id":str(uuid4()), "clientId":client, "currency":"BYN", "direction":"OUT",
               "amount":"400.00", "occurredAt":AT.isoformat(), "operationKind":"PURCHASE",
               "operationChannel":"POS"}
    rows = []
    for kind, channel, amount in (("PURCHASE","POS","100.00"),("TRANSFER","BANK_API","1000.00")):
        for i in range(20):
            day = (AT-timedelta(days=1+i//2, minutes=i%2)).isoformat()
            rows.append({**subject, "id":str(uuid4()), "amount":amount, "operationKind":kind,
                         "operationChannel":channel, "occurredAt":day, "ingestedAt":day})
    profile = build_metrics(rows,AT,AT,"BYN","OUT",30,subject["id"],client_id=client)
    profile["id"] = str(uuid4())
    return subject, rows, profile


def test_only_amount_uses_the_contextual_distribution():
    tx, rows, profile = example()
    rules = default_rules()
    basis, selected = select_amount_basis(tx, profile, rows, rules)
    assert basis is not None
    assert basis["scope"] == "KIND" and basis["sampleSize"] == 20
    assert basis["statistics"]["medianAmount"] == "100.00"
    ordinary = assess(tx, profile, rows, rules)
    actual = assess(tx, profile, rows, rules, amount_profile=selected, amount_basis_id=basis["id"])
    assert ordinary["checks"][0]["status"] == "NOT_TRIGGERED"
    assert actual["checks"][0]["status"] == "TRIGGERED" and actual["checks"][0]["contribution"] == 40
    assert actual["checks"][0]["baseline"]["comparisonBasisId"] == basis["id"]
    assert actual["checks"][1:] == ordinary["checks"][1:]
    assert select_amount_basis(tx, profile, list(reversed(rows)), rules)[0] == basis


def test_missing_and_short_contexts_explain_the_fallback():
    tx, rows, profile = example()
    rules = default_rules()
    short = {**tx, "operationKind":"WITHDRAWAL", "operationChannel":"BANK_API"}
    channel, _ = select_amount_basis(short, profile, rows, rules)
    assert channel is not None
    assert channel["scope"] == "CHANNEL" and channel["statistics"]["medianAmount"] == "1000.00"
    assert channel["candidates"][0]["reason"] == "INSUFFICIENT_CONTEXT_HISTORY"
    general, narrowed = select_amount_basis({**short,"operationChannel":"UNKNOWN"}, profile, rows, rules)
    assert general is not None
    assert general["scope"] == "GENERAL" and narrowed is None
    assert [c["reason"] for c in general["candidates"]] == ["INSUFFICIENT_CONTEXT_HISTORY","NO_SUBJECT_CLASSIFICATION"]


def test_context_respects_feedback_filtered_history_and_disabled_amount():
    tx, rows, profile = example()
    # The observed rows include a confirmed anomaly. Only the IDs in the
    # canonical baseline are eligible to establish the amount distribution.
    profile["history_ids"].remove(rows[0]["id"])
    basis, _ = select_amount_basis(tx, profile, rows, default_rules())
    assert basis is not None
    assert basis["scope"] == "GENERAL" and basis["candidates"][0]["sampleSize"] == 19
    assert select_amount_basis(tx, profile, rows, default_rules()[1:]) == (None,None)
    rules = default_rules()
    parameters = rules[0]["parameters"]
    assert isinstance(parameters, dict)
    parameters["enabled"] = False
    assert select_amount_basis(tx, profile, rows, rules) == (None,None)
