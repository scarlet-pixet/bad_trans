"""Real owner-service checks, using only newly created disposable records."""
import json
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
import requests

API = os.getenv('ADMIN_FIX_API') or os.getenv('PRODUCT_API')
pytestmark = pytest.mark.skipif(not API, reason='Set ADMIN_FIX_API for live administrator checks')


def test_unblock_and_admin_data_entry_across_real_services():
    def gql(query, variables=None, token=None, status=200):
        response=requests.post(API,json={'query':query,'variables':variables or {}},headers={'Authorization':'Bearer '+token} if token else {},timeout=30)
        assert response.status_code==status,response.text[:400]
        value=response.json()
        if status==200: assert not value.get('errors'),value.get('errors')
        return value.get('data',value)
    def login(name,password='BadTrans-demo-2026!'):
        return gql('mutation($l:String!,$p:String!){signIn(login:$l,password:$p){accessToken}}',{'l':name,'p':password})['signIn']['accessToken']
    admin,manager=login('admin'),login('manager')
    suffix=uuid4().hex[:12];name='unlock_'+suffix;password='Test-unlock-'+suffix+'!'
    person=gql('mutation($x:StaffInput!){registerStaff(input:$x){id login isActive}}',{'x':{'login':name,'password':password,'roleCode':'ANALYST','idempotencyKey':uuid4().hex}},admin)['registerStaff']
    old=login(name,password)
    args={'id':person['id'],'k':uuid4().hex}
    block='mutation($id:ID!,$k:String!){blockStaff(userId:$id,idempotencyKey:$k){id isActive}}'
    unblock='mutation($id:ID!,$k:String!){unblockStaff(userId:$id,idempotencyKey:$k){id isActive}}'
    assert not gql(block,args,admin)['blockStaff']['isActive']
    assert not gql(block,args,admin)['blockStaff']['isActive']
    gql(unblock,{'id':person['id'],'k':uuid4().hex},manager,status=403)
    args={'id':person['id'],'k':uuid4().hex}
    assert gql(unblock,args,admin)['unblockStaff']['isActive']
    assert gql(unblock,args,admin)['unblockStaff']['isActive']
    gql('query{me{login}}',token=old,status=401)
    fresh=login(name,password)
    assert gql('query{me{login}}',token=fresh)['me']['login']==name
    client=gql('mutation($x:ClientInput!){createClient(input:$x){id}}',{'x':{'externalId':'ADMIN-'+suffix,'displayName':'Проверка администратора '+suffix,'idempotencyKey':uuid4().hex}},admin)['createClient']
    account=gql('mutation($x:AccountInput!){openAccount(input:$x){id currency}}',{'x':{'clientId':client['id'],'externalId':'ADMIN-ACC-'+suffix,'currency':'BYN','idempotencyKey':uuid4().hex}},admin)['openAccount']
    counterparties=gql('query{counterparties}',token=admin)['counterparties']
    assert counterparties
    payload={'source':'admin-live-test','externalId':suffix,'accountId':account['id'],'counterpartyId':counterparties[0]['id'],'amount':'12.34','currency':'BYN','direction':'OUT','occurredAt':'2026-11-05T00:00:00Z','idempotencyKey':uuid4().hex}
    register='mutation($x:TransactionInput!){registerTransaction(input:$x){id amount currency}}'
    transaction=gql(register,{'x':payload},admin)['registerTransaction']
    assert gql(register,{'x':payload},admin)['registerTransaction']['id']==transaction['id']
    assert gql('query($id:ID!){transaction(id:$id){id}}',{'id':transaction['id']},admin)['transaction']['id']==transaction['id']
    deadline=monotonic()+45;context=None
    while monotonic()<deadline:
        context=gql('query($id:ID!){detectionContext(transactionId:$id)}',{'id':transaction['id']},manager)['detectionContext']
        if context:break
        sleep(0.5)
    assert context is not None and len(context['profiles'])==3
    # Leave the temporary staff account blocked; never alter existing users.
    gql(block,{'id':person['id'],'k':uuid4().hex},admin)
    gql('query{me{login}}',token=fresh,status=401)
    out=Path(__file__).resolve().parents[2]/'artifacts/admin-fixes-20261105'
    out.mkdir(parents=True,exist_ok=True)
    (out/'live-proof.json').write_text(json.dumps({'temporaryLogin':name,'unblockIdempotent':True,'previousSessionRemainsRevoked':True,'newLoginWorks':True,'clientId':client['id'],'accountId':account['id'],'transaction':transaction,'automaticProfileCount':len(context['profiles']),'automaticAnalysisResult':context['resultId']},ensure_ascii=False,indent=2),encoding='utf-8')
    for token in (admin,manager):gql('mutation{signOut}',token=token)
