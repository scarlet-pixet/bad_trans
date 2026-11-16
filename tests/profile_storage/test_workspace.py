from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.common.data import DomainError
from backend.services.analysis.profiles.storage import save_profile_versions
from backend.services.analysis.profiles.workspace import WorkspaceService
from backend.services.analysis.profiles.pages import ProfilePages
from tests.profile_storage import test_profiles as storage_fixtures

profiles = storage_fixtures.profiles
insert_result = storage_fixtures.insert_result


def actor(role="MANAGER"):
    return SimpleNamespace(id=str(uuid4()), role=role, is_active=True)


def test_history_pages_select_exact_old_result_and_validate_cursor(profiles):
    db, tx, rows, t = profiles
    with db.connect() as c:
        versions = save_profile_versions(c, tx, rows, t, t)
        old = insert_result(c, tx, versions, t)
        new = insert_result(c, tx, versions, t)
        old, new = sorted(
            [old, new], key=str
        )  # Identical timestamps: UUID tie-break must paginate correctly.
    svc = WorkspaceService(db)
    user = actor()
    first = svc.get_result_history(user, tx["id"], limit=1)
    assert first["items"][0]["resultId"] == str(new) and first["nextPageToken"]
    second = svc.get_result_history(
        user, tx["id"], limit=1, pageToken=first["nextPageToken"]
    )
    assert (
        second["items"][0]["resultId"] == str(old) and second["nextPageToken"] is None
    )
    with pytest.raises(DomainError):
        svc.get_result_history(user, str(uuid4()), pageToken=first["nextPageToken"])


def test_frozen_profile_page_is_bound_to_principal(profiles):
    db, tx, rows, t = profiles
    with db.connect() as c:
        for mode in ["EVENT", "SCHEDULED"]:
            save_profile_versions(
                c,
                tx if mode == "EVENT" else {k: v for k, v in tx.items() if k != "id"},
                rows,
                t,
                t,
                mode,
            )
    page = ProfilePages(db).get(actor(), history=True, limit=1)
    assert page["nextPageToken"]
    with pytest.raises(DomainError):
        ProfilePages(db).get(
            actor(), history=True, limit=1, pageToken=page["nextPageToken"]
        )


def test_scheduled_profile_is_manager_only_and_selected_metrics_do_not_change(profiles):
    db, tx, rows, t = profiles
    with db.connect() as c:
        versions = save_profile_versions(
            c, {k: v for k, v in tx.items() if k != "id"}, rows, t, t, "SCHEDULED"
        )
    svc = WorkspaceService(db)
    identifier = versions[0]["id"]
    manager = actor()
    before = svc.get_profile_version(manager, identifier)
    with pytest.raises(DomainError):
        svc.get_profile_version(actor("ANALYST"), identifier)
    after = svc.get_profile_version(manager, identifier)
    assert (
        before["metadata"] == after["metadata"]
        and before["metrics"] == after["metrics"]
    )
