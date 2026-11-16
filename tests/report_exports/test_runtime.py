"""Actual Case RPC file generation from persisted reports; no collector processes."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from io import BytesIO
import os,json,base64,copy
import psycopg
from psycopg.rows import dict_row
import pytest
from pypdf import PdfReader
from backend.infrastructure.clients import RemoteClient
from backend.common.data import DomainError
from backend.common.report_contract import sign,canonical


def test_independent_case_exports_all_types_and_denies_after_reassignment(storage_stand,monkeypatch):
    filename=os.getenv('REPORT_EXPORT_RUNTIME')
    if not filename:pytest.skip('Explicit Case-only audit runtime required')
    r=json.loads(Path(filename).read_text());monkeypatch.setenv('SERVICE_TOKEN',r['token']);monkeypatch.setenv('REPORT_SIGNING_KEY',r['reportSigningKey'])
    params={k:storage_stand[k] for k in ['host','port','user','password']}
    def connection():return psycopg.connect(**params,dbname=r['databases']['case'],row_factory=dict_row)
    manager=SimpleNamespace(id=str(uuid4()),login='export_manager',role='MANAGER',is_active=True)
    analyst=SimpleNamespace(id=str(uuid4()),login='export_analyst',role='ANALYST',is_active=True)
    m=RemoteClient('case',manager,address='127.0.0.1:'+str(r['casePort']))
    a=RemoteClient('case',analyst,address='127.0.0.1:'+str(r['casePort']))
    with connection() as c:
        rows=c.execute("SELECT DISTINCT ON(snapshot->>'reportType') id,snapshot FROM reports WHERE snapshot->>'reportSchemaVersion'='1' ORDER BY snapshot->>'reportType',created_at").fetchall()
    assert {v['snapshot']['reportType'] for v in rows}=={'PROFILE','OPERATION','PERIOD'}
    for row in rows:
        saved=m._invoke('get_snapshot_report',dict(id=str(row['id'])))
        for format in ['PDF','XLSX','CSV']:
            args=dict(id=str(row['id']),format=format)
            result=m._invoke('export_snapshot_report',args);data=base64.b64decode(result['contentBase64'])
            assert result['snapshotHash']==saved['snapshotHash']
            assert result==m._invoke('export_snapshot_report',args)
            if format=='PDF':assert PdfReader(BytesIO(data)).attachments['snapshot.json'][0]==canonical(saved['snapshot'])
    source=next(x['snapshot'] for x in rows if x['snapshot']['reportType']=='OPERATION')
    snapshot=copy.deepcopy(source);snapshot['author']=dict(id=analyst.id,login=analyst.login,role=analyst.role)
    request=dict(snapshot['filters'],idempotencyKey='runtime-export-analyst')
    with connection() as c:c.execute('UPDATE review_cases SET assignee_id=%s WHERE transaction_id=%s',(analyst.id,request['transactionId']))
    saved=a._invoke('store_snapshot_report',dict(input=request,snapshot=snapshot,signature=sign(analyst,request,snapshot)))
    assert a._invoke('export_snapshot_report',dict(id=saved['id'],format='XLSX'))['contentBase64']
    with connection() as c:c.execute('UPDATE review_cases SET assignee_id=%s WHERE transaction_id=%s',(str(uuid4()),request['transactionId']))
    for format in ['PDF','XLSX','CSV']:
        with pytest.raises(DomainError) as error:a._invoke('export_snapshot_report',dict(id=saved['id'],format=format))
        assert error.value.code=='FORBIDDEN'
