"""Run the frozen business evaluator against an independent synthetic manifest."""

import argparse
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
import json
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from backend.common.report_contract import digest
from backend.services.analysis.assessments.behavior import evaluate_quality
from backend.services.analysis.assessments.calculation import POLICY, assess, default_rules
from backend.services.analysis.profiles.metrics import build_metrics
from tools.assessment_evaluation import example


# Scenario labels are assigned by the synthetic business situation, before scoring.
SCENARIOS = {
    "routine": ("normal", False),
    "within-usual-range": ("normal", False),
    "high-amount": ("amount_upper", True),
    "low-amount": ("amount_lower", True),
    "burst": ("burst", True),
    "unusual-hour": ("rare_time", True),
    "first-counterparty": ("new_counterparty", True),
    "activity-shift": ("change", True),
    "combined": ("combined", True),
}
REGIMES = {"full": None, "covered-short": 40, "shallow": 20, "one-day": 3, "none": 0}
ANCHOR = datetime(2026, 11, 17, 12, tzinfo=UTC)


def cases():
    for number in range(3):
        for index, (scenario, (template, label)) in enumerate(SCENARIOS.items()):
            for j, (regime, limit) in enumerate(REGIMES.items()):
                t = ANCHOR + timedelta(days=(number * len(SCENARIOS) + index) * len(REGIMES) + j)
                scale = Decimal("1.19") + Decimal("0.37") * number
                payload, _ = example(template, t, scale)
                tx = payload["transaction"]
                rows = sorted(payload["history"], key=lambda row: row["occurredAt"], reverse=True)
                if limit is not None:
                    rows = rows[:limit]
                if scenario == "within-usual-range":
                    for i, row in enumerate(rows):
                        row["amount"] = str(scale * (60 + (i % 5) * 20))
                    tx["amount"] = str(scale * 125)
                cutoff = datetime.fromisoformat(tx["occurredAt"])
                assert all(datetime.fromisoformat(row["occurredAt"]) < cutoff for row in rows)
                coverage = (payload["coverage"] if regime in ("full", "covered-short")
                            else {"mode": "UNKNOWN"})
                yield dict(id=f"{number+1}:{scenario}:{regime}", label=label,
                           scenario=scenario, regime=regime, transaction=tx,
                           history=rows, coverage=coverage)


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    control = list(cases())
    rules = default_rules()
    frozen = dict(threshold=POLICY["threshold"], policy=POLICY, rules=rules,
                  tuning="Frozen before this holdout; no tuning on control cases",
                  cases=control)
    manifest = output / "control-manifest.json"
    manifest.write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
    fingerprint = digest(frozen)
    evaluated = []
    for item in control:
        tx = item["transaction"]
        t = datetime.fromisoformat(tx["occurredAt"])
        profile = build_metrics(item["history"], t, t, tx["currency"], tx["direction"], 30,
                                coverage=item["coverage"], client_id=tx["clientId"])
        profile["id"] = str(uuid5(NAMESPACE_URL, "stage9-profile:" + item["id"]))
        selected_ids = set(profile["history_ids"])
        history = [r for r in item["history"] if r["id"] in selected_ids]
        result = assess(tx, profile, history, rules)
        evaluated.append(dict(
            id=item["id"], scenario=item["scenario"], regime=item["regime"],
            historyCount=len(history), expected=item["label"],
            predicted=result["reviewRequired"], score=result["assessmentScore"],
            status=result["assessmentStatus"],
            checks=[{key: check[key] for key in ("code", "status", "contribution", "dataReasons")}
                    for check in result["checks"]],
        ))
    assert digest(frozen) == fingerprint
    def metrics(records):
        return evaluate_quality([row for row in records if row["predicted"] is not None])

    quality = metrics(evaluated)
    unknown = [row for row in evaluated if row["predicted"] is None]
    errors = [row for row in evaluated
              if row["predicted"] is not None and row["predicted"] != row["expected"]]
    report = dict(
        total=len(evaluated), manifestDigest=fingerprint, policyHash=digest(dict(policy=POLICY, rules=rules)),
        threshold=POLICY["threshold"], summary=quality,
        byRegime={regime: metrics([row for row in evaluated if row["regime"] == regime])
                  for regime in REGIMES},
        exclusions={
            "unavailable": len(unknown),
            "byRegime": dict(Counter(row["regime"] for row in unknown)),
            "byReason": dict(Counter(reason for row in unknown for reason in {
                reason for check in row["checks"] for reason in check["dataReasons"]
            })),
        },
        precisionNumerator=quality["truePositive"],
        precisionDenominator=quality["truePositive"] + quality["falsePositive"],
        recallNumerator=quality["truePositive"],
        recallDenominator=quality["truePositive"] + quality["falseNegative"],
        errors=errors, records=evaluated,
    )
    (output / "quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = run(parser.parse_args().output)
    print(result["summary"])
