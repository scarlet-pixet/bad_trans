"""Descriptive comparison of disjoint time periods, never a risk score."""

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from statistics import median
from typing import TypedDict

from backend.services.analysis.profiles.observation import covered_seconds, timestamp

POLICY = "profile-dynamics-v1"

PeriodStats = TypedDict(
    "PeriodStats",
    {
        "from": str,
        "to": str,
        "days": int,
        "count": int,
        "totalAmount": str,
        "medianAmount": str | None,
        "activeDays": int,
        "completeObservation": bool,
        "operationsPerDay": str | None,
        "amountPerDay": str | None,
    },
)


def compare_periods(rows, cutoff, days, intervals):
    if days not in (7, 30, 90):
        raise ValueError("Unsupported dynamics window")
    cutoff = timestamp(cutoff)
    if days == 7:
        return dict(
            policyVersion=POLICY, status="WINDOW_TOO_SHORT", short=None, prior=None
        )
    with localcontext() as ctx:
        ctx.prec = 50

        def wire(v, places=8):
            return format(
                v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN), "f"
            )

        def period(start, end, duration) -> PeriodStats:
            selected = [r for r in rows if start <= timestamp(r["occurredAt"]) < end]
            values = [Decimal(str(r["amount"])) for r in selected]
            total = sum(values, Decimal(0))
            complete = covered_seconds(intervals, start, end) == duration * 86400
            return {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "days": duration,
                "count": len(values),
                "totalAmount": wire(total, 2),
                "medianAmount": format(median(values), "f") if values else None,
                "activeDays": len(
                    {timestamp(r["occurredAt"]).date() for r in selected}
                ),
                "completeObservation": complete,
                "operationsPerDay": wire(Decimal(len(values)) / duration)
                if complete
                else None,
                "amountPerDay": wire(total / duration) if complete else None,
            }

        boundary = cutoff - timedelta(days=7)
        short = period(boundary, cutoff, 7)
        prior = period(cutoff - timedelta(days=days), boundary, days - 7)
        complete = short["completeObservation"] and prior["completeObservation"]
        status = (
            "UNKNOWN_COVERAGE"
            if not complete
            else "ZERO_BASELINE"
            if not prior["count"] and short["count"]
            else "NO_ACTIVITY"
            if not prior["count"]
            else "AVAILABLE"
        )

        def rate(p, field):
            # Use unrounded values for comparisons, not serialized rates.
            value = (
                Decimal(p["count"]) if field == "count" else Decimal(p["totalAmount"])
            )
            return value / Decimal(p["days"])

        result: dict = dict(
            policyVersion=POLICY,
            status=status,
            short=short,
            prior=prior,
            overlappingPeriods=False,
            interpretation="DESCRIPTIVE_NOT_RISK",
            sufficientSamples=short["count"] >= 5
            and prior["count"] >= 20
            and prior["activeDays"] >= 7,
        )
        for field, prefix in [("count", "count"), ("amount", "amount")]:
            a, b = rate(short, field), rate(prior, field)
            result[prefix + "RateRatio"] = wire(a / b) if complete and b else None
            result[prefix + "RateDelta"] = wire(a - b) if complete else None
        result["medianAmountDelta"] = (
            wire(Decimal(short["medianAmount"]) - Decimal(prior["medianAmount"]))
            if short["medianAmount"] is not None and prior["medianAmount"] is not None
            else None
        )
        return result
