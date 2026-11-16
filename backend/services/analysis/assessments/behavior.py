"""Deterministic behavioral statistics. No storage, network or outcome labels."""

from collections import Counter
from datetime import datetime, timedelta
from math import ceil
from statistics import quantiles

from backend.services.analysis.profiles.observation import timestamp


def explanation(code: str, feature, limit, contribution: int) -> dict:
    templates = {
        "AMOUNT": f"Сумма {feature} превышает индивидуальный порог {limit}.",
        "FREQUENCY": f"Операций в контрольном интервале: {feature}; порог: {limit}.",
        "NEW_COUNTERPARTY": "Получатель отсутствует в предшествующей истории клиента.",
    }
    return {
        "code": code,
        "observed": feature,
        "baseline": limit,
        "contribution": contribution,
        "text": templates[code],
    }


def frequency_signal(history: list[dict], cutoff: datetime) -> dict:
    """An explicit profile-v2 alert policy, distinct from static rule weights.

    A burst exceeds three times the upper quartile of historical active-day
    counts, with a conservative minimum of four operations/hour. A triggered
    behavioral alert raises the total to the review threshold, without double
    counting existing rule contributions. The policy is recorded with results.
    """
    daily = list(Counter(timestamp(r["occurredAt"]).date() for r in history).values())
    q75 = (
        quantiles(daily, n=4, method="inclusive")[2]
        if len(daily) >= 2
        else (daily[0] if daily else 0)
    )
    limit = max(4, ceil(q75 * 3))
    observed = 1 + sum(
        timestamp(r["occurredAt"]) >= cutoff - timedelta(hours=1) for r in history
    )
    return {
        "code": "PROFILE_FREQUENCY",
        "observed": observed,
        "baseline": limit,
        "triggered": len(history) >= 5 and len(daily) >= 5 and observed > limit,
        "policyVersion": "profile-frequency-v1",
        "windowMinutes": 60,
        "text": f"За час {observed} операций при индивидуальном пороге {limit}; порог рассчитан по предшествующей активности.",
    }


def evaluate_quality(rows: list[dict]) -> dict:
    counts = dict(
        truePositive=0,
        falsePositive=0,
        trueNegative=0,
        falseNegative=0,
        pending=0,
        warmup=0,
    )
    for row in rows:
        expected, predicted = row["expected"], row.get("predicted")
        if expected is None:
            counts["warmup"] += 1
        elif predicted is None:
            counts["pending"] += 1
        else:
            key = (
                ("truePositive" if expected else "falsePositive")
                if predicted
                else ("falseNegative" if expected else "trueNegative")
            )
            counts[key] += 1
    tp, fp, tn, fn = (
        counts[k]
        for k in ("truePositive", "falsePositive", "trueNegative", "falseNegative")
    )
    return {
        **counts,
        "evaluated": tp + fp + tn + fn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "falsePositiveRate": round(fp / (fp + tn), 4) if fp + tn else None,
    }
