from types import SimpleNamespace

import pytest

from backend.common.data import DomainError
from backend.services.transaction.history.groups import get_groups
from tests.profile_storage.test_history import history


def test_group_owner_pagination_and_system_only(history):
    db, snapshots, parameters, add = history
    add()
    add(direction="IN")
    system = SimpleNamespace(role="SYSTEM", is_active=True)
    p = get_groups(db, system, limit=1)
    assert len(p["items"]) == 1 and p["nextPageToken"]
    items = p["items"][:]
    token = p["nextPageToken"]
    while token:
        q = get_groups(db, system, limit=1, pageToken=token)
        items.extend(q["items"])
        token = q["nextPageToken"]
    keys = [(x["clientId"], x["currency"], x["direction"]) for x in items]
    assert keys == sorted(set(keys))
    assert {
        x["direction"] for x in items if x["clientId"] == parameters["clientId"]
    } == {"IN", "OUT"}
    with pytest.raises(DomainError):
        get_groups(db, SimpleNamespace(role="MANAGER", is_active=True))


@pytest.mark.parametrize(
    "kwargs", [{"limit": True}, {"limit": 0}, {"limit": 101}, {"pageToken": "bad"}]
)
def test_invalid_group_paging(history, kwargs):
    with pytest.raises(DomainError):
        get_groups(history[0], SimpleNamespace(role="SYSTEM", is_active=True), **kwargs)
