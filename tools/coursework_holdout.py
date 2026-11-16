"""Fixed, previously unused synthetic holdout; never tunes production rules."""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5
from pathlib import Path
import argparse
import json

from backend.common.report_contract import digest
from backend.services.analysis.assessments.calculation import assess, default_rules, POLICY
from backend.services.analysis.profiles.metrics import build_metrics
from backend.services.analysis.assessments.behavior import evaluate_quality

SEED = "coursework-holdout-20261111-v1"
AT = datetime(2026, 11, 11, 12, tzinfo=UTC)


def identifier(name):
    return str(uuid5(NAMESPACE_URL, SEED + ":" + name))


def dataset(name, wide=False, count=145):
    client = identifier(name)
    return [
        dict(
            id=identifier(name + ":" + str(i)),
            clientId=client,
            currency="BYN",
            direction="OUT",
            amount=str([20, 60, 100, 140, 180][i % 5] if wide else 100),
            counterpartyId=identifier("known"),
            occurredAt=(AT - timedelta(days=1 + i // 5, minutes=i % 5)).isoformat(),
            ingestedAt=(AT - timedelta(minutes=1)).isoformat(),
        )
        for i in range(count)
    ]


def run():
    rules = default_rules()
    policy_hash = digest(dict(policy=POLICY, rules=rules))
    profiles, rows_by_group = {}, {}
    coverage = dict(
        mode="DECLARED_COMPLETE",
        revision=SEED,
        intervals=[
            dict(
                **{"from": (AT - timedelta(days=90)).isoformat(), "to": AT.isoformat()}
            )
        ],
    )
    for name, wide, n in [
        ("narrow", False, 145),
        ("wide", True, 145),
        ("short", False, 3),
        ("warmup", False, 0),
        ("unknown", False, 3),
    ]:
        rows = dataset(name, wide, n)
        rows_by_group[name] = rows
        p = build_metrics(
            rows,
            AT,
            AT,
            "BYN",
            "OUT",
            30,
            coverage=dict(coverage, mode="UNKNOWN") if name == "unknown" else coverage,
            client_id=identifier(name),
        )
        p["id"] = identifier("profile:" + name)
        profiles[name] = p
    # Labels are declared before assessment, independent of returned scores.
    selectors = [
        ("narrow-normal", "narrow", "100", False, False),
        ("wide-normal", "wide", "100", False, False),
        ("narrow-high", "narrow", "200", True, False),
        ("wide-high", "wide", "200", False, False),
        ("narrow-low", "narrow", "5", True, False),
        ("novel-counterparty", "narrow", "100", True, True),
        ("short-history", "short", "200", True, False),
        ("unknown-history", "unknown", "200", True, False),
        ("warmup", "warmup", "100", None, False),
    ]
    detections = []
    for name, group, amount, expected, novel in selectors:
        tx = dict(
            id=identifier(name + ":tx"),
            clientId=identifier(group),
            currency="BYN",
            direction="OUT",
            amount=amount,
            counterpartyId=identifier("new" if novel else "known"),
            occurredAt=AT.isoformat(),
        )
        assessment = assess(tx, profiles[group], rows_by_group[group], rules)
        category = (
            "warmup"
            if expected is None
            else "unavailable"
            if assessment["assessmentStatus"] == "UNAVAILABLE"
            else "evaluated"
        )
        detections.append(
            dict(
                scenario=name,
                expected=expected,
                predicted=assessment["reviewRequired"],
                category=category,
                assessmentStatus=assessment["assessmentStatus"],
                score=assessment["assessmentScore"],
                checks=assessment["checks"],
            )
        )
    detections.append(
        dict(
            scenario="not-yet-processed",
            expected=False,
            predicted=None,
            category="pending",
            assessmentStatus=None,
            score=None,
        )
    )
    # This adapter deliberately keeps pending distinct from impossible assessment.
    scored = [r for r in detections if r["category"] == "evaluated"]
    quality = evaluate_quality(scored)
    excluded = {
        k: sum(r["category"] == k for r in detections)
        for k in ["pending", "warmup", "unavailable"]
    }
    assert digest(dict(policy=POLICY, rules=rules)) == policy_hash
    return dict(
        seed=SEED,
        asOf=AT.isoformat(),
        policyHash=policy_hash,
        threshold=POLICY["threshold"],
        tuningPerformed=False,
        scope="small synthetic holdout; not banking accuracy",
        profiles={
            k: {
                field: p[field]
                for field in [
                    "sampleSize",
                    "totalAmount",
                    "medianAmount",
                    "madAmount",
                    "q25",
                    "q75",
                    "observedCount",
                    "includedCount",
                ]
            }
            for k, p in profiles.items()
        },
        datasetDigests={k: digest(v) for k, v in rows_by_group.items()},
        detections=detections,
        quality=quality,
        excluded=excluded,
        emptyDenominators=evaluate_quality([]),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(run(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
