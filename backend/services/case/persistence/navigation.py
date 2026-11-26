"""Fixed case ordering with live access checks at the owner's read boundary."""
from uuid import uuid4

from backend.common.data import DomainError
from backend.common.navigation import TTL, cursor_error

MAX_CASES = 50_000


def case_page(db, clauses, params, nav, analyst_id, render):
    access = "(assignee_id=%s OR (assignee_id IS NULL AND status='NEW'))"
    state = nav.state
    with db.connect() as connection:
        connection.autocommit = True
        if state is None:
            # Purge a bounded batch of abandoned snapshots on new browsing sessions.
            connection.execute("""DELETE FROM case_list_views WHERE id IN (
                SELECT id FROM case_list_views WHERE expires_at<=now()
                ORDER BY expires_at LIMIT 20 FOR UPDATE SKIP LOCKED)""")
        with connection.transaction():
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            if state is None:
                view_id = str(uuid4())
                connection.execute("INSERT INTO case_list_views VALUES (%s,now()+%s*interval '1 second')",
                                   (view_id, TTL))
                filters = [*clauses]
                scope = [*params]
                if analyst_id is not None:
                    filters.append(access)
                    scope.append(analyst_id)
                where = " WHERE " + " AND ".join(filters) if filters else ""
                order = f"{nav.sort} {nav.direction}"
                if nav.sort != "id":
                    order += f", id {nav.direction}"
                total = connection.execute(
                    f"""INSERT INTO case_list_positions(view_id,position,case_id)
                    SELECT %s,row_number() OVER (ORDER BY {order}),id
                    FROM review_cases {where} ORDER BY {order} LIMIT %s""",
                    (view_id, *scope, MAX_CASES + 1),
                ).rowcount
                if total > MAX_CASES:
                    raise DomainError("BAD_INPUT", "Сузьте отбор случаев до 50 000 записей.")
                state = nav.first(view=view_id, total=total)
            elif not connection.execute(
                "SELECT id FROM case_list_views WHERE id=%s AND expires_at>now() FOR SHARE", (state["view"],)
            ).fetchone():
                raise cursor_error()
            where = " AND " + access if analyst_id is not None else ""
            scope = [analyst_id] if analyst_id is not None else []
            rows = connection.execute(
                f"""SELECT r.*,p.position FROM case_list_positions p
                JOIN review_cases r ON r.id=p.case_id
                WHERE p.view_id=%s AND p.position>%s{where}
                ORDER BY p.position LIMIT %s""",
                (state["view"], state["after"] or 0, *scope, nav.limit + 1),
            ).fetchall()
            return nav.result(rows, state, lambda row: row["position"], lambda page: render(page, connection))
