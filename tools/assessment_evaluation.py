"""Offline synthetic calibration/holdout. Labels never enter runtime scoring."""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.common.behavior_contract import digest
from backend.services.analysis.assessments.calculation import POLICY, assess, default_rules
from backend.services.analysis.assessments.behavior import frequency_signal, evaluate_quality
from backend.services.analysis.profiles.metrics import build_metrics

SCENARIOS = (
    "normal",
    "amount_upper",
    "amount_lower",
    "rare_time",
    "new_counterparty",
    "burst",
    "change",
    "combined",
    "unknown",
)


def example(name, cutoff, scale=1):
    def uid(key):
        return str(
            uuid5(
                NAMESPACE_URL,
                "offline-assessment:" + cutoff.isoformat() + ":" + name + ":" + key,
            )
        )

    cp, client = uid("cp"), uid("client")
    rows = []
    for day in range(1, 31):
        count = 40 if name == "change" and day <= 6 else 5
        for i in range(count):
            rows.append(
                dict(
                    id=uid(f"{day}:{i}"),
                    clientId=client,
                    amount=str(Decimal(100) * scale),
                    currency="BYN",
                    direction="OUT",
                    counterpartyId=cp,
                    occurredAt=(
                        cutoff - timedelta(days=day, minutes=5 + i)
                    ).isoformat(),
                    ingestedAt=(cutoff - timedelta(days=day)).isoformat(),
                )
            )
    tx = dict(
        id=uid("subject"),
        clientId=client,
        currency="BYN",
        direction="OUT",
        counterpartyId=uid("new") if name == "new_counterparty" else cp,
        occurredAt=cutoff.isoformat(),
        amount=str(
            Decimal(
                200
                if name in ("amount_upper", "combined")
                else 10
                if name == "amount_lower"
                else 100
            )
            * scale
        ),
    )
    if name == "rare_time":
        tx["occurredAt"] = cutoff.replace(hour=3).isoformat()
    if name in ("burst", "combined"):
        rows += [
            dict(
                rows[0],
                id=uid("burst:" + str(i)),
                occurredAt=(cutoff - timedelta(minutes=1)).isoformat(),
                ingestedAt=(cutoff - timedelta(seconds=10)).isoformat(),
            )
            for i in range(20)
        ]
    if name == "unknown":
        rows = []
        tx["counterpartyId"] = None
    t = datetime.fromisoformat(tx["occurredAt"])
    coverage = dict(
        mode="UNKNOWN" if name == "unknown" else "DECLARED_COMPLETE",
        revision="offline-fixture",
        intervals=[{"from": (t - timedelta(days=90)).isoformat(), "to": t.isoformat()}],
    )
    p = build_metrics(
        rows, t, t, "BYN", "OUT", 30, tx["id"], coverage=coverage, client_id=client
    )
    p["id"] = uid("profile")
    return dict(
        transaction=tx,
        history=rows,
        coverage=coverage,
        expected=name
        in ("amount_upper", "amount_lower", "burst", "change", "combined"),
    ), p


def legacy(tx, p, rows):
    # Historical demo seed: AMOUNT x5/weight60, static hour>5/25, novelty/15.
    hits = []
    n = p["sampleSize"]
    cutoff = datetime.fromisoformat(tx["occurredAt"])
    if n >= 5 and Decimal(tx["amount"]) > Decimal(p["medianAmount"]) * 5:
        hits.append(60)
    if (
        1
        + sum(
            cutoff - timedelta(hours=1)
            <= datetime.fromisoformat(r["occurredAt"])
            < cutoff
            for r in rows
        )
        > 5
    ):
        hits.append(25)
    if (
        n >= 5
        and tx.get("counterpartyId")
        and tx["counterpartyId"] not in p["knownCounterparties"]
    ):
        hits.append(15)
    score = sum(hits)
    if frequency_signal(rows, cutoff)["triggered"]:
        score += max(0, 60 - score)
    return score


def evaluate():
    def run(t, scale):
        output = []
        for name in SCENARIOS:
            e, p = example(name, t, scale)
            r = assess(e["transaction"], p, e["history"], default_rules())
            output.append(
                dict(
                    scenario=name,
                    expected=None if name == "unknown" else e["expected"],
                    score=r["assessmentScore"],
                    assessmentStatus=r["assessmentStatus"],
                    legacyScore=legacy(e["transaction"], p, e["history"]),
                    historyDigest=digest(e["history"]),
                    checks=[
                        dict(
                            code=x["code"],
                            status=x["status"],
                            contribution=x["contribution"],
                        )
                        for x in r["checks"]
                    ],
                )
            )
        return output

    tuning = run(datetime(2026, 10, 1, 12, tzinfo=UTC), 1)
    candidates = []
    for threshold in (30, 40, 50):
        q = evaluate_quality(
            [
                dict(
                    expected=x["expected"],
                    predicted=x["score"] >= threshold
                    if x["score"] is not None
                    else None,
                )
                for x in tuning
            ]
        )
        candidates.append(
            dict(
                threshold=threshold,
                errors=q["falsePositive"] + q["falseNegative"],
                metrics=q,
            )
        )
    chosen = min(candidates, key=lambda x: (x["errors"], -x["threshold"]))["threshold"]
    assert chosen == POLICY["threshold"], (
        "The checked-in frozen policy must match tuning, not be changed from holdout"
    )
    policy_hash = digest(POLICY)
    holdout = run(datetime(2026, 11, 8, 12, tzinfo=UTC), Decimal("1.7"))
    assert digest(POLICY) == policy_hash
    quality = evaluate_quality(
        [
            dict(
                expected=x["expected"],
                predicted=x["score"] >= chosen if x["score"] is not None else None,
            )
            for x in holdout
        ]
    )
    return dict(
        scope="small synthetic demonstration only; no banking accuracy estimate",
        selection="minimum tuning error; ties choose highest threshold; holdout never tunes",
        policyHash=policy_hash,
        chosenThreshold=chosen,
        candidates=candidates,
        tuning=tuning,
        holdout=holdout,
        holdoutQuality=quality,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        "Frozen synthetic tuning/holdout completed; threshold",
        result["chosenThreshold"],
    )
