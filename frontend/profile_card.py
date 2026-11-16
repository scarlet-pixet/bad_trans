"""Read-only metrics renderer shared by client and pinned operation profiles."""

import streamlit as st


def direction(value):
    return {"IN": "Входящие", "OUT": "Исходящие"}.get(value, "—")


def origin(value):
    return {
        "EVENT": "После операции",
        "SCHEDULED": "По расписанию",
        "MANUAL": "Ручной запрос",
    }.get(value, "Исторический расчёт")


def status_label(value):
    return {
        "NEW": "Новый",
        "ASSIGNED": "Назначен",
        "IN_PROGRESS": "В работе",
        "ESCALATED": "Эскалирован",
        "CLOSED": "Закрыт",
        "RUNNING": "Выполняется",
        "PAUSED": "Приостановлен",
        "COMPLETED": "Завершён",
        "PENDING": "Ожидает",
        "FAILED": "Ошибка",
        "RETRY": "Повтор",
        "BLOCKED": "Заблокирован",
    }.get(value, value or "—")


def render_profiles(profiles):
    for p in profiles:
        st.subheader(
            f"{p['currency']} · {direction(p['direction'])} · версия {p['version']}"
        )
        a, b = st.columns(2)
        c, d = st.columns(2)
        a.metric("Операций", p["sampleSize"])
        b.metric("Медиана", p["medianAmount"] or "—")
        c.metric("Разброс", p["madAmount"] or "—")
        d.metric("Контрагентов", len(p["knownCounterparties"]))
        quality_label = {
            "EMPTY": "нет операций",
            "BASELINE_EMPTY": "база сравнения пуста",
            "PARTIAL": "частичная готовность признаков",
            "READY": "истории достаточно",
        }.get(
            p.get("quality", {}).get("status"),
            "исторический критерий: " + p["dataStatus"],
        )
        if p.get("feedbackReference"):
            st.caption(
                f"Наблюдалось операций: {p['observedCount']}; в базе сравнения: {p['includedCount']}; исключено по решениям: {p['excludedCount']}."
            )
            st.caption(
                "Из базы исключаются только подтверждённые сотрудником нетипичные операции. Автоматический балл сам по себе ничего не исключает."
            )
            if p.get("exclusions"):
                with st.expander("Исключённые из базы операции"):
                    st.dataframe(
                        [
                            {
                                "Операция": e["transactionId"],
                                "Основание": "Подтверждённая нетипичность",
                                "Решение": e["decisionId"],
                                "Дата решения": e["decidedAt"],
                            }
                            for e in p["exclusions"]
                        ],
                        hide_index=True,
                    )
        st.caption(
            f"Срез {p['asOf'][:19]} UTC · {origin(p['origin'])} · {quality_label} · наполненность выборки {p['confidence']:.0%}"
        )
        if p.get("quality"):
            if p["quality"]["status"] != "READY":
                st.info(
                    "Профиль рассчитан. Достаточность истории различается для разных признаков."
                )
            total, mean, spread = st.columns(3)
            total.metric("Объём операций", p["totalAmount"], help=p["currency"])
            mean.metric("Средняя сумма", p["meanAmount"] or "—")
            spread.metric("Межквартильный размах", p["iqrAmount"] or "—")
            with st.expander("Качество истории"):
                names = {
                    "amount": "Суммы",
                    "time": "Время",
                    "frequency": "Частота",
                    "counterparty": "Контрагенты",
                    "dynamics": "Динамика",
                }
                reasons = {
                    "SAMPLE_SIZE_LT_20": "меньше 20 операций",
                    "SAMPLE_SIZE_LT_100": "меньше 100 операций",
                    "ACTIVE_DAYS_LT_7": "меньше 7 активных дней",
                    "ACTIVE_DAYS_LT_14": "меньше 14 активных дней",
                    "CONFIRMED_DAYS_LT_28": "нет 28 подтверждённых суток наблюдения",
                    "CONFIRMED_DAYS_LT_14": "нет 14 подтверждённых суток наблюдения",
                    "REFERENCE_HOURS_LT_14": "нет 14 сопоставимых наблюдавшихся часов",
                    "CURRENT_HOUR_NOT_COVERED": "охват контрольного часа не подтверждён",
                    "KNOWN_COUNTERPARTY_FRACTION_LT_90_PERCENT": "известны контрагенты менее чем у 90% операций",
                    "WINDOW_SHORTER_THAN_30_DAYS": "для динамики нужно окно не короче 30 дней",
                    "COMPARISON_PERIODS_NOT_COVERED": "охват сравниваемых периодов не подтверждён",
                    "SHORT_SAMPLE_SIZE_LT_5": "в коротком периоде меньше 5 операций",
                    "PRIOR_SAMPLE_SIZE_LT_20": "в базовом периоде меньше 20 операций",
                    "PRIOR_ACTIVE_DAYS_LT_7": "в базовом периоде меньше 7 активных дней",
                }
                st.dataframe(
                    [
                        {
                            "Признаки": names[name],
                            "Истории достаточно": "Да" if f["eligible"] else "Нет",
                            "Причины": ", ".join(
                                str(reasons.get(r, r)) for r in f["reasons"]
                            ),
                        }
                        for name, f in p["quality"]["eligibility"].items()
                    ],
                    hide_index=True,
                )
                st.caption(
                    "Критерии готовности статистических признаков; оценка операции показана в её карточке."
                )
                st.caption(
                    f"Операций без известного контрагента: {p['counterparties']['unknownCount']}"
                )
        elif p["dataStatus"] == "INSUFFICIENT":
            st.info("Исторический профиль по прежнему критерию: меньше пяти операций.")
        if p["dailyCounts"]:
            st.bar_chart(
                [{"День": k, "Операций": v} for k, v in p["dailyCounts"].items()],
                x="День",
                y="Операций",
            )
    for p in profiles:
        rate = (
            p.get("operationsPerDay")
            if p.get("observation", {}).get("fullWindowCovered")
            else None
        )
        rate_text = (
            rate
            if rate is not None
            else "нет данных — период наблюдения не подтверждён"
        )
        st.caption(
            f"Центральный диапазон сумм: {p['q25'] or '—'}–{p['q75'] or '—'} {p['currency']} · операций за сутки в среднем: {rate_text}"
        )
        if p.get("freshness"):
            st.caption(f"Возраст среза: {p['freshness']['ageSeconds'] / 60:.1f} мин.")
        with st.expander("Часы активности · UTC"):
            if p["hourCountsUtc"]:
                st.bar_chart(
                    [
                        {"Час": int(hour), "Операций": count}
                        for hour, count in p["hourCountsUtc"].items()
                    ],
                    x="Час",
                    y="Операций",
                )
        with st.expander("Интервалы и интенсивность"):
            median = p.get("interarrivalMedianSeconds")
            st.write(
                "Медианный интервал между операциями: "
                + (
                    str(median) + " с"
                    if median is not None
                    else "недостаточно операций"
                )
            )
            st.write(
                "Интервал Q25–Q75: "
                + str(p.get("interarrivalQ25Seconds") or "—")
                + " … "
                + str(p.get("interarrivalQ75Seconds") or "—")
                + " с"
            )
            st.caption(
                "Ставки в сутки доступны только при подтверждённом охвате источника. Интервалы описывают включённые операции."
            )
        with st.expander("Дни недели · UTC"):
            weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            weekday_rows = [
                {"День": weekdays[int(k)], "Доля": float(v or 0)}
                for k, v in p.get("weekdayShareUtc", {}).items()
            ]
            if weekday_rows:
                st.bar_chart(weekday_rows, x="День", y="Доля")
            else:
                st.info(
                    "Распределение по дням недели не сохранено в этой исторической версии."
                )
        with st.expander("Контрагенты и доли"):
            st.dataframe(
                [
                    {
                        "Контрагент": r.get("id") or "Неизвестный / прочие",
                        "Операций": r["count"],
                        "Доля операций": r.get("countShare") or "—",
                        "Объём": r["totalAmount"],
                        "Доля объёма": r.get("amountShare") or "—",
                    }
                    for r in p.get("counterparties", {}).get("items", [])
                ],
                hide_index=True,
            )
            st.caption(
                "Концентрация HHI известных контрагентов: "
                + str(
                    p.get("knownCounterpartyHHI")
                    if p.get("knownCounterpartyHHI") is not None
                    else "нет данных"
                )
            )
        with st.expander("Изменение поведения"):
            dyn = p.get("dynamics", {})
            labels = {
                "UNKNOWN_COVERAGE": "Охват сравниваемых периодов неизвестен",
                "ZERO_BASELINE": "Нет положительной базы для отношения",
                "NO_ACTIVITY": "Нет активности",
                "WINDOW_TOO_SHORT": "Нужно окно не короче 30 дней",
                "AVAILABLE": "Сравнение доступно",
                "SUFFICIENT": "Сравнение доступно",
                "INSUFFICIENT_DATA": "Истории недостаточно",
            }
            st.write(
                labels.get(
                    dyn.get("status"), "См. достаточность истории сравниваемых периодов"
                )
            )
            for title, part in [
                ("Последние 7 дней", "short"),
                ("Предшествующий период", "prior"),
            ]:
                item = dyn.get(part) or {}
                st.write(
                    f"{title}: {item.get('count', '—')} операций за {item.get('days', '—')} дней"
                )
            st.write(
                "Отношение интенсивностей: "
                + str(
                    dyn.get("countRateRatio")
                    if dyn.get("countRateRatio") is not None
                    else "недоступно"
                )
            )
            st.caption(
                "Периоды не пересекаются. Отсутствие подтверждённого охвата не означает нулевую активность."
            )
