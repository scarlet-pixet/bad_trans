"""Actual independent RPC owners, persistence, signing and outage recovery reads."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, UTC, timedelta
from uuid import uuid4
import subprocess

import psycopg
from psycopg.rows import dict_row
import pytest
from backend.infrastructure.clients import RemoteClient
from backend.common.data import DomainError
from backend.gateway.reporting import create_snapshot


def test_real_owner_reports_and_dependency_outage(storage_stand,monkeypatch):
    filename=os.getenv('REPORT_RUNTIME')
    if not filename:pytest.skip('Explicit isolated report runtime required')
    r=json.loads(Path(filename).read_text())
    monkeypatch.setenv('SERVICE_TOKEN',r['token']);monkeypatch.setenv('REPORT_SIGNING_KEY',r['reportSigningKey'])
    manager=SimpleNamespace(id=str(uuid4()),login='report_manager',role='MANAGER',is_active=True)
    analyst=SimpleNamespace(id=str(uuid4()),login='report_analyst',role='ANALYST',is_active=True)
    def clients(actor):return SimpleNamespace(**{k:RemoteClient(k,actor,address='127.0.0.1:'+str(r[k+'Port'])) for k in ['transaction','analysis','case']})
    params={k:storage_stand[k] for k in ['host','port','user','password']}
    def connection(owner):return psycopg.connect(**params,dbname=r['databases'][owner],row_factory=dict_row)
    client,account,subject=[str(uuid4()) for _ in range(3)];t=datetime.now(UTC)-timedelta(minutes=1)
    with connection('transaction') as c:
        c.execute("INSERT INTO bank_clients VALUES (%s,%s,'Report RPC demo',NULL,true)",(client,client))
        c.execute("INSERT INTO accounts VALUES (%s,%s,%s,'BYN','OPEN')",(account,client,account))
        for i in range(21):
            txid=subject if i==0 else str(uuid4())
            c.execute("INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at) VALUES (%s,'report-rpc',%s,%s,%s,'BYN','OUT',%s,%s)",(txid,txid,account,'1000.00' if i==0 else '100.00',t-timedelta(days=i),t-timedelta(seconds=1)))
    m=clients(manager)
    m.analysis._invoke('analyze_transaction',dict(transactionId=subject,idempotencyKey='report-analyze'))
    context=m.analysis._invoke('get_detection_context',dict(transactionId=subject))
    result=context['resultId'];pv=context['profiles'][1]['id']
    request=dict(reportType='PROFILE',profileVersionId=pv,idempotencyKey='rpc-profile')
    profile=create_snapshot(m,manager,request)
    assert profile['snapshot']['data']['profile']['metadata']['id']==pv
    op_request=dict(reportType='OPERATION',transactionId=subject,resultId=result,idempotencyKey='rpc-operation')
    operation=create_snapshot(m,manager,op_request)
    assert len(operation['snapshot']['data']['profiles'])==3 and len(operation['snapshot']['data']['assessment']['checks'])==5
    captured_case = operation["snapshot"]["data"]["review"]["case"]
    assert captured_case is None or captured_case["transaction_id"] == subject
    period=dict(reportType='PERIOD',clientId=client,period={'from':(t-timedelta(days=30)).isoformat(),'to':(t+timedelta(days=1)).isoformat()},idempotencyKey='rpc-period')
    report=create_snapshot(m,manager,period)
    assert report['snapshot']['data']['transactions']['transactionCount']==21
    assert report['snapshot']['data']['analysis']['resultCount']==1
    with pytest.raises(DomainError) as forged:m.case._invoke('store_snapshot_report',dict(input=request,snapshot=profile['snapshot'],signature='0'*64))
    assert forged.value.code=='FORBIDDEN'
    # Scope after assignment is tested through actual owner callbacks, not a mocked Case grant.
    caseid=str(uuid4())
    with connection('case') as c:
        caseid=str(c.execute("INSERT INTO review_cases(id,transaction_id,client_id,initial_result_id,latest_result_id,latest_score,latest_calculated_at,status,priority,assignee_id,review_cycle,row_version,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'ASSIGNED','HIGH',%s,1,1,now(),now()) ON CONFLICT(transaction_id) DO UPDATE SET assignee_id=EXCLUDED.assignee_id,status='ASSIGNED' RETURNING id",(caseid,subject,client,result,result,context['score'],t,analyst.id)).fetchone()['id'])
    a=clients(analyst);own=create_snapshot(a,analyst,dict(op_request,idempotencyKey='analyst-operation'))
    with connection('case') as c:c.execute('UPDATE review_cases SET assignee_id=%s WHERE id=%s',(str(uuid4()),caseid))
    for name in ['get_snapshot_report','get_report','export_report']:
        with pytest.raises(DomainError) as denied:a.case._invoke(name,dict(id=own['id']))
        assert denied.value.code=='FORBIDDEN'
    with pytest.raises(DomainError):create_snapshot(a,analyst,dict(op_request,idempotencyKey='analyst-operation'))
    artifacts=os.getenv('REPORT_ARTIFACTS')
    if artifacts:
        output=Path(artifacts);output.mkdir(exist_ok=True)
        for name,value in [('profile',profile),('operation',operation),('period',report)]:
            (output/(name+'-rpc-snapshot.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    # Stop exactly this audit Analysis process; stored reads and receipt replay survive.
    wrapper=Path(r['processes']['analysis']['wrapper']).resolve();assert wrapper.is_relative_to(Path(__file__).resolve().parents[2]/'.local')
    env=os.environ.copy();env['AUDIT_OWNER_WRAPPER']=str(wrapper)
    command='$p=Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" | Where-Object {$_.CommandLine -and $_.CommandLine.Contains($env:AUDIT_OWNER_WRAPPER)}; $p | ForEach-Object {Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue}'
    subprocess.run(['powershell','-NoProfile','-Command',command],env=env,capture_output=True,check=True,timeout=12)
    assert create_snapshot(m,manager,request)==profile
    assert m.case._invoke('get_snapshot_report',dict(id=operation['id']))==operation
    with pytest.raises(DomainError) as outage:create_snapshot(m,manager,dict(request,idempotencyKey='new-after-stop'))
    assert outage.value.code=='DEPENDENCY_UNAVAILABLE'
