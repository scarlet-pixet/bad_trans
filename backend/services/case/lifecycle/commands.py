from datetime import datetime, timezone, timedelta
from decimal import Decimal
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
from backend.services.case.lifecycle.service import CaseService
from backend.services.case.notifications import notification_job

class CaseCommands(CaseService):

    def distribute_cases(self, actor, input):
        require_role(actor, 'MANAGER')
        if not 1 <= input['limit'] <= 100:
            raise DomainError('BAD_INPUT', 'limit: 1–100')

        def perform(c):
            from backend.infrastructure.resilience import channel_for
            from backend.generated import bank_pb2 as bank, bank_pb2_grpc as rpc
            import grpc
            from google.protobuf.json_format import MessageToDict
            try:
                with channel_for(os.getenv('AUTH_GRPC', 'auth-service:50051'), 'auth') as channel:
                    response = rpc.AuthServiceStub(channel).ListActiveAnalysts(bank.ListActiveAnalystsRequest(), timeout=2, metadata=(('x-service-token', os.environ['SERVICE_TOKEN']),))
                analysts = [user.id for user in response.data]
            except grpc.RpcError:
                raise DomainError('DEPENDENCY_UNAVAILABLE', 'Auth gRPC временно недоступен') from None
            if not analysts:
                raise DomainError('CONFLICT', 'Нет активных аналитиков')
            loads = {id: c.execute("SELECT count(*) AS n FROM review_cases WHERE assignee_id=%s AND status<>'CLOSED'", (id,)).fetchone()['n'] for id in analysts}
            rows = c.execute("SELECT * FROM review_cases WHERE status='NEW' AND assignee_id IS NULL ORDER BY CASE priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,created_at,id FOR UPDATE SKIP LOCKED LIMIT %s", (input['limit'],)).fetchall()
            assigned = []
            for row in rows:
                analyst = min(loads, key=lambda id: (loads[id], id))
                loads[analyst] += 1
                c.execute("UPDATE review_cases SET status='ASSIGNED',assignee_id=%s,row_version=row_version+1,updated_at=now() WHERE id=%s", (analyst, row['id']))
                notification_job(c, str(row['id']), 'ASSIGNED', assignee=analyst)
                assigned.append({'caseId': str(row['id']), 'assigneeId': analyst})
            left = c.execute("SELECT count(*) AS n FROM review_cases WHERE status='NEW' AND assignee_id IS NULL").fetchone()['n']
            return {'assignedCount': len(assigned), 'unassignedCount': left, 'assignments': assigned}
        with self.repository.db.connect() as c:
            return receipt(c, actor, 'distributeCases', input['idempotencyKey'], input, perform)

    def create_report(self, actor, input):
        require_role(actor, 'MANAGER', 'ANALYST')
        start, end = period(input['period'])
        if input.get('outcome') not in (None, 'EXPLAINED', 'UNUSUAL_CONFIRMED'):
            raise DomainError('BAD_INPUT', 'Неизвестное решение')
        if actor.role == 'ANALYST' and input.get('assigneeId') not in (None, actor.id):
            raise DomainError('FORBIDDEN', 'Доступен только свой отчёт')

        def perform(c):
            clauses = ['d.decided_at >= %s', 'd.decided_at < %s']
            params = [start, end]
            for column, value in [('d.outcome', input.get('outcome')), ('r.assignee_id', actor.id if actor.role == 'ANALYST' else input.get('assigneeId'))]:
                if value:
                    clauses.append(column + '=%s')
                    params.append(value)
            rows = c.execute('SELECT d.outcome,count(*) AS n FROM case_decisions d JOIN review_cases r ON r.id=d.case_id WHERE ' + ' AND '.join(clauses) + ' GROUP BY d.outcome', params).fetchall()
            snapshot = {'decisionCount': sum((r['n'] for r in rows)), 'outcomes': {r['outcome']: r['n'] for r in rows}}
            value = ident()
            c.execute('INSERT INTO reports VALUES (%s,%s,%s,%s,%s,%s,now())', (value, actor.id, start, end, Jsonb(json.loads(json.dumps(input, default=str))), Jsonb(snapshot)))
            return {'id': value}
        with self.repository.db.connect() as c:
            result = receipt(c, actor, 'createReport', input['idempotencyKey'], input, perform)
        return self.repository.report(result['id'])
