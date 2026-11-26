"""Repeatable-read, immutable selection of groups with activity in [from,to)."""
from datetime import UTC, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from backend.common.behavior_contract import digest
from backend.common.commands import require_role, required_text
from backend.common.data import DomainError, uuid
from backend.common.profile_run_contract import normalize_selection


class ProfileSelection:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def wire(row):
        return dict(id=str(row['id']), digest=row['digest'], count=row['group_count'],
                    capturedAt=row['captured_at'].isoformat(), parameters=row['parameters'])

    def prepare(self, actor, parameters):
        require_role(actor, 'SYSTEM')
        if not isinstance(parameters, dict) or set(parameters) != {'requestKey','selection','ingestedBefore'}:
            raise DomainError('BAD_INPUT', 'Некорректный запрос снимка групп')
        key = required_text(parameters['requestKey'], 'requestKey', 128)
        selection = normalize_selection(parameters['selection'])
        try:
            watermark = datetime.fromisoformat(parameters['ingestedBefore'].replace('Z','+00:00'))
            if watermark.tzinfo is None or watermark > datetime.now(UTC):
                raise ValueError()
            watermark = watermark.astimezone(UTC)
        except (ValueError, TypeError, AttributeError):
            raise DomainError('BAD_INPUT', 'Некорректная граница поступления') from None
        p = dict(selection=selection, ingestedBefore=watermark.isoformat())
        fingerprint = digest(p)
        with self.db.connect() as c:
            c.autocommit = True
            lock = 'profile-selection:' + key
            c.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))', (lock,))
            try:
                with c.transaction():
                    c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                    old = c.execute('SELECT * FROM profile_group_selections WHERE request_key=%s', (key,)).fetchone()
                    if old:
                        if old['request_hash'] != fingerprint:
                            raise DomainError('CONFLICT', 'Ключ отбора использован с другими параметрами')
                        return self.wire(old)
                    rows = c.execute('''SELECT DISTINCT a.client_id,t.currency,t.direction
                        FROM transactions t JOIN accounts a ON a.id=t.account_id
                        JOIN bank_clients b ON b.id=a.client_id
                        WHERE t.occurred_at>=%s AND t.occurred_at<%s AND t.ingested_at<%s
                        AND (%s::uuid[] IS NULL OR a.client_id=ANY(%s::uuid[]))
                        AND (%s::text IS NULL OR b.segment=%s)
                        AND (%s::text IS NULL OR t.currency=%s)
                        AND (%s::text IS NULL OR t.direction=%s)
                        ORDER BY a.client_id,t.currency,t.direction LIMIT 10001''',
                        (selection['period']['from'],selection['period']['to'],watermark,
                         selection['clientIds'],selection['clientIds'],selection['segment'],selection['segment'],
                         selection['currency'],selection['currency'],selection['direction'],selection['direction'])).fetchall()
                    if len(rows)>10000:
                        raise DomainError('BAD_INPUT','GROUP_LIMIT: более 10000 групп; сузьте отбор')
                    groups = [dict(clientId=str(r['client_id']),currency=r['currency'],direction=r['direction']) for r in rows]
                    row = c.execute('''INSERT INTO profile_group_selections
                        (id,request_key,request_hash,parameters,groups,group_count,digest,captured_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,clock_timestamp()) RETURNING *''',
                        (str(uuid4()),key,fingerprint,Jsonb(p),Jsonb(groups),len(groups),digest(groups))).fetchone()
                    return self.wire(row)
            finally:
                if not c.closed:
                    c.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(lock,))

    def page(self, actor, parameters):
        require_role(actor, 'SYSTEM')
        if not isinstance(parameters,dict) or set(parameters)-{'id','offset','limit'}:
            raise DomainError('BAD_INPUT','Некорректная страница отбора')
        offset,limit = parameters.get('offset',0),parameters.get('limit',100)
        if type(offset) is not int or not 0<=offset<=10000 or type(limit) is not int or not 1<=limit<=100:
            raise DomainError('BAD_INPUT','Некорректные границы страницы')
        row=self.db.one('SELECT * FROM profile_group_selections WHERE id=%s',(uuid(parameters.get('id')),))
        if not row:
            raise DomainError('NOT_FOUND','Снимок отбора не найден')
        items=row['groups'][offset:offset+limit]
        next_offset=offset+len(items)
        return dict(id=str(row['id']),digest=row['digest'],count=row['group_count'],items=items,
                    nextOffset=next_offset if next_offset<row['group_count'] else None)
