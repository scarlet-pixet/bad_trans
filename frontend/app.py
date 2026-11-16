"""Role workspaces backed exclusively by the authenticated Gateway."""

import base64
import hashlib
import json
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from uuid import uuid4

import requests
import streamlit as st
from frontend.assessment_view import shown_score, render_assessment, render_case_score
from frontend.profile_card import status_label

API = os.getenv("GRAPHQL_URL", "http://127.0.0.1:18100/graphql")
st.set_page_config(
    page_title="Bad Trans · Поведение клиентов", page_icon="◈", layout="wide"
)
st.markdown(
    "<style>[data-testid='stMetric']{border:1px solid #8297b355;border-radius:12px;padding:18px}[data-testid='stMetricValue']{font-size:1.5rem}[data-testid='stMetricLabel'] p{font-size:.8rem}.block-container{padding-top:2rem}</style>",
    unsafe_allow_html=True,
)


def call(query, variables=None):
    headers = (
        {"Authorization": "Bearer " + st.session_state.token}
        if st.session_state.get("token")
        else {}
    )
    try:
        response = requests.post(
            API,
            json={"query": query, "variables": variables or {}},
            headers=headers,
            timeout=35,
        )
        value = response.json()
        if response.status_code >= 400:
            detail = value.get("detail", value)
            st.error(
                detail.get("message", "Запрос недоступен")
                if isinstance(detail, dict)
                else str(detail)
            )
            return None
        if value.get("errors"):
            st.error(" · ".join(e["message"] for e in value["errors"]))
            return None
        return value["data"]
    except (requests.RequestException, ValueError):
        st.error("Сервис временно недоступен. Обновите данные после восстановления.")
        return None


def command(query, variables):
    result = call(query, variables)
    if result:
        st.success("Сохранено")
    return result


def key():
    return str(uuid4())


if not st.session_state.get("token"):
    _, center, _ = st.columns([1, 2, 1])
    with center:
        st.title("◈ Bad Trans")
        st.subheader("Поведение клиента. Понятные отклонения.")
        st.write("Автоматические профили и очередь операций для рассмотрения.")
        with st.form("login"):
            login = st.text_input("Логин", "manager")
            password = st.text_input("Пароль", type="password")
            submit = st.form_submit_button(
                "Войти", type="primary", use_container_width=True
            )
        if submit:
            value = call(
                "mutation($l:String!,$p:String!){signIn(login:$l,password:$p){accessToken}}",
                {"l": login, "p": password},
            )
            if value:
                st.session_state.token = value["signIn"]["accessToken"]
                st.rerun()
        st.caption("Учебный вход: manager / analyst / admin · BadTrans-demo-2026!")
    st.stop()
identity = call("query{me{id login role}}")
if not identity:
    if st.button("Вернуться ко входу"):
        st.session_state.clear()
        st.rerun()
    st.stop()
user = identity["me"]
role = user["role"]
with st.sidebar:
    st.title("◈ Bad Trans")
    st.caption("Мониторинг поведения клиентов")
    st.write(f"**{user['login']}** · " + {"MANAGER":"Руководитель","ANALYST":"Аналитик","ADMIN":"Администратор"}[role])
    pages = {
        "MANAGER": [
            "Обзор",
            "Предупреждения",
            "Профили клиентов",
            "Операции",
            "Источники данных",
            "Качество анализа",
            "Отчёты",
        ],
        "ANALYST": [
            "Предупреждения",
            "Уведомления",
            "Отчёты",
        ],
        "ADMIN": ["Источники данных", "Сотрудники", "Клиенты и счета", "Ввод транзакций", "Состояние системы"],
    }[role]
    page = st.radio("Раздел", pages, label_visibility="collapsed")
    if st.button("Обновить данные", use_container_width=True):
        st.rerun()
    if st.button("Выйти", use_container_width=True):
        call("mutation{signOut}")
        st.session_state.clear()
        st.rerun()
st.title(page)


def clients():
    value = call(
        "query{clients(page:{limit:100}){items{id displayName accounts{id externalId currency status}}}}"
    )
    return value["clients"]["items"] if value else []


def choose_client(optional=False):
    values = ([None] if optional else []) + clients()
    if not values:
        st.info("Клиенты появятся после запуска источника.")
        st.stop()
    return st.selectbox(
        "Клиент", values, format_func=lambda c: c["displayName"] if c else "Все клиенты"
    )


if page == "Обзор":

    @st.fragment(run_every=5)
    def overview():
        value = call("query{managerDashboard}")
        if not value:
            return
        dash = value["managerDashboard"]
        parts = dash["components"]
        analysis = parts.get("analysis", {})
        cases = parts.get("case", {})
        statuses = cases.get("statuses", {})
        for error in dash["partialErrors"]:
            st.warning(error["service"] + ": " + error["message"])
        a, b, c, d = st.columns(4)
        a.metric("Проанализировано", analysis.get("analyzed", "—"))
        b.metric("Выше порога", analysis.get("flagged", "—"))
        c.metric(
            "Открытых случаев", sum(v for k, v in statuses.items() if k != "CLOSED")
        )
        d.metric("Мало истории", analysis.get("insufficient", "—"))
        left, right = st.columns(2)
        with left:
            st.subheader("Новые предупреждения")
            if cases.get("trend"):
                st.bar_chart([{"День":r["day"],"Случаев":r["count"]} for r in cases["trend"]], x="День", y="Случаев")
            else:
                st.info("Предупреждений пока нет.")
        with right:
            st.subheader("Состояние очереди")
            st.dataframe(
                [{"Состояние": k, "Количество": v} for k, v in statuses.items()],
                hide_index=True,
            )
            st.caption("Открытые случаи по исполнителям")
            st.dataframe(
                [
                    {
                        "Исполнитель": r["assigneeId"][:8]
                        if r["assigneeId"]
                        else "Не назначен",
                        "Открыто": r["count"],
                    }
                    for r in cases.get("loads", [])
                ],
                hide_index=True,
            )
        st.subheader("Объём операций по валютам")
        st.dataframe(
            [
                {"Валюта": r["currency"], "Операций": r["count"], "Сумма": r["total"]}
                for r in parts.get("transaction", {}).get("currencies", [])
            ],
            hide_index=True,
        )
        st.caption(
            "Отклонение рассматривает сотрудник. Автоматическая оценка не устанавливает факт мошенничества."
        )

    overview()
elif page == "Источники данных":
    st.write(
        "Воспроизведение истории трёх клиентов и потока новых операций. Обработка продолжается в backend без открытого браузера."
    )
    st.info(
        "Синтетический источник: обычные платежи, всплески сумм, новый получатель и частые операции."
    )
    interval = st.slider("Интервал между операциями потока, секунд", 0.1, 5.0, 1.0, 0.1)
    if st.button("Запустить новый поток", type="primary"):
        command(
            "mutation($k:String!,$i:Float!){startSource(idempotencyKey:$k,intervalSeconds:$i)}",
            {"k": key(), "i": interval},
        )

    @st.fragment(run_every=3)
    def source_status():
        value = call("query{sources}")
        runs = value["sources"] if value else []
        if not runs:
            st.info("Источник ещё не запускался.")
        for run in runs[:5]:
            with st.container(border=True):
                st.write(f"**Поток {run['created_at'][:19]}** · {status_label(run['status'])}")
                if run.get("last_error"):
                    st.error(run["last_error"])
                st.progress(
                    run["processed"] / run["total"],
                    text=f"Поступило {run['processed']} из {run['total']} · осталось истории {run['history_pending']}",
                )
                if run["status"] != "COMPLETED" and st.button(
                    "Продолжить" if run["status"] == "PAUSED" else "Приостановить",
                    key=run["id"],
                ):
                    call(
                        "mutation($id:ID!,$p:Boolean!){setSourceState(runId:$id,paused:$p)}",
                        {"id": run["id"], "p": run["status"] != "PAUSED"},
                    )
                    st.rerun(scope="fragment")

    source_status()
elif page == "Профили клиентов":
    from frontend.workspace_view import client_workspace
    client_workspace(call,choose_client())
elif page == "Предупреждения":
    status = st.selectbox(
        "Состояние", ["Все", "NEW", "ASSIGNED", "IN_PROGRESS", "ESCALATED", "CLOSED"],format_func=status_label
    )
    value = call(
        'query($f:CaseFilter){reviewCases(filter:$f,page:{limit:100,sortBy:"createdAt",sortDirection:"DESC"}){items{id clientId transactionId latestScore status priority rowVersion}}}',
        {"f": {} if status == "Все" else {"status": status}},
    )
    cases = value["reviewCases"]["items"] if value else []
    names = {c["id"]: c["displayName"] for c in clients()} if role=="MANAGER" else {}
    if not cases:
        st.info("Доступных предупреждений пока нет. Анализ выполняется автоматически.")
    else:
        selected = st.selectbox(
            "Предупреждение",
            cases,
            format_func=lambda c: (
                f"{names.get(c['clientId'], 'Клиент')} · {c['latestScore']} баллов · {status_label(c['status'])} · {c['id'][:8]}"
            ),
        )
        value = call("query($id:ID!){caseContext(caseId:$id)}", {"id": selected["id"]})
        if value:
            data = value["caseContext"]
            case = data["case"]
            tx = data["transaction"] or {}
            det = data["detection"] or {}
            a, b, c = st.columns(3)
            with a:
                render_case_score(case, det)
            b.metric("Сумма", f"{tx.get('amount', '—')} {tx.get('currency', '')}")
            c.metric("Статус", status_label(case["status"]))
            st.subheader("Результаты анализа операции")
            from frontend.workspace_view import operation_workspace
            operation_workspace(call,tx,key_prefix="case-"+case["id"])
            for reason in det.get("explanations", []):
                st.warning(f"+{reason['contribution']} · {reason['text']}")
            baseline = det.get("baseline", {})
            if baseline:
                st.write(
                    f"Профиль последней оценки случая: {baseline['sampleSize']} операций · медиана {baseline['medianAmount'] if baseline['medianAmount'] is not None else '—'} · 30 дней"
                )
            for reason in det.get("missingReasons", []):
                st.caption(reason)
            with st.expander("История рассмотрения"):
                st.dataframe(case.get("comments", []), hide_index=True)
                st.dataframe(case.get("decisions", []), hide_index=True)
            args = {"id": case["id"], "v": case["rowVersion"], "k": key()}
            if (
                role == "ANALYST"
                and case["status"] in ("NEW", "ASSIGNED")
                and st.button("Взять в работу", type="primary")
            ):
                if command(
                    "mutation($id:ID!,$v:Int!,$k:String!){takeCase(caseId:$id,expectedVersion:$v,idempotencyKey:$k){status}}",
                    args,
                ):
                    st.rerun()
            reason = st.text_area("Комментарий / основание решения")
            if role == "ANALYST" and case["status"] in ("IN_PROGRESS", "ESCALATED"):
                if st.button("Добавить комментарий"):
                    if command(
                        "mutation($x:CaseCommentInput!){addCaseComment(input:$x){status}}",
                        {
                            "x": {
                                "caseId": case["id"],
                                "text": reason,
                                "idempotencyKey": key(),
                            }
                        },
                    ):
                        st.rerun()
                if case["status"] == "IN_PROGRESS" and st.button("Эскалировать"):
                    if command(
                        "mutation($id:ID!,$v:Int!,$k:String!,$r:String!){escalateCase(caseId:$id,expectedVersion:$v,idempotencyKey:$k,reason:$r){status}}",
                        {**args, "r": reason},
                    ):
                        st.rerun()
            if case["status"] in ("IN_PROGRESS", "ESCALATED"):
                outcome = st.selectbox(
                    "Решение",
                    ["EXPLAINED", "UNUSUAL_CONFIRMED"],
                    format_func=lambda x: (
                        "Операция объяснена"
                        if x == "EXPLAINED"
                        else "Нетипичность подтверждена"
                    ),
                )
                if st.button("Зафиксировать решение и закрыть", type="primary"):
                    if command(
                        "mutation($x:CaseDecisionInput!){closeCase(input:$x){status}}",
                        {
                            "x": {
                                "caseId": case["id"],
                                "outcome": outcome,
                                "reason": reason,
                                "expectedVersion": case["rowVersion"],
                                "idempotencyKey": key(),
                            }
                        },
                    ):
                        st.rerun()
            if (
                role == "MANAGER"
                and case["status"] == "CLOSED"
                and st.button("Открыть повторно")
            ):
                if command(
                    "mutation($id:ID!,$v:Int!,$k:String!,$r:String!){reopenCase(caseId:$id,expectedVersion:$v,idempotencyKey:$k,reason:$r){status}}",
                    {**args, "r": reason},
                ):
                    st.rerun()
    if role == "MANAGER" and st.button("Распределить новые случаи между аналитиками"):
        value = command(
            "mutation($x:DistributionInput!){distributeCases(input:$x){assignedCount unassignedCount}}",
            {"x": {"limit": 100, "idempotencyKey": key()}},
        )
        if value:
            st.write(value["distributeCases"])
elif page == "Операции":
    client = choose_client(True)
    value = call(
        'query($f:TransactionFilter){transactions(filter:$f,page:{limit:100,sortBy:"occurredAt",sortDirection:"DESC"}){items{id externalId amount currency direction occurredAt}}}',
        {"f": {"clientId": client["id"]} if client else {}},
    )
    rows = value["transactions"]["items"] if value else []
    st.dataframe(
        [
            {
                "Время": r["occurredAt"],
                "Сумма": r["amount"],
                "Валюта": r["currency"],
                "Направление": r["direction"],
                "Номер": r["externalId"],
            }
            for r in rows
        ],
        hide_index=True,
        use_container_width=True,
    )
    if rows:
        tx = st.selectbox(
            "Открыть результат",
            rows,
            format_func=lambda r: (
                f"{r['occurredAt'][:19]} · {r['amount']} {r['currency']} · {r['externalId']}"
            ),
        )
        from frontend.workspace_view import operation_workspace
        operation_workspace(call,tx)
elif page == "Качество анализа":
    value = call("query{sources}")
    runs = value["sources"] if value else []
    if not runs:
        st.info("Сначала запустите источник.")
    else:
        run = st.selectbox(
            "Контрольный поток",
            runs,
            format_func=lambda r: r["created_at"][:19] + " · " + r["status"],
        )
        value = call("query($id:ID!){qualityReport(runId:$id)}", {"id": run["id"]})
        if value:
            q = value["qualityReport"]
            a, b, c, d = st.columns(4)
            a.metric(
                "Precision",
                f"{q['precision']:.1%}" if q["precision"] is not None else "—",
            )
            b.metric("Recall", f"{q['recall']:.1%}" if q["recall"] is not None else "—")
            c.metric("Ложные тревоги", q["falsePositive"])
            d.metric("Пропуски", q["falseNegative"])
            st.caption(
                f"Проверено {q['evaluated']} · ожидает {q['pending']} · прогрев {q['warmup']}"
            )
            st.dataframe(
                [
                    {"Сценарий": {"amount_spike":"Всплеск суммы","frequency_burst":"Частые операции","new_recipient_spike":"Новый получатель","usual_payment":"Обычный платёж"}.get(name,name),
                     "Найдено":metrics['truePositive'],"Пропуски":metrics['falseNegative'],
                     "Ложные":metrics['falsePositive'],"Норма":metrics['trueNegative'],"Ожидает":metrics['pending']}
                    for name, metrics in q["byScenario"].items()
                ],
                hide_index=True,
            )
            st.warning(
                "Метрики относятся только к этому синтетическому набору. Пропуски и ложные срабатывания учитываются отдельно; банковская точность на реальных данных не измерена."
            )
elif page == "Отчёты":
    from frontend.report_view import reports_workspace
    reports_workspace(call, role)
    with st.expander("Прежний отчёт по решениям сотрудников · decided_at"):
        start = st.date_input("Начало", date(2026, 1, 1))
        end = st.date_input("Конец (не включая)", date(2027, 1, 1))
        if st.button("Сформировать отчёт", type="primary"):
            value = call(
                "mutation($x:ReportInput!){createReport(input:$x){id snapshot}}",
                {
                    "x": {
                        "period": {
                            "from": str(start) + "T00:00:00Z",
                            "to": str(end) + "T00:00:00Z",
                        },
                        "idempotencyKey": key(),
                    }
                },
            )
            if value:
                st.session_state.report = value["createReport"]
        if st.session_state.get("report"):
            report = st.session_state.report
            st.dataframe(
                [
                    {"Показатель": k, "Значение": str(v)}
                    for k, v in report["snapshot"].items()
                ],
                hide_index=True,
            )
            value = call(
                "query($id:ID!){exportReport(id:$id){fileName contentBase64}}",
                {"id": report["id"]},
            )
            if value:
                export = value["exportReport"]
                st.download_button(
                    "Скачать CSV",
                    base64.b64decode(export["contentBase64"]),
                    export["fileName"],
                    "text/csv",
                )
elif page == "Уведомления":
    value = call(
        "query{notifications(page:{limit:100}){items{id type createdAt readAt}}}"
    )
    for row in value["notifications"]["items"] if value else []:
        with st.container(border=True):
            st.write(row["type"] + " · " + row["createdAt"])
            if not row["readAt"] and st.button("Прочитано", key=row["id"]):
                call(
                    "mutation($id:ID!){markNotificationRead(id:$id){readAt}}",
                    {"id": row["id"]},
                )
                st.rerun()
elif page == "Сотрудники":
    value = call("query{staffUsers}")
    staff = value["staffUsers"] if value else []
    st.dataframe(
        [
            {"Логин": r["login"], "Роль": r["role"], "Активен": r["isActive"]}
            for r in staff
        ],
        hide_index=True,
    )
    with st.expander("Добавить сотрудника"):
        with st.form("staff"):
            login = st.text_input("Логин сотрудника")
            password = st.text_input("Начальный пароль", type="password")
            new_role = st.selectbox("Роль", ["ANALYST", "MANAGER", "ADMIN"])
            if st.form_submit_button("Создать"):
                command(
                    "mutation($x:StaffInput!){registerStaff(input:$x){login}}",
                    {
                        "x": {
                            "login": login,
                            "password": password,
                            "roleCode": new_role,
                            "idempotencyKey": key(),
                        }
                    },
                )
    others = [r for r in staff if r["id"] != user["id"]]
    if others:
        chosen = st.selectbox("Сотрудник", others,
            format_func=lambda r: r['login'] + (' · активен' if r['isActive'] else ' · заблокирован'))
        if chosen['isActive']:
            new_role = st.selectbox("Новая роль", ["ANALYST", "MANAGER", "ADMIN"])
            if st.button("Изменить роль"):
                if command("mutation($id:ID!,$r:RoleCode!,$k:String!){changeStaffRole(userId:$id,roleCode:$r,idempotencyKey:$k){role}}",
                           {"id":chosen['id'],"r":new_role,"k":key()}):
                    st.rerun()
            if st.button("Заблокировать"):
                if command("mutation($id:ID!,$k:String!){blockStaff(userId:$id,idempotencyKey:$k){login}}",
                           {"id":chosen['id'],"k":key()}):
                    st.rerun()
        else:
            st.info('После разблокировки сотрудник сможет выполнить новый вход. Прежние сессии останутся отозванными.')
            if st.button("Разблокировать", type='primary'):
                if command("mutation($id:ID!,$k:String!){unblockStaff(userId:$id,idempotencyKey:$k){login isActive}}",
                           {"id":chosen['id'],"k":key()}):
                    st.rerun()
elif page == "Клиенты и счета":
    with st.expander("Новый клиент"):
        external = st.text_input("Номер клиента")
        name = st.text_input("Имя клиента")
        if st.button("Создать клиента"):
            command(
                "mutation($x:ClientInput!){createClient(input:$x){displayName}}",
                {
                    "x": {
                        "externalId": external,
                        "displayName": name,
                        "idempotencyKey": key(),
                    }
                },
            )
    client = choose_client()
    st.dataframe(client["accounts"], hide_index=True)
    number = st.text_input("Номер нового счёта")
    currency = st.selectbox("Валюта", ["BYN", "USD", "EUR"])
    if st.button("Открыть счёт"):
        command(
            "mutation($x:AccountInput!){openAccount(input:$x){externalId}}",
            {
                "x": {
                    "clientId": client["id"],
                    "externalId": number,
                    "currency": currency,
                    "idempotencyKey": key(),
                }
            },
        )
    with st.expander("Новый контрагент"):
        external = st.text_input("Номер контрагента")
        name = st.text_input("Имя контрагента")
        if st.button("Создать контрагента"):
            command(
                "mutation($x:CounterpartyInput!){registerCounterparty(input:$x){externalId}}",
                {
                    "x": {
                        "externalId": external,
                        "displayName": name,
                        "idempotencyKey": key(),
                    }
                },
            )
elif page == "Ввод транзакций":
    st.write('Регистрация наблюдаемой операции. После сохранения профили и оценка обновятся автоматически.')
    client = choose_client()
    accounts = [account for account in client['accounts'] if account.get('status')=='OPEN']
    if not accounts:
        st.info('У клиента нет открытого счёта. Создайте его в разделе «Клиенты и счета».')
        st.stop()
    account = st.selectbox('Счёт клиента', accounts,
        format_func=lambda a: f"{a['externalId']} · {a['currency']}")
    values = call('query{counterparties}')
    counterparties = [None] + (values['counterparties'] if values else [])
    now = datetime.now(ZoneInfo('Europe/Minsk'))
    if st.button('Новая операция'):
        for name in ('admin_tx_reference','admin_tx_day','admin_tx_time','admin_tx_submission'):
            st.session_state.pop(name,None)
        st.rerun()
    if 'admin_tx_reference' not in st.session_state:
        st.session_state.admin_tx_reference = 'ADMIN-'+now.strftime('%Y%m%d%H%M%S')
        st.session_state.admin_tx_day = now.date()
        st.session_state.admin_tx_time = now.time().replace(microsecond=0)
    with st.form('admin_transaction'):
        external = st.text_input('Номер операции во внешней системе',key='admin_tx_reference')
        amount = st.text_input('Сумма','100.00')
        st.caption('Валюта операции: '+account['currency'])
        direction = st.selectbox('Направление',['OUT','IN'],format_func=lambda x:'Исходящая' if x=='OUT' else 'Входящая')
        counterparty = st.selectbox('Контрагент',counterparties,
            format_func=lambda x:x['displayName'] if x else 'Не указан')
        day = st.date_input('Дата операции · Минск',key='admin_tx_day')
        clock = st.time_input('Время операции · Минск',key='admin_tx_time')
        submitted = st.form_submit_button('Зарегистрировать транзакцию',type='primary')
    if submitted:
        occurred = datetime.combine(day,clock,tzinfo=ZoneInfo('Europe/Minsk')).astimezone(timezone.utc)
        payload = {'source':'admin-ui','externalId':external,'accountId':account['id'],
                   'counterpartyId':counterparty['id'] if counterparty else None,
                   'amount':amount,'currency':account['currency'],'direction':direction,'occurredAt':occurred.isoformat()}
        signature = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        previous = st.session_state.get('admin_tx_submission',{})
        if previous.get('signature')!=signature:
            previous = {'signature':signature,'key':key()}
            st.session_state.admin_tx_submission = previous
        payload['idempotencyKey'] = previous['key']
        value = command('mutation($x:TransactionInput!){registerTransaction(input:$x){id externalId amount currency}}',{'x':payload})
        if value:
            tx = value['registerTransaction']
            st.success(f"Операция {tx['externalId']} на {tx['amount']} {tx['currency']} зарегистрирована. Автоматический анализ поставлен в очередь.")
    with st.expander('Последние операции клиента'):
        value = call('query($f:TransactionFilter){transactions(filter:$f,page:{limit:10,sortBy:"occurredAt",sortDirection:"DESC"}){items{externalId amount currency direction occurredAt}}}',
                     {'f':{'clientId':client['id']}})
        if value:
            st.dataframe([{'Номер':r['externalId'],'Сумма':r['amount'],'Валюта':r['currency'],'Направление':r['direction'],'Время UTC':r['occurredAt']} for r in value['transactions']['items']],hide_index=True)
elif page == "Состояние системы":
    try:
        health = requests.get(
            API.removesuffix("/graphql") + "/health", timeout=15
        ).json()
        if not isinstance(health, dict) or not isinstance(health.get('services'), list):
            st.error('Сервис состояния вернул некорректный ответ')
            st.stop()
        from frontend.system_health import render_health
        render_health(health)
    except (requests.RequestException, ValueError):
        st.error("Проверка состояния недоступна")
