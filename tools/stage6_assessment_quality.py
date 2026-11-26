"""Reproduce the stage 6 experiment with frozen labels and source inputs."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from random import Random
from uuid import NAMESPACE_URL, uuid5

from backend.common.behavior_contract import digest
from backend.services.analysis.assessments.calculation import POLICY, assess, default_rules
from backend.services.analysis.assessments.quality import summarize
from backend.services.analysis.profiles.metrics import build_metrics

# Each scenario names outside context that justifies its review label. The scorer
# receives only the transactions and coverage, never the scenario or its label.
TUNING = (
    ("regular-bill", False, "routine"),
    ("ordinary-transfer", False, "routine"),
    ("planned-first-supplier", False, "new"),
    ("approved-overnight-renewal", False, "late"),
    ("inflated-invoice", True, "high"),
    ("unexpected-low-payment", True, "low"),
    ("rapid-repeat-payout", True, "burst"),
    ("unexpected-night-payee", True, "late-new"),
    ("persistent-activity-increase", True, "drift"),
    ("recipient-change-mismatch", True, "new"),
)
CONTROL = (
    ("routine-grocery-transfer", False, "routine"),
    ("expected-second-order", False, "routine"),
    ("first-utility-merchant", False, "new"),
    ("authorized-after-hours-settlement", False, "late"),
    ("verified-emergency-vendor-at-night", False, "late-new"),
    ("irregular-large-purchase", True, "high"),
    ("small-repeated-test-charge", True, "low"),
    ("unplanned-collection-burst", True, "burst"),
    ("unverified-new-beneficiary-at-night", True, "late-new"),
    ("unexpected-sustained-surge", True, "drift"),
    ("unverified-novel-recipient", True, "new"),
    ("unexpected-after-hours-payment", True, "late"),
)
REGIMES = ("full",) * 8 + ("sparse-confirmed",) * 2 + ("short-unknown", "empty-unknown")


def build_manifest(split, seed, definitions, anchor):
    random = Random(seed)
    records = []
    for scenario, label, pattern in definitions:
        for index, regime in enumerate(REGIMES):
            stem = f"bad-trans:quality6:{split}:{scenario}:{index}"
            client, known, novel, subject = (
                str(uuid5(NAMESPACE_URL, stem + ":" + name)) for name in ("client", "known", "new", "subject")
            )
            day = anchor + timedelta(days=len(records))
            cutoff = day.replace(hour=random.randrange(2, 5) if pattern.startswith("late") else random.randrange(11, 14))
            scale = random.randint(65, 250)
            rows = []
            for ago in range(1, 30):
                if regime == "full":
                    count = random.randrange(4, 9) * (6 if pattern == "drift" and ago <= 6 else 1)
                elif regime == "sparse-confirmed":
                    count = random.randrange(1, 3) if ago % 2 else 0
                elif regime == "short-unknown":
                    count = 3 if ago <= 2 else 0
                else:
                    count = 0
                for ordinal in range(count):
                    stamp = (day - timedelta(days=ago)).replace(
                        hour=random.randrange(8, 14), minute=random.randrange(60), second=ordinal
                    )
                    rows.append(dict(
                        id=str(uuid5(NAMESPACE_URL, f"{stem}:history:{ago}:{ordinal}")),
                        clientId=client, counterpartyId=known, currency="BYN", direction="OUT",
                        occurredAt=stamp.isoformat(), ingestedAt=(stamp+timedelta(seconds=5)).isoformat(),
                        amount=str(scale + random.randrange(-16, 17)),
                    ))
            if pattern == "burst" and regime in ("full", "sparse-confirmed"):
                for ordinal in range(24):
                    stamp = cutoff - timedelta(minutes=5, seconds=ordinal)
                    rows.append(dict(
                        id=str(uuid5(NAMESPACE_URL, f"{stem}:burst:{ordinal}")),
                        clientId=client, counterpartyId=known, currency="BYN", direction="OUT",
                        occurredAt=stamp.isoformat(), ingestedAt=stamp.isoformat(), amount=str(scale),
                    ))
            amount = scale * 12 if pattern == "high" else max(1, scale // 10) if pattern == "low" else scale
            tx = dict(id=subject, clientId=client, counterpartyId=novel if pattern.endswith("new") else known,
                      currency="BYN", direction="OUT", amount=str(amount), occurredAt=cutoff.isoformat())
            coverage = {"mode": "UNKNOWN"}
            if regime in ("full", "sparse-confirmed"):
                coverage = dict(mode="DECLARED_COMPLETE", revision="stage6-synthetic-generator",
                                intervals=[{"from": (cutoff-timedelta(days=90)).isoformat(), "to": cutoff.isoformat()}])
            records.append(dict(label=dict(scenario=scenario, expected=label, regime=regime),
                                inputs=dict(transaction=tx, history=rows, coverage=coverage)))
    return dict(split=split, seed=seed, cases=records)


def policies():
    configs = [dict(policy=POLICY, rules=default_rules())]
    for threshold in (30, 20, 10):
        version = f"behavior-demo-experiment-threshold-{threshold}-v1"
        policy = deepcopy(POLICY)
        policy.update(version=version, threshold=threshold)
        rules = default_rules()
        for rule in rules:
            parameters = dict(rule["parameters"], policyVersion=version)
            policy["rules"][rule["code"]]["parameters"] = parameters
            rule.update(parameters=parameters, id=str(uuid5(NAMESPACE_URL, f"{version}:{rule['code']}")))
        configs.append(dict(policy=policy, rules=rules))
    return configs


def evaluate(manifest, configs):
    records = {config["policy"]["version"]: [] for config in configs}
    for case in manifest["cases"]:
        tx, rows, coverage = (case["inputs"][k] for k in ("transaction", "history", "coverage"))
        cutoff = datetime.fromisoformat(tx["occurredAt"])
        assert all(datetime.fromisoformat(r["occurredAt"]) < cutoff for r in rows)
        profile = build_metrics(rows, cutoff, cutoff, tx["currency"], tx["direction"], 30,
                                coverage=coverage, client_id=tx["clientId"])
        profile["id"] = str(uuid5(NAMESPACE_URL, "stage6-profile:" + tx["id"]))
        used = set(profile.get("observedHistoryIds", profile["history_ids"]))
        history = [row for row in rows if row["id"] in used]
        for config in configs:
            policy = config["policy"]
            score = assess(tx, profile, history, config["rules"], policy=policy)
            reasons = sorted({reason for check in score["checks"]
                              if check["status"] == "INSUFFICIENT_DATA" for reason in check["dataReasons"]})
            records[policy["version"]].append(dict(
                case["label"], clientId=tx["clientId"], transactionId=tx["id"], historyCount=len(history),
                predicted=score["reviewRequired"], score=score["assessmentScore"],
                assessmentStatus=score["assessmentStatus"], unavailableReasons=reasons,
                checks=[{name: check[name] for name in ("code","status","contribution","dataReasons")}
                        for check in score["checks"]],
            ))
    return {version: dict(summary=summarize(rows), rows=rows,
                          byScenario={name: summarize([r for r in rows if r["scenario"] == name])
                                      for name, _ in Counter(r["scenario"] for r in rows).items()})
            for version, rows in records.items()}


def manifest_check(tuning, control):
    def identities(manifest):
        clients, scenarios, histories = set(), set(), set()
        for row in manifest["cases"]:
            tx, history = row["inputs"]["transaction"], row["inputs"]["history"]
            clients.add(tx["clientId"])
            scenarios.add(row["label"]["scenario"])
            if history:
                cutoff = datetime.fromisoformat(tx["occurredAt"])
                fingerprint = digest(sorted(
                    ((cutoff - datetime.fromisoformat(v["occurredAt"])).total_seconds(), v["amount"])
                    for v in history
                ))
                histories.add(fingerprint)
        return clients, scenarios, histories
    assert all(not (a & b) for a, b in zip(identities(tuning), identities(control), strict=True))


def save(output, filename, value):
    (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def improves(candidate, baseline):
    return (
        candidate["unavailable"] == baseline["unavailable"]
        and candidate["falsePositive"] <= baseline["falsePositive"]
        and candidate["truePositive"] > baseline["truePositive"]
    )


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    tuning = build_manifest("tuning", 61987, TUNING, datetime(2026, 4, 1, 12, tzinfo=UTC))
    control = build_manifest("control", 29673, CONTROL, datetime(2026, 11, 1, 12, tzinfo=UTC))
    manifest_check(tuning, control)
    configs = policies()
    hashes = {name: digest(value) for name, value in (("tuning", tuning), ("control", control), ("policies", configs))}
    save(output, "tuning-manifest.json", tuning)
    save(output, "control-manifest.json", control)
    save(output, "policies.json", configs)
    save(output, "frozen-manifests.json", hashes)

    tuning_results = evaluate(tuning, configs)
    baseline = POLICY["version"]
    eligible = [c for c in configs[1:]
                if improves(tuning_results[c["policy"]["version"]]["summary"], tuning_results[baseline]["summary"])]
    proposed = max(eligible, key=lambda c: (
        tuning_results[c["policy"]["version"]]["summary"]["truePositive"], c["policy"]["threshold"]
    ), default=configs[1])
    version = proposed["policy"]["version"]
    save(output, "tuning-decision.json", dict(
        selected=version, eligible=bool(eligible), holdoutDigest=hashes["control"],
        criterion="Higher recall with no additional false positives and unchanged availability",
        metrics={key: result["summary"] for key, result in tuning_results.items()},
    ))
    holdout = evaluate(control, [configs[0], proposed])
    accepted = bool(eligible) and improves(holdout[version]["summary"], holdout[baseline]["summary"])
    assert (digest(control), digest(tuning), digest(configs)) == (hashes["control"], hashes["tuning"], hashes["policies"])
    result = dict(baseline=baseline, proposed=version, accepted=accepted, holdout=holdout, tuning=tuning_results)
    save(output, "experiment.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = run(parser.parse_args().output)
    print({"accepted": result["accepted"], "proposed": result["proposed"],
           "holdout": {key: value["summary"] for key, value in result["holdout"].items()}})
