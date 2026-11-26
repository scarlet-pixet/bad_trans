from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from backend.common.data import DomainError


def test_case_grant_is_revoked_after_reassignment():
    from backend.services.case.reports.metrics import CaseMetrics

    actor = SimpleNamespace(id="analyst", role="ANALYST", is_active=True)
    db = Mock()
    db.one.return_value = dict(assignee_id="analyst", status="CLOSED")
    service = CaseMetrics(db)
    from uuid import uuid4

    tx = str(uuid4())
    assert service.authorize_transaction(actor, tx)["allowed"] is True
    db.one.return_value = dict(assignee_id="another", status="CLOSED")
    with pytest.raises(DomainError):
        service.authorize_transaction(actor, tx)


def test_analyst_cached_export_always_rechecks_owner():
    from backend.infrastructure.cache import CachedClient

    client = CachedClient.__new__(CachedClient)
    client.owner = "case"
    client.actor = dict(id="analyst", role="ANALYST")
    client.cache = Mock()
    owner = Mock(
        side_effect=[dict(id="report"), DomainError("FORBIDDEN", "Access revoked")]
    )
    with patch(
        "backend.infrastructure.clients.RemoteClient.__getattr__", return_value=owner
    ):
        assert client.export_report(id="report")["id"] == "report"
        with pytest.raises(DomainError):
            client.export_report(id="report")
    client.cache.get.assert_not_called()


def test_analyst_global_scope_denied_before_storage():
    from backend.infrastructure.workspace_access import authorize

    actor = SimpleNamespace(id="analyst", role="ANALYST", is_active=True)
    db = Mock()
    with pytest.raises(DomainError):
        authorize("analysis", db, actor, "get_behavior_profiles", {})
    db.one.assert_not_called()


@pytest.mark.parametrize('owner,method,args,row',[
    ('analysis','get_result',{'id':'00000000-0000-0000-0000-000000000001'},{'transaction_id':'00000000-0000-0000-0000-000000000002'}),
    ('analysis','get_profile',{'id':'00000000-0000-0000-0000-000000000001'},{'cutoff_transaction_id':'00000000-0000-0000-0000-000000000002'}),
    ('transaction','get_transaction',{'id':'00000000-0000-0000-0000-000000000002'},None),
])
def test_legacy_reads_require_current_case_grant(owner,method,args,row):
    from backend.infrastructure.workspace_access import authorize
    user=SimpleNamespace(id='analyst',role='ANALYST',is_active=True);db=Mock();db.one.return_value=row
    with patch('backend.infrastructure.workspace_access.transaction_access',side_effect=DomainError('DEPENDENCY_UNAVAILABLE','Case down')) as gate:
        with pytest.raises(DomainError):authorize(owner,db,user,method,args)
        gate.assert_called_once()


def test_gateway_analyst_scope_allows_batch_import_and_keeps_case_reads_scoped():
    from backend.gateway.security import QUERY_ROLES,MUTATION_ROLES
    import backend.gateway.app
    assert not ({'clients','transactions','analysisRun','behaviorProfilePage'} & QUERY_ROLES['ANALYST'])
    assert 'importTransactions' in MUTATION_ROLES['ANALYST']
    assert not ({'buildProfile','registerTransaction','publishRuleVersion','reanalyzePeriod'} & MUTATION_ROLES['ANALYST'])
    assert {'caseContext','profileVersion','resultHistory'} <= QUERY_ROLES['ANALYST']
