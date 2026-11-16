from types import SimpleNamespace
import pytest
from backend.common.data import DomainError


def test_request_does_not_accept_client_snapshot():
    from backend.common.report_contract import normalize_request
    with pytest.raises(DomainError):
        normalize_request(dict(reportType="PROFILE", profileVersionId="00000000-0000-0000-0000-000000000001", idempotencyKey="one", snapshot={}))


def test_signature_binds_actor_request_and_content(monkeypatch):
    from backend.common.report_contract import sign, verify
    monkeypatch.setenv("REPORT_SIGNING_KEY", "test-signing-key-is-separate-from-service-token")
    actor=SimpleNamespace(id="00000000-0000-0000-0000-000000000001", login="manager", role="MANAGER")
    request={"idempotencyKey":"one"}; snapshot={"value":1}
    signature=sign(actor,request,snapshot)
    verify(actor,request,snapshot,signature)
    with pytest.raises(DomainError):verify(actor,request,{"value":2},signature)
    actor.id="00000000-0000-0000-0000-000000000002"
    with pytest.raises(DomainError):verify(actor,request,snapshot,signature)


@pytest.mark.parametrize('changes',[{'period':{'from':'2026-01-01','to':'2026-01-02'}},{'period':{'from':'2026-01-02T00:00:00Z','to':'2026-01-01T00:00:00Z'}},{'windowDays':True},{'currency':'byn'},{'period':{'from':'2020-01-01T00:00:00Z','to':'2026-01-01T00:00:00Z'}}])
def test_invalid_period_selectors(changes):
    from backend.common.report_contract import normalize_request
    value=dict(reportType='PERIOD',period={'from':'2026-01-01T00:00:00Z','to':'2026-01-02T00:00:00Z'},idempotencyKey='key')
    with pytest.raises(DomainError):normalize_request(dict(value,**changes))
