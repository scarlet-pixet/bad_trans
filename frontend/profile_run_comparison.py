"""Manager coverage comparison and drill-down to the exact saved versions."""

import streamlit as st

from frontend.profile_comparison import render_saved_pair
from frontend.analysis_commands import render_assessment_comparison


def render_run_comparison(call, runs, statuses):
    with st.form("profile_run_comparison"):
        first = st.text_input("ID первого запуска", value=runs[1]["id"] if len(runs) > 1 else "")
        second = st.text_input("ID второго запуска", value=runs[0]["id"] if runs else "")
        if st.form_submit_button("Сравнить запуски"):
            st.session_state.pop("profile_run_comparison_pair", None)
            if not first.strip() or not second.strip() or first.strip() == second.strip():
                st.warning("Укажите ID двух разных запусков.")
            else:
                st.session_state["profile_run_comparison_pair"] = (first.strip(), second.strip())
                st.session_state["profile_run_comparison_offset"] = 0
    pair = st.session_state.get("profile_run_comparison_pair")
    if pair is None:
        return
    offset = st.session_state["profile_run_comparison_offset"]
    response = call(
        "query($a:ID!,$b:ID!,$o:Int!){compareProfileRuns(firstId:$a,secondId:$b,offset:$o)}",
        dict(a=pair[0], b=pair[1], o=offset),
    )
    if not response:
        return
    result = response["compareProfileRuns"]
    for title, column, run in zip(("Первый запуск", "Второй запуск"), st.columns(2),
                                  (result["first"], result["second"])):
        with column:
            st.write(f"**{title}** · {statuses.get(run['status'], run['status'])}")
            st.code(run["id"])
            if run["legacy"]:
                st.warning("Состав групп этого запуска не сохранён. Покрытие неизвестно.")
                continue
            st.json(run["selection"], expanded=False)
            if not run["selectionComplete"]:
                st.warning("Состав отбора неполный. Показаны сохранённые группы.")
            if run["failedGroups"] or run["skippedGroups"] or run["unfinishedGroups"]:
                st.info(f"Ошибки — {run['failedGroups']}, пропуски — {run['skippedGroups']}, "
                        f"ожидают результата — {run['unfinishedGroups']}. Причины доступны в группах.")
            if run["error"]:
                st.error(run["error"])
    if not (result["first"]["legacy"] or result["second"]["legacy"]):
        st.table([
            {"Клиенты": label, "Первый": row["first"], "Второй": row["second"],
             "Общие": row["common"], "Только первый": row["onlyFirst"], "Только второй": row["onlySecond"]}
            for label, row in (("В отборе", result["selected"]),
                               ("С готовыми профилями", result["successful"]))
        ])
    if not result["clients"]:
        st.info("На этой странице нет сохранённых групп.")
    else:
        client_key = f"profile-run-client:{pair}:{offset}"
        clients = {client["clientId"]: client["groups"] for client in result["clients"]}
        client = st.selectbox("Клиент из сравнения", list(clients), key=client_key)
        groups = {f"{group['currency']} / {group['direction']}": group for group in clients[client]}
        group_label = st.selectbox("Группа клиента", list(groups), key=f"{client_key}:{client}")
        group = groups[group_label]
        for label, side in (("Первый", group["first"]), ("Второй", group["second"])):
            if side is None:
                st.write(f"{label} — группа отсутствует в сохранённом составе")
            else:
                status = statuses[side["status"]]
                st.write(f"{label} — {status}" + (f" · {side['error']}" if side["error"] else ""))
        if group["pairs"]:
            pairs = {pair["windowDays"]: pair for pair in group["pairs"]}
            window = st.selectbox("Окно профиля, дней", list(pairs), key=f"{client_key}:{client}:{group_label}")
            versions = pairs[window]
            render_saved_pair(call, versions["firstId"], versions["secondId"],
                              f"{client_key}:{client}:{group_label}:{window}")
        else:
            st.info("В этой группе нет пары профилей с одинаковым окном.")
    back, forward = st.columns(2)
    if back.button("Предыдущие клиенты", disabled=offset == 0):
        st.session_state["profile_run_comparison_offset"] = max(0, offset - 20)
        st.rerun()
    if forward.button("Следующие клиенты", disabled=result["nextOffset"] is None):
        st.session_state["profile_run_comparison_offset"] = result["nextOffset"]
        st.rerun()

    st.subheader("Сравнение оценок операций")
    st.info("При формировании профилей оценка операций не запускалась.")
    render_assessment_comparison(call, "profile_run_assessment_comparison")
