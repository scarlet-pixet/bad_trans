"""Pure, versioned, explainable checks; labels and storage never enter here."""

import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from pathlib import Path
from statistics import median, quantiles
from uuid import NAMESPACE_URL, uuid5

from backend.common.behavior_contract import digest
from backend.common.data import DomainError, uuid
from backend.services.analysis.profiles.observation import covered_seconds, timestamp

POLICY = json.loads(
    (
        Path(__file__).resolve().parents[3] / "contracts/assessment_policy.json"
    ).read_text(encoding="utf-8")
)
ORDER = tuple(POLICY["order"])
LEGACY_CODES = ("AMOUNT", "FREQUENCY", "NEW_COUNTERPARTY")


def default_rules():
    return [
        dict(
            id=str(uuid5(NAMESPACE_URL, "badtrans:version:" + code + ":1")),
            code=code,
            parameters=deepcopy(POLICY["rules"][code]["parameters"]),
            weight=POLICY["rules"][code]["weight"],
        )
        for code in ORDER
    ]


def validate_rules(rules, *, policy=POLICY):
    order = tuple(policy["order"])
    if not isinstance(rules, list) or not 1 <= len(rules) <= len(order):
        raise DomainError("BAD_INPUT", "Нужен непустой набор современных версий правил")
    codes, ids = set(), set()
    normalized = []
    for r in rules:
        if not isinstance(r, dict):
            raise DomainError("BAD_INPUT", "Rule must be an object")
        code = r.get("code")
        cfg = r.get("parameters")
        weight = r.get("weight")
        identifier = uuid(r.get("id"))
        if code not in order or code in codes or not identifier or identifier in ids:
            raise DomainError(
                "BAD_INPUT", "Неизвестный/legacy код или повтор версии правила"
            )
        expected = policy["rules"][code]["parameters"]
        if (
            not isinstance(cfg, dict)
            or set(cfg) != set(expected)
            or cfg.get("policyVersion") != policy["version"]
            or type(cfg.get("enabled")) is not bool
        ):
            raise DomainError(
                "BAD_INPUT", "Некорректная схема параметров современной проверки"
            )
        for key, example in expected.items():
            if key in ("policyVersion", "enabled"):
                continue
            try:
                value = Decimal(str(cfg[key]))
                if (
                    isinstance(cfg[key], bool)
                    or not value.is_finite()
                    or value <= 0
                    or (type(example) is int and (type(cfg[key]) is not int))
                ):
                    raise ValueError()
                if (
                    key
                    in (
                        "constant_fraction",
                        "min_known_fraction",
                        "hour_probability",
                        "weekday_probability",
                        "lower_ratio",
                    )
                    and value > 1
                ):
                    raise ValueError()
                if key == "upper_ratio" and value <= 1:
                    raise ValueError()
            except (ValueError, InvalidOperation, TypeError):
                raise DomainError(
                    "BAD_INPUT", "Параметры должны быть конечными и допустимыми"
                ) from None
        group = policy["rules"][code]["group"]
        if type(weight) is not int or not 0 <= weight <= policy["budgets"][group]:
            raise DomainError("BAD_INPUT", "Вес выходит за бюджет семейства")
        codes.add(code)
        ids.add(identifier)
        normalized.append(
            dict(id=identifier, code=code, weight=weight, parameters=deepcopy(cfg))
        )
    return sorted(normalized, key=lambda r: order.index(r["code"]))


def fixed(v):
    return format(Decimal(v).quantize(Decimal("0.00000001")), "f")


def robust_range(values, cfg, amount=False):
    m = median(values)
    mad = median([abs(v - m) for v in values])
    q1, _, q3 = (
        quantiles(values, n=4, method="inclusive") if len(values) > 1 else (m, m, m)
    )
    if mad:
        span = Decimal(cfg["mad_multiplier"]) * Decimal(cfg["mad_scale"]) * mad
        low, high, method = m - span, m + span, "SCALED_MAD"
    elif q3 > q1:
        span = Decimal(cfg["iqr_multiplier"]) * (q3 - q1)
        low, high, method = q1 - span, q3 + span, "IQR"
    elif amount:
        span = m * Decimal(cfg["constant_fraction"])
        low, high, method = m - span, m + span, "CONSTANT_FRACTION"
    else:
        low, high, method = Decimal(0), m + cfg["constant_add"], "CONSTANT_COUNT_MARGIN"
    return max(Decimal(0), low), high, method


def run_check(code, tx, p, history, cfg):
    observed = {}
    base = {}
    deviation = {}
    reasons = []

    def insufficient(*why):
        return "INSUFFICIENT_DATA", observed, base, deviation, list(why)

    if not cfg["enabled"]:
        return "NOT_APPLICABLE", observed, base, deviation, ["DISABLED_BY_RULE_VERSION"]
    if tx is None and code != "ACTIVITY_CHANGE":
        return "NOT_APPLICABLE", observed, base, deviation, ["NO_SUBJECT_TRANSACTION"]
    tx = tx or {}
    n, active = p["sampleSize"], p["activeDays"]
    complete_days = sum(
        b["coverageStatus"] == "COMPLETE" and Decimal(b["durationSeconds"]) == 86400
        for b in p["calendarActivity"]
    )
    if code in ("AMOUNT_DEVIATION", "UNUSUAL_TIME", "COUNTERPARTY_NOVELTY"):
        if n < cfg["min_samples"]:
            reasons.append("INSUFFICIENT_SAMPLE")
        if active < cfg["min_active_days"]:
            reasons.append("INSUFFICIENT_ACTIVE_DAYS")
    if code in ("UNUSUAL_TIME", "PROFILE_FREQUENCY"):
        if tx.get("timePrecision") == "DATE":
            return insufficient("SUBJECT_TIME_UNKNOWN")
        if p["observation"].get("unknownTimeCount", 0):
            return insufficient("HISTORY_TIME_UNKNOWN")
    if code == "AMOUNT_DEVIATION":
        value = Decimal(str(tx["amount"]))
        observed = dict(amount=fixed(value), currency=tx["currency"])
        if reasons:
            return insufficient(*reasons)
        # Use the frozen profile statistics, never a newly fetched baseline.
        m = Decimal(p["medianAmount"])
        mad = Decimal(p["madAmount"])
        q1 = Decimal(p["q25"])
        q3 = Decimal(p["q75"])
        if mad:
            span = Decimal(cfg["mad_scale"]) * Decimal(cfg["mad_multiplier"]) * mad
            low, high, method = m - span, m + span, "SCALED_MAD"
        elif q3 > q1:
            span = Decimal(cfg["iqr_multiplier"]) * (q3 - q1)
            low, high, method = q1 - span, q3 + span, "IQR"
        else:
            span = m * Decimal(cfg["constant_fraction"])
            low, high, method = m - span, m + span, "CONSTANT_FRACTION"
        low = max(Decimal(0), low)
        base = dict(
            median=p["medianAmount"],
            mad=p["madAmount"],
            q25=p["q25"],
            q75=p["q75"],
            lower=fixed(low),
            upper=fixed(high),
        )
        triggered = value < low or value > high
        deviation = dict(
            method=method,
            outside="LOWER" if value < low else "UPPER" if value > high else None,
        )
    elif code == "PROFILE_FREQUENCY":
        cutoff = timestamp(tx["occurredAt"])
        observed = dict(
            count=1
            + sum(
                cutoff - timedelta(hours=1) <= timestamp(r["occurredAt"]) < cutoff
                for r in history
            ),
            windowMinutes=60,
        )
        bins = [
            Decimal(h["count"]) for h in p["referenceHours"] if h["count"] is not None
        ]
        intervals = [
            (timestamp(i["from"]), timestamp(i["to"]))
            for i in p["observation"].get("hourConfirmedIntervals", p["observation"]["confirmedIntervals"])
        ]
        if len(bins) < cfg["min_reference_hours"]:
            reasons.append("INSUFFICIENT_REFERENCE_HOURS")
        if p["observation"].get("hourConfirmedDays", complete_days) < cfg["min_confirmed_days"]:
            reasons.append("INSUFFICIENT_CONFIRMED_DAYS")
        if covered_seconds(intervals, cutoff - timedelta(hours=1), cutoff) != 3600:
            reasons.append("CURRENT_HOUR_NOT_COVERED")
        if reasons:
            return insufficient(*reasons)
        _, upper, method = robust_range(bins, cfg)
        base = dict(
            upper=fixed(upper), referenceCount=len(bins), unit="operations/60min"
        )
        triggered = observed["count"] > upper
        deviation = dict(method=method)
    elif code == "UNUSUAL_TIME":
        cutoff = timestamp(tx["occurredAt"])
        hour, day = cutoff.hour, cutoff.weekday()
        observed = dict(hourUtc=hour, weekdayUtc=day)
        if p["observation"].get("hourConfirmedDays", complete_days) < cfg["min_confirmed_days"]:
            reasons.append("INSUFFICIENT_CONFIRMED_DAYS")
        if reasons:
            return insufficient(*reasons)
        counts = p["hourCountsUtc"]
        alpha = Decimal(cfg["alpha"])
        smooth = (
            Decimal("0.25") * counts[str((hour - 1) % 24)]
            + Decimal("0.5") * counts[str(hour)]
            + Decimal("0.25") * counts[str((hour + 1) % 24)]
        )
        ph = (smooth + alpha) / (n + 24 * alpha)
        pd = (Decimal(p["weekdayCountsUtc"][str(day)]) + alpha) / (n + 7 * alpha)
        base = dict(
            hourProbability=fixed(ph),
            weekdayProbability=fixed(pd),
            hourLimit=cfg["hour_probability"],
            weekdayLimit=cfg["weekday_probability"],
            timeZone="UTC",
            smoothing=["0.25", "0.5", "0.25"],
        )
        triggered = ph < Decimal(cfg["hour_probability"]) or pd < Decimal(
            cfg["weekday_probability"]
        )
    elif code == "COUNTERPARTY_NOVELTY":
        cp = tx.get("counterpartyId")
        observed = dict(counterpartyId=cp)
        if not cp:
            reasons.append("MISSING_COUNTERPARTY")
        if p["knownCounterpartyFraction"] is None or Decimal(
            p["knownCounterpartyFraction"]
        ) < Decimal(cfg["min_known_fraction"]):
            reasons.append("INSUFFICIENT_KNOWN_COUNTERPARTIES")
        if reasons:
            return insufficient(*reasons)
        item = next((x for x in p["counterparties"]["items"] if x["id"] == cp), None)
        base = dict(
            knownCount=p["counterpartyCount"],
            knownFraction=p["knownCounterpartyFraction"],
            knownHHI=p["knownCounterpartyHHI"],
            counterpartyShare=item["countShare"] if item else "0.00000000",
            seenInObservedHistory=cp in p.get('observed',p).get("knownCounterparties",p["knownCounterparties"]),
        )
        triggered = cp not in p["knownCounterparties"]
    else:
        d = p["dynamics"]
        observed = deepcopy(d.get("short") or {})
        base = deepcopy(d.get("prior") or {})
        deviation = dict(countRateRatio=d.get("countRateRatio"))
        if d["status"] in ("WINDOW_TOO_SHORT", "NO_ACTIVITY"):
            return "NOT_APPLICABLE", observed, base, deviation, [d["status"]]
        if d["status"] in ("UNKNOWN_COVERAGE", "ZERO_BASELINE"):
            return insufficient(d["status"])
        if observed["count"] < cfg["min_short"] and not (
            observed["count"] == 0 and tx is None
        ):
            reasons.append("INSUFFICIENT_SHORT_SAMPLE")
        if (
            base["count"] < cfg["min_prior"]
            or base["activeDays"] < cfg["min_prior_active_days"]
        ):
            reasons.append("INSUFFICIENT_PRIOR_SAMPLE")
        if reasons:
            return insufficient(*reasons)
        ratio = (Decimal(observed["count"]) / observed["days"]) / (
            Decimal(base["count"]) / base["days"]
        )
        deviation["countRateRatio"] = fixed(ratio)
        triggered = ratio > Decimal(cfg["upper_ratio"]) or ratio < Decimal(
            cfg["lower_ratio"]
        )
    return "TRIGGERED" if triggered else "NOT_TRIGGERED", observed, base, deviation, []


def assess(tx, p, history, rules, *, policy=POLICY, amount_profile=None, amount_basis_id=None):
    rules = validate_rules(rules, policy=policy)
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        checks = []
        for rule in rules:
            code = rule["code"]
            is_amount = code == "AMOUNT_DEVIATION"
            selected = {**p, **amount_profile} if is_amount and amount_profile is not None else p
            status, observed, baseline, deviation, reasons = run_check(
                code, tx, selected, history, rule["parameters"]
            )
            if is_amount and amount_basis_id is not None:
                baseline["comparisonBasisId"] = amount_basis_id
            candidate = rule["weight"] if status == "TRIGGERED" else 0
            checks.append(
                dict(
                    code=code,
                    ruleVersionId=rule["id"],
                    policyVersion=policy["version"],
                    status=status,
                    observed=observed,
                    baseline=baseline,
                    deviation=deviation,
                    candidateContribution=candidate,
                    contribution=0,
                    dataReasons=reasons,
                    profileVersionIds=[p["id"]] if status != "NOT_APPLICABLE" else [],
                    suppressedBy=None,
                )
            )
        winners = {}
        for check in checks:
            if check["status"] != "TRIGGERED":
                continue
            group = policy["rules"][check["code"]]["group"]
            winner = winners.get(group)
            if (
                winner is None
                or check["candidateContribution"] > winner["candidateContribution"]
            ):
                winners[group] = check
        for check in checks:
            if check["status"] == "TRIGGERED":
                winner = winners[policy["rules"][check["code"]]["group"]]
                if winner is check:
                    check["contribution"] = check["candidateContribution"]
                else:
                    check["suppressedBy"] = winner["code"]
        executed = sum(c["status"] in ("TRIGGERED", "NOT_TRIGGERED") for c in checks)
        missing = any(c["status"] == "INSUFFICIENT_DATA" for c in checks)
        status = "UNAVAILABLE" if not executed else "PARTIAL" if missing else "COMPLETE"
        score = sum(c["contribution"] for c in checks) if executed else None
        assert score is None or 0 <= score <= 100
        return dict(
            assessmentSchemaVersion=1,
            assessmentAlgorithmVersion=policy["algorithmVersion"],
            assessmentPolicyVersion=policy["version"],
            assessmentPolicyHash=digest(dict(policy=policy, rules=rules)),
            assessmentPolicy=deepcopy(policy),
            assessmentScore=score,
            assessmentStatus=status,
            reviewRequired=score >= policy["threshold"] if score is not None else None,
            threshold=policy["threshold"],
            checks=checks,
            rules=rules,
        )
