"""Reproduce five controlled checks against the unchanged coursework policy."""

import argparse
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5

from backend.common.behavior_contract import digest
from backend.services.analysis.assessments.calculation import (
    POLICY,
    assess,
    default_rules,
)
from backend.services.analysis.profiles.metrics import build_metrics

DAY = datetime(2026, 11, 16, tzinfo=UTC)
NOON = DAY + timedelta(hours=12)
NIGHT = DAY + timedelta(hours=2)
WINDOW = timedelta(days=30)
CODES = ("AMOUNT_DEVIATION", "PROFILE_FREQUENCY", "UNUSUAL_TIME",
         "COUNTERPARTY_NOVELTY", "ACTIVITY_CHANGE")
PASS = ("NOT_TRIGGERED", 0, ())
GAP = ("INSUFFICIENT_DATA", 0, ("UNKNOWN_COVERAGE",))


def identifier(name):
    return str(uuid5(NAMESPACE_URL, "bad-trans-profile-control-v1:" + name))


def interval(start, end):
    return {"from": start.isoformat(), "to": end.isoformat()}


class ControlInput(TypedDict):
    transaction: dict[str, str]
    history: list[dict[str, str]]
    coverage: dict


def fixture(cutoff, amount="100.00", burst=False, coverage_gap=None) -> ControlInput:
    def operation(name, stamp, value):
        return {"id": identifier(name), "clientId": identifier("client"), "currency": "BYN",
                    "direction": "OUT", "counterpartyId": identifier("known"), "amount": value,
                    "occurredAt": stamp.isoformat(), "ingestedAt": (stamp + timedelta(seconds=5)).isoformat()}

    regular = [
        operation(f"{day}:{ordinal}", NOON - timedelta(days=day, minutes=30-ordinal), "100.00")
        for day in range(1, 30) for ordinal in range(4)
    ]
    recent = [
        operation(f"burst:{ordinal}", cutoff - timedelta(minutes=15-ordinal), "100.00")
        for ordinal in range(8)
    ] if burst else []
    start = cutoff - WINDOW
    bounds = {
        None: [(start, cutoff)],
        "days": [(start, DAY-timedelta(days=21)), (DAY-timedelta(days=19), cutoff)],
        "hour": [(start, cutoff-timedelta(hours=1))],
    }
    coverage = {"mode": "DECLARED_COMPLETE", "revision": "synthetic-control-v1",
                    "intervals": [interval(a, b) for a, b in bounds[coverage_gap]]}
    return {"transaction": operation(f"subject:{cutoff.hour}:{amount}:{burst}", cutoff, amount),
                "history": regular + recent, "coverage": coverage}


def scenarios():
    # Values are obtained from the construction of the fixture and the published
    # policy constants. This oracle never calls the profile builder or scorer.
    return (
        ("amount", fixture(NOON, "900.00"), 116, 29, 29, 3600, 40,
         (("TRIGGERED", 40, ()), PASS, PASS, PASS, PASS)),
        ("time-covered", fixture(NIGHT), 116, 29, 29, 3600, 10,
         (PASS, PASS, ("TRIGGERED", 10, ()), PASS, PASS)),
        ("time-days-gap", fixture(NIGHT, coverage_gap="days"), 116, 27, 27, 3600, 0,
         (PASS, PASS, ("INSUFFICIENT_DATA", 0, ("INSUFFICIENT_CONFIRMED_DAYS",)), PASS, GAP)),
        ("frequency-covered", fixture(NOON, burst=True), 124, 29, 29, 3600, 40,
         (PASS, ("TRIGGERED", 40, ()), PASS, PASS, PASS)),
        ("frequency-hour-gap", fixture(NOON, burst=True, coverage_gap="hour"), 124, 29, 29, 0, 0,
         (PASS, ("INSUFFICIENT_DATA", 0, ("CURRENT_HOUR_NOT_COVERED",)), PASS, PASS, GAP)),
    )


def evaluate():
    assert POLICY["version"] == "behavior-demo-v1" and POLICY["threshold"] == 40
    rules = default_rules()
    cases = scenarios()
    for left, right in ((cases[1], cases[2]), (cases[3], cases[4])):
        assert left[1]["transaction"] == right[1]["transaction"]
        assert left[1]["history"] == right[1]["history"]
        assert left[1]["coverage"] != right[1]["coverage"]
    records = []
    for label, inputs, count, days, hours, last_hour, score, expectations in cases:
        tx, rows, coverage = inputs["transaction"], inputs["history"], inputs["coverage"]
        cutoff = datetime.fromisoformat(tx["occurredAt"])
        profile = build_metrics(rows, cutoff, cutoff, "BYN", "OUT", 30, tx["id"],
                                coverage=coverage, client_id=tx["clientId"])
        profile["id"] = identifier("profile:" + tx["id"] + ":" + digest(coverage))
        assert set(profile["history_ids"]) == {row["id"] for row in rows}
        observed_days = sum(day["coverageStatus"] == "COMPLETE" and Decimal(day["durationSeconds"]) == 86400
                            for day in profile["calendarActivity"])
        observed_hours = sum(item["count"] is not None for item in profile["referenceHours"])
        hour_start = cutoff - timedelta(hours=1)
        observed_last_hour = sum(
            max(0, (min(cutoff, datetime.fromisoformat(i["to"]))
                    - max(hour_start, datetime.fromisoformat(i["from"]))).total_seconds())
            for i in profile["observation"]["confirmedIntervals"]
        )
        assert (profile["sampleSize"], observed_days, observed_hours, observed_last_hour) == (
            count, days, hours, last_hour)
        result = assess(tx, profile, rows, rules)
        checks = []
        assert [c["code"] for c in result["checks"]] == list(CODES)
        for (status, contribution, reasons), actual in zip(expectations, result["checks"], strict=True):
            expected = {"status": status, "contribution": contribution, "dataReasons": list(reasons)}
            assert all(actual[key] == value for key, value in expected.items()), (label, expected, actual)
            checks.append({"code": actual["code"], "expected": expected, "actual": actual})
        expected_status = "PARTIAL" if any(check[0] == "INSUFFICIENT_DATA" for check in expectations) else "COMPLETE"
        assert (result["assessmentScore"], result["threshold"], result["assessmentStatus"], result["reviewRequired"]) == (
            score, 40, expected_status, score >= 40)
        by_code = {check["code"]: check for check in result["checks"]}
        assert (Decimal(by_code["AMOUNT_DEVIATION"]["baseline"]["lower"]),
                Decimal(by_code["AMOUNT_DEVIATION"]["baseline"]["upper"])) == (Decimal(75), Decimal(125))
        if label == "time-covered":
            actual = Decimal(by_code["UNUSUAL_TIME"]["baseline"]["hourProbability"])
            assert actual == (Decimal(1) / 140).quantize(Decimal("0.00000001"))
        if label.startswith("frequency-"):
            assert by_code["PROFILE_FREQUENCY"]["observed"]["count"] == 9
            if last_hour:
                assert Decimal(by_code["PROFILE_FREQUENCY"]["baseline"]["upper"]) == 6
        records.append({"scenario": label, "inputs": inputs, "window": interval(cutoff-WINDOW, cutoff),
                            "priorOperations": count, "confirmedFullDays": days, "confirmedReferenceHours": hours,
                            "confirmedSecondsInLastHour": int(observed_last_hour), "checks": checks, "expectedScore": score,
                            "actualScore": result["assessmentScore"], "status": result["assessmentStatus"],
                            "threshold": result["threshold"], "reviewRequired": result["reviewRequired"],
                            "policyHash": result["assessmentPolicyHash"]})
    return {"policy": deepcopy(POLICY), "rules": rules, "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in result["records"]:
        print(item["scenario"], item["status"], item["actualScore"], item["reviewRequired"])
