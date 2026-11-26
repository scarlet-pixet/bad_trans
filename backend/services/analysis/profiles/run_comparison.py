"""Compare the saved membership and versions of two profile runs."""

from collections import defaultdict

from backend.common.commands import require_role
from backend.common.data import DomainError, uuid
from backend.services.analysis.profiles.bulk import ProfileRuns


def coverage(first, second):
    return dict(
        first=len(first), second=len(second), common=len(first & second),
        onlyFirst=len(first - second), onlySecond=len(second - first),
    )


def compare_profile_runs(db, actor, first_id, second_id, offset=0, limit=20):
    require_role(actor, "MANAGER")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise DomainError("BAD_INPUT", "Некорректная страница клиентов")
    ids = [uuid(first_id), uuid(second_id)]
    if ids[0] == ids[1]:
        raise DomainError("BAD_INPUT", "Выберите два разных запуска")

    with db.connect() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = connection.execute(
            "SELECT * FROM profile_runs WHERE id=ANY(%s::uuid[])", (ids,)
        ).fetchall()
        by_id = {str(row["id"]): row for row in rows}
        runs = []
        for identifier in ids:
            row = by_id.get(identifier)
            if row is None:
                legacy = connection.execute(
                    "SELECT id,status FROM analysis_runs WHERE id=%s", (identifier,)
                ).fetchone()
                if legacy is None:
                    raise DomainError("NOT_FOUND", "Запуск не найден")
                runs.append(dict(id=identifier, status=legacy["status"],
                                 selection=None, selectionComplete=False, legacy=True))
                continue
            snapshot = row["selection_snapshot"]
            runs.append(dict(ProfileRuns.wire(row), legacy=False,
                             selectionComplete=snapshot is not None
                             and row["next_offset"] == snapshot["count"]))

        groups = [defaultdict(dict), defaultdict(dict)]
        for row in connection.execute(
            """SELECT run_id,client_id,currency,direction,status,last_error,version_ids
               FROM profile_run_groups WHERE run_id=ANY(%s::uuid[])""", (ids,)
        ):
            key = (row["currency"], row["direction"])
            groups[ids.index(str(row["run_id"]))][str(row["client_id"])][key] = row

        selected = [set(side) for side in groups]
        successful = [
            {client for client, entries in side.items()
             if any(group["status"] == "COMPLETED" for group in entries.values())}
            for side in groups
        ]
        all_clients = sorted(selected[0] | selected[1])
        page = all_clients[offset:offset + limit]
        version_ids = {
            version_id for side in groups for client in page
            for group in side.get(client, {}).values() for version_id in group["version_ids"]
        }
        windows = {}
        if version_ids:
            windows = {
                str(row["id"]): row["window_days"] for row in connection.execute(
                    "SELECT id,window_days FROM profile_versions WHERE id=ANY(%s::uuid[])",
                    (sorted(version_ids),),
                )
            }

    def group_detail(group):
        if group is None:
            return None
        return dict(status=group["status"], error=group["last_error"], versions={
            windows[identifier]: identifier for identifier in group["version_ids"]
            if identifier in windows
        })

    clients = []
    for client in page:
        left, right = (side.get(client, {}) for side in groups)
        entries = []
        for currency, direction in sorted(left.keys() | right.keys()):
            key = currency, direction
            first, second = group_detail(left.get(key)), group_detail(right.get(key))
            first_versions = first["versions"] if first else {}
            second_versions = second["versions"] if second else {}
            pairs = [
                dict(windowDays=window, firstId=first_versions[window], secondId=second_versions[window])
                for window in sorted(first_versions.keys() & second_versions.keys())
            ]
            entries.append(dict(currency=currency, direction=direction, first=first,
                                second=second, pairs=pairs))
        clients.append(dict(clientId=client, groups=entries))

    for index, run in enumerate(runs):
        statuses = [g["status"] for entries in groups[index].values() for g in entries.values()]
        run["unfinishedGroups"] = sum(s in ("PENDING", "RUNNING", "RETRY") for s in statuses)
        run["failedGroups"] = statuses.count("FAILED")
        run["skippedGroups"] = statuses.count("SKIPPED")
    return dict(
        first=runs[0], second=runs[1],
        selected=coverage(*selected), successful=coverage(*successful),
        totalClients=len(all_clients), clients=clients,
        nextOffset=offset + len(page) if offset + len(page) < len(all_clients) else None,
    )
