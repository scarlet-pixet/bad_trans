from datetime import datetime, UTC, timedelta
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
import json

import pytest
from psycopg.types.json import Jsonb

from backend.common.data import DomainError
from backend.common.report_contract import digest
from backend.gateway.reporting import create_snapshot
from backend.infrastructure.product_migrations import migrate
from backend.services.transaction.reports.slices import ReportSlices as Transaction
from backend.services.analysis.reports.slices import ReportSlices as Analysis
from backend.services.case.reports.snapshots import ReportSnapshots as Case
from backend.services.analysis.profiles.storage import HistoryBatch, save_profile_versions


def actor(role="MANAGER"):
    return SimpleNamespace(id=str(uuid4()),login=role.lower(),role=role,is_active=True)


@pytest.fixture
def system(database, monkeypatch):
    dbs={k:database(k) for k in ["transaction","analysis","case"]}
    for k,db in dbs.items():migrate(db,k)
    monkeypatch.setenv("REPORT_SIGNING_KEY","test-only-report-signing-key-long-enough")
    manager,analyst=actor(),actor("ANALYST")
    owners={"transaction":Transaction(dbs["transaction"]),"analysis":Analysis(dbs["analysis"]),"case":Case(dbs["case"])}
    class Client:
        def __init__(self,owner,user):self.owner,self.user=owner,user
        def _invoke(self,name,args):
            if name == "authorize_transaction":
                from backend.services.case.reports.metrics import CaseMetrics
                return CaseMetrics(dbs['case']).authorize_transaction(self.user,**args)
            return getattr(owners[self.owner],name)(self.user,**args)
    def clients(user):return SimpleNamespace(**{k:Client(k,user) for k in owners})
    monkeypatch.setattr("backend.infrastructure.workspace_access.RemoteClient",lambda owner,user,address=None:Client(owner,user))
    ids=[str(uuid4()) for _ in range(4)];clients_ids=[str(uuid4()),str(uuid4())]
    t=datetime(2026,11,8,12,tzinfo=UTC)
    with dbs["transaction"].connect() as c:
        for cid in clients_ids:c.execute("INSERT INTO bank_clients VALUES (%s,%s,'Report client',NULL,true)",(cid,cid))
        for i,(cid,cur,direct,amount) in enumerate([(clients_ids[0],'BYN','OUT','10.00'),(clients_ids[0],'BYN','OUT','20.00'),(clients_ids[0],'USD','IN','7.00'),(clients_ids[1],'BYN','OUT','5.00')]):
            aid=str(uuid4());c.execute("INSERT INTO accounts VALUES (%s,%s,%s,%s,'OPEN')",(aid,cid,aid,cur))
            c.execute("INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at) VALUES (%s,'report-test',%s,%s,%s,%s,%s,%s,%s)",(ids[i],ids[i],aid,amount,cur,direct,t,t))
    run,result=str(uuid4()),str(uuid4())
    with dbs['analysis'].connect() as c:
        c.execute("INSERT INTO analysis_runs VALUES (%s,'AUTO','{}',%s,'COMPLETED',NULL,'{}',%s,%s)",(run,t,t,t))
        for r,txid,delta,score in [(str(uuid4()),ids[0],-1,20),(result,ids[0],0,70),(str(uuid4()),ids[1],0,0)]:
            c.execute("INSERT INTO analysis_results(id,transaction_id,run_id,profile_id,score,threshold,data_status,missing_reasons,calculated_at) VALUES (%s,%s,%s,NULL,%s,60,'SUFFICIENT','[]',%s)",(r,txid,run if txid!=ids[0] or delta==0 else new_run(c,t),score,t+timedelta(seconds=delta)))
        subject=dict(id=ids[3],clientId=clients_ids[1],currency='BYN',direction='OUT')
        rows=[dict(id=str(uuid4()),clientId=clients_ids[1],currency='BYN',direction='OUT',amount='10.00',counterpartyId=None,occurredAt=(t-timedelta(days=1)).isoformat(),ingestedAt=t.isoformat())]
        history=HistoryBatch(rows,dict(snapshotId=str(uuid4()),digest=digest(rows),itemCount=1,capturedAt=t.isoformat(),selection=dict(requestKey='report-test',clientId=clients_ids[1],currency='BYN',direction='OUT',windowStart=(t-timedelta(days=90)).isoformat(),asOf=t.isoformat(),ingestedBefore=t.isoformat()),coverage=dict(mode='UNKNOWN',revision='unknown-v1',intervals=[],declaredBySource=None)))
        versions=save_profile_versions(c,subject,history,t,t)
    caseid=str(uuid4())
    with dbs['case'].connect() as c:
        c.execute("INSERT INTO review_cases(id,transaction_id,client_id,initial_result_id,latest_result_id,latest_score,latest_calculated_at,status,priority,assignee_id,review_cycle,row_version,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,70,%s,'ASSIGNED','HIGH',%s,1,1,%s,%s)",(caseid,ids[0],clients_ids[0],result,result,t,analyst.id,t,t))
        c.execute("INSERT INTO case_comments VALUES (%s,%s,%s,'COMMENT','Report comment',%s)",(str(uuid4()),caseid,analyst.id,t))
    request=dict(reportType='PERIOD',period={'from':'2026-11-08T00:00:00Z','to':'2026-11-09T00:00:00Z'},idempotencyKey='period-one')
    return SimpleNamespace(dbs=dbs,owners=owners,manager=manager,analyst=analyst,clients=clients,ids=ids,clientIds=clients_ids,result=result,caseid=caseid,request=request,versions=versions,t=t)


def new_run(c,t):
    rid=str(uuid4());c.execute("INSERT INTO analysis_runs VALUES (%s,'AUTO','{}',%s,'COMPLETED',NULL,'{}',%s,%s)",(rid,t,t,t));return rid


def test_period_manual_counts_and_immutable_replay(system):
    s=system
    saved=create_snapshot(s.clients(s.manager),s.manager,s.request)
    tx=saved['snapshot']['data']['transactions'];analysis=saved['snapshot']['data']['analysis']
    assert tx['transactionCount']==4 and tx['uniqueClients']==2
    assert tx['groups']==[dict(currency='BYN',direction='OUT',transactionCount=3,amount='35.00',uniqueClients=2),dict(currency='USD',direction='IN',transactionCount=1,amount='7.00',uniqueClients=1)]
    assert analysis['denominator']==4 and analysis['resultCount']==2 and analysis['statuses']['NO_RESULT']==2
    assert analysis['usableAssessmentCount']==2 and analysis['resultCoverage']==0.5
    assert len(analysis['resultRefs'])==2 and next(r for r in analysis['resultRefs'] if str(r['transaction_id'])==s.ids[0])['score']==70
    assert analysis['profileGroupDenominator']==3 and analysis['profiledGroups']==1
    with s.dbs['analysis'].connect() as c:
        run=new_run(c,s.t);c.execute("INSERT INTO analysis_results(id,transaction_id,run_id,score,threshold,data_status,missing_reasons,calculated_at) VALUES (%s,%s,%s,0,60,'SUFFICIENT','[]',%s)",(str(uuid4()),s.ids[0],run,s.t+timedelta(hours=1)))
    replay_clients=s.clients(s.manager)
    with patch.object(replay_clients.analysis,'_invoke',side_effect=AssertionError('should not collect')):
        replay=create_snapshot(replay_clients,s.manager,s.request)
    assert replay==saved
    assert s.owners['case'].get_snapshot_report(s.manager,saved['id'])==saved
    fresh=create_snapshot(s.clients(s.manager),s.manager,dict(s.request,idempotencyKey='period-two'))
    assert fresh['snapshotHash']!=saved['snapshotHash']
    with pytest.raises(DomainError) as error:create_snapshot(s.clients(s.manager),s.manager,dict(s.request,currency='BYN'))
    assert error.value.code=='CONFLICT'


def test_profile_without_case_and_operation_revocation_on_all_reads(system):
    s=system
    profile=create_snapshot(s.clients(s.manager),s.manager,dict(reportType='PROFILE',profileVersionId=s.versions[1]['id'],idempotencyKey='profile'))
    assert profile['snapshot']['data']['profile']['metadata']['id']==s.versions[1]['id']
    request=dict(reportType='OPERATION',transactionId=s.ids[0],resultId=s.result,idempotencyKey='operation')
    saved=create_snapshot(s.clients(s.analyst),s.analyst,request)
    assert saved['snapshot']['data']['assessment']['score']==70
    assert saved['snapshot']['data']['review']['comments'][0]['text']=='Report comment'
    from backend.services.case.lifecycle.service import CaseService
    from backend.services.case.persistence.repository import CaseRepository
    old=CaseService(CaseRepository(s.dbs['case']),SimpleNamespace(get_current_user=lambda:s.analyst))
    first_export=old.export_report(saved['id'])
    assert old.export_report(saved['id'])==first_export
    with s.dbs['case'].connect() as c:c.execute("UPDATE review_cases SET assignee_id=%s WHERE id=%s",(str(uuid4()),s.caseid))
    for action in [lambda:s.owners['case'].get_snapshot_report(s.analyst,saved['id']),lambda:create_snapshot(s.clients(s.analyst),s.analyst,request),lambda:old.get_report(saved['id']),lambda:old.export_report(saved['id'])]:
        with pytest.raises(DomainError) as error:action()
        assert error.value.code=='FORBIDDEN'
    assert not s.owners['case'].list_snapshot_reports(s.analyst)['items']
    assert s.owners['case'].get_snapshot_report(s.manager,saved['id'])==saved


def test_bad_signature_outage_and_role_do_not_store(system):
    s=system
    with pytest.raises(DomainError):s.owners['case'].store_snapshot_report(s.manager,s.request,{},'forged')
    clients=s.clients(s.manager)
    with patch.object(clients.analysis,'_invoke',side_effect=DomainError('DEPENDENCY_UNAVAILABLE','test outage')):
        with pytest.raises(DomainError):create_snapshot(clients,s.manager,s.request)
    assert not s.owners['case'].list_snapshot_reports(s.manager)['items']
    with pytest.raises(DomainError):create_snapshot(s.clients(s.analyst),s.analyst,s.request)


def test_empty_cohort_has_unknown_ratios_and_half_open_period(system):
    s=system
    at=(s.t+timedelta(hours=1)).isoformat()
    request=dict(s.request,period={'from':'2020-01-01T00:00:00Z','to':'2020-01-02T00:00:00Z'})
    part=s.owners['transaction'].report_transactions_part(s.manager,request,at)
    assert part['data']['transactionCount']==0 and part['data']['groups']==[]
    a=s.owners['analysis'].report_analysis_part(s.manager,[],30,request['period'],at)['data']
    assert a['denominator']==0 and a['resultCoverage'] is None and a['usableAssessmentCoverage'] is None
    period={'from':s.t.isoformat(),'to':(s.t+timedelta(seconds=1)).isoformat()}
    assert s.owners['transaction'].report_transactions_part(s.manager,dict(s.request,period=period),at)['data']['transactionCount']==4
    period={'from':(s.t-timedelta(seconds=1)).isoformat(),'to':s.t.isoformat()}
    assert s.owners['transaction'].report_transactions_part(s.manager,dict(s.request,period=period),at)['data']['transactionCount']==0


def test_forged_valid_shape_and_foreign_result_cannot_be_saved(system):
    s=system
    saved=create_snapshot(s.clients(s.manager),s.manager,s.request)
    from backend.common.report_contract import sign
    edited=json.loads(json.dumps(saved['snapshot']));edited['data']['transactions']['transactionCount']=999
    with pytest.raises(DomainError):s.owners['case'].store_snapshot_report(s.manager,s.request,edited,'f'*64)
    # Even a valid signed shape must bind exact selectors and author.
    edited['filters']['currency']='USD'
    with pytest.raises(DomainError):s.owners['case'].store_snapshot_report(s.manager,s.request,edited,sign(s.manager,s.request,edited))
    with pytest.raises(DomainError):create_snapshot(s.clients(s.manager),s.manager,dict(reportType='OPERATION',transactionId=s.ids[3],resultId=s.result,idempotencyKey='wrong-result'))


def test_cohort_limit_is_explicit_not_silent_truncation(system):
    s=system
    with s.dbs['transaction'].connect() as c:
        account=c.execute("SELECT account_id FROM transactions WHERE id=%s",(s.ids[0],)).fetchone()['account_id']
        with c.cursor() as cur:cur.executemany("INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at) VALUES (%s,'report-cap',%s,%s,1,'BYN','OUT',%s,%s)",[(str(uuid4()),str(uuid4()),account,s.t,s.t) for _ in range(2001)])
    with pytest.raises(DomainError) as error:create_snapshot(s.clients(s.manager),s.manager,s.request)
    assert error.value.code=='BAD_INPUT' and '2000' in str(error.value)
    assert not s.owners['case'].list_snapshot_reports(s.manager)['items']
