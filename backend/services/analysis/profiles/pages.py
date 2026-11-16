"""Immutable page selections owned by Analysis; no cross-service SQL."""

import base64
import json
from uuid import uuid4

from backend.common.behavior_contract import digest
from backend.common.commands import require_role
from backend.common.data import DomainError, currency as check_currency, uuid


def encode(value):
    return base64.urlsafe_b64encode(
        json.dumps(value, separators=(",", ":")).encode()
    ).decode()


def decode(token):
    try:
        if not isinstance(token, str) or len(token) > 1024:
            raise ValueError()
        value = json.loads(base64.b64decode(token, altchars=b"-_", validate=True))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError):
        raise DomainError("BAD_INPUT", "Invalid page token") from None


class ProfilePages:
    def __init__(self, db):
        self.db = db

    def get(
        self,
        actor,
        clientId=None,
        windowDays=30,
        history=False,
        currency=None,
        direction=None,
        origin=None,
        limit=50,
        pageToken=None,
    ):
        require_role(actor, "MANAGER")
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(windowDays) is not int
            or windowDays not in (7, 30, 90)
            or type(history) is not bool
        ):
            raise DomainError("BAD_INPUT", "Invalid profile page options")
        if direction not in (None, "IN", "OUT") or origin not in (
            None,
            "EVENT",
            "SCHEDULED",
            "MANUAL",
        ):
            raise DomainError("BAD_INPUT", "Invalid profile filters")
        filters = dict(
            clientId=uuid(clientId) if clientId is not None else None,
            windowDays=windowDays,
            history=history,
            currency=check_currency(currency) if currency is not None else None,
            direction=direction,
            origin=origin,
        )
        fingerprint = digest(dict(filters=filters,principalId=uuid(actor.id),role=actor.role))
        with self.db.connect() as c:
            if pageToken is None:
                c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                selection, offset = str(uuid4()), 0
                c.execute(
                    "INSERT INTO profile_page_selections(id,filter_hash) VALUES (%s,%s)",
                    (selection, fingerprint),
                )
                rows = c.execute(
                    """WITH candidates AS (
                    SELECT *,row_number() OVER(PARTITION BY client_id,currency,direction,window_days
                        ORDER BY as_of DESC,version DESC,id DESC) AS rank
                    FROM profile_versions WHERE window_days=%s AND as_of<=now()
                    AND (%s::uuid IS NULL OR client_id=%s) AND (%s::text IS NULL OR currency=%s)
                    AND (%s::text IS NULL OR direction=%s) AND (%s::text IS NULL OR origin=%s))
                    SELECT id FROM candidates WHERE (%s OR rank=1)
                    ORDER BY client_id,currency,direction,as_of DESC,version DESC,id DESC LIMIT 10001""",
                    (
                        windowDays,
                        filters["clientId"],
                        filters["clientId"],
                        currency,
                        currency,
                        direction,
                        direction,
                        origin,
                        origin,
                        history,
                    ),
                ).fetchall()
                if len(rows) > 10000:
                    raise DomainError(
                        "BAD_INPUT",
                        "Profile selection exceeds 10000 versions; narrow filters",
                    )
                if rows:
                    with c.cursor() as cur:
                        cur.executemany(
                            "INSERT INTO profile_page_items(selection_id,ordinal,profile_id) VALUES (%s,%s,%s)",
                            [(selection, i, r["id"]) for i, r in enumerate(rows, 1)],
                        )
            else:
                value = decode(pageToken)
                try:
                    selection, offset = uuid(value["selectionId"]), value["offset"]
                    if (
                        type(offset) is not int
                        or offset < 0
                        or value["filterHash"] != fingerprint
                    ):
                        raise ValueError()
                except (KeyError, ValueError, TypeError):
                    raise DomainError(
                        "BAD_INPUT", "Page token does not match filters"
                    ) from None
            # Keep expiry cleanup from removing the selection between reading
            # its metadata, count, and items in this request's transaction.
            selected = c.execute(
                "SELECT * FROM profile_page_selections WHERE id=%s AND expires_at>now() FOR SHARE",
                (selection,),
            ).fetchone()
            if not selected or selected["filter_hash"] != fingerprint:
                raise DomainError(
                    "BAD_INPUT", "Page selection expired or does not match filters"
                )
            total = c.execute(
                "SELECT count(*) AS n FROM profile_page_items WHERE selection_id=%s",
                (selection,),
            ).fetchone()["n"]
            if offset > total:
                raise DomainError("BAD_INPUT", "Invalid page offset")
            rows = c.execute(
                """SELECT p.* FROM profile_page_items i JOIN profile_versions p ON p.id=i.profile_id
                WHERE i.selection_id=%s AND i.ordinal>%s ORDER BY i.ordinal LIMIT %s""",
                (selection, offset, limit),
            ).fetchall()
        from backend.services.analysis.profiles.service import ProfileService

        next_offset = offset + len(rows)
        token = (
            encode(
                dict(selectionId=selection, offset=next_offset, filterHash=fingerprint)
            )
            if next_offset < total
            else None
        )
        return dict(
            items=[ProfileService.profile_wire(r) for r in rows],
            currentPageToken=encode(dict(selectionId=selection, offset=offset, filterHash=fingerprint)),
            nextPageToken=token,
            totalCount=total,
            selectionId=selection,
            selectedAt=selected["selected_at"].isoformat(),
            expiresAt=selected["expires_at"].isoformat(),
            historyStatus="AVAILABLE" if total else "NO_HISTORY",
        )
