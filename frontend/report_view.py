"""Inspect saved server snapshots without recalculating their contents."""

from datetime import date
import hashlib
import json
from uuid import uuid4

import streamlit as st

from frontend.assessment_view import render_assessment, shown_score
from frontend.profile_card import render_profiles, direction as direction_label, status_label


OUTCOMES = {"UNUSUAL_CONFIRMED": "Подтверждённая нетипичность", "EXPLAINED": "Операция объяснена"}
NAMES = {"PROFILE": "Профиль клиента", "OPERATION": "Оценка операции", "PERIOD": "Аналитика за период"}


def save_button(call, selectors, label, key):
    if st.button(label, key=key):
        scope = "report-request:" + hashlib.sha256(json.dumps(selectors, sort_keys=True).encode()).hexdigest()
        command_key = st.session_state.setdefault(scope, str(uuid4()))
        value = call("mutation($input:JSON!){createSnapshotReport(input:$input)}", dict(input=dict(selectors, idempotencyKey=command_key)))
        if value and value.get("createSnapshotReport"):
            report = value["createSnapshotReport"]
            st.session_state.saved_report_id = report["id"]
            st.success("Снимок сохранён. Откройте раздел «Отчёты»: " + report["id"])
            st.session_state.pop(scope, None)


def render_saved(report):
    from frontend.workspace_view import snapshot_profile
    snapshot = report["snapshot"]
    st.subheader(NAMES[snapshot["reportType"]])
    st.caption(f"Отчёт {report['id']} · автор {snapshot['author']['login']} · сохранён {report['createdAt']}")
    st.caption(f"Предметный срез {snapshot['cutoff']} · сборка {snapshot['assembledAt']} · SHA256 {report['snapshotHash']}")
    st.info("Сохранённый снимок. Новые операции и решения не меняют его содержимое. Срезы владельцев собраны раздельно, общей атомарной точки нет.")
    with st.expander("Фильтры и происхождение данных"):
        st.json(snapshot["filters"])
        st.dataframe([{"Владелец":c["owner"],"Время чтения UTC":c["capturedAt"],"Предметный срез UTC":c["cutoff"],"SHA256 ссылок":c["referencesHash"]} for c in snapshot["sourceCuts"]], hide_index=True)
    data = snapshot["data"]
    kind = snapshot["reportType"]
    if kind == "PROFILE":
        st.write(data["client"].get("displayName", "Клиент"))
        st.caption("Свежесть и готовность профиля зафиксированы на момент сборки отчёта.")
        render_profiles([snapshot_profile(data["profile"])])
    elif kind == "OPERATION":
        tx = data["transaction"]
        st.write(f"{tx.get('clientName', 'Клиент')} · {tx['amount']} {tx['currency']} · {direction_label(tx['direction'])}")
        st.caption(f"Операция {tx['id']} · время {tx.get('occurred_at', '—')}")
        assessment = data["assessment"]
        st.metric("Оценка выбранного результата", shown_score(assessment, "—"))
        st.caption(f"Результат {assessment['resultId']} · запуск {assessment['runId']} · порог {assessment.get('threshold', '—')}")
        render_assessment(assessment)
        if not assessment.get("checks"):
            st.info("Исторический результат без современных проверок. Сохранена исходная оценка.")
            for reason in assessment.get("explanations", []):
                st.write(f"+{reason.get('contribution', '—')} · {reason.get('text', 'Сохранённая причина')}")
        for p in data["profiles"]:
            with st.expander(f"Профиль контекста · {p['metadata']['windowDays']} дней"):
                st.caption("Использован оценкой" if p.get("reportUsage") == "ASSESSMENT_BASELINE" else "Дополнительное окно сохранённого контекста")
                render_profiles([snapshot_profile(p)])
        review = data["review"]
        st.subheader("Рассмотрение на срезе сборки")
        st.caption("Действия до среза отчёта; состояние случая на момент чтения Case. Это не состояние в момент исторического расчёта.")
        if review["case"]:
            st.write("Статус: " + status_label(review["case"]["status"]))
            if review["comments"]:
                st.dataframe([{"Автор":r["author_id"],"Комментарий":r["text"],"Дата UTC":r["created_at"]} for r in review["comments"]], hide_index=True)
            else:
                st.info("Комментариев на срезе нет.")
            if review["decisions"]:
                st.dataframe([{"Автор":r["author_id"],"Решение":OUTCOMES.get(r["outcome"],r["outcome"]),"Основание":r["reason"],"Дата UTC":r["decided_at"],"Цикл":r["review_cycle"]} for r in review["decisions"]], hide_index=True)
            else:
                st.info("Решений на срезе нет.")
        else:
            st.info("Случай рассмотрения отсутствует.")
    else:
        tx, analysis, cases = data["transactions"], data["analysis"], data["cases"]
        a, b, c = st.columns(3)
        a.metric("Операций в периоде", tx["transactionCount"])
        b.metric("Уникальных клиентов", tx["uniqueClients"])
        c.metric("Операций с результатом", analysis["resultCount"])
        st.caption("Период операций: occurred_at, начало включено, конец исключён. Повторные расчёты одной операции не увеличивают количество.")
        st.dataframe([{"Валюта":r["currency"],"Направление":direction_label(r["direction"]),"Операций":r["transactionCount"],"Сумма":r["amount"],"Уникальных клиентов":r.get("uniqueClients", "—")} for r in tx["groups"]], hide_index=True)
        st.subheader("Охват анализом и профилями")
        st.write(f"Доступная оценка: {analysis['usableAssessmentCount']} / {analysis['denominator']}. Профили групп: {analysis['profiledGroups']} / {analysis['profileGroupDenominator']}; полностью готовы: {analysis['readyProfileGroups']}.")
        st.dataframe([dict(Состояние={"NO_RESULT":"Результата пока нет","LEGACY":"Историческая оценка","COMPLETE":"Полная оценка","PARTIAL":"Частичная оценка","UNAVAILABLE":"Оценка недоступна"}.get(k,k), Операций=v) for k, v in analysis["statuses"].items()], hide_index=True)
        st.caption("NO_RESULT — результат пока не наблюдается; наличие задания PENDING из этого не следует. Профили: последнее состояние группы выбранного окна на срезе.")
        st.subheader("Предупреждения и решения")
        st.write(f"Случаев операций подборки: {cases['caseCount']}; создано в периоде: {cases['newCasesInPeriod']}; решений в периоде: {cases['decisionCount']}; рассмотренных случаев: {cases['decidedCases']}.")
        st.dataframe([dict(Решение=OUTCOMES.get(k,k), Количество=v) for k, v in cases["outcomes"].items()], hide_index=True)
        with st.expander("Очередь, ошибки и ссылки на источники"):
            st.json(dict(refreshJobs=analysis["refreshJobsByStatus"], serviceEventFailures=analysis["eventFailuresAllClients"], axes=analysis["axes"]))
            st.caption("Ошибки событий — весь Analysis за время создания записи, не число неоценённых операций подборки. Статусы заданий текущие на момент чтения владельца.")
            st.dataframe(analysis["resultRefs"], hide_index=True)
            st.dataframe(analysis["profileRefs"], hide_index=True)
            st.dataframe(tx["cohort"], hide_index=True)
    st.download_button("Скачать сохранённый снимок JSON", json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8"), f"report-{report['id']}.json", "application/json")
    from frontend.export_buttons import download_buttons
    download_buttons(report["id"])
    st.caption("PDF, XLSX и CSV строятся из сохранённого снимка. CSV скачивается архивом отдельных таблиц с исходным JSON.")


def reports_workspace(call, role):
    if role == "MANAGER":
        st.subheader("Аналитика за период")
        a, b = st.columns(2)
        start = a.date_input("Период операций: начало UTC", date(2026, 11, 1))
        end = b.date_input("Период операций: конец UTC, не включая", date(2026, 11, 10))
        left, right = st.columns(2)
        currency = left.text_input("Валюта отчёта (пусто — все)", max_chars=3).strip().upper()
        direction = right.selectbox("Направление отчёта", ["Все", "IN", "OUT"])
        selectors = dict(reportType="PERIOD", period={"from": str(start) + "T00:00:00Z", "to": str(end) + "T00:00:00Z"})
        if currency:
            selectors["currency"] = currency
        if direction != "Все":
            selectors["direction"] = direction
        save_button(call, selectors, "Сохранить аналитику за период", "save-period")
    st.caption("Профиль сохраняется из карточки клиента, оценка — из выбранного результата операции или доступного случая.")
    offset = st.session_state.get("report-list-offset", 0)
    value = call("query($offset:Int!){snapshotReports(limit:20,offset:$offset)}", dict(offset=offset))
    page = value.get("snapshotReports") if value else None
    if page and page["items"]:
        items = {r["id"]: r for r in page["items"]}
        selected = st.selectbox("Сохранённый отчёт", list(items), format_func=lambda id: NAMES[items[id]["reportType"]] + " · " + items[id]["created_at"][:19] + " · " + id[:8])
        if st.button("Открыть выбранный отчёт"):
            st.session_state.saved_report_id = selected
        a, b = st.columns(2)
        if a.button("Предыдущие отчёты", disabled=offset == 0):
            st.session_state['report-list-offset'] = max(0, offset - 20)
            st.rerun()
        if b.button("Следующие отчёты", disabled=page.get("nextOffset") is None):
            st.session_state['report-list-offset'] = page['nextOffset']
            st.rerun()
    elif page:
        st.info("Сохранённых доступных снимков пока нет.")
    if st.session_state.get("saved_report_id"):
        result = call("query($id:ID!){snapshotReport(id:$id)}", dict(id=st.session_state.saved_report_id))
        if result and result.get("snapshotReport"):
            render_saved(result["snapshotReport"])
