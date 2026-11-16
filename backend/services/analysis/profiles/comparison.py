"""Deterministic comparisons of saved baseline metrics without recalculation."""

from decimal import Decimal, InvalidOperation, localcontext

from backend.common.data import DomainError


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def wire(value):
    return format(value.normalize(), "f") if value is not None else None


def membership(first, second, key, fallback=None):
    a, b = first.get(key), second.get(key)
    complete = isinstance(a, list) and isinstance(b, list)
    if fallback:
        if not isinstance(a, list) and isinstance(first.get(fallback), list):
            a = [
                item["id"]
                for item in first.get(fallback, [])
                if isinstance(item, dict) and item.get("id")
            ]
        if not isinstance(b, list) and isinstance(second.get(fallback), list):
            b = [
                item["id"]
                for item in second.get(fallback, [])
                if isinstance(item, dict) and item.get("id")
            ]
    available = isinstance(a, list) and isinstance(b, list)
    a, b = set(a or []), set(b or [])
    if key == 'knownCounterparties':
        complete = complete and first.get('counterpartyCount') == len(a) and second.get('counterpartyCount') == len(b)
    added, removed = sorted(b - a), sorted(a - b)
    return dict(
        available=available,
        complete=complete,
        added=added[:200] if available else [],
        removed=removed[:200] if available else [],
        addedCount=len(added) if available else None,
        removedCount=len(removed) if available else None,
        outputTruncated=len(added) > 200 or len(removed) > 200,
    )


def compare_profiles(first, second):
    a, b = first["metadata"], second["metadata"]
    group = ("clientId", "currency", "direction", "windowDays")
    if any(a[key] != b[key] for key in group):
        raise DomainError("BAD_INPUT", "Версии относятся к разным группам профиля")
    x, y = first["metrics"], second["metrics"]
    method_keys = ("algorithmVersion", "metricsSchemaVersion", "historyPolicyVersion")
    comparable = all(
        x.get(key) is not None and x.get(key) == y.get(key) for key in method_keys
    )
    rows = []
    fields = [
        ("medianAmount", "Медиана суммы", a["currency"]),
        ("sampleSize", "Операции базовой истории", "operations"),
        ("operationsPerDay", "Частота", "operations/24h"),
        ("counterpartyCount", "Известные контрагенты", "counterparties"),
    ]
    with localcontext() as context:
        context.prec = 50
        for key, label, unit in fields:
            before, after = number(x.get(key)), number(y.get(key))
            reason = None
            if before is None or after is None:
                reason = "MISSING_VALUE"
            elif not comparable:
                reason = "METHOD_CHANGED_OR_UNKNOWN"
            elif key == "operationsPerDay" and (
                x.get("units", {}).get("activityRate") != unit
                or y.get("units", {}).get("activityRate") != unit
            ):
                reason = "UNIT_CHANGED_OR_UNKNOWN"
            delta = after - before if reason is None else None
            percent = (
                delta / abs(before) * 100 if delta is not None and before != 0 else None
            )
            rows.append(
                dict(
                    key=key,
                    label=label,
                    unit=unit,
                    before=wire(before),
                    after=wire(after),
                    delta=wire(delta),
                    percentChange=wire(percent),
                    reason=reason,
                )
            )
    rows.append(
        dict(
            key="qualityStatus",
            label="Достаточность истории",
            unit=None,
            before=x.get("quality", {}).get("status"),
            after=y.get("quality", {}).get("status"),
            delta=None,
            percentChange=None,
            reason=None if comparable else "METHOD_CHANGED_OR_UNKNOWN",
        )
    )
    # No servedAt: identical saved IDs produce an identical comparison.
    return dict(
        schemaVersion=1,
        group={key: a[key] for key in group},
        first=dict(metadata=a, method={key: x.get(key) for key in method_keys}),
        second=dict(metadata=b, method={key: y.get(key) for key in method_keys}),
        methodComparable=comparable,
        rows=rows,
        counterparties=membership(x, y, "knownCounterparties", "topCounterparties"),
        operations=membership(x, y, "history_ids"),
        operationPopulation="SAVED_INCLUDED_BASELINE",
        listLimit=200,
    )
