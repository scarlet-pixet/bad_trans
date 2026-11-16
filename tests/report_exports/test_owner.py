from unittest.mock import patch
from uuid import uuid4
import pytest
from backend.common.data import DomainError
from backend.gateway.reporting import create_snapshot


def test_owner_checks_assignment_before_and_after_render_and_saved_content_stable(system):
    s=system;request=dict(reportType='OPERATION',transactionId=s.ids[0],resultId=s.result,idempotencyKey='owner-export')
    report=create_snapshot(s.clients(s.analyst),s.analyst,request)
    def render(*args):
        with s.dbs['case'].connect() as c:c.execute('UPDATE review_cases SET assignee_id=%s WHERE id=%s',(str(uuid4()),s.caseid))
        return b'PDF','application/pdf','.pdf'
    with patch('backend.reporting.exporters.render_export',side_effect=render):
        with pytest.raises(DomainError) as denied:s.owners['case'].export_snapshot_report(s.analyst,report['id'],'PDF')
        assert denied.value.code=='FORBIDDEN'
    assert s.owners['case'].get_snapshot_report(s.manager,report['id'])==report
    with patch('backend.reporting.exporters.render_export') as function:
        with pytest.raises(DomainError):s.owners['case'].export_snapshot_report(s.analyst,report['id'],'XLSX')
        function.assert_not_called()
