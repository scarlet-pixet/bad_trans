from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from backend.common.data import DomainError
from backend.infrastructure.product_migrations import migrate
from backend.services.analysis.profiles.bulk import ProfileRuns
from backend.services.analysis.profiles.refresh import ProfileRefresh
from backend.services.analysis.profiles.service import ProfileService
from backend.services.transaction.history.profile_selection import ProfileSelection
from backend.services.transaction.history.snapshots import HistorySnapshots


def actor(role="MANAGER"):
    return SimpleNamespace(id=str(uuid4()),role=role,is_active=True)


def seed(db, count=2):
    end=datetime.now(UTC).replace(microsecond=0)-timedelta(days=1)
    start=end-timedelta(days=3)
    clients=[]
    with db.connect() as c:
        for i in range(count):
            client,account=str(uuid4()),str(uuid4());clients.append(client)
            c.execute("INSERT INTO bank_clients VALUES (%s,%s,%s,'bulk-test',true)",(client,client,'Bulk client'))
            c.execute("INSERT INTO accounts VALUES (%s,%s,%s,'BYN','OPEN')",(account,client,account))
            c.execute("INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at) VALUES (%s,'bulk-test',%s,%s,100,'BYN','OUT',%s,%s)",(str(uuid4()),str(uuid4()),account,start,start))
        c.execute("INSERT INTO accounts VALUES (%s,%s,%s,'USD','OPEN')",(str(uuid4()),clients[0],str(uuid4())))
        account=c.execute("SELECT id FROM accounts WHERE client_id=%s AND currency='USD'",(clients[0],)).fetchone()['id']
        c.execute("INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at) VALUES (%s,'bulk-test',%s,%s,20,'USD','IN',%s,%s)",(str(uuid4()),str(uuid4()),account,start,start))
    return dict(period={'from':start.isoformat(),'to':end.isoformat()},segment='bulk-test'),clients


class Owner:
    def __init__(self, db):
        self.db=db;self.pages=[];self.fail=False
    def _invoke(self, method, arguments):
        if self.fail:raise DomainError('DEPENDENCY_UNAVAILABLE','offline')
        selection=ProfileSelection(self.db)
        if method=='prepare_profile_groups':return selection.prepare(actor('SYSTEM'),arguments['parameters'])
        self.pages.append(arguments['parameters']['offset'])
        return selection.page(actor('SYSTEM'),arguments['parameters'])
    def prepare_behavior_history(self,p):
        if self.fail:raise DomainError('DEPENDENCY_UNAVAILABLE','offline')
        return HistorySnapshots(self.db).prepare(p)
    def get_behavior_history_page(self,p):return HistorySnapshots(self.db).page(p)


@pytest.fixture
def owners(database):
    transaction=database('transaction');analysis=database('analysis')
    migrate(transaction,'transaction');migrate(analysis,'analysis')
    return transaction,analysis,Owner(transaction)


def finish(service,a,identifier):
    for _ in range(20):
        value=service.get(a,identifier)
        if value['status'] in ('EMPTY','FAILED','PARTIAL','COMPLETED','COMPLETED_WITH_SKIPS'):return value
        service.tick(batch=100,budget_seconds=120)
    pytest.fail('Run did not finish')


def test_selection_over_first_hundred_is_frozen_and_pages_are_stable(owners):
    tx,db,client=owners;selection,ids=seed(tx,103);a=actor();service=ProfileRuns(db,client)
    run=service.start(a,selection,'many')
    service.tick();assert service.get(a,run['id'])['discoveredGroups']==100
    # A second page sees the same owner snapshot despite an additional matching client.
    seed(tx,1)
    service.tick();state=service.get(a,run['id'])
    assert state['status']=='RUNNING' and state['totalGroups']==104 and state['selectedClients']==103
    assert client.pages==[0,100]
    assert len(service.get(a,run['id'],100,100)['groups'])==4


def test_idempotency_profiles_manual_new_command_and_historical_current(owners):
    tx,db,client=owners;selection,ids=seed(tx);a=actor();service=ProfileRuns(db,client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:service.start(a,selection,'same'),range(2)))
    assert results[0]['id']==results[1]['id']
    with pytest.raises(DomainError) as e:service.start(a,dict(selection,currency='USD'),'same')
    assert e.value.code=='CONFLICT'
    completed=finish(service,a,results[0]['id'])
    assert completed['status']=='COMPLETED' and completed['completedGroups']==3
    assert completed['completedClients']==2
    assert all(len(g['versionIds'])==3 for g in completed['groups'])
    count=db.one('SELECT count(*) AS n FROM profile_versions')['n'];assert count==9
    replay=service.start(a,selection,'same');assert replay['id']==completed['id']
    service.tick();assert db.one('SELECT count(*) AS n FROM profile_versions')['n']==9
    newer=dict(selection,period=dict(selection['period'],to=(datetime.now(UTC)-timedelta(hours=1)).isoformat()))
    fresh=finish(service,a,service.start(a,newer,'newer')['id']);assert fresh['status']=='COMPLETED'
    historical=finish(service,a,service.start(a,selection,'explicit-new')['id']);assert historical['id']!=completed['id']
    assert db.one('SELECT count(*) AS n FROM profile_versions')['n']==count*3
    current=ProfileService(db,client).get_behavior_profiles(a,windowDays=30)
    assert {p['asOf'] for p in current if p['clientId'] in ids}=={newer['period']['to']}
    assert all(db.one('SELECT origin FROM profile_versions WHERE id=%s',(identifier,))['origin']=='MANUAL' for g in historical['groups'] for identifier in g['versionIds'])


def test_empty_filters_boundaries_and_late_ingestion(owners):
    tx,db,client=owners;selection,ids=seed(tx,1);a=actor();service=ProfileRuns(db,client)
    empty=service.start(a,dict(selection,currency='EUR'),'empty')
    assert finish(service,a,empty['id'])['status']=='EMPTY'
    run=service.start(a,selection,'with-late')
    with tx.connect() as c:
        account=c.execute("SELECT id FROM accounts WHERE client_id=%s AND currency='BYN'",(ids[0],)).fetchone()['id']
        for stamp,ingested,amount in [(selection['period']['to'],selection['period']['from'],999),
                (selection['period']['from'],datetime.now(UTC)+timedelta(seconds=1),888),
                ((datetime.fromisoformat(selection['period']['from'])-timedelta(days=2)).isoformat(),selection['period']['from'],50)]:
            c.execute("INSERT INTO transactions(id,source,external_id,account_id,amount,currency,direction,occurred_at,ingested_at) VALUES (%s,'bulk-test',%s,%s,%s,'BYN','OUT',%s,%s)",(str(uuid4()),str(uuid4()),account,amount,stamp,ingested))
    result=finish(service,a,run['id']);assert result['status']=='COMPLETED'
    group=next(g for g in result['groups'] if g['currency']=='BYN')
    # Earlier history participates, activity exactly at end and late ingestion do not.
    assert group['snapshot']['itemCount']==2
    version=db.one('SELECT metrics FROM profile_versions WHERE id=%s',(group['versionIds'][1],))
    assert version['metrics']['sampleSize']==2


def test_recovery_after_history_freeze_and_parallel_scheduled_calculation(owners,monkeypatch):
    tx,db,client=owners;selection,ids=seed(tx,1);a=actor();service=ProfileRuns(db,client)
    run=service.start(a,selection,'recover');service.tick()
    import backend.services.analysis.profiles.bulk as module
    original=module.save_profile_versions
    def crash(*args,**kwargs):raise KeyboardInterrupt('worker stopped')
    monkeypatch.setattr(module,'save_profile_versions',crash)
    with pytest.raises(KeyboardInterrupt):service.tick()
    assert db.one("SELECT count(*) AS n FROM profile_run_groups WHERE status='RUNNING' AND snapshot IS NOT NULL")['n']==1
    monkeypatch.setattr(module,'save_profile_versions',original)
    # Independent scheduled work uses the same group version-number locks.
    refresh=ProfileRefresh(db,client)
    with db.connect() as c:
        for currency,direction in [('BYN','OUT'),('USD','IN')]:
            c.execute('INSERT INTO profile_refresh_groups(client_id,currency,direction) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',(ids[0],currency,direction))
    refresh.enqueue(batch=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(refresh.drain,batch=2,budget_seconds=120),pool.submit(service.tick,batch=100,budget_seconds=120)]
        for j in jobs:j.result()
    result=finish(service,a,run['id']);assert result['status']=='COMPLETED'
    assert db.one("SELECT count(*) AS n FROM profile_calculations WHERE job_key LIKE %s",('bulk:%',))['n']==2
    assert db.one('SELECT count(*) AS n FROM profile_versions')['n']==12
    assert db.one('SELECT count(*) AS n FROM (SELECT client_id,currency,direction,window_days,version FROM profile_versions GROUP BY 1,2,3,4,5 HAVING count(*)>1) q')['n']==0


def test_one_bad_group_is_partial_and_owner_access_is_system_only(owners):
    tx,db,client=owners;selection,ids=seed(tx,1);a=actor();service=ProfileRuns(db,client)
    with pytest.raises(DomainError):ProfileSelection(tx).prepare(a,{})
    run=service.start(a,selection,'partial');service.tick()
    original=client.prepare_behavior_history
    def unavailable(p):
        if p['currency']=='USD':raise DomainError('BAD_INPUT','HISTORY_LIMIT')
        return original(p)
    client.prepare_behavior_history=unavailable
    result=finish(service,a,run['id'])
    assert result['status']=='PARTIAL' and result['completedGroups']==1 and result['failedGroups']==1
    assert next(g for g in result['groups'] if g['status']=='FAILED')['error']=='HISTORY_LIMIT'


def test_discovery_retry_exhaustion_and_policy_mismatch_are_not_complete_selections(owners):
    tx,db,client=owners;selection,_=seed(tx,1);a=actor();service=ProfileRuns(db,client)
    def execute(statement,params):
        with db.connect() as c:c.execute(statement,params)
    run=service.start(a,selection,'offline-recovery')
    client.fail=True;service.tick()
    pending=service.get(a,run['id'])
    assert pending['status']=='QUEUED' and not pending['selectionComplete']
    client.fail=False
    execute("UPDATE profile_runs SET available_at=now() WHERE id=%s",(run['id'],))
    assert finish(service,a,run['id'])['selectionComplete']
    failed=service.start(a,selection,'offline-exhausted');client.fail=True
    for _ in range(5):
        execute("UPDATE profile_runs SET available_at=now() WHERE id=%s",(failed['id'],))
        service.tick()
    result=service.get(a,failed['id'])
    assert result['status']=='FAILED' and not result['selectionComplete']
    assert result['error']=='DEPENDENCY_UNAVAILABLE' and result['completedAt']
    mismatch=service.start(a,selection,'old-policy')
    execute("UPDATE profile_runs SET context=jsonb_set(context,'{runPolicy}','\"obsolete\"') WHERE id=%s",(mismatch['id'],))
    service.tick()
    result=service.get(a,mismatch['id'])
    assert result['status']=='FAILED' and result['error']=='VERSION_MISMATCH'
    assert not result['selectionComplete']


def test_group_without_history_is_skipped_instead_of_silently_succeeding(owners):
    tx,db,client=owners;selection,_=seed(tx,1);a=actor();service=ProfileRuns(db,client)
    run=service.start(a,selection,'missing-history');service.tick()
    # Source deletion between selection and the first per-group snapshot is explicit.
    with tx.connect() as c:c.execute("DELETE FROM transactions WHERE currency='USD'")
    result=finish(service,a,run['id'])
    assert result['status']=='COMPLETED_WITH_SKIPS' and result['selectionComplete']
    assert result['completedGroups']==1 and result['skippedGroups']==1
    skipped=next(g for g in result['groups'] if g['status']=='SKIPPED')
    assert skipped['error']=='NO_HISTORY' and skipped['versionIds']==[]


def test_comparison_matches_saved_groups_and_windows_without_double_counting(owners):
    tx, db, client = owners
    selection, ids = seed(tx, 3)
    selection.pop("segment")
    user = actor()
    service = ProfileRuns(db, client)
    profiles = ProfileService(db, client)

    def run(key, ids, currency=None):
        command = service.start(user, dict(selection, clientIds=ids, currency=currency), key)
        return finish(service, user, command["id"])["id"]

    first = run("first", ids[:2])
    second = run("second", ids[1:])
    comparison = profiles.compare_profile_runs(user, first, second)
    expected = dict(first=2, second=2, common=1, onlyFirst=1, onlySecond=1)
    assert comparison["selected"] == comparison["successful"] == expected
    assert comparison["totalClients"] == 3
    common = next(item for item in comparison["clients"] if item["clientId"] == ids[1])
    versions = common["groups"][0]["pairs"]
    assert [pair["windowDays"] for pair in versions] == [7, 30, 90]
    exact = profiles.compare_profile_versions(user, versions[1]["firstId"], versions[1]["secondId"])
    assert exact["methodComparable"]
    assert exact["first"]["metadata"]["id"] == versions[1]["firstId"]
    paged = profiles.compare_profile_runs(user, first, second, offset=0, limit=1)
    assert paged["nextOffset"] == 1 and paged["selected"] == expected
    assert len(paged["clients"]) == 1

    third = run("disjoint", ids[:1])
    assert profiles.compare_profile_runs(user, third, second)["selected"]["common"] == 0
    with pytest.raises(DomainError) as error:
        profiles.compare_profile_runs(actor("ANALYST"), first, second)
    assert error.value.code == "FORBIDDEN"

    legacy = str(uuid4())
    with db.connect() as c:
        c.execute("""INSERT INTO analysis_runs(id,mode,scope,cutoff,status,counters,created_at)
                     VALUES (%s,'PERIOD','{}',now(),'COMPLETED','{}',now())""", (legacy,))
    comparison = profiles.compare_profile_runs(user, legacy, second)
    assert comparison["first"]["legacy"] and not comparison["first"]["selectionComplete"]
    assert comparison["second"]["selectionComplete"]


def test_comparison_keeps_failed_group_reason_and_distinguishes_coverage(owners):
    tx, db, client = owners
    selection, ids = seed(tx, 2)
    user = actor()
    service = ProfileRuns(db, client)
    profiles = ProfileService(db, client)
    first = finish(service, user, service.start(user, selection, "complete")["id"])
    prepare = client.prepare_behavior_history

    def reject_one_group(parameters):
        if parameters["clientId"] == ids[1]:
            raise DomainError("BAD_INPUT", "HISTORY_LIMIT")
        return prepare(parameters)

    client.prepare_behavior_history = reject_one_group
    second = finish(service, user, service.start(user, selection, "partial")["id"])
    comparison = profiles.compare_profile_runs(user, first["id"], second["id"])
    assert comparison["selected"]["common"] == 2
    assert comparison["successful"]["common"] == 1
    assert comparison["second"]["status"] == "PARTIAL"
    assert comparison["second"]["failedGroups"] == 1
    failed = next(item for item in comparison["clients"] if item["clientId"] == ids[1])["groups"][0]
    assert failed["second"]["error"] == "HISTORY_LIMIT"
    assert not failed["pairs"]
