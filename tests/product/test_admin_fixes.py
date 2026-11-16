"""Regressions for actual Streamlit execution and administrator workflows."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from streamlit.testing.v1 import AppTest

from backend.services.auth.service import AuthService, AuthFailure, hash_password
from backend.services.transaction.ingest.registration import TransactionService


ROOT = Path(__file__).resolve().parents[2]
ADMIN = 'b4703b75-0c9e-55cf-8ad2-bd607b00f7ed'


@pytest.mark.parametrize('ready', [True, False])
def test_system_status_really_renders_without_streamlit_magic_error(ready):
    response = Mock(status_code=200)
    response.json.return_value = {'data': {'me': {'id': ADMIN, 'login': 'admin', 'role': 'ADMIN'}}, 'sources': []}
    # App initially opens Sources; return the correct payload for both queries.
    def post(_url, json, **_kwargs):
        return Mock(status_code=200, json=lambda: {'data': {'me': {'id': ADMIN, 'login': 'admin', 'role': 'ADMIN'}}} if 'me{' in json['query'] else {'data': {'sources': []}})
    health = {'allReady': ready, 'services': [{'service': 'analysis', 'status': 'READY' if ready else 'UNAVAILABLE', 'database': 'bank_analysis', 'kafkaReady': ready}]}
    with patch('requests.post', side_effect=post), patch('requests.get', return_value=Mock(json=lambda: health)):
        app = AppTest.from_file(str(ROOT/'frontend/app.py'), default_timeout=15)
        app.session_state.token = 'ui-test-token'
        app.run()
        app.sidebar.radio[0].set_value('Состояние системы').run()
        assert not app.exception, [e.message for e in app.exception]
        assert len(app.dataframe)==1
        assert bool(app.success) if ready else bool(app.warning)


def test_unblock_does_not_restore_previously_blocked_sessions():
    user_id = 'c7fba0b2-b5bd-59e7-b722-4f0e6c5b4c45'
    row = {'id': user_id, 'login': 'tester', 'role': 'ANALYST', 'is_active': True,
           'password_hash': hash_password('Test-password-2026!'), 'session_version': 0}
    class Repo:
        def by_login(self, _login): return dict(row)
        def by_id(self, _id): return dict(row)
        def block(self, *_args):
            row.update(is_active=False, session_version=row['session_version']+1)
            return dict(row)
        def unblock(self, *_args):
            row['is_active']=True
            return dict(row)
    class Sessions(dict):
        def setex(self, key, ttl, value): self[key]=value
    service=AuthService(Repo(), 'redis://localhost:1', 'unit-test-secret-that-is-at-least-32-characters')
    service.redis=Sessions()
    old=service.sign_in('tester','Test-password-2026!')['accessToken']
    service.block_staff({'id':ADMIN,'role':'ADMIN'},user_id,'block')
    service.unblock_staff({'id':ADMIN,'role':'ADMIN'},user_id,'unblock')
    with pytest.raises(AuthFailure): service.validate(old)
    fresh=service.sign_in('tester','Test-password-2026!')['accessToken']
    assert service.validate(fresh)[0]['is_active']


def test_admin_transaction_reaches_validation_instead_of_forbidden():
    service=TransactionService(None)
    actor=SimpleNamespace(role='ADMIN',is_active=True)
    with pytest.raises(Exception) as failure:
        service.register_transaction(actor, {'accountId':'invalid'})
    assert getattr(failure.value,'code',None)=='BAD_INPUT'


def test_admin_sees_blocked_staff_and_unblock_button():
    def post(_url, json, **_kwargs):
        query=json['query']
        data=({'me':{'id':ADMIN,'login':'admin','role':'ADMIN'}} if 'me{' in query else
              {'staffUsers':[{'id':'other','login':'blocked-user','role':'ANALYST','isActive':False}]} if 'staffUsers' in query else {'sources':[]})
        return Mock(status_code=200,json=lambda:{'data':data})
    with patch('requests.post',side_effect=post):
        app=AppTest.from_file(str(ROOT/'frontend/app.py'),default_timeout=15)
        app.session_state.token='test-token';app.run()
        app.sidebar.radio[0].set_value('Сотрудники').run()
        assert not app.exception
        assert any(b.label=='Разблокировать' for b in app.button)
        assert not any(b.label=='Заблокировать' for b in app.button)


def test_admin_entry_form_uses_selected_account_and_reuses_retry_key():
    sent=[]
    def post(_url, json, **_kwargs):
        query=json['query']
        if 'me{' in query:data={'me':{'id':ADMIN,'login':'admin','role':'ADMIN'}}
        elif 'clients(' in query:data={'clients':{'items':[{'id':'client','displayName':'Клиент','accounts':[{'id':'account','externalId':'ACCOUNT-001','currency':'BYN','status':'OPEN'}]}]}}
        elif 'counterparties' in query:data={'counterparties':[]}
        elif 'registerTransaction' in query:
            sent.append(json['variables']['x'])
            data={'registerTransaction':{'id':'tx','externalId':'UI-TEST','amount':'23.45','currency':'BYN'}}
        elif 'transactions(' in query:data={'transactions':{'items':[]}}
        else:data={'sources':[]}
        return Mock(status_code=200,json=lambda:{'data':data})
    with patch('requests.post',side_effect=post):
        app=AppTest.from_file(str(ROOT/'frontend/app.py'),default_timeout=15)
        app.session_state.token='test-token';app.run()
        app.sidebar.radio[0].set_value('Ввод транзакций').run()
        assert not app.exception
        next(x for x in app.text_input if x.label=='Сумма').set_value('23.45')
        next(x for x in app.text_input if x.label=='Номер операции во внешней системе').set_value('UI-TEST')
        next(x for x in app.button if x.label=='Зарегистрировать транзакцию').click().run()
        assert not app.exception
        next(x for x in app.button if x.label=='Зарегистрировать транзакцию').click().run()
        assert len(sent)==2 and sent[0]==sent[1]
        assert sent[0]['accountId']=='account' and sent[0]['currency']=='BYN'
        assert sent[0]['occurredAt'].endswith('+00:00')
