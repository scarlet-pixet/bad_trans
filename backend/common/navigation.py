"""Signed, scoped cursors and index seeks for owner-managed lists."""
import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from backend.common.data import DomainError

TTL = 15 * 60
RESET = "Список изменился или срок просмотра истёк. Нажмите «Обновить список»."


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def cursor_error():
    return DomainError("BAD_INPUT", RESET)


def signature(raw):
    return hmac.digest(os.environ["SERVICE_TOKEN"].encode(), b"list-cursor:" + raw, "sha256")


@dataclass(frozen=True)
class Navigation:
    limit: int
    sort: str
    direction: str
    fingerprint: str
    state: dict | None

    def token(self, state):
        raw = packed(state)
        return base64.urlsafe_b64encode(raw + signature(raw)).decode()

    def first(self, **state):
        return dict(state, scope=self.fingerprint, expires=int(time.time()) + TTL, after=None)

    def result(self, rows, state, key, render):
        page = rows[:self.limit]
        return dict(items=render(page), totalCount=state["total"], currentCursor=self.token(state),
                    nextCursor=self.token(dict(state, after=key(page[-1]))) if len(rows)>self.limit else None)


def navigation(value, allowed, default, *, actor, owner, filters):
    value = value or {}
    limit = value.get("limit", 20)
    sort = value.get("sortBy") or default
    direction = value.get("sortDirection") or "ASC"
    if type(limit) is not int or not 1 <= limit <= 100:
        raise DomainError("BAD_INPUT", "limit должен быть от 1 до 100")
    if sort not in allowed or direction not in ("ASC", "DESC"):
        raise DomainError("BAD_INPUT", "Недопустимое поле или направление сортировки")
    identity = [owner, str(actor.id), actor.role, filters, sort, direction, limit]
    fingerprint = hashlib.sha256(packed(identity)).hexdigest()
    state = None
    token = value.get("cursor")
    if token:
        try:
            if len(token) > 32768:
                raise ValueError()
            signed = base64.b64decode(token, altchars=b"-_", validate=True)
            raw, proof = signed[:-32], signed[-32:]
            if not hmac.compare_digest(signature(raw), proof):
                raise ValueError()
            state = json.loads(raw)
            if state["scope"] != fingerprint or state["expires"] <= time.time():
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, KeyError):
            raise cursor_error() from None
    return Navigation(limit, allowed[sort], direction, fingerprint, state)


def seek(db, select, clauses, params, nav, render, *, client_revision=False, unread=False, sort_prefix=""):
    """The insertion XID remains stable even when the row is frozen or updated."""
    command = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    with db.connect() as connection:
        connection.execute(command)
        revision = None
        if client_revision:
            revision = connection.execute("SELECT version FROM client_navigation_revision WHERE id=true").fetchone()["version"]
        state = nav.state
        if state and state["revision"] != revision:
            raise cursor_error()
        boundary = state["snapshot"] if state else connection.execute(
            "SELECT pg_current_snapshot()::text AS snapshot"
        ).fetchone()["snapshot"]
        clauses = [*clauses, "(navigation_xid IS NULL OR pg_visible_in_snapshot(navigation_xid,%s::pg_snapshot))"]
        params = [*params, boundary]
        if unread:
            clauses.append("(read_at IS NULL OR (navigation_read_xid IS NOT NULL AND NOT "
                           "pg_visible_in_snapshot(navigation_read_xid,%s::pg_snapshot)))")
            params.append(boundary)
        where = " WHERE " + " AND ".join(clauses)
        query = select + where
        if state is None:
            total = connection.execute("SELECT count(*)::int AS n FROM (" + query + ") q", params).fetchone()["n"]
            state = nav.first(total=total, snapshot=boundary, revision=revision)
        key = [nav.sort, "id"] if nav.sort != "id" else ["id"]
        qualified = [sort_prefix + column for column in key]
        if state["after"] is not None:
            operator = "<" if nav.direction == "DESC" else ">"
            columns = ",".join(qualified)
            placeholders = ",".join(["%s"] * len(key))
            query += f" AND ({columns}) {operator} ({placeholders})"
            params += state["after"]
        order = ", ".join(f"{column} {nav.direction}" for column in qualified)
        rows = connection.execute(query + " ORDER BY " + order + " LIMIT %s", [*params, nav.limit+1]).fetchall()
    return nav.result(rows, state, lambda row: [row[column] for column in key], render)
