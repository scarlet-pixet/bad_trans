"""Manager forms for rule publication and operation reassessment."""

from datetime import UTC, datetime, timedelta
import json
from uuid import UUID

import streamlit as st

from backend.common.behavior_contract import digest

LEGACY_CONTROLS = {
    "AMOUNT": [{"amount": 100, "median": 100, "expected": False},
               {"amount": 1000, "median": 100, "expected": True}],
    "FREQUENCY": [{"count": 0, "expected": False}, {"count": 100, "expected": True}],
    "NEW_COUNTERPARTY": [{"counterpartyId": "known", "knownCounterparties": ["known"], "expected": False},
                         {"counterpartyId": "new", "knownCounterparties": ["known"], "expected": True}],
}


def json_value(text, kind, label):
    def invalid_constant(value):
        raise ValueError(f"Недопустимое значение {value}")

    try:
        value = json.loads(text, parse_constant=invalid_constant)
        # Reject overflow values such as 1e900 before sending over HTTP.
        json.dumps(value, allow_nan=False)
    except ValueError:
        raise ValueError(f"{label}: нужен корректный JSON.") from None
    if not isinstance(value, kind):
        raise ValueError(f"{label}: ожидается {'объект' if kind is dict else 'массив'}.")
    return value


def render_publication(call, catalog):
    if not catalog:
        st.info("В каталоге пока нет правил.")
        return
    rules = {rule["code"]: rule for rule in catalog}
    code = st.selectbox("Правило", list(rules))
    rule = rules[code]
    existing = rule["versions"]
    latest = existing[0] if existing else dict(version=0, parameters={}, weight=0)
    st.caption(f"Последняя опубликованная версия — {latest['version'] or 'нет'}.")
    prefix = f"rule-publish:{rule['id']}"
    with st.form(prefix):
        version = st.number_input("Номер новой версии", 1, 2147483647, latest["version"] + 1,
                                  key=prefix + ":version")
        weight = st.number_input("Вес правила", 0, 100, latest["weight"], key=prefix + ":weight")
        params = st.text_area("Параметры (JSON)", json.dumps(latest["parameters"], ensure_ascii=False, indent=2),
                              key=prefix + ":parameters", height=180)
        if code not in LEGACY_CONTROLS:
            st.caption("Для каждого примера задайте transaction, history и expected (true/false). "
                       "Включите примеры с обоими исходами. coverage задаёт полноту истории при необходимости.")
        examples = st.text_area("Контрольные примеры (JSON)", json.dumps(LEGACY_CONTROLS.get(code, []), indent=2),
                                key=prefix + ":controls", height=155)
        submitted = st.form_submit_button("Опубликовать версию")
    if submitted:
        st.session_state.pop(prefix + ":result", None)
        try:
            payload = dict(ruleId=rule["id"], version=version, weight=weight,
                           parameters=json_value(params, dict, "Параметры"),
                           controlExamples=json_value(examples, list, "Контрольные примеры"))
        except ValueError as exc:
            st.error(str(exc))
        else:
            value = call("mutation($input:RuleVersionInput!){publishRuleVersion(input:$input)"
                         "{id ruleId version weight status publishedAt}}",
                         dict(input=dict(payload, idempotencyKey="rule:" + digest(payload))))
            if value:
                st.session_state[prefix + ":result"] = value["publishRuleVersion"]
    result = st.session_state.get(prefix + ":result")
    if result:
        st.success(f"Версия {result['version']} опубликована.")
        st.json(result, expanded=False)


def render_reanalysis(call, catalog):
    st.caption("Период в UTC, включая начало и исключая конец. Один фоновый запуск поддерживает до 10 000 операций.")
    now = st.session_state.setdefault("period_form_defaults", datetime.now(UTC).replace(second=0, microsecond=0))
    selected_versions = []
    with st.form("period_reanalysis"):
        start, end = st.columns(2)
        begin_day = start.date_input("Дата начала (UTC)", (now - timedelta(days=7)).date())
        begin_time = start.time_input("Время начала (UTC)", now.time())
        end_day = end.date_input("Дата окончания (UTC)", now.date())
        end_time = end.time_input("Время окончания (UTC)", now.time())
        client = st.text_input("ID клиента (пусто — все клиенты)").strip()
        for rule in catalog:
            versions = {v["id"]: v for v in rule["versions"] if v["parameters"].get("policyVersion")}
            if versions:
                chosen = st.selectbox(
                    rule["code"], [None, *versions], index=1,
                    format_func=lambda identifier, choices=versions: (
                        f"Версия {choices[identifier]['version']}" if identifier else "Пропустить"
                    ),
                    key=f"reanalyze-rule:{rule['id']}",
                )
                if chosen:
                    selected_versions.append(chosen)
        attempt = st.text_input("Метка нового прогона (необязательно)", max_chars=100,
                                help="Чтобы повторить команду после сбоя, сохраните параметры и метку. "
                                     "Новая метка запускает отдельную оценку с теми же параметрами.").strip()
        submitted = st.form_submit_button("Поставить переоценку в очередь")
    if submitted:
        st.session_state.pop("period_run_selected", None)
        st.session_state["period_results_offset"] = 0
        begin = datetime.combine(begin_day, begin_time, tzinfo=UTC)
        finish = datetime.combine(end_day, end_time, tzinfo=UTC)
        try:
            if begin >= finish:
                raise ValueError("Начало должно предшествовать концу периода.")
            if not selected_versions:
                raise ValueError("Выберите хотя бы одну опубликованную версию.")
            payload = dict(period={"from": begin.isoformat(), "to": finish.isoformat()},
                           ruleVersionIds=sorted(selected_versions))
            if client:
                try:
                    payload["clientId"] = str(UUID(client))
                except ValueError:
                    raise ValueError("ID клиента должен быть UUID.") from None
        except ValueError as exc:
            st.error(str(exc))
        else:
            key = "period:" + digest([payload, attempt])
            value = call("mutation($input:ReanalysisInput!){startPeriodReassessment(input:$input)}",
                         dict(input=dict(payload, idempotencyKey=key)))
            if value:
                st.session_state["period_run_selected"] = value["startPeriodReassessment"]["id"]
                st.success("Задача переоценки сохранена.")
            else:
                st.info("При обрыве связи повторите отправку с теми же параметрами и меткой.")
    identifier = st.text_input("ID запуска для просмотра", key="period_run_selected").strip()
    if st.session_state.get("period_results_id") != identifier:
        st.session_state["period_results_id"] = identifier
        st.session_state["period_results_offset"] = 0
    if identifier:
        render_period_status(call, identifier)
    render_assessment_comparison(call, "period_comparison", identifier)


def render_period_status(call, identifier):
    offset_key = "period_results_offset"
    offset = st.session_state.get(offset_key, 0)
    st.button("Обновить состояние переоценки")
    value = call("query($id:ID!,$offset:Int!){periodRunResults(runId:$id,offset:$offset,limit:50)}",
                 dict(id=identifier, offset=offset))
    if value is None:
        return
    result = value["periodRunResults"]
    if result is None:
        st.info("Запуск повторной оценки с таким ID не найден.")
        return
    st.code(result["id"])
    labels = dict(PENDING="Ожидает отбора операций", RUNNING="Выполняется",
                  COMPLETED="Завершён", PARTIAL="Завершён частично", FAILED="Ошибка")
    st.write(f"Статус — {labels[result['status']]}")
    counters = result["counters"]
    if result["selection"] is not None:
        st.write(f"Выбрано — {counters['total']}, оценено — {counters['assessed']}, "
                 f"оценка недоступна — {counters['unavailable']}, "
                 f"ошибки выполнения — {counters['failed']}, ожидают — {counters['pending']}.")
        if counters["total"]:
            st.progress((counters["processed"] + counters["failed"]) / counters["total"], text="Обработано операций")
    elif result["status"] == "COMPLETED":
        st.write(f"Обработано — {counters.get('processed', 0)}.")
    if result["error"]:
        st.warning(f"Ошибка отбора или версия расчёта — {result['error']}.")
    with st.expander("Параметры запуска"):
        st.json({key: result[key] for key in ("scope", "ingestedBefore", "policyVersion", "feedbackReference")})
    if result["results"]:
        st.caption(f"Операции {offset + 1}–{offset + len(result['results'])}")
        st.dataframe(result["results"], hide_index=True)
        render_page_buttons(offset_key, offset, result["nextOffset"])
    elif result["status"] == "COMPLETED":
        st.info("В выбранном периоде нет операций для оценки.")


def render_page_buttons(key, offset, next_offset):
    previous, following = st.columns(2)
    if previous.button("Предыдущая страница", key=key + ":prev", disabled=offset == 0):
        st.session_state[key] = max(0, offset - 50)
        st.rerun()
    if following.button("Следующая страница", key=key + ":next", disabled=next_offset is None):
        st.session_state[key] = next_offset
        st.rerun()


def render_assessment_comparison(call, scope, second_default=""):
    with st.form(scope):
        first = st.text_input("ID первого запуска оценки")
        second = st.text_input("ID второго запуска оценки", value=second_default)
        if st.form_submit_button("Сравнить оценки"):
            ids = first.strip(), second.strip()
            if not all(ids) or ids[0] == ids[1]:
                st.warning("Укажите ID двух разных запусков оценки.")
                return
            st.session_state[scope + ":ids"] = ids
            st.session_state[scope + ":offset"] = 0
    ids = st.session_state.get(scope + ":ids")
    if not ids:
        return
    offset_key = scope + ":offset"
    offset = st.session_state[offset_key]
    value = call("query($a:ID!,$b:ID!,$offset:Int!){comparePeriodRuns(firstId:$a,secondId:$b,offset:$offset)}",
                 dict(a=ids[0], b=ids[1], offset=offset))
    if value:
        result = value["comparePeriodRuns"]
        st.caption(f"Запуски оценки — {ids[0]} и {ids[1]}")
        st.write(f"Общих — {result['commonCount']}, только в первом — {result['onlyFirstCount']}, "
                 f"только во втором — {result['onlySecondCount']}.")
        if result["items"]:
            st.caption(f"Различия {offset + 1}–{offset + len(result['items'])}")
            st.dataframe(result["items"], hide_index=True)
            render_page_buttons(offset_key, offset, result["nextOffset"])
        else:
            st.info("Различий в составе и оценках операций нет.")


def render_analysis_commands(call):
    response = call("query{ruleCatalog}")
    if response is None:
        return
    mode = st.radio("Действие", ["Публикация правила", "Повторная оценка периода"], horizontal=True)
    if mode == "Публикация правила":
        render_publication(call, response["ruleCatalog"])
    else:
        render_reanalysis(call, response["ruleCatalog"])
