"""Select an amount baseline from a frozen, feedback-filtered profile window."""

from decimal import Decimal, localcontext
from statistics import median

from backend.common.behavior_contract import digest
from backend.services.analysis.profiles.metrics import decimal_wire, quartiles
from backend.services.analysis.profiles.observation import timestamp

COMPARISON_POLICY = "amount-context-v1"


def select_amount_basis(tx, profile, rows, rules):
    """Use kind, then channel, then the existing general amount baseline."""
    amount_rule = next((rule for rule in rules if rule["code"] == "AMOUNT_DEVIATION"), None)
    if not amount_rule or not amount_rule["parameters"]["enabled"]:
        return None, None
    policy = amount_rule["parameters"]
    included = set(profile["history_ids"])
    eligible = [row for row in rows if row["id"] in included]
    candidates = []
    basis = {
        "policy": COMPARISON_POLICY,
        "scope": "GENERAL",
        "value": None,
        "subjectKind": tx.get("operationKind"),
        "subjectChannel": tx.get("operationChannel"),
        "windowDays": profile["windowDays"],
        "windowStart": profile["windowStart"],
        "asOf": profile["asOf"],
        "currency": tx["currency"],
        "direction": tx["direction"],
        "generalProfileVersionId": profile["id"],
        "sampleSize": profile["sampleSize"],
        "activeDays": profile["activeDays"],
        "generalCoverage": {
            "mode": profile["observation"]["coverageMode"],
            "fraction": profile["observation"]["coverageFraction"],
            "full": profile["observation"]["fullWindowCovered"],
        },
        "required": {"sampleSize": policy["min_samples"], "activeDays": policy["min_active_days"]},
        "candidates": candidates,
    }
    narrowed = None
    for scope, field in (("KIND", "operationKind"), ("CHANNEL", "operationChannel")):
        value = tx.get(field)
        if value in (None, "UNKNOWN"):
            candidates.append({"scope": scope, "value": None, "reason": "NO_SUBJECT_CLASSIFICATION"})
            continue
        cohort = [row for row in eligible if row.get(field) == value]
        active = len({timestamp(row["occurredAt"]).date() for row in cohort})
        candidate = {"scope": scope, "value": value, "sampleSize": len(cohort), "activeDays": active,
                     "knownSampleSize": sum(row.get(field) not in (None, "UNKNOWN") for row in eligible)}
        if len(cohort) < policy["min_samples"] or active < policy["min_active_days"]:
            candidates.append({**candidate, "reason": "INSUFFICIENT_CONTEXT_HISTORY"})
            continue
        with localcontext() as context:
            context.prec = 50
            amounts = [Decimal(str(row["amount"])) for row in cohort]
            center = median(amounts)
            mad = median(abs(value - center) for value in amounts)
            q25, q75 = quartiles(amounts)
        narrowed = {"sampleSize": len(cohort), "activeDays": active, "medianAmount": decimal_wire(center),
                    "madAmount": decimal_wire(mad), "q25": decimal_wire(q25), "q75": decimal_wire(q75)}
        basis.update(scope=scope, value=candidate["value"], sampleSize=len(cohort), activeDays=active,
                     knownSampleSize=candidate["knownSampleSize"])
        eligible = cohort
        break

    source_stats = narrowed or profile
    basis["statistics"] = {name: source_stats[name] for name in ("medianAmount", "madAmount", "q25", "q75")}
    basis["historyDigest"] = digest(sorted(row["id"] for row in eligible))
    basis["id"] = digest(basis)
    return basis, narrowed
