import os

import streamlit as st


def state(value):
    return (
        "Готов" if value is True else "Недоступен" if value is False else "Неизвестно"
    )


def render_kafka_lag(value):
    st.write("Связь с брокером Kafka: " + state(value["brokerReachable"]))
    st.subheader("Отставание потребителей Kafka")
    if value["measuredAt"]:
        st.caption(f"Измерено UTC {value['measuredAt']} · актуально до {value['maxAgeSeconds']} с")
    if value["status"] == "STALE":
        st.warning("Измерение устарело. Текущее отставание неизвестно.")
        return
    if value["status"] == "NO_DATA":
        st.info("Нет данных об отставании.")
        return
    for group in value["groups"]:
        total = group["lag"]
        st.write(f"**{group['group']}** · {f'Отставание {total}' if total is not None else 'Нет данных о сумме'}")
        with st.expander(f"Партиции · {group['group']}"):
            st.caption(f"Измерено {group['knownPartitions']} из {group['totalPartitions']} известных партиций.")
            if group["missingTopics"]:
                st.warning("Нет метаданных топиков: " + ", ".join(group["missingTopics"]))
            if group["partitions"]:
                st.dataframe([
                    {"Топик": p["topic"], "Партиция": p["partition"],
                     "Конец журнала": str(p["logEndOffset"]) if p["logEndOffset"] is not None else "Нет данных",
                     "Зафиксированное смещение": str(p["committedOffset"]) if p["committedOffset"] is not None else "Нет данных",
                     "Отставание": str(p["lag"]) if p["lag"] is not None else "Нет данных"}
                    for p in group["partitions"]
                ], hide_index=True)


def render_health(health):
    if health.get("allReady") and not health.get("degraded"):
        st.success("Система готова")
    else:
        st.warning("Система работает с ограничениями")
    st.caption("Измерено UTC: " + str(health.get("measuredAt", "Неизвестно")))
    st.write("Синхронные операции: " + state(health.get("synchronousReady")))
    st.write("Готовность событийных сервисов: " + state(health.get("asynchronousReady")))
    auth = health.get("auth", {})
    cache = health.get("cache", {})
    rows = [
        dict(
            Сервис="Gateway",
            Процесс=state(health.get("gateway", {}).get("processAlive")),
            API="Ответ получен",
            БД="Не владеет",
            Kafka="Не используется",
        ),
        dict(
            Сервис="Auth",
            Процесс=state(auth.get("processAlive")),
            API=state(auth.get("ready")),
            БД=auth.get("database") or "Неизвестно",
            Kafka="Не используется",
        ),
    ]
    for s in health["services"]:
        rows.append(
            dict(
                Сервис=s["service"],
                Процесс=state(s.get("processAlive")),
                API=state(s.get("ready")),
                БД=s.get("database") or "Неизвестно",
                Kafka=state(s.get("kafkaReady")),
            )
        )
    st.dataframe(rows, hide_index=True)
    st.write("Redis-сессии: " + state(auth.get("redisReady")))
    st.write(
        "Redis-кеш: "
        + (
            "Деградация — работа без кеша"
            if cache.get("mode") == "bypass"
            else state(cache.get("ready"))
        )
    )
    st.caption(
        "Frontend: эта страница отображена; независимая проверка самого интерфейса не выполнялась. Доступность Kafka не гарантирует отсутствие задержек обработки."
    )
    for service in health["services"]:
        with st.expander(
            service["service"] + " — очереди и последние успешные действия"
        ):
            a = service.get("activity", {})
            st.write(
                "Последняя публикация UTC:", a.get("lastPublishedAt") or "Нет сведений"
            )
            st.write("Ожидают публикации:", a.get("pendingOutbox", "Неизвестно"))
            st.write(
                "Ошибки событий по состоянию:", a.get("eventFailures", "Неизвестно")
            )
            if service["service"] == "analysis":
                st.write(
                    "Последний успешный пересчёт UTC:",
                    a.get("lastRefreshAt") or "Нет сведений",
                )
                st.write("Задания пересчёта:", a.get("refreshJobs", "Неизвестно"))
            st.caption("Срез владельца UTC: " + str(a.get("measuredAt", "Неизвестно")))
    if os.getenv("NETWORK_MODE") != "TLS":
        st.link_button("pgAdmin", "http://127.0.0.1:18180")
        st.link_button("Kafka UI", "http://127.0.0.1:18190")
