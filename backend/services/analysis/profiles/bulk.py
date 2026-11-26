"""Durable manager-requested runs, with immutable owner inputs per group."""
import logging
import os
import time
from collections import Counter
from datetime import timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from backend.common.behavior_contract import digest
from backend.common.commands import require_role, required_text
from backend.common.data import DomainError, uuid
from backend.common.profile_run_contract import normalize_selection
from backend.services.analysis.feedback.events import capture
from backend.services.analysis.profiles.metrics import ALGORITHM, METRICS_SCHEMA_VERSION
from backend.services.analysis.profiles.observation import timestamp
from backend.services.analysis.profiles.storage import (
    HISTORY_POLICY, POLICY_HASH, prepare_snapshot, read_snapshot_history, save_profile_versions,
)

TERMINAL = ('EMPTY','COMPLETED','COMPLETED_WITH_SKIPS','PARTIAL','FAILED')


def policy():
    return dict(algorithmVersion=ALGORITHM,metricsSchemaVersion=METRICS_SCHEMA_VERSION,
                historyPolicyVersion=HISTORY_POLICY,historyPolicyHash=POLICY_HASH,
                runPolicy='ACTIVITY_INTERVAL_END-v1')


def failure_reason(exc):
    detail=str(exc).split(':',1)[0]
    return detail if detail in ('HISTORY_LIMIT','GROUP_LIMIT','VERSION_MISMATCH') else getattr(exc,'code',type(exc).__name__)


class ProfileRuns:
    def __init__(self, db, client):
        self.db, self.client = db, client

    def start(self, actor, selection, idempotency_key):
        require_role(actor,'MANAGER')
        p=normalize_selection(selection)
        key=required_text(idempotency_key,'idempotencyKey',128)
        fingerprint=digest(p)
        with self.db.connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                      ('profile-run-command:'+actor.id+':'+key,))
            old=c.execute('SELECT id,request_hash FROM profile_runs WHERE author_id=%s AND idempotency_key=%s',
                          (actor.id,key)).fetchone()
            if old:
                if old['request_hash']!=fingerprint:
                    raise DomainError('CONFLICT','Ключ команды использован с другим отбором')
                identifier=str(old['id'])
            else:
                ref=capture(c,'MANUAL_PROFILE')
                context=dict(policy(),asOf=p['period']['to'],ingestedBefore=ref['capturedAt'],
                             feedbackReference=ref,feedbackMeaning='KNOWLEDGE_AT_COMMAND')
                identifier=str(uuid4())
                c.execute('''INSERT INTO profile_runs(id,author_id,idempotency_key,request_hash,parameters,context,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                    (identifier,actor.id,key,fingerprint,Jsonb(p),Jsonb(context),ref['capturedAt']))
        return self.get(actor,identifier)

    @staticmethod
    def wire(row):
        return dict(id=str(row['id']),authorId=str(row['author_id']),status=row['status'],
                    selection=row['parameters'],context=row['context'],
                    selectionSnapshot=row['selection_snapshot'],createdAt=row['created_at'].isoformat(),
                    completedAt=row['completed_at'].isoformat() if row['completed_at'] else None,
                    error=row['last_error'])

    def get(self, actor, identifier, offset=0, limit=50):
        require_role(actor,'MANAGER')
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
            raise DomainError('BAD_INPUT','Некорректная страница групп')
        identifier=uuid(identifier)
        row=self.db.one('SELECT * FROM profile_runs WHERE id=%s',(identifier,))
        if not row:
            raise DomainError('NOT_FOUND','Запуск профилирования не найден')
        summary=self.db.one('''SELECT count(*)::int AS selected,
            count(DISTINCT client_id)::int AS clients,
            count(DISTINCT client_id) FILTER(WHERE status='COMPLETED')::int AS successful_clients,
            count(*) FILTER(WHERE status='COMPLETED')::int AS completed,
            count(*) FILTER(WHERE status='FAILED')::int AS failed,
            count(*) FILTER(WHERE status='SKIPPED')::int AS skipped
            FROM profile_run_groups WHERE run_id=%s''',(identifier,))
        groups=self.db.query('''SELECT * FROM profile_run_groups WHERE run_id=%s
            ORDER BY client_id,currency,direction LIMIT %s OFFSET %s''',(identifier,limit,offset))
        total=(row['selection_snapshot'] or {}).get('count',summary['selected'])
        return dict(self.wire(row),totalGroups=total,discoveredGroups=summary['selected'],
                    selectedClients=summary['clients'],completedClients=summary['successful_clients'],
                    completedGroups=summary['completed'],failedGroups=summary['failed'],
                    skippedGroups=summary['skipped'],selectionComplete=(row['selection_snapshot'] is not None
                        and row['next_offset']==row['selection_snapshot']['count']),
                    groups=[dict(clientId=str(g['client_id']),currency=g['currency'],direction=g['direction'],
                                 status=g['status'],attempts=g['attempts'],error=g['last_error'],
                                 versionIds=g['version_ids'],snapshot=g['snapshot']) for g in groups],
                    nextOffset=offset+len(groups) if offset+len(groups)<summary['selected'] else None)

    def list(self, actor, limit=20, offset=0):
        require_role(actor,'MANAGER')
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0:
            raise DomainError('BAD_INPUT','Некорректная страница запусков')
        return [self.wire(r) for r in self.db.query(
            'SELECT * FROM profile_runs ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s',(limit,offset))]

    def discover(self, c, run):
        snapshot=run['selection_snapshot']
        if snapshot is None:
            snapshot=self.client._invoke('prepare_profile_groups',dict(parameters=dict(
                requestKey='profile-run:'+str(run['id']),selection=run['parameters'],
                ingestedBefore=run['context']['ingestedBefore'])))
            if (snapshot.get('parameters') != dict(selection=run['parameters'],ingestedBefore=run['context']['ingestedBefore'])
                    or type(snapshot.get('count')) is not int or not 0<=snapshot['count']<=10000):
                raise DomainError('DATA_ACCESS_ERROR','Некорректный контекст отбора владельца')
            uuid(snapshot['id'])
            c.execute("UPDATE profile_runs SET selection_snapshot=%s,status='DISCOVERING' WHERE id=%s",
                      (Jsonb(snapshot),run['id']))
        page=self.client._invoke('profile_group_page',dict(parameters=dict(id=snapshot['id'],offset=run['next_offset'],limit=100)))
        if (page.get('id'),page.get('digest'),page.get('count')) != (snapshot['id'],snapshot['digest'],snapshot['count']):
            raise DomainError('DATA_ACCESS_ERROR','Страница другого снимка отбора')
        items=page.get('items')
        if not isinstance(items,list) or len(items)>100 or (not items and snapshot['count']>run['next_offset']):
            raise DomainError('DATA_ACCESS_ERROR','Некорректная страница отбора')
        offset=run['next_offset']+len(items)
        expected=offset if offset<snapshot['count'] else None
        if page.get('nextOffset')!=expected or offset>snapshot['count']:
            raise DomainError('DATA_ACCESS_ERROR','Отбор не продвинулся')
        with c.transaction():
            for group in items:
                normalized=normalize_selection(dict(period=run['parameters']['period'],clientIds=[group['clientId']],
                    currency=group['currency'],direction=group['direction']))
                c.execute('''INSERT INTO profile_run_groups(run_id,client_id,currency,direction)
                    VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (run['id'],normalized['clientIds'][0],normalized['currency'],normalized['direction']))
            if expected is None:
                selected=[dict(clientId=str(r['client_id']),currency=r['currency'],direction=r['direction'])
                          for r in c.execute('SELECT client_id,currency,direction FROM profile_run_groups WHERE run_id=%s ORDER BY client_id,currency,direction',(run['id'],))]
                if len(selected)!=snapshot['count'] or digest(selected)!=snapshot['digest']:
                    raise DomainError('DATA_ACCESS_ERROR','Состав отбора не соответствует снимку')
            c.execute('''UPDATE profile_runs SET next_offset=%s,status=%s,discovery_attempts=0,last_error=NULL,
                completed_at=CASE WHEN %s THEN now() ELSE NULL END WHERE id=%s''',
                (offset,'DISCOVERING' if expected is not None else 'RUNNING' if offset else 'EMPTY',not offset,run['id']))

    def group(self, c, run, g, deadline):
        key=(run['id'],g['client_id'],g['currency'],g['direction'])
        where='run_id=%s AND client_id=%s AND currency=%s AND direction=%s'
        c.execute(f"UPDATE profile_run_groups SET status='RUNNING',attempts=attempts+1 WHERE {where}",key)
        try:
            tx=dict(clientId=str(g['client_id']),currency=g['currency'],direction=g['direction'])
            cutoff,watermark=timestamp(run['context']['asOf']),timestamp(run['context']['ingestedBefore'])
            snapshot=g['snapshot']
            if snapshot is None:
                snapshot=prepare_snapshot(self.client,tx,cutoff,watermark)
                c.execute(f'UPDATE profile_run_groups SET snapshot=%s WHERE {where}',(Jsonb(snapshot),*key))
            rows=read_snapshot_history(self.client,snapshot,deadline=deadline)
            if not rows:
                c.execute(f"UPDATE profile_run_groups SET status='SKIPPED',last_error='NO_HISTORY',completed_at=now() WHERE {where}",key)
                return
            with c.transaction():
                versions=save_profile_versions(c,tx,rows,cutoff,watermark,'MANUAL',
                    job_key=f"bulk:{run['id']}:{g['client_id']}:{g['currency']}:{g['direction']}",
                    feedback_ref=run['context']['feedbackReference'])
                c.execute(f'''UPDATE profile_run_groups SET status='COMPLETED',calculation_id=%s,
                    version_ids=%s,last_error=NULL,completed_at=now() WHERE {where}''',
                    (versions[0]['calculationId'],Jsonb([v['id'] for v in versions]),*key))
        except Exception as exc:
            if c.closed:
                raise
            permanent=isinstance(exc,DomainError) and exc.code in ('BAD_INPUT','CONFLICT','NOT_FOUND','FORBIDDEN')
            failed=permanent or g['attempts']>=4
            c.execute(f'''UPDATE profile_run_groups SET status=%s,last_error=%s,
                available_at=now()+interval '2 seconds',completed_at=CASE WHEN %s THEN now() ELSE NULL END
                WHERE {where}''',('FAILED' if failed else 'RETRY',failure_reason(exc),failed,*key))

    def tick(self, batch=2, budget_seconds=45):
        deadline=time.monotonic()+budget_seconds
        candidates=self.db.query('''SELECT id FROM profile_runs
            WHERE status IN ('QUEUED','DISCOVERING','RUNNING') AND available_at<=now()
            ORDER BY available_at,created_at,id LIMIT 32''')
        for candidate in candidates:
            if time.monotonic()>=deadline:
                break
            with self.db.connect() as c:
                c.autocommit=True
                lock='profile-run:'+str(candidate['id'])
                if not c.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok',(lock,)).fetchone()['ok']:
                    continue
                run=None
                try:
                    run=c.execute('SELECT * FROM profile_runs WHERE id=%s',(candidate['id'],)).fetchone()
                    if run['status'] in TERMINAL:
                        continue
                    if any(run['context'].get(k)!=v for k,v in policy().items()):
                        raise DomainError('CONFLICT','VERSION_MISMATCH')
                    if run['status'] in ('QUEUED','DISCOVERING'):
                        self.discover(c,run)
                        continue
                    groups=c.execute('''SELECT * FROM profile_run_groups WHERE run_id=%s
                        AND status IN ('PENDING','RUNNING','RETRY') AND available_at<=now()
                        ORDER BY client_id,currency,direction LIMIT %s''',(run['id'],batch)).fetchall()
                    for group in groups:
                        if time.monotonic()>=deadline:
                            break
                        self.group(c,run,group,deadline)
                    counts=Counter({r['status']:r['n'] for r in c.execute(
                        'SELECT status,count(*) AS n FROM profile_run_groups WHERE run_id=%s GROUP BY status',(run['id'],))})
                    if not any(counts[s] for s in ('PENDING','RUNNING','RETRY')):
                        status=('PARTIAL' if counts['COMPLETED'] else 'FAILED') if counts['FAILED'] else (
                            'COMPLETED_WITH_SKIPS' if counts['SKIPPED'] else 'COMPLETED')
                        c.execute('UPDATE profile_runs SET status=%s,completed_at=now() WHERE id=%s',(status,run['id']))
                except Exception as exc:
                    if c.closed or run is None:
                        raise
                    permanent=isinstance(exc,DomainError) and exc.code in ('BAD_INPUT','CONFLICT','FORBIDDEN','NOT_FOUND')
                    failed=permanent or run['discovery_attempts']>=4
                    c.execute('''UPDATE profile_runs SET status=CASE WHEN %s THEN 'FAILED' ELSE status END,
                        discovery_attempts=discovery_attempts+1,last_error=%s,available_at=now()+interval '2 seconds',
                        completed_at=CASE WHEN %s THEN now() ELSE NULL END WHERE id=%s''',
                        (failed,failure_reason(exc),failed,run['id']))
                finally:
                    if not c.closed:
                        c.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(lock,))

    def loop(self):
        while True:
            try:
                self.tick()
            except Exception as exc:
                logging.warning('profiles.bulk.retry %s',type(exc).__name__)
            time.sleep(max(1,min(30,int(os.getenv('PROFILE_RUN_POLL_SECONDS','2')))))
