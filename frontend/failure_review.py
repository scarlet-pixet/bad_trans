"""Admin workspace for parked events and their disposition."""
from uuid import uuid4

import streamlit as st

STATES = {"Активные":"ACTIVE","Ожидают повтора":"WAITING","Завершённые":"CLOSED"}
LABELS = {"OPEN":"Открыта","WAITING":"Ожидает повтора","CLOSED":"Завершена","RETRY_FAILED":"Повтор не удался"}
ACTIONS = {"Проверить результат":"VERIFY","Повторить обработку":"RETRY","Завершить разбор":"CLOSE"}


def render_failure_reviews(call):
    st.subheader("Разбор ошибок обработки")
    owner = st.selectbox("Сервис-владелец", ["analysis","case"])
    state = STATES[st.radio("Ошибки", list(STATES), horizontal=True)]
    st.button("Обновить разборы")
    state_key = f"failure-offset:{owner}:{state}"
    offset = st.session_state.get(state_key,0)
    value = call(
        "query($o:String!,$s:String!,$p:Int!){failureReviews(owner:$o,state:$s,offset:$p)}",
        dict(o=owner,s=state,p=offset),
    )
    if value is None:
        return
    page = value["failureReviews"]
    items = {row["id"]:row for row in page["items"]}
    previous, following = st.columns(2)
    if previous.button("Назад",key=state_key+":prev",disabled=not offset):
        st.session_state[state_key]=max(0,offset-20)
        st.rerun()
    if following.button("Далее",key=state_key+":next",disabled=page["nextOffset"] is None):
        st.session_state[state_key]=page["nextOffset"]
        st.rerun()
    if not items:
        st.info("В этом разделе ошибок нет.")
        return
    selected=st.selectbox(
        "Ошибка обработки", list(items),
        format_func=lambda key: f"{items[key]['topic']} · {items[key]['cause']} · {key[:8]}",
        key=f"failure-selector:{owner}:{state}",
    )
    value=call("query($o:String!,$id:ID!){failureReview(owner:$o,id:$id)}",dict(o=owner,id=selected))
    if value is None:
        return
    detail=value["failureReview"]
    st.write(f"Разбор — {LABELS[detail['state']]}. Доставка — {detail['transportStatus']}.")
    st.code(detail["id"])
    if detail["eventId"]:
        st.caption(f"Событие {detail['eventId']} · раздел {detail['partition']} · смещение {detail['offset']}")
    if not detail["valid"]:
        st.warning("Исходное сообщение не соответствует контракту. Укажите решение в обосновании.")
    if detail["proof"]:
        proof=detail["proof"]
        if proof.get("resultId"):
            st.write(f"Результат {proof['resultId']}")
        st.caption("Владелец подтвердил событие." if proof["accepted"] else
                   "Подтверждение обработки исходного события отсутствует.")
    if detail["actions"]:
        with st.expander("История разбора", expanded=detail["state"]=="CLOSED"):
            st.dataframe(detail["actions"],hide_index=True)
    if detail["state"] in ("CLOSED","WAITING"):
        return
    choices=list(ACTIONS)
    if not detail["valid"] or detail["transportStatus"]=="RETRY_PENDING":
        choices.remove("Повторить обработку")
    action=ACTIONS[st.selectbox("Действие",choices,key=f"failure-action:{selected}")]
    reason=st.text_area("Обоснование",max_chars=500,key=f"failure-reason:{selected}") if action!="VERIFY" else None
    if st.button("Применить",key=f"failure-apply:{selected}"):
        arguments=dict(o=owner,id=selected,a=action,r=reason)
        key_state=f"failure-command:{selected}"
        previous=st.session_state.get(key_state)
        key=previous[1] if previous and previous[0]==arguments else str(uuid4())
        st.session_state[key_state]=(arguments,key)
        result=call(
            "mutation($o:String!,$id:ID!,$a:String!,$r:String,$k:String!){"
            "changeFailureReview(owner:$o,id:$id,action:$a,reason:$r,idempotencyKey:$k)}",
            dict(arguments,k=key),
        )
        if result:
            st.session_state.pop(key_state,None)
            st.rerun()
