import os
import requests
from backend.common.data import DomainError
from backend.common.events import ident
from backend.infrastructure.resilience import current_trace

def recipients():
    try:
        response = requests.get(os.environ['AUTH_URL'] + '/internal/recipients', headers={'X-Service-Token': os.environ['SERVICE_TOKEN']}, timeout=2)
        response.raise_for_status()
        return response.json()['users']
    except (requests.RequestException, ValueError, KeyError):
        raise DomainError('DEPENDENCY_UNAVAILABLE', 'Список сотрудников Auth временно недоступен') from None

def notification_job(c, case_id, kind, source_id=None, assignee=None):
    c.execute('INSERT INTO notification_jobs(id,case_id,type,assignee_id,created_at,correlation_id) VALUES (%s,%s,%s,%s,now(),%s) ON CONFLICT DO NOTHING', (source_id or ident(), case_id, kind, assignee, current_trace()))
