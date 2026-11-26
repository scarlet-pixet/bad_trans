"""Manager commands return immediately; durable runs can be reopened by ID."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import streamlit as st

from backend.common.behavior_contract import digest

STATUSES = dict(QUEUED='В очереди',DISCOVERING='Отбор групп',RUNNING='Расчёт',EMPTY='Пустой отбор',
                COMPLETED='Завершён',COMPLETED_WITH_SKIPS='Завершён с пропусками',
                PARTIAL='Частичный успех',FAILED='Ошибка',PENDING='Ожидает',RETRY='Повтор',
                SKIPPED='Пропущена')


def command_key(selection):
    """Keep the same command across double clicks and uncertain HTTP outcomes."""
    fingerprint=digest(selection)
    if st.session_state.get('bulk_selection_hash')!=fingerprint:
        st.session_state['bulk_selection_hash']=fingerprint
        st.session_state['bulk_command_key']=str(uuid4())
    return st.session_state['bulk_command_key']


def render_profile_runs(call):
    st.header('Запуски профилей')
    st.write('Отберите группы с операциями в периоде [начало, конец). Профили строятся на конец '
             'периода за 7, 30 и 90 дней: история может начинаться раньше периода отбора.')
    st.caption('Учитываются поступления до момента команды и решения аналитика, известные при её '
               'создании. Это ретроспективный расчёт с текущим знанием. Оценки операций не запускаются.')
    now=datetime.now(UTC).replace(second=0,microsecond=0)
    a,b=st.columns(2)
    start_date=a.date_input('Начало отбора (UTC)',value=(now-timedelta(days=30)).date())
    start_time=a.time_input('Время начала (UTC)',value=now.time())
    end_date=b.date_input('Конец отбора (UTC)',value=now.date())
    end_time=b.time_input('Время конца (UTC)',value=now.time())
    mode=st.radio('Область отбора',['Все клиенты','Клиенты по ID','Сегмент'],horizontal=True)
    selection=dict(period={'from':datetime.combine(start_date,start_time,tzinfo=UTC).isoformat(),
                           'to':datetime.combine(end_date,end_time,tzinfo=UTC).isoformat()})
    if mode=='Клиенты по ID':
        raw=st.text_area('ID клиентов через запятую или по одному в строке')
        selection['clientIds']=sorted(set(raw.replace(',', ' ').split()))
        st.caption('ID доступен в API клиента; можно выбрать до 1000 ID. Пустой список не означает всех клиентов.')
    elif mode=='Сегмент':
        selection['segment']=st.text_input('Код сегмента')
    left,right=st.columns(2)
    curr=left.text_input('Валюта (пусто — все)',max_chars=3)
    direct=right.selectbox('Направление отбора',['Все','IN','OUT'])
    if curr:
        selection['currency']=curr.strip()
    if direct!='Все':
        selection['direction']=direct
    key=command_key(selection)
    if st.button('Новая команда с теми же параметрами'):
        st.session_state['bulk_command_key']=str(uuid4())
        st.rerun()
    if st.button('Запустить формирование',type='primary'):
        result=call('mutation($s:JSON!,$k:String!){startProfileRun(selection:$s,idempotencyKey:$k)}',
                    dict(s=selection,k=key))
        if result:
            identifier=result['startProfileRun']['id']
            st.session_state['profile_run_selected']=identifier
            st.query_params['profileRun']=identifier
            st.success('Команда принята: '+identifier)
    st.caption('Повтор кнопки возвращает ту же команду. Для отдельного пересчёта сначала нажмите «Новая команда».')

    run_offset=st.session_state.get('bulk_runs_offset',0)
    result=call('query($o:Int!){profileRuns(limit:20,offset:$o)}',dict(o=run_offset))
    runs=result.get('profileRuns',[]) if result else []
    if runs:
        st.dataframe([dict(ID=r['id'],Состояние=STATUSES[r['status']],Создан=r['createdAt']) for r in runs],
                     hide_index=True,use_container_width=True)
    prev,next_=st.columns(2)
    if prev.button('Предыдущие запуски',disabled=run_offset==0):
        st.session_state['bulk_runs_offset']=max(0,run_offset-20)
        st.rerun()
    if next_.button('Следующие запуски',disabled=len(runs)<20):
        st.session_state['bulk_runs_offset']=run_offset+20
        st.rerun()
    with st.expander('Сравнить два запуска профилирования'):
        from frontend.profile_run_comparison import render_run_comparison
        render_run_comparison(call, runs, STATUSES)
    if 'profile_run_selected' not in st.session_state:
        st.session_state['profile_run_selected']=st.query_params.get('profileRun',runs[0]['id'] if runs else '')
    identifier=st.text_input('ID запуска для открытия',key='profile_run_selected').strip()
    if not identifier:
        return
    st.query_params['profileRun']=identifier
    st.code('?profileRun='+identifier)
    if st.button('Обновить прогресс'):
        st.rerun()
    offset_key='bulk_group_offset:'+identifier
    offset=st.session_state.get(offset_key,0)
    result=call('query($id:ID!,$o:Int!){profileRun(id:$id,offset:$o,limit:50)}',dict(id=identifier,o=offset))
    if not result:
        return
    run=result['profileRun']
    st.subheader(STATUSES[run['status']])
    st.json(dict(отбор=run['selection'],срез=run['context']['asOf'],
                 граница_поступления=run['context']['ingestedBefore'],
                 знание_решений=run['context']['feedbackReference'],снимок_отбора=run['selectionSnapshot']),expanded=False)
    st.write(f"Группы: {run['completedGroups']} готовы, {run['failedGroups']} ошибок, "
             f"{run['skippedGroups']} пропусков, всего {run['totalGroups']}. "
             f"Уникальные клиенты: {run['selectedClients']} в отборе, {run['completedClients']} с результатами.")
    if not run['selectionComplete']:
        st.info('Отбор не завершён: число клиентов пока неполное.')
    if run['error']:
        st.error(run['error'])
    if run['status']=='EMPTY':
        st.info('Операций, соответствующих отбору, не найдено. Версии не создавались.')
    groups=run['groups']
    if groups:
        st.dataframe([dict(Клиент=g['clientId'],Валюта=g['currency'],Направление=g['direction'],
                           Состояние=STATUSES[g['status']],Причина=g['error'],Версии=g['versionIds']) for g in groups],
                     hide_index=True,use_container_width=True)
        successful=[g for g in groups if g['versionIds']]
        if successful:
            group=st.selectbox('Группа с результатом',successful,
                               format_func=lambda g:f"{g['clientId']} / {g['currency']} / {g['direction']}")
            version=st.selectbox('ID созданной версии',group['versionIds'])
            if st.button('Открыть сохранённый профиль'):
                value=call('query($id:ID!){profileVersion(id:$id)}',dict(id=version))
                if value:
                    from frontend.profile_card import render_profiles
                    from frontend.workspace_view import snapshot_profile
                    render_profiles([snapshot_profile(value['profileVersion'])])
    a,b=st.columns(2)
    if a.button('Предыдущие группы',disabled=offset==0):
        st.session_state[offset_key]=max(0,offset-50)
        st.rerun()
    if b.button('Следующие группы',disabled=run['nextOffset'] is None):
        st.session_state[offset_key]=run['nextOffset']
        st.rerun()
