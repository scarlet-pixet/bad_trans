"""Explain saved insufficiency reasons in the source quality view."""
REASONS = {
    "INSUFFICIENT_SAMPLE": "Мало операций в истории клиента",
    "INSUFFICIENT_ACTIVE_DAYS": "Мало дней с операциями",
    "INSUFFICIENT_CONFIRMED_DAYS": "Мало дней с подтверждённым покрытием",
    "INSUFFICIENT_REFERENCE_HOURS": "Мало часов для сравнения частоты",
    "CURRENT_HOUR_NOT_COVERED": "Покрытие последнего часа не подтверждено",
    "UNKNOWN_COVERAGE": "Полнота истории не подтверждена",
    "ZERO_BASELINE": "Нет базовой активности для сравнения",
    "INSUFFICIENT_KNOWN_COUNTERPARTIES": "Мало операций с известным контрагентом",
    "MISSING_COUNTERPARTY": "У операции не указан контрагент",
    "INSUFFICIENT_SHORT_SAMPLE": "Мало операций в коротком окне",
    "INSUFFICIENT_PRIOR_SAMPLE": "Мало операций или активных дней в базовом окне",
    "NO_RECORDED_REASON": "Причина не сохранена в результате",
}


def reason_rows(counts):
    return [{"Причина": REASONS.get(code, code), "Операций": count}
            for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
