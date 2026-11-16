from datetime import datetime, timezone, timedelta

from decimal import Decimal, InvalidOperation
from math import isfinite

from statistics import median

from uuid import uuid4

import json

import re

import os

import psycopg

from psycopg.types.json import Jsonb

from backend.common.data import DomainError, uuid, period, currency

from backend.common.commands import require_role, required_text

from backend.common.events import utc, ident, make_event, add_outbox

from backend.common.receipts import receipt

from backend.infrastructure.resilience import current_trace, log, channel_for

from backend.services.analysis.application.queries import AnalysisService

from backend.services.analysis.events.transactions import AnalysisEventService

from backend.infrastructure.clients import EventTransactionClient



class AnalysisCommands(AnalysisService):



    def _command(self, actor, name, key, payload, perform):

        require_role(actor, *(('MANAGER', 'ANALYST') if name in ('buildProfile', 'analyzeTransaction') else ('MANAGER',)))

        with self.repository.db.connect() as c:

            result = receipt(c, actor, name, key, payload, perform)

        return self.repository.run(result['id'])



    def build_profile(self, actor, input):
        from backend.services.analysis.profiles.storage import fetch_snapshot_history as fetch_history, save_profile_versions as save_versions
        from backend.services.analysis.profiles.metrics import build_metrics
        cutoff = datetime.fromisoformat(str(input['cutoff']).replace('Z', '+00:00'))
        if not cutoff.tzinfo or input['direction'] not in ('IN', 'OUT'):
            raise DomainError('BAD_INPUT', 'Некорректный срез профиля')
        client = uuid(input['clientId'])
        curr = currency(input['currency'])

        def perform(c):
            if not self.transaction_client.get_client(client):
                raise DomainError('NOT_FOUND', 'Клиент не найден')
            now = utc()
            tx = {'clientId': client, 'currency': curr, 'direction': input['direction']}
            from backend.services.analysis.profiles.requests import prepare
            from backend.services.analysis.assessments.requests import history as pinned_history
            from backend.services.analysis.profiles.observation import timestamp
            request=prepare(c,tx,cutoff,actor.id,input['idempotencyKey'])
            now=timestamp(request['context']['ingestedBefore'])
            history = pinned_history(c,EventTransactionClient(),request,table='profile_build_requests')
            versions = save_versions(c, tx, history, cutoff, now, 'MANUAL',feedback_ref=request['context']['feedbackReference'])
            metrics = next(v for v in versions if v['windowDays'] == 30)
            run = ident()
            c.execute('INSERT INTO analysis_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      (run, 'PROFILE', Jsonb({'clientId': client}), cutoff, 'COMPLETED', actor.id, Jsonb({'profiles': 1}), now, utc()))
            c.execute('INSERT INTO behavior_profiles (id,run_id,client_id,currency,direction,window_start,window_end,cutoff_transaction_id,ingested_before,sample_size,median_amount,mad_amount,metrics,algorithm_version,data_status) VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s)',
                      (ident(), run, client, curr, input['direction'], cutoff-timedelta(days=30), cutoff, now,
                       metrics['sampleSize'], metrics['medianAmount'], metrics['madAmount'], Jsonb(metrics), metrics['algorithmVersion'], metrics['dataStatus']))
            return {'id': run}
        return self._command(actor, 'buildProfile', input['idempotencyKey'], input, perform)

    def analyze_transaction(self, actor, transactionId, idempotencyKey):

        transactionId = uuid(transactionId)



        def perform(c):

            tx = EventTransactionClient().get_transaction(transactionId)

            if not tx:

                raise DomainError('NOT_FOUND', 'Транзакция не найдена')

            run = ident()

            event = make_event({'transaction_id': transactionId, 'client_id': tx['clientId']})

            event['correlation_id'] = current_trace()

            from backend.common.behavior_contract import digest
            AnalysisEventService(EventTransactionClient()).process(c, event, run_id=run, initiated_by=actor.id,
                request_key='manual:'+digest(dict(actor=actor.id,key=idempotencyKey,transaction=transactionId)))

            return {'id': run}

        return self._command(actor, 'analyzeTransaction', idempotencyKey, {'transactionId': transactionId}, perform)



    def reanalyze_period(self, actor, input):

        start, end = period(input['period'])

        ids = [uuid(x) for x in input['ruleVersionIds']]

        if not 1<=len(ids)<=5 or len(set(ids)) != len(ids):

            raise DomainError('BAD_INPUT', 'Нужны разные современные версии правил (1–5)')



        def perform(c):

            rows = c.execute("SELECT v.*,a.code FROM rule_versions v JOIN analysis_rules a ON a.id=v.rule_id WHERE v.id=ANY(%s::uuid[]) AND status='PUBLISHED'", (ids,)).fetchall()

            if len(rows)!=len(ids):

                raise DomainError('BAD_INPUT', 'Набор опубликованных версий некорректен')
            from backend.services.analysis.assessments.calculation import validate_rules
            validate_rules(rows)

            run = ident()

            now = utc()

            transactions = []

            cursor = None

            filters = {'period': input['period']}

            if input.get('clientId'):

                filters['clientId'] = uuid(input['clientId'])

            while True:

                page = self.transaction_client.search_transactions(filter=filters, page={'limit': 100, 'cursor': cursor})

                transactions.extend(page['items'])

                cursor = page['nextCursor']

                if len(transactions) > 100:

                    raise DomainError('BAD_INPUT', 'Сократите период до 100 операций')

                if not cursor:

                    break

            c.execute('INSERT INTO analysis_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', (run, 'REANALYSIS', Jsonb(json.loads(json.dumps(input, default=str))), end, 'COMPLETED', actor.id, Jsonb({'processed': len(transactions)}), now, now))

            for rule in ids:

                c.execute('INSERT INTO run_rule_versions VALUES (%s,%s)', (run, rule))

            engine = AnalysisEventService(EventTransactionClient())

            for tx in transactions:

                event = make_event({'transaction_id': tx.id, 'client_id': tx.clientId})

                event['correlation_id'] = current_trace()

                from backend.common.behavior_contract import digest
                engine.process(c, event, run_id=run, rule_ids=ids, initiated_by=actor.id, ingested_before=now,
                    request_key='reanalyze:'+digest(dict(actor=actor.id,key=input['idempotencyKey'],transaction=tx.id)))

            return {'id': run}

        return self._command(actor, 'reanalyzePeriod', input['idempotencyKey'], input, perform)



    def publish_rule_version(self,actor,input):
        require_role(actor,'MANAGER')
        rule=uuid(input['ruleId']);cfg=input['parameters'];weight=input['weight']
        if type(input['version']) is not int or input['version']<1 or type(weight) is not int or not 0<=weight<=100:
            raise DomainError('BAD_INPUT','Некорректная версия или вес')
        def perform(c):
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended('published-rules',0))")
            row=c.execute('SELECT code FROM analysis_rules WHERE id=%s',(rule,)).fetchone()
            if not row:raise DomainError('NOT_FOUND','Правило не найдено')
            code=row['code']
            from backend.services.analysis.assessments.calculation import ORDER
            if code in ORDER:
                from backend.services.analysis.assessments.publication import validate_controls
                validate_controls(code,cfg,weight,input['controlExamples'])
                latest=c.execute('SELECT coalesce(max(version),0) AS v FROM rule_versions WHERE rule_id=%s',(rule,)).fetchone()['v']
                if input['version']<=latest:raise DomainError('CONFLICT','Версия должна быть новее существующих')
                value=ident()
                c.execute("INSERT INTO rule_versions VALUES (%s,%s,%s,%s,%s,'PUBLISHED',%s,now())",(value,rule,input['version'],Jsonb(cfg),weight,actor.id))
                return {'id':value}
            if code not in ('AMOUNT','FREQUENCY','NEW_COUNTERPARTY'):
                raise DomainError('BAD_INPUT','Неизвестный код проверки')
            required={'AMOUNT':('min_samples','multiplier'),'FREQUENCY':('window_minutes','limit'),'NEW_COUNTERPARTY':('min_samples',)}[code]
            if not isinstance(cfg,dict) or any(type(cfg.get(k)) not in (int,float) or
                    (type(cfg[k]) is float and not isfinite(cfg[k])) or
                    cfg[k]<=0 or int(cfg[k])!=cfg[k] for k in required):
                raise DomainError('BAD_INPUT','Параметры должны быть положительными целыми числами')
            cfg.update({k:int(cfg[k]) for k in required})
            other=c.execute("SELECT coalesce(sum(weight),0) AS total FROM (SELECT DISTINCT ON (v.rule_id) v.weight FROM rule_versions v JOIN analysis_rules r ON r.id=v.rule_id WHERE v.status='PUBLISHED' AND v.rule_id<>%s AND r.code IN ('AMOUNT','FREQUENCY','NEW_COUNTERPARTY') ORDER BY v.rule_id,v.version DESC) x",(rule,)).fetchone()['total']
            if other+weight>100:raise DomainError('BAD_INPUT','Сумма весов превышает 100')
            latest=c.execute('SELECT coalesce(max(version),0) AS v FROM rule_versions WHERE rule_id=%s',(rule,)).fetchone()['v']
            if input['version']<=latest:raise DomainError('CONFLICT','Версия должна быть новее существующих')
            examples=input['controlExamples']
            if not isinstance(examples,list) or len(examples)<2:raise DomainError('BAD_INPUT','Нужны положительный и отрицательный контрольные примеры')
            seen=set()
            for example in examples:
                if not isinstance(example,dict) or type(example.get('expected')) is not bool:raise DomainError('BAD_INPUT','Пример требует expected')
                try:
                    if code=='AMOUNT':
                        amount=Decimal(str(example['amount']))
                        median_amount=Decimal(str(example['median']))
                        if not amount.is_finite() or not median_amount.is_finite() or amount<=0 or median_amount<=0:
                            raise ValueError('Positive finite monetary values required')
                        actual=amount>median_amount*cfg['multiplier']
                    elif code=='FREQUENCY':
                        if type(example['count']) is not int or example['count']<0:
                            raise ValueError('Nonnegative integer count required')
                        actual=example['count']>cfg['limit']
                    else:actual=example['counterpartyId'] not in example['knownCounterparties']
                except (KeyError,TypeError,ValueError,InvalidOperation):raise DomainError('BAD_INPUT','Неверный контрольный пример') from None
                if actual != example['expected']:raise DomainError('BAD_INPUT','Контрольный пример не пройден')
                seen.add(actual)
            if seen!={True,False}:raise DomainError('BAD_INPUT','Нужны оба исхода проверки')
            value=ident()
            c.execute("INSERT INTO rule_versions VALUES (%s,%s,%s,%s,%s,'PUBLISHED',%s,now())",
                      (value,rule,input['version'],Jsonb(cfg),weight,actor.id))
            return {'id':value}
        with self.repository.db.connect() as c:
            result=receipt(c,actor,'publishRuleVersion',input['idempotencyKey'],input,perform)
        return next(r for r in self.repository.rule_versions(rule) if r.id==result['id'])
