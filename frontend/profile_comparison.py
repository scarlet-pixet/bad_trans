"""Manager comparison with independent cursors over both version selectors."""

import hashlib
import json

import streamlit as st

from frontend.profile_card import direction, origin


def choose_version(call, group, scope, label, initial_index):
    stack = st.session_state.get(scope, [None])
    value = call(
        "query($c:ID!,$d:Int!,$cur:String,$dir:String,$t:String){behaviorProfilePage(clientId:$c,windowDays:$d,history:true,currency:$cur,direction:$dir,limit:20,pageToken:$t)}",
        dict(
            c=group["clientId"],
            d=group["windowDays"],
            cur=group["currency"],
            dir=group["direction"],
            t=stack[-1],
        ),
    )
    if value is None:
        return None
    page = value.get("behaviorProfilePage")
    if page is None:
        st.error("Не удалось получить историю версий.")
        return None
    if page.get("currentPageToken"):
        stack = [*stack[:-1], page["currentPageToken"]]
        st.session_state[scope] = stack
    versions = {item["id"]: item for item in page["items"]}
    if not versions:
        st.info("Версий в этой группе пока нет.")
        return None
    selected = st.selectbox(
        label,
        list(versions),
        index=min(initial_index, len(versions) - 1),
        format_func=lambda key: (
            f"Версия {versions[key]['version']} · {versions[key]['asOf'][:19]} UTC · {origin(versions[key]['origin'])}"
        ),
        key=scope + ":selected:" + str(stack[-1]),
    )
    st.caption(f"Страница {len(stack)} · версий {page['totalCount']}")
    back, forward = st.columns(2)
    if back.button("Назад", disabled=len(stack) == 1, key=scope + ":back"):
        st.session_state[scope] = stack[:-1]
        st.rerun()
    if forward.button(
        "Далее", disabled=not page.get("nextPageToken"), key=scope + ":next"
    ):
        st.session_state[scope] = stack + [page["nextPageToken"]]
        st.rerun()
    return selected


def render_profile_comparison(call, group):
    filters = {
        key: group[key] for key in ("clientId", "currency", "direction", "windowDays")
    }
    scope = (
        "comparison:"
        + hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()
    )
    st.subheader("Сравнение версий профиля")
    st.caption(
        f"{group['currency']} · {direction(group['direction'])} · {group['windowDays']} дней · базовая история"
    )
    left, right = st.columns(2)
    with left:
        first = choose_version(call, filters, scope + ":first", "Было", 1)
    with right:
        second = choose_version(call, filters, scope + ":second", "Стало", 0)
    if not first or not second:
        return
    response = call(
        "query($a:ID!,$b:ID!){compareProfileVersions(firstId:$a,secondId:$b)}",
        dict(a=first, b=second),
    )
    if response is None:
        return
    result = response.get("compareProfileVersions")
    if result is None:
        st.error("Сравнение недоступно.")
        return
    if not result["methodComparable"]:
        st.warning(
            "Методика различается или неизвестна. Численные изменения не рассчитаны."
        )
    reasons = dict(
        MISSING_VALUE="Нет показателя в одном из снимков",
        METHOD_CHANGED_OR_UNKNOWN="Методика различается или неизвестна",
        UNIT_CHANGED_OR_UNKNOWN="Единица измерения различается или неизвестна",
    )
    units = dict(
        operations="операций",
        counterparties="контрагентов",
        **{"operations/24h": "операций / 24 ч"},
    )
    rows = []
    for row in result["rows"]:
        change = row["delta"]
        if row["key"] == "qualityStatus":
            change = f"{row['before'] or 'нет данных'} → {row['after'] or 'нет данных'}"
        percent = row["percentChange"]
        rows.append(
            {
                "Показатель": row["label"],
                "Единица": units.get(row["unit"], row["unit"]) or "—",
                "Было": row["before"] if row["before"] is not None else "нет данных",
                "Стало": row["after"] if row["after"] is not None else "нет данных",
                "Изменение": change if change is not None else "не определено",
                "Изменение, %": percent if percent is not None else "не определено",
                "Условия": reasons.get(row["reason"], ""),
            }
        )
    st.dataframe(rows, hide_index=True, use_container_width=True)
    st.caption(
        "Процент при нулевом исходном значении не определён. Статусы характеризуют сохранённую достаточность данных."
    )
    with st.expander("Срезы и методика"):
        st.json(dict(first=result["first"], second=result["second"]))
    for key, title in [
        ("counterparties", "Изменения известных контрагентов"),
        ("operations", "Изменения состава сохранённой базовой истории"),
    ]:
        data = result[key]
        with st.expander(title):
            if not data["available"]:
                st.info("Состав не сохранён в одной из версий.")
                continue
            if not data["complete"]:
                st.warning(
                    "Сравниваются только сохранённые элементы списка. Полнота списка неизвестна."
                )
            st.write(
                f"Добавлены: {data['addedCount']} · Выбыли: {data['removedCount']}"
            )
            if data["outputTruncated"]:
                st.caption("Показано до 200 идентификаторов в каждом направлении.")
            st.json(dict(added=data["added"], removed=data["removed"]))
            if key == "operations" and (data["added"] or data["removed"]):
                identifier = st.selectbox(
                    "Операция из изменившегося состава",
                    sorted(set(data["added"] + data["removed"])),
                    key=scope + ":operation",
                )
                if st.button("Открыть операцию", key=scope + ":open-operation"):
                    value = call(
                        "query($id:ID!){transaction(id:$id){id externalId amount currency direction occurredAt}}",
                        dict(id=identifier),
                    )
                    if value and value.get("transaction"):
                        st.json(value["transaction"])
                    elif value is not None:
                        st.info("Операция недоступна.")
