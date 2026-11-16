"""UTC observation intervals; absence of records is not proof of zero activity."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal


def timestamp(value: str | datetime) -> datetime:
    result = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if result.tzinfo is None:
        raise ValueError("Timestamp requires a timezone")
    return result.astimezone(UTC)


def seconds(delta: timedelta) -> Decimal:
    return (
        Decimal(delta.days * 86400 + delta.seconds)
        + Decimal(delta.microseconds) / 1_000_000
    )


def confirmed_intervals(
    metadata: dict | None, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    metadata = metadata or {"mode": "UNKNOWN", "intervals": []}
    if metadata.get("mode") not in ("UNKNOWN", "FIRST_SEEN_ONLY", "DECLARED_COMPLETE"):
        raise ValueError("Unknown observation coverage mode")
    intervals = []
    for item in metadata.get("intervals", []):
        a, b = timestamp(item["from"]), timestamp(item["to"])
        if a >= b:
            raise ValueError("Coverage interval requires from < to")
        a, b = max(a, start), min(b, end)
        if a < b and metadata["mode"] == "DECLARED_COMPLETE":
            intervals.append((a, b))
    merged = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def covered_seconds(intervals, start, end) -> Decimal:
    return sum(
        (
            seconds(min(b, end) - max(a, start))
            for a, b in intervals
            if max(a, start) < min(b, end)
        ),
        Decimal(0),
    )


def calendar_activity(daily_counts, intervals, start, end) -> list[dict]:
    bins = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        a, b = max(start, day), min(end, day + timedelta(days=1))
        covered = covered_seconds(intervals, a, b)
        complete = covered == seconds(b - a)
        count = daily_counts.get(day.date().isoformat(), 0)
        bins.append(
            dict(
                date=day.date().isoformat(),
                **{"from": a.isoformat(), "to": b.isoformat()},
                observedCount=count,
                count=count if complete else None,
                coveredSeconds=format(covered, "f"),
                durationSeconds=format(seconds(b - a), "f"),
                coverageStatus="COMPLETE"
                if complete
                else "PARTIAL"
                if covered
                else "UNKNOWN",
            )
        )
        day += timedelta(days=1)
    return bins


def reference_hours(history, intervals, cutoff, days) -> list[dict]:
    """Same UTC ending time, equal duration; zeros only for confirmed bins."""
    bins = []
    times = [timestamp(row["occurredAt"]) for row in history]
    for offset in range(1, min(days, 30)):
        end = cutoff - timedelta(days=offset)
        start = end - timedelta(hours=1)
        complete = covered_seconds(intervals, start, end) == 3600
        observed = sum(start <= at < end for at in times)
        bins.append(
            dict(
                **{"from": start.isoformat(), "to": end.isoformat()},
                observedCount=observed,
                count=observed if complete else None,
                coverageStatus="COMPLETE" if complete else "UNKNOWN",
            )
        )
    return bins
