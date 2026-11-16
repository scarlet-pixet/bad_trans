from types import SimpleNamespace
from backend.services.analysis.profiles.storage import save_profile_versions
from backend.services.analysis.profiles.pages import ProfilePages


def test_more_than_100_profiles_are_returned_without_loss(profiles):
    db, tx, rows, t = profiles
    with db.connect() as c:
        save_profile_versions(c, tx, rows, t, t)
        source = c.execute(
            "SELECT id FROM profile_versions WHERE window_days=30 LIMIT 1"
        ).fetchone()["id"]
        c.execute(
            """INSERT INTO profile_versions(id,client_id,currency,direction,window_days,version,as_of,ingested_before,origin,metrics)
            SELECT gen_random_uuid(),client_id,currency,direction,window_days,g,as_of,ingested_before,'MANUAL',metrics
            FROM profile_versions CROSS JOIN generate_series(100,220) g WHERE id=%s""",
            (source,),
        )
    api = ProfilePages(db)
    actor = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001", role="MANAGER", is_active=True
    )
    page = api.get(actor, windowDays=30, history=True, limit=100)
    identifiers = [r["id"] for r in page["items"]]
    assert len(identifiers) == 100 and page["totalCount"] == 122
    assert page["nextPageToken"]
    other = api.get(
        actor, windowDays=30, history=True, limit=100, pageToken=page["nextPageToken"]
    )
    identifiers += [r["id"] for r in other["items"]]
    assert len(set(identifiers)) == 122 and other["nextPageToken"] is None


def test_more_than_100_results_are_returned_without_loss(profiles):
    from tests.profile_storage.test_profiles import insert_result
    from backend.services.analysis.profiles.workspace import WorkspaceService

    db, tx, rows, t = profiles
    with db.connect() as c:
        versions = save_profile_versions(c, tx, rows, t, t)
        expected = {str(insert_result(c, tx, versions, t)) for _ in range(122)}
    actor = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001", role="MANAGER", is_active=True
    )
    api = WorkspaceService(db)
    first = api.get_result_history(actor, tx["id"], limit=100)
    assert len(first["items"]) == 100 and first["nextPageToken"]
    second = api.get_result_history(
        actor, tx["id"], limit=100, pageToken=first["nextPageToken"]
    )
    ids = [r["resultId"] for page in [first, second] for r in page["items"]]
    assert len(ids) == len(set(ids)) == 122 and set(ids) == expected
    assert second["nextPageToken"] is None
