from datetime import datetime, UTC
from types import SimpleNamespace
import pytest
from backend.common.data import DomainError
from backend.common.profile_run_contract import normalize_selection
from backend.services.analysis.profiles.bulk import ProfileRuns


def valid(**extra):
    return dict(period={"from":"2026-10-01T00:00:00Z","to":"2026-11-01T00:00:00Z"}, **extra)


def test_normalized_equivalent_filters_have_identical_context():
    a="00000000-0000-0000-0000-000000000001"
    b="00000000-0000-0000-0000-000000000002"
    assert normalize_selection(valid(clientIds=[a,b,a]),now=datetime(2026,11,17,tzinfo=UTC)) == normalize_selection(valid(clientIds=[b,a]),now=datetime(2026,11,17,tzinfo=UTC))


@pytest.mark.parametrize("extra",[{"direction":"BOTH"},{"currency":"byn"},{"clientIds":[]},{"clientIds":["bad"]},{"segment":""},{"segment":"a","clientIds":["00000000-0000-0000-0000-000000000001"]},{"surprise":1}])
def test_invalid_selection_is_rejected(extra):
    with pytest.raises(DomainError):normalize_selection(valid(**extra),now=datetime(2026,11,17,tzinfo=UTC))


def test_future_cutoff_is_rejected():
    with pytest.raises(DomainError):normalize_selection(valid(),now=datetime(2026,10,10,tzinfo=UTC))


@pytest.mark.parametrize("role",["ADMIN","ANALYST","SYSTEM"])
def test_only_manager_may_submit_or_read_runs(role):
    service=ProfileRuns(None,None)
    actor=SimpleNamespace(is_active=True,role=role,id="00000000-0000-0000-0000-000000000001")
    with pytest.raises(DomainError) as e:service.start(actor,valid(),"key")
    assert e.value.code=="FORBIDDEN"
    with pytest.raises(DomainError):service.get(actor,"00000000-0000-0000-0000-000000000001")
