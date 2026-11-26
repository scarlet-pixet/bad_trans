"""Show posted events and the customer's spending for an explicit period."""

from datetime import UTC, date, datetime, time, timedelta

import streamlit as st

KINDS = {
    "PURCHASE": "Покупка", "TRANSFER": "Перевод", "WITHDRAWAL": "Снятие",
    "DEPOSIT": "Зачисление", "REFUND": "Возврат", "REVERSAL": "Сторно",
}
CHANNELS = {
    "POS": "Терминал", "ONLINE": "Интернет", "ATM": "Банкомат", "BRANCH": "Отделение",
    "BANK_API": "Банковский API", "UNKNOWN": "Канал неизвестен",
}
STATUSES = {"AUTHORIZED": "Авторизована", "POSTED": "Проведена", "CANCELLED": "Отменена"}


def lifecycle_card(call, transaction_id):
    value = call("query($id:ID!){operationLifecycle(transactionId:$id)}", {"id": transaction_id})
    if value is None:
        st.warning("События операции сейчас недоступны.")
        return
    details = value["operationLifecycle"]
    if not details:
        st.info("История статусов операции отсутствует.")
        st.info("География операции неизвестна.")
        return
    st.write(f"{KINDS[details['kind']]} · {CHANNELS[details['channel']]} · {STATUSES[details['status']]}")
    if details["originalTransactionId"]:
        st.caption("Исходная покупка " + details["originalTransactionId"])
    posted = next((event for event in details["events"] if event["status"] == "POSTED"), None)
    if details.get("counterpartyName"):
        st.caption("Контрагент: " + details["counterpartyName"])
    show_location(posted.get("location") if posted else None, details["source"])
    for event in details["events"]:
        stamp = event["occurredAt"]
        if event.get("timePrecision") == "DATE":
            stamp = stamp[:10] + " · время неизвестно"
        st.write(f"{STATUSES[event['status']]} · {event['amount']} · {stamp}")


def show_location(location, source):
    if location is None:
        st.info("География операции неизвестна.")
        return
    label = {
        "POINT_OF_SALE": "Место торговой точки",
        "ATM_DEVICE": "Место банкомата",
        "MERCHANT_REGISTRATION": "Место регистрации продавца",
    }[location["kind"]]
    place = location["countryCode"]
    if location.get("city"):
        place += " · " + location["city"]
    st.text(f"{label}: {place}")
    if location.get("placeRef"):
        st.text(f"ID точки или устройства: {location['placeRef']}")
    st.text(f"Источник места: {source}")
    if location["kind"] == "MERCHANT_REGISTRATION":
        st.caption("Местонахождение клиента неизвестно.")


def spending_panel(call, client):
    today = datetime.now(UTC).date()
    currencies = sorted({account["currency"] for account in client.get("accounts", [])})
    if not currencies:
        return
    code = st.selectbox("Валюта расходов", currencies)
    start = st.date_input("Расходы с (UTC)", value=today - timedelta(days=29))
    end = st.date_input("Расходы по (UTC, включительно)", value=today)
    if not isinstance(start, date) or not isinstance(end, date) or start > end:
        st.warning("Выберите корректный период расходов.")
        return
    window = {"from": datetime.combine(start, time.min, UTC).isoformat(),
              "to": datetime.combine(end + timedelta(days=1), time.min, UTC).isoformat()}
    result = call("query($id:ID!,$c:String!,$p:PeriodInput!){clientSpending(clientId:$id,currencyCode:$c,period:$p)}",
                  {"id": client["id"], "c": code, "p": window})
    if not result:
        return
    totals = result["clientSpending"]
    st.metric("Чистые расходы", f"{totals['netSpending']} {code}")
    st.write(f"Покупки {totals['purchases']} · возвраты {totals['refunds']} · сторно {totals['reversals']} {code}")
    st.write(f"Между своими счетами · списания {totals['ownTransfersOut']} · зачисления {totals['ownTransfersIn']} {code}")
    st.caption(f"Списания без классификации {totals['unclassifiedOut']} {code}. Суммы учтены по дате проведения.")
