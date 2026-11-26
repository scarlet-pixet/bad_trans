"""Cursor navigation and client selection on the owner-backed lists."""

import streamlit as st

PAGE_SIZE = 20


def cursor_page(call, query, field, filters, sort, scope, descending=True):
    options = dict(limit=PAGE_SIZE, sortBy=sort, sortDirection="DESC" if descending else "ASC")
    state_key = scope + ":cursor"
    signature = (filters, options)
    previous = st.session_state.get(state_key)
    stack = previous[1] if previous and previous[0] == signature else [None]
    st.session_state[state_key] = (signature, stack)
    if st.button("Обновить список", key=scope+":refresh"):
        st.session_state.pop(state_key, None)
        st.rerun()
    response = call(query, dict(**filters, page=dict(options, cursor=stack[-1])))
    if response is None:
        if stack[-1] and st.button("Вернуться на первую страницу", key=scope+":reset"):
            st.session_state.pop(state_key, None)
            st.rerun()
        return None
    result = response[field]
    current = result.get("currentCursor")
    if current:
        stack = [*stack[:-1], current]
        st.session_state[state_key] = (signature, stack)
    st.caption(f"Страница {len(stack)} · при открытии списка {result.get('totalCount', len(result['items']))}")
    back, forward = st.columns(2)
    if back.button("Назад", key=scope+":back", disabled=len(stack) == 1):
        st.session_state[state_key] = (signature, stack[:-1])
        st.rerun()
    next_cursor = result.get("nextCursor")
    if forward.button("Далее", key=scope+":next", disabled=not next_cursor):
        st.session_state[state_key] = (signature, [*stack, next_cursor])
        st.rerun()
    return result["items"]


def choose_client(call, scope, optional=False):
    with st.expander("Поиск клиента"):
        external = st.text_input("Внешний ID клиента", key=scope+":external").strip()
        segment = st.text_input("Сегмент клиента", key=scope+":segment").strip()
        activity = st.selectbox("Активность клиента", ["Все", "Активные", "Неактивные"],
                                key=scope+":activity")
        sort = st.selectbox("Сортировка клиентов", ["По имени", "По внешнему ID"],
                            key=scope+":sort")
    filters = {}
    if external:
        filters["externalId"] = external
    if segment:
        filters["segment"] = segment
    if activity != "Все":
        filters["isActive"] = activity == "Активные"
    rows = cursor_page(
        call,
        "query($f:ClientFilter,$page:PageInput){clients(filter:$f,page:$page)"
        "{totalCount currentCursor nextCursor items{id externalId displayName segment isActive accounts{id externalId currency status}}}}",
        "clients", dict(f=filters), "displayName" if sort == "По имени" else "externalId",
        scope, descending=False,
    )
    if rows is None:
        st.stop()
    if not rows and not optional:
        st.info("Клиенты по этому отбору не найдены.")
        st.stop()
    options = {row["id"]: row for row in rows}
    selected = st.selectbox(
        "Клиент", ([None] if optional else []) + list(options),
        format_func=lambda identifier: (
            f"{options[identifier]['displayName']} · {options[identifier].get('externalId', identifier)}"
            if identifier else "Все клиенты"
        ),
        key=scope+":selected:"+",".join(options),
    )
    return options.get(selected)
