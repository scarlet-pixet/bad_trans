"""Small assessment presentation reused by the existing workspaces."""

import streamlit as st

from frontend.amount_basis import render_amount_basis

NAMES = {
    "AMOUNT_DEVIATION": "Сумма",
    "PROFILE_FREQUENCY": "Частота",
    "UNUSUAL_TIME": "Время",
    "COUNTERPARTY_NOVELTY": "Контрагент",
    "ACTIVITY_CHANGE": "Изменение активности",
}
STATUSES = {
    "TRIGGERED": "Отклонение",
    "NOT_TRIGGERED": "Не сработала",
    "INSUFFICIENT_DATA": "Недостаточно данных",
    "NOT_APPLICABLE": "Не применяется",
}
REASONS = {
    "INSUFFICIENT_SAMPLE": "Мало операций",
    "INSUFFICIENT_ACTIVE_DAYS": "Мало активных дней",
    "INSUFFICIENT_REFERENCE_HOURS": "Недостаточно сопоставимых часов",
    "INSUFFICIENT_CONFIRMED_DAYS": "Недостаточно подтверждённых дней наблюдения",
    "CURRENT_HOUR_NOT_COVERED": "Охват текущего часа не подтверждён",
    "SUBJECT_TIME_UNKNOWN": "Время операции неизвестно: источник передал только дату",
    "HISTORY_TIME_UNKNOWN": "В истории есть операции без точного времени",
    "MISSING_COUNTERPARTY": "Контрагент не указан",
    "INSUFFICIENT_KNOWN_COUNTERPARTIES": "Недостаточно известных контрагентов",
    "UNKNOWN_COVERAGE": "Охват сравниваемых периодов неизвестен",
    "ZERO_BASELINE": "Нет положительной базы сравнения",
    "INSUFFICIENT_SHORT_SAMPLE": "Мала короткая выборка",
    "INSUFFICIENT_PRIOR_SAMPLE": "Мала базовая выборка",
    "DISABLED_BY_RULE_VERSION": "Отключена версией правила",
    "NO_SUBJECT_TRANSACTION": "Нет оцениваемой операции",
    "NO_ACTIVITY": "Нет активности в наблюдавшихся периодах",
    "WINDOW_TOO_SHORT": "Короткое окно",
}


def shown_score(details, fallback: int | str = 0):
    if details.get("assessmentStatus") == "UNAVAILABLE":
        return "недоступна"
    return details.get("assessmentScore", details.get("score", fallback))


def shown_threshold(details):
    value = details.get("threshold")
    return "порог неизвестен" if value is None else str(value)


def render_case_score(case, details):
    saved = details or {}
    if not saved.get("resultId") or saved["resultId"] != case.get("latestResultId"):
        saved = {}
    score = shown_score(saved, fallback=case.get("latestScore", "—"))
    st.metric(
        "Последняя оценка случая / порог",
        f"{score if score is not None else '—'} / {shown_threshold(saved)}",
    )


def render_assessment(details):
    if details.get("amountBasis"):
        render_amount_basis(details["amountBasis"])
    status = details.get("assessmentStatus")
    if status == "UNAVAILABLE":
        st.warning(
            "Анализ завершён. Для числовой оценки недостаточно истории или покрытия."
        )
    elif status == "PARTIAL":
        st.warning("Анализ завершён с частичной оценкой. Недоступные проверки и причины указаны ниже.")
    elif status == "COMPLETE":
        st.caption(
            "Анализ завершён. Все применимые включённые проверки выполнены. "
            "Балл не является вероятностью мошенничества."
        )
    if not details.get("checks"):
        return
    rows = []
    for check in details["checks"]:
        o, b = check["observed"], check["baseline"]
        code = check["code"]
        if code == "AMOUNT_DEVIATION":
            observed = o.get("amount", "—")
            baseline = f"{b.get('lower', '—')} … {b.get('upper', '—')}"
        elif code == "PROFILE_FREQUENCY":
            observed = f"{o.get('count', '—')} операций/час"
            baseline = f"до {b.get('upper', '—')} операций/час"
        elif code == "UNUSUAL_TIME":
            observed = f"{o.get('hourUtc', '—')}:00 UTC"
            baseline = f"Доля часа: {b.get('hourProbability', '—')}; дня недели: {b.get('weekdayProbability', '—')}"
        elif code == "COUNTERPARTY_NOVELTY":
            observed = "Указан" if o.get("counterpartyId") else "Не указан"
            baseline = (
                "Есть в наблюдаемой истории; доля в базе: "+str(b.get('counterpartyShare','—'))
                if b.get("seenInObservedHistory")
                else "Нет в базовой истории"
                if b
                else "—"
            )
        else:
            observed = f"{o.get('count', '—')} за 7 дней"
            baseline = f"{b.get('count', '—')} за {b.get('days', '—')} дней"
        reasons = [REASONS.get(str(r), str(r)) for r in check["dataReasons"]]
        if check.get("suppressedBy"):
            suppressed = str(check["suppressedBy"])
            reasons.append("Вклад уже учтён: " + NAMES.get(suppressed, suppressed))
        rows.append(
            {
                "Проверка": NAMES[code],
                "Результат": STATUSES[check["status"]],
                "Наблюдение": observed,
                "База сравнения": baseline,
                "Вклад": check["contribution"],
                "Пояснение": "; ".join(reasons),
            }
        )
    with st.expander("Все проверки оценки", expanded=True):
        st.dataframe(rows, hide_index=True, use_container_width=True)
