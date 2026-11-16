"""Workspaces select immutable IDs; mutable lists are never browser-cached."""

import hashlib
import json
from datetime import datetime

import streamlit as st

from frontend.profile_card import render_profiles, origin, direction
from frontend.assessment_view import render_assessment, shown_score, shown_threshold


def snapshot_profile(value):
    p = {**value["metrics"], **value["metadata"]}
    served = datetime.fromisoformat(value["servedAt"])
    at = datetime.fromisoformat(p["asOf"])
    p["freshness"] = dict(ageSeconds=max(0, (served - at).total_seconds()))
    return p


def client_workspace(call, client):
    left, middle, right = st.columns(3)
    currencies = sorted({a["currency"] for a in client.get("accounts", [])})
    curr = left.selectbox("Валюта", ["Все валюты"] + currencies)
    direct = middle.selectbox(
        "Направление",
        ["Все направления", "OUT", "IN"],
        format_func=lambda x: direction(x) if x in ("OUT", "IN") else x,
    )
    days = right.selectbox("Окно наблюдения, дней", [7, 30, 90], index=1)
    history = st.toggle("История версий", value=False)
    filters = dict(
        c=client["id"],
        d=days,
        h=history,
        cur=None if curr == "Все валюты" else curr,
        dir=None if direct == "Все направления" else direct,
    )
    scope = (
        "profiles:"
        + hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()
    )
    stack = st.session_state.get(scope, [None])
    token = stack[-1]
    value = call(
        "query($c:ID!,$d:Int!,$h:Boolean!,$cur:String,$dir:String,$t:String){behaviorProfilePage(clientId:$c,windowDays:$d,history:$h,currency:$cur,direction:$dir,limit:20,pageToken:$t)}",
        dict(filters, t=token),
    )
    if value is None:
        return
    page = value.get("behaviorProfilePage")
    if page is None:
        st.error("Не удалось получить страницу профилей.")
        return
    if page.get("currentPageToken"):
        stack = [*stack[:-1], page["currentPageToken"]]
        st.session_state[scope] = stack
    if st.button("Обновить список профилей", key=scope + ":refresh"):
        st.session_state.pop(scope, None)
        st.session_state.pop(scope + ":version", None)
        st.rerun()
    if not page["items"]:
        st.info(
            "Для выбранной группы профиля пока нет. Он появится после поступления операций; предупреждение не требуется."
        )
        return
    versions = {p["id"]: p for p in page["items"]}
    selected_id = st.selectbox(
        "Версия профиля",
        list(versions),
        format_func=lambda identifier: (
            f"{versions[identifier]['currency']} · {direction(versions[identifier]['direction'])} · версия {versions[identifier]['version']} · {versions[identifier]['asOf'][:19]} UTC · {origin(versions[identifier]['origin'])}"
        ),
        key=scope + ":version:" + str(stack[-1]),
    )
    selected = versions[selected_id]
    if st.toggle("Сравнить две версии", key=scope + ":compare"):
        from frontend.profile_comparison import render_profile_comparison
        render_profile_comparison(call, selected)
    # Page entries already identify immutable versions; direct lookup verifies
    # the selected ID and supplies canonical relational metadata.
    exact = call("query($id:ID!){profileVersion(id:$id)}", dict(id=selected["id"]))
    if exact and exact.get("profileVersion"):
        render_profiles([snapshot_profile(exact["profileVersion"])])
        from frontend.report_view import save_button
        save_button(call, dict(reportType="PROFILE", profileVersionId=selected["id"]), "Сохранить отчёт профиля", scope + ":report:" + selected["id"])
    st.caption(
        f"Страница {len(stack)} · версий в подборке: {page['totalCount']}. Состав подборки зафиксирован; для новых данных обновите список."
    )
    previous, next_ = st.columns(2)
    if previous.button(
        "Предыдущая страница", disabled=len(stack) == 1, key=scope + ":prev"
    ):
        st.session_state[scope] = stack[:-1]
        st.rerun()
    if next_.button(
        "Следующая страница",
        disabled=not page.get("nextPageToken"),
        key=scope + ":next",
    ):
        st.session_state[scope] = stack + [page["nextPageToken"]]
        st.rerun()


def operation_workspace(call, tx, key_prefix="operation"):
    if not tx.get("id"):
        st.warning("Реквизиты операции недоступны.")
        return
    scope = key_prefix + ":" + tx["id"]
    st.caption(
        f"Операция {tx.get('externalId', tx['id'])} · {tx.get('amount', '—')} {tx.get('currency', '')} · {direction(tx.get('direction'))} · {tx.get('occurredAt', '—')}"
    )
    stack = st.session_state.get(scope, [None])
    history = call(
        "query($id:ID!,$t:String){resultHistory(transactionId:$id,limit:20,pageToken:$t)}",
        dict(id=tx["id"], t=stack[-1]),
    )
    if history is None:
        return
    history = history.get("resultHistory")
    if not history or not history["items"]:
        st.info("Результатов пока нет. Ожидается автоматический анализ.")
        return
    listed_key = scope + ":listed:" + str(stack[-1])
    if listed_key not in st.session_state:
        st.session_state[listed_key] = history
    listed = st.session_state[listed_key]
    options = {r["resultId"]: r for r in listed["items"]}
    selected_id = st.selectbox(
        "Результат / запуск анализа",
        list(options),
        format_func=lambda identifier: (
            f"{options[identifier]['calculatedAt'][:19]} UTC · запуск {options[identifier]['runId'][:8]} · результат {identifier[:8]}"
        ),
        key=scope + ":result:" + str(stack[-1]),
    )
    selected = options[selected_id]
    st.caption(
        "Список для выбора зафиксирован. Обновление списка добавит новые расчёты; выбранный результат читается по точному ID с повторной проверкой доступа."
    )
    if st.button("Обновить список результатов", key=scope + ":refresh"):
        del st.session_state[listed_key]
        st.rerun()
    result = call(
        "query($id:ID!,$r:ID!){detectionContext(transactionId:$id,resultId:$r)}",
        dict(id=tx["id"], r=selected["resultId"]),
    )
    if result is None:
        return
    det = result.get("detectionContext")
    if not det:
        st.info("Выбранный результат недоступен.")
        return
    st.caption(
        "Показан выбранный исторический результат; новое рассмотрение не изменяет его профиль и оценку."
    )
    score = shown_score(det, fallback="—")
    st.metric("Оценка выбранного результата", score if score is not None else "—")
    st.caption(
        f"Порог рассмотрения: {shown_threshold(det)} · результат {selected['resultId']} · запуск {selected['runId']}"
    )
    if not det.get("checks"):
        st.info(
            "Исторический результат не содержит полного набора современных проверок. Его оценка сохранена без переинтерпретации."
        )
    render_assessment(det)
    profiles = det.get("profiles", [])
    for p in profiles:
        with st.expander(
            f"Использованный профиль · {p.get('windowDays', '—')} дней · версия {p.get('version', '—')}"
        ):
            # Prefer a fresh authorized lookup; no fallback to a cached profile
            # after assignment changes or a Case outage.
            if p.get("id"):
                value = call("query($id:ID!){profileVersion(id:$id)}", dict(id=p["id"]))
                if value and value.get("profileVersion"):
                    render_profiles([snapshot_profile(value["profileVersion"])])
    from frontend.report_view import save_button
    save_button(call, dict(reportType="OPERATION", transactionId=tx["id"], resultId=selected["resultId"]), "Сохранить отчёт оценки", scope + ":report:" + selected["resultId"])
    a, b = st.columns(2)
    if a.button(
        "Более новые результаты", disabled=len(stack) == 1, key=scope + ":prev"
    ):
        st.session_state[scope] = stack[:-1]
        st.rerun()
    if b.button(
        "Более ранние результаты",
        disabled=not listed.get("nextPageToken"),
        key=scope + ":next",
    ):
        st.session_state[scope] = stack + [listed["nextPageToken"]]
        st.rerun()
