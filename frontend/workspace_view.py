"""Workspaces select immutable IDs; mutable lists are never browser-cached."""

import hashlib
import json
from datetime import datetime

import streamlit as st

from frontend.profile_card import render_profiles, origin, direction
from frontend.operation_lifecycle import lifecycle_card
from frontend.assessment_view import render_assessment, shown_score, shown_threshold


def snapshot_profile(value):
    p = {**value["metrics"], **value["metadata"]}
    served = datetime.fromisoformat(value["servedAt"])
    at = datetime.fromisoformat(p["asOf"])
    p["freshness"] = dict(ageSeconds=max(0, (served - at).total_seconds()))
    return p


def client_workspace(call, client):
    identifier = st.text_input("ID версии профиля").strip()
    if identifier:
        response = call("query($id:ID!){profileVersion(id:$id)}", dict(id=identifier))
        if response is not None:
            version = response.get("profileVersion")
            if version and version["metadata"]["clientId"] == client["id"]:
                render_profile_version(call, version, "profile-exact:" + identifier)
                if st.toggle("Сравнить две версии", key="compare-exact:"+identifier):
                    from frontend.profile_comparison import render_profile_comparison
                    render_profile_comparison(call, version["metadata"])
            else:
                st.info("Версия у выбранного клиента не найдена.")
        return
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
    source = st.selectbox("Происхождение профиля", [None, "EVENT", "SCHEDULED", "MANUAL"],
                          format_func=lambda code: origin(code) if code else "Любое")
    filters = dict(
        c=client["id"],
        d=days,
        h=history,
        cur=None if curr == "Все валюты" else curr,
        dir=None if direct == "Все направления" else direct,
        source=source,
    )
    scope = (
        "profiles:"
        + hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()
    )
    if st.session_state.get("active_profile_filters") != scope:
        st.session_state.pop(scope, None)
        st.session_state["active_profile_filters"] = scope
    stack = st.session_state.get(scope, [None])
    token = stack[-1]
    value = call(
        "query($c:ID!,$d:Int!,$h:Boolean!,$cur:String,$dir:String,$source:String,$t:String)"
        "{behaviorProfilePage(clientId:$c,windowDays:$d,history:$h,currency:$cur,direction:$dir,origin:$source,limit:20,pageToken:$t)}",
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
        render_profile_version(call, exact["profileVersion"], scope + ":report:" + selected["id"], selected["id"])
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


def render_profile_version(call, version, scope, identifier=None):
    from frontend.report_view import save_button
    identifier = identifier or version["metadata"]["id"]
    st.caption(f"ID версии профиля {identifier}")
    render_profiles([snapshot_profile(version)])
    save_button(call, dict(reportType="PROFILE", profileVersionId=identifier),
                "Сохранить отчёт профиля", scope)


def operation_stamp(tx):
    timestamp = tx.get("occurredAt", "—")
    if tx.get("timePrecision") == "DATE":
        return timestamp[:10] + " · время неизвестно"
    return timestamp


def operation_workspace(call, tx, key_prefix="operation", *, read_call=None):
    if not tx.get("id"):
        st.warning("Реквизиты операции недоступны.")
        return
    scope = key_prefix + ":" + tx["id"]
    st.caption(
        f"Операция {tx.get('externalId', tx['id'])} · {tx.get('amount', '—')} {tx.get('currency', '')} · {direction(tx.get('direction'))} · {operation_stamp(tx)}"
    )
    st.caption(f"ID операции {tx['id']}")
    if read_call and st.toggle("Показать стороны перевода", key=scope + ":transfer"):
        response = read_call("query($id:ID!){operationTransfer(transactionId:$id)}", {"id": tx["id"]})
        details = response["operationTransfer"] if response else None
        if details:
            labels = {
                "SAME_OWNER": "Перевод между своими счетами",
                "DIFFERENT_OWNERS": "Перевод на счёт другого клиента",
                "UNKNOWN": "Владелец внешнего счёта не подтверждён",
            }
            st.write(labels[details["ownerRelation"]])
            st.caption(f"ID перевода {details['transferId']}")
            if details["ownerEvidence"] == "BANK_ACCOUNT_DIRECTORY":
                st.caption("Владельцы подтверждены по справочнику счетов банка.")
            for caption, side in (("Списание", details["debit"]), ("Зачисление", details["credit"])):
                ref = side.get("accountId") or side["externalAccountId"]
                origin = side.get("externalNamespace", "внутренний счёт")
                st.caption(f"{caption} · {origin} · …{ref[-4:]}")
        elif response:
            st.info("Сведения о сторонах для этой операции отсутствуют.")
        else:
            st.warning("Стороны перевода сейчас недоступны.")

    if read_call and st.toggle("Показать события операции", key=scope + ":lifecycle"):
        lifecycle_card(read_call, tx["id"])
    exact_id = st.text_input("ID результата", key=scope+":lookup").strip()
    if exact_id:
        render_operation_result(call, tx, exact_id, scope)
        return
    read = read_call or call
    stack = st.session_state.get(scope, [None])
    response = read(
        "query($id:ID!,$t:String){resultHistory(transactionId:$id,limit:20,pageToken:$t)}",
        dict(id=tx["id"], t=stack[-1]),
    )
    history = response.get("resultHistory") if response else None
    if not history or not history["items"]:
        processing = read(
            "query($id:ID!){operationProcessing(transactionId:$id){state}}",
            {"id": tx["id"]},
        )
        state = processing["operationProcessing"]["state"] if processing else "UNKNOWN"
        if state == "PENDING":
            st.info("Ожидается автоматический анализ.")
        elif state == "FAILED":
            st.error("Обработка завершилась ошибкой. Сообщите администратору ID операции.")
        else:
            st.warning("Состояние обработки пока не удалось подтвердить.")
        st.button("Проверить состояние", key=scope + ":status")
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
    render_operation_result(call, tx, selected["resultId"], scope, selected["runId"])
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


def render_operation_result(call, tx, result_id, scope, run_id=None):
    result = call(
        "query($id:ID!,$r:ID!){detectionContext(transactionId:$id,resultId:$r)}",
        dict(id=tx["id"], r=result_id),
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
        f"Порог рассмотрения: {shown_threshold(det)} · результат {result_id} · запуск {run_id or det.get('runId', '—')}"
    )
    if not det.get("checks"):
        st.info(
            "Исторический результат не содержит полного набора современных проверок. Его оценка сохранена без переинтерпретации."
        )
    render_assessment(det)
    primary_id = det.get("primaryProfileVersionId")
    for p in det.get("profiles", []):
        label = "Сохранённый профиль"
        if primary_id:
            basis_label = "Общий профиль" if (det.get("amountBasis") or {}).get("scope") in ("KIND", "CHANNEL") else "База оценки"
            label = basis_label if p.get("id") == primary_id else "Дополнительное окно расчёта"
        with st.expander(
            f"{label} · {p.get('windowDays', '—')} дней · версия {p.get('version', '—')}"
        ):
            # Prefer a fresh authorized lookup; no fallback to a cached profile
            # after assignment changes or a Case outage.
            if p.get("id"):
                value = call("query($id:ID!){profileVersion(id:$id)}", dict(id=p["id"]))
                if value and value.get("profileVersion"):
                    render_profiles([snapshot_profile(value["profileVersion"])])
    from frontend.report_view import save_button
    save_button(call, dict(reportType="OPERATION", transactionId=tx["id"], resultId=result_id),
                "Сохранить отчёт оценки", scope + ":report:" + result_id)
