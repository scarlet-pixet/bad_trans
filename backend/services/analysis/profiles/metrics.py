"""Versioned descriptive features. No rules, storage, network or outcome labels."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from statistics import median, quantiles

from backend.services.analysis.profiles.dynamics import compare_periods

from backend.services.analysis.profiles.observation import (
    calendar_activity,
    confirmed_intervals,
    covered_seconds,
    reference_hours,
    seconds,
    timestamp,
)

WINDOWS = (7, 30, 90)
ALGORITHM = "behavior-profile-v3.2"
METRICS_SCHEMA_VERSION = 4
LEGACY_ALGORITHM = "behavior-profile-v3.1"
LEGACY_METRICS_SCHEMA_VERSION = 3
READINESS_POLICY = "profile-readiness-demo-v1"


def fixed(value, places=8):
    if value is None:
        return None
    return format(
        Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN),
        "f",
    )


def ratio(numerator, denominator):
    return fixed(Decimal(numerator) / Decimal(denominator)) if denominator else None


def decimal_wire(value):
    return format(value, "f") if value is not None else None


def quartiles(values):
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], values[0]
    q1, _, q3 = quantiles(values, n=4, method="inclusive")
    return q1, q3


def counterparties(history, amounts):
    grouped = defaultdict(lambda: {"count": 0, "amount": Decimal(0)})
    for row, amount in zip(history, amounts):
        key = row.get("counterpartyId") or None
        if key is not None and not isinstance(key, str):
            raise ValueError("Counterparty ID must be a string or null")
        grouped[key]["count"] += 1
        grouped[key]["amount"] += amount
    n, total = len(history), sum(amounts, Decimal(0))

    def bucket(key, kind, count, amount):
        return dict(
            id=key,
            kind=kind,
            count=count,
            totalAmount=fixed(amount, 2),
            countShare=ratio(count, n),
            amountShare=ratio(amount, total),
        )

    known = sorted(
        (key for key in grouped if key is not None),
        key=lambda key: (-grouped[key]["count"], -grouped[key]["amount"], key),
    )
    items = [
        bucket(key, "KNOWN", grouped[key]["count"], grouped[key]["amount"])
        for key in sorted(known)
    ]
    unknown = grouped.get(None, {"count": 0, "amount": Decimal(0)})
    if unknown["count"]:
        items.append(bucket(None, "UNKNOWN", unknown["count"], unknown["amount"]))
    top = [
        bucket(key, "KNOWN", grouped[key]["count"], grouped[key]["amount"])
        for key in known[:5]
    ]
    if len(known) > 5:
        top.append(
            bucket(
                None,
                "OTHER_KNOWN",
                sum(grouped[k]["count"] for k in known[5:]),
                sum((grouped[k]["amount"] for k in known[5:]), Decimal(0)),
            )
        )
    if unknown["count"]:
        top.append(bucket(None, "UNKNOWN", unknown["count"], unknown["amount"]))
    known_operations = n - unknown["count"]
    hhi = ratio(sum(grouped[k]["count"] ** 2 for k in known), known_operations**2)
    return dict(
        knownCount=len(known),
        knownOperationCount=known_operations,
        unknownCount=unknown["count"],
        knownFraction=ratio(known_operations, n),
        knownHHI=hhi,
        hhiDenominator="KNOWN_OPERATIONS_ONLY",
        shareDenominator="ALL_INCLUDED_OPERATIONS",
        items=items,
        top=top,
    )


def eligibility(
    history, days, intervals, calendar, hours, cp, cutoff, event_mode=False
):
    """Data readiness only; these flags do not execute or score a check."""
    n = len(history)
    active = len({timestamp(row["occurredAt"]).date() for row in history})
    full_days = sum(
        b["coverageStatus"] == "COMPLETE" and Decimal(b["durationSeconds"]) == 86400
        for b in calendar
    )
    flags = {}

    def flag(name, conditions, required):
        reasons = [reason for passed, reason in conditions if not passed]
        flags[name] = dict(eligible=not reasons, reasons=reasons, required=required)

    flag(
        "amount",
        [(n >= 20, "SAMPLE_SIZE_LT_20"), (active >= 7, "ACTIVE_DAYS_LT_7")],
        dict(minSamples=20, minActiveDays=7),
    )
    flag(
        "time",
        [
            (n >= 100, "SAMPLE_SIZE_LT_100"),
            (active >= 14, "ACTIVE_DAYS_LT_14"),
            (full_days >= 28, "CONFIRMED_DAYS_LT_28"),
        ],
        dict(minSamples=100, minActiveDays=14, minConfirmedUtcDays=28),
    )
    complete_hours = sum(h["count"] is not None for h in hours)
    flag(
        "frequency",
        [
            (complete_hours >= 14, "REFERENCE_HOURS_LT_14"),
            (full_days >= 14, "CONFIRMED_DAYS_LT_14"),
            (
                covered_seconds(intervals, cutoff - timedelta(hours=1), cutoff) == 3600,
                "CURRENT_HOUR_NOT_COVERED",
            ),
        ],
        dict(referenceWindowMinutes=60, minReferenceHours=14, minConfirmedUtcDays=14),
    )
    flag(
        "counterparty",
        [
            (n >= 20, "SAMPLE_SIZE_LT_20"),
            (active >= 7, "ACTIVE_DAYS_LT_7"),
            (
                cp["knownFraction"] is not None
                and Decimal(cp["knownFraction"]) >= Decimal("0.90"),
                "KNOWN_COUNTERPARTY_FRACTION_LT_90_PERCENT",
            ),
        ],
        dict(minSamples=20, minActiveDays=7, minKnownFraction="0.90"),
    )
    short = [
        r
        for r in history
        if cutoff - timedelta(days=7) <= timestamp(r["occurredAt"]) < cutoff
    ]
    prior = [
        r
        for r in history
        if cutoff - timedelta(days=30)
        <= timestamp(r["occurredAt"])
        < cutoff - timedelta(days=7)
    ]
    prior_active = len({timestamp(r["occurredAt"]).date() for r in prior})
    flag(
        "dynamics",
        [
            (days >= 30, "WINDOW_SHORTER_THAN_30_DAYS"),
            (
                covered_seconds(intervals, cutoff - timedelta(days=30), cutoff)
                == 30 * 86400,
                "COMPARISON_PERIODS_NOT_COVERED",
            ),
            (
                len(short) >= 5 or (len(short) == 0 and not event_mode),
                "SHORT_SAMPLE_SIZE_LT_5",
            ),
            (len(prior) >= 20, "PRIOR_SAMPLE_SIZE_LT_20"),
            (prior_active >= 7, "PRIOR_ACTIVE_DAYS_LT_7"),
        ],
        dict(
            shortDays=7,
            priorDays=23,
            minShortSamples=5,
            minPriorSamples=20,
            minPriorActiveDays=7,
            zeroShortRequiresCompleteObservation=True,
        ),
    )
    return flags


def _build_metrics(
    rows,
    cutoff: datetime,
    watermark: datetime,
    currency: str,
    direction: str,
    days: int,
    excluded_id: str | None = None,
    *,
    coverage: dict | None = None,
    client_id: str | None = None,
) -> dict:
    if type(days) is not int or days not in WINDOWS:
        raise ValueError("Supported windows: 7, 30, 90 days")
    if (
        direction not in ("IN", "OUT")
        or not isinstance(currency, str)
        or len(currency) != 3
        or not currency.isascii()
        or not currency.isalpha()
        or not currency.isupper()
    ):
        raise ValueError("Invalid profile currency/direction")
    cutoff, watermark = timestamp(cutoff), timestamp(watermark)
    start = cutoff - timedelta(days=days)
    rows = list(rows)
    identifiers = [str(row["id"]) for row in rows]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate history transaction ID")
    history = sorted(
        (
            row
            for row in rows
            if str(row["id"]) != excluded_id
            and row["currency"] == currency
            and row["direction"] == direction
            and (client_id is None or row.get("clientId") == client_id)
            and start <= timestamp(row["occurredAt"]) < cutoff
            and timestamp(row["ingestedAt"]) < watermark
        ),
        key=lambda row: (timestamp(row["occurredAt"]), str(row["id"])),
    )
    with localcontext() as context:
        context.prec = 50
        try:
            values = [Decimal(str(row["amount"])) for row in history]
        except (InvalidOperation, ValueError):
            raise ValueError("Invalid historical amount") from None
        if any(
            not value.is_finite()
            or value <= 0
            or value > Decimal("9999999999999999.99")
            or value.quantize(Decimal("0.01")) != value
            for value in values
        ):
            raise ValueError(
                "Historical amounts must be finite, positive and have at most two decimal places"
            )
        n = len(history)
        total = sum(values, Decimal(0))
        med = median(values) if values else None
        mad = median([abs(a - med) for a in values]) if med is not None else None
        q1, q3 = quartiles(values)
        hourly = Counter(timestamp(row["occurredAt"]).hour for row in history)
        weekdays = Counter(timestamp(row["occurredAt"]).weekday() for row in history)
        daily = Counter(
            timestamp(row["occurredAt"]).date().isoformat() for row in history
        )
        intervals = confirmed_intervals(coverage, start, cutoff)
        covered = covered_seconds(intervals, start, cutoff)
        full = covered == days * 86400
        calendar = calendar_activity(daily, intervals, start, cutoff)
        reference = reference_hours(history, intervals, cutoff, days)
        cp = counterparties(history, values)
        deltas = [
            seconds(timestamp(b["occurredAt"]) - timestamp(a["occurredAt"]))
            for a, b in zip(history, history[1:])
        ]
        dq1, dq3 = quartiles(deltas)
        rates = ratio(n, days) if full else None
        known_start = timestamp(history[0]["occurredAt"]) if history else None
        known_end = timestamp(history[-1]["occurredAt"]) if history else None
        flags = eligibility(
            history,
            days,
            intervals,
            calendar,
            reference,
            cp,
            cutoff,
            event_mode=excluded_id is not None,
        )
        statistics = dict(
            sampleSize=n,
            observedCount=n,
            includedCount=n,
            excludedCount=0,
            totalAmount=fixed(total, 2),
            meanAmount=fixed(total / n) if n else None,
            medianAmount=decimal_wire(med),
            madAmount=decimal_wire(mad),
            q25=decimal_wire(q1),
            q75=decimal_wire(q3),
            iqrAmount=decimal_wire(q3 - q1) if q1 is not None and q3 is not None else None,
            activeDays=len(daily),
            operationsPerDay=rates,
            amountPerDay=ratio(total, days) if full else None,
            rateDenominatorDays=days if full else None,
            dailyCounts=dict(sorted(daily.items())),
            hourCountsUtc={str(h): hourly[h] for h in range(24)},
            weekdayCountsUtc={str(d): weekdays[d] for d in range(7)},
            hourShareUtc={str(h): ratio(hourly[h], n) for h in range(24)},
            weekdayShareUtc={str(d): ratio(weekdays[d], n) for d in range(7)},
            interarrivalMedianSeconds=decimal_wire(median(deltas)) if deltas else None,
            interarrivalQ25Seconds=decimal_wire(dq1),
            interarrivalQ75Seconds=decimal_wire(dq3),
            counterparties=cp,
            counterpartyCount=cp["knownCount"],
            knownCounterpartyFraction=cp["knownFraction"],
            knownCounterpartyHHI=cp["knownHHI"],
            topCounterparties=cp["top"],
            knownCounterparties=sorted(
                {r["counterpartyId"] for r in history if r.get("counterpartyId")}
            ),
        )
        return dict(
            **statistics,
            algorithmVersion=ALGORITHM,
            metricsSchemaVersion=METRICS_SCHEMA_VERSION,
            windowDays=days,
            windowStart=start.isoformat(),
            asOf=cutoff.isoformat(),
            ingestedBefore=watermark.isoformat(),
            dailyAverage=float(rates) if rates is not None else None,
            dailyCountsMeaning="REGISTERED_POSITIVE_COUNTS_ONLY; use calendarActivity for coverage-aware zeros",
            calendarActivity=calendar,
            referenceHours=reference,
            observation=dict(
                coverageMode=(coverage or {}).get("mode", "UNKNOWN"),
                revision=(coverage or {}).get("revision", "unknown-v1"),
                coveredSeconds=decimal_wire(covered),
                coverageFraction=ratio(covered, days * 86400),
                fullWindowCovered=full,
                knownHistoryStart=known_start.isoformat() if known_start else None,
                knownHistoryEnd=known_end.isoformat() if known_end else None,
                knownSpanSeconds=decimal_wire(seconds(known_end - known_start))
                if known_start is not None and known_end is not None
                else None,
                confirmedIntervals=[
                    {"from": a.isoformat(), "to": b.isoformat()} for a, b in intervals
                ],
            ),
            quality=dict(
                sampleSize=n,
                activeDays=len(daily),
                eligibilityPolicyVersion=READINESS_POLICY,
                eligibility=flags,
                status="EMPTY"
                if not n
                else "READY"
                if all(f["eligible"] for f in flags.values())
                else "PARTIAL",
                clientScopeVerified=client_id is not None,
                exclusionFraction=ratio(0, n),
                fieldCompleteness=dict(
                    amount=ratio(n, n),
                    occurredAt=ratio(n, n),
                    counterpartyId=cp["knownFraction"],
                ),
            ),
            units=dict(
                amount=currency,
                count="operations",
                interval="seconds",
                activityRate="operations/24h",
                amountRate=f"{currency}/24h",
                share="fraction",
                timeZone="UTC",
            ),
            observed=dict(statistics),
            baseline=dict(statistics),
            dynamics=compare_periods(history, cutoff, days, intervals),
            sampleFillRatio=ratio(min(n, 30), 30),
            confidence=round(min(1, n / 30), 3),
            confidenceDeprecated=True,
            confidenceMeaning="SAMPLE_FILL_ONLY_NOT_PROBABILITY",
            dataStatus="SUFFICIENT" if n >= 5 else "INSUFFICIENT",
            dataStatusDeprecated=True,
            history_ids=[str(row["id"]) for row in history],
        )



def build_metrics(rows,cutoff,watermark,currency,direction,days,excluded_id=None,*,coverage=None,client_id=None,feedback=None,legacy=False):
    """Retain registered observations while selecting a human-confirmed baseline."""
    rows=list(rows)
    observed=_build_metrics(rows,cutoff,watermark,currency,direction,days,excluded_id,coverage=coverage,client_id=client_id)
    if legacy:
        if feedback is not None:raise ValueError('Legacy profiles cannot reinterpret feedback')
        observed['algorithmVersion']=LEGACY_ALGORITHM;observed['metricsSchemaVersion']=LEGACY_METRICS_SCHEMA_VERSION
        return observed
    from backend.services.analysis.profiles.history_policy import LEGACY_VERSION,LEGACY_HASH,validate_reference
    ref=validate_reference(feedback['reference']) if feedback is not None else None
    candidates=feedback['decisions'] if feedback is not None else []
    observed_ids=set(observed['history_ids'])
    exclusions=[dict(d,reason='UNUSUAL_CONFIRMED') for d in candidates if d['transactionId'] in observed_ids and d['outcome']=='UNUSUAL_CONFIRMED' and ref is not None and d['knowledgeRevision']<=ref['revision'] and timestamp(d['decidedAt'])<=timestamp(ref['capturedAt'])]
    removed={d['transactionId'] for d in exclusions}
    result=_build_metrics([r for r in rows if str(r['id']) not in removed],cutoff,watermark,currency,direction,days,excluded_id,coverage=coverage,client_id=client_id) if removed else observed.copy()
    n,m=result['sampleSize'],observed['sampleSize']
    result.update(observed=observed['observed'],observedCount=m,includedCount=n,excludedCount=m-n,
                  observedHistoryIds=observed['history_ids'],exclusions=sorted(exclusions,key=lambda d:d['transactionId']),
                  feedbackReference=ref,feedbackRevision=ref['revision'] if ref else 0,
                  historyPolicyVersion=ref['policyVersion'] if ref else LEGACY_VERSION,
                  historyPolicyHash=ref['policyHash'] if ref else LEGACY_HASH,
                  observedDynamics=observed['dynamics'])
    result['baseline']=dict(result['baseline'],observedCount=m,includedCount=n,excludedCount=m-n)
    result['observation']=observed['observation']
    with localcontext() as decimal_context:
        decimal_context.prec=50
        fraction=ratio(m-n,m)
    result['quality']=dict(result['quality'],observedCount=m,includedCount=n,excludedCount=m-n,
                           exclusionFraction=fraction,status='BASELINE_EMPTY' if m and not n else result['quality']['status'])
    source_days={b['date']:b for b in observed['calendarActivity']}
    result['calendarActivity']=[dict(b,includedCount=b['observedCount'],observedCount=source_days[b['date']]['observedCount'],countPopulation='BASELINE') for b in result['calendarActivity']]
    result['referenceHours']=[dict(b,includedCount=b['observedCount'],observedCount=o['observedCount']) for b,o in zip(result['referenceHours'],observed['referenceHours'])]
    result['dailyCountsMeaning']='INCLUDED_BASELINE_POSITIVE_COUNTS_ONLY; observed.dailyCounts retains registered activity'
    return result
