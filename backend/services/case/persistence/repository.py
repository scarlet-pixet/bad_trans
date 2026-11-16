from backend.common.data import Repository, model
from backend.services.case.persistence.models import Case, Comment, Decision, Report, Notification
from uuid import uuid4
from backend.services.case.persistence.models import Notification
from backend.common.data import DomainError, model
from backend.common.commands import command
from backend.common.data import DomainError, uuid, period, pagination, paged, model
from backend.services.case.persistence.models import Report
from backend.services.case.notifications import notification_job

class CaseRepository(Repository):

    def enrich(self, rows):
        ids = [r['id'] for r in rows]
        comments = self.db.query('SELECT * FROM case_comments WHERE case_id=ANY(%s::uuid[]) ORDER BY created_at,id', (ids,)) if ids else []
        decisions = self.db.query('SELECT * FROM case_decisions WHERE case_id=ANY(%s::uuid[]) ORDER BY review_cycle,id', (ids,)) if ids else []
        return [model(Case, r, comments=[model(Comment, x) for x in comments if x['case_id'] == r['id']], decisions=[model(Decision, x) for x in decisions if x['case_id'] == r['id']]) for r in rows]

    def case(self, id):
        rows = self.db.query('SELECT * FROM review_cases WHERE id=%s', (id,))
        return self.enrich(rows)[0] if rows else None

    def cases(self, filters, page):
        clauses, params = ([], [])
        for key, col in [('status', 'status'), ('priority', 'priority'), ('assigneeId', 'assignee_id'), ('clientId', 'client_id')]:
            if key in filters:
                clauses.append(col + '=%s')
                params.append(filters[key])
        if 'period' in filters:
            clauses.extend(['created_at>=%s', 'created_at<%s'])
            params.extend(filters['period'])
        rows, total = self.page_rows('SELECT * FROM review_cases', clauses, params, page)
        return (self.enrich(rows), total)

    def report(self, id):
        return model(Report, self.db.one('SELECT * FROM reports WHERE id=%s', (id,)))

    def notifications(self, recipient, unread, page):
        clauses = ['recipient_id=%s']
        params = [recipient]
        if unread:
            clauses.append('read_at IS NULL')
        rows, total = self.page_rows('SELECT * FROM notifications', clauses, params, page)
        return ([model(Notification, r) for r in rows], total)

    def _execute_command(self, actor, name, key, payload, perform):
        with self.db.connect() as conn:
            return command(conn, actor=actor, name=name, key=key, payload=payload, perform=perform)

    def read_notification(self, actor_id, notification_id):
        with self.db.connect() as conn:
            row = conn.execute('UPDATE notifications SET read_at=coalesce(read_at,now()) WHERE id=%s AND recipient_id=%s RETURNING *', (notification_id, actor_id)).fetchone()
            if row is None:
                raise DomainError('NOT_FOUND', 'Уведомление не найдено у текущего сотрудника')
        return model(Notification, row)

    def report_visible_to(self, id, author_id):
        return model(Report, self.db.one('SELECT * FROM reports WHERE id=%s AND author_id=%s', (id, author_id)))

    def cases_visible_to(self, filters, page, analyst_id):
        clauses = ["(assignee_id=%s OR (assignee_id IS NULL AND status='NEW'))"]
        params = [analyst_id]
        for key, column in (('status', 'status'), ('priority', 'priority'), ('assigneeId', 'assignee_id'), ('clientId', 'client_id')):
            if key in filters:
                clauses.append(column + '=%s')
                params.append(filters[key])
        if 'period' in filters:
            clauses.extend(['created_at >= %s', 'created_at < %s'])
            params.extend(filters['period'])
        rows, total = self.page_rows('SELECT * FROM review_cases', clauses, params, page)
        return (self.enrich(rows), total)

    def execute(self, actor, name, key, payload, perform):

        def action(c):
            result = perform(c)
            if name in ('closeCase','reopenCase'):
                from backend.services.case.lifecycle.feedback import CaseFeedback
                CaseFeedback.publish(c,result)
            row = c.execute('SELECT assignee_id FROM review_cases WHERE id=%s', (result,)).fetchone()
            notification_job(c, result, name, assignee=str(row['assignee_id']) if row['assignee_id'] else None)
            return result
        return self._execute_command(actor, name, key, payload, action)
