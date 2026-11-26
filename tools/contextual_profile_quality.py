"""Compare general and contextual amount assessment on a frozen synthetic holdout."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from uuid import NAMESPACE_URL, uuid5

from backend.common.behavior_contract import digest
from backend.services.analysis.assessments.behavior import evaluate_quality
from backend.services.analysis.assessments.calculation import (
    POLICY,
    assess,
    default_rules,
)
from backend.services.analysis.assessments.comparison import (
    COMPARISON_POLICY,
    select_amount_basis,
)
from backend.services.analysis.profiles.metrics import build_metrics

ANCHOR = datetime(2026, 11, 1, 9, tzinfo=UTC)
# Labels describe the scenario known to its reviewer. They stay outside scorer inputs.
SCENARIOS = (
    ("regular-purchase", False, "PURCHASE", "POS", 100, 24),
    ("unexpected-large-purchase", True, "PURCHASE", "POS", 400, 24),
    ("approved-one-off-purchase", False, "PURCHASE", "POS", 400, 24),
    ("regular-transfer", False, "TRANSFER", "BANK_API", 1_000, 24),
    ("unexpected-small-transfer", True, "TRANSFER", "BANK_API", 400, 24),
    ("unplanned-large-transfer", True, "TRANSFER", "BANK_API", 4_000, 24),
    ("short-kind-history", False, "WITHDRAWAL", "BANK_API", 1_000, 24),
    ("missing-classification", False, "UNKNOWN", "UNKNOWN", 1_000, 24),
    ("empty-history", True, "PURCHASE", "POS", 400, 0),
)


def identifier(stem: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"bad-trans:context-holdout-v1:{stem}"))


def manifest() -> dict:
    random = Random(7319)
    cases = []
    for scenario, expected, kind, channel, amount, history_count in SCENARIOS:
        for iteration in range(12):
            case_id = f"{scenario}-{iteration + 1:02d}"
            cutoff = ANCHOR + timedelta(days=iteration)
            scale = random.randint(8, 15)
            client_id = identifier(f"{case_id}:client")
            common = {
                "clientId": client_id, "currency": "BYN", "direction": "OUT",
                "counterpartyId": identifier(f"{case_id}:payee"),
            }
            history = []
            for category, route, center in (("PURCHASE", "POS", 100), ("TRANSFER", "BANK_API", 1_000)):
                for index in range(history_count):
                    happened = cutoff - timedelta(days=1 + index % 20, minutes=20 + index)
                    history.append({
                        **common, "id": identifier(f"{case_id}:{category}:{index}"),
                        "occurredAt": happened.isoformat(),
                        "ingestedAt": (happened + timedelta(seconds=10)).isoformat(),
                        "amount": str(scale * (center + random.randint(-4, 4))),
                        "operationKind": category, "operationChannel": route,
                    })
            tx = {
                **common, "id": identifier(f"{case_id}:subject"), "occurredAt": cutoff.isoformat(),
                "amount": str(scale * amount), "operationKind": kind, "operationChannel": channel,
            }
            coverage: dict = {"mode": "UNKNOWN"}
            if history and iteration % 2 == 0:
                coverage = {
                    "mode": "DECLARED_COMPLETE", "revision": "synthetic-context-holdout-v1",
                    "intervals": [{"from": (cutoff - timedelta(days=30)).isoformat(), "to": cutoff.isoformat()}],
                }
            cases.append({
                "id": case_id, "scenario": scenario, "expected": expected,
                "inputs": {"transaction": tx, "history": history, "coverage": coverage},
            })
    return {"source": "synthetic", "seed": 7319, "cases": cases}


def compare(inputs: dict, rules: list[dict]) -> dict:
    tx, rows, coverage = (inputs[key] for key in ("transaction", "history", "coverage"))
    cutoff = datetime.fromisoformat(tx["occurredAt"])
    if any(
        row["clientId"] != tx["clientId"]
        or datetime.fromisoformat(row["occurredAt"]) >= cutoff
        or datetime.fromisoformat(row["ingestedAt"]) > cutoff
        for row in rows
    ):
        raise ValueError("History must belong to the same client and be available before the evaluation cut")
    profile = build_metrics(rows, cutoff, cutoff, tx["currency"], tx["direction"], 30,
                            coverage=coverage, client_id=tx["clientId"])
    profile["id"] = identifier(f'{tx["id"]}:profile')
    observed = set(profile.get("observedHistoryIds", profile["history_ids"]))
    history = [row for row in rows if row["id"] in observed]
    basis, narrowed = select_amount_basis(tx, profile, rows, rules)
    if basis is None:
        raise ValueError("The frozen comparison requires the amount assessment rule")
    result = {}
    for name, overrides in (
        ("general", {}),
        ("contextual", {"amount_profile": narrowed, "amount_basis_id": basis["id"]}),
    ):
        assessment = assess(tx, profile, history, rules, **overrides)
        amount = next(check for check in assessment["checks"] if check["code"] == "AMOUNT_DEVIATION")
        result[name] = {
            "predicted": assessment["reviewRequired"], "score": assessment["assessmentScore"],
            "status": assessment["assessmentStatus"], "amountStatus": amount["status"],
            "amountContribution": amount["contribution"], "amountBounds": amount["baseline"],
            "amountReasons": amount["dataReasons"],
        }
    return {
        **result, "scope": basis["scope"], "sampleSize": basis["sampleSize"],
        "generalSampleSize": profile["sampleSize"], "basisId": basis["id"],
        "coverage": basis["generalCoverage"], "fallbacks": basis["candidates"],
    }


def metrics(records: list[dict], method: str) -> dict:
    labels = [{"expected": row["expected"], "predicted": row[method]["predicted"]} for row in records]
    summary = evaluate_quality([label for label in labels if label["predicted"] is not None])
    unavailable = len(labels) - summary["evaluated"]
    return {
        **{key: value for key, value in summary.items() if key not in {"pending", "warmup"}},
        "unavailable": unavailable,
        "positiveUnavailable": sum(label["predicted"] is None and label["expected"] for label in labels),
        "numericCoverage": round(summary["evaluated"] / len(labels), 4) if labels else None,
        "routineAmountTested": sum(
            not row["expected"] and row[method]["amountStatus"] in {"TRIGGERED", "NOT_TRIGGERED"}
            for row in records
        ),
        "routineInsideAmountBounds": sum(
            not row["expected"] and row[method]["amountStatus"] == "NOT_TRIGGERED" for row in records
        ),
    }


def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    data = manifest()
    policy = {"policy": POLICY, "rules": default_rules()}
    manifest_hash, policy_hash = digest(data), digest(policy)
    (output / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (output / "policy.json").write_text(json.dumps(policy, indent=2), encoding="utf-8")
    records = [
        {**{key: case[key] for key in ("id", "scenario", "expected")},
         **compare(case["inputs"], policy["rules"])}
        for case in data["cases"]
    ]
    if digest(data) != manifest_hash or digest(policy) != policy_hash:
        raise ValueError("Evaluation modified the frozen input or policy")
    def paired_metrics(group: list[dict]) -> dict:
        return {method: metrics(group, method) for method in ("general", "contextual")}

    report = {
        "source": data["source"], "total": len(records), "threshold": POLICY["threshold"],
        "comparisonPolicy": COMPARISON_POLICY, "manifestDigest": manifest_hash, "policyDigest": policy_hash,
        "overall": paired_metrics(records),
        "byScenario": {
            scenario: paired_metrics([row for row in records if row["scenario"] == scenario])
            for scenario, *_ in SCENARIOS
        },
        "byCoverage": {
            mode: paired_metrics([row for row in records if row["coverage"]["mode"] == mode])
            for mode in ("DECLARED_COMPLETE", "UNKNOWN")
        },
        "byContextualScope": {
            scope: paired_metrics([row for row in records if row["scope"] == scope])
            for scope in ("KIND", "CHANNEL", "GENERAL")
        },
        "records": records,
    }
    (output / "quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output)["overall"], indent=2))
