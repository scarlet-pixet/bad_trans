"""Bounded group discovery in the Transaction-owned database.

This is an eventually repeating keyset scan, not a historical-data snapshot.
New groups inserted behind the cursor are discovered on the next full pass.
"""

import base64
import json

from backend.common.commands import require_role
from backend.common.data import DomainError, currency, uuid


def get_groups(db, actor, pageToken=None, limit=100):
    require_role(actor, "SYSTEM")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise DomainError("BAD_INPUT", "Group page limit must be 1..100")
    key = None
    if pageToken is not None:
        try:
            if not isinstance(pageToken, str) or len(pageToken) > 512:
                raise ValueError()
            key = json.loads(base64.b64decode(pageToken, altchars=b"-_", validate=True))
            if (
                not isinstance(key, list)
                or len(key) != 3
                or key[2] not in ("IN", "OUT")
            ):
                raise ValueError()
            key = [uuid(key[0]), currency(key[1]), key[2]]
        except (ValueError, TypeError, UnicodeError):
            raise DomainError("BAD_INPUT", "Invalid group page token") from None
    rows = db.query(
        """SELECT DISTINCT a.client_id,t.currency,t.direction
        FROM transactions t JOIN accounts a ON a.id=t.account_id
        WHERE (%s::uuid IS NULL OR (a.client_id,t.currency,t.direction)>(%s::uuid,%s,%s))
        ORDER BY a.client_id,t.currency,t.direction LIMIT %s""",
        (key[0] if key else None, *(key or [None, None, None]), limit + 1),
    )
    more = len(rows) > limit
    rows = rows[:limit]
    token = (
        base64.urlsafe_b64encode(
            json.dumps(
                [
                    str(rows[-1]["client_id"]),
                    rows[-1]["currency"],
                    rows[-1]["direction"],
                ]
            ).encode()
        ).decode()
        if more
        else None
    )
    return dict(
        items=[
            dict(
                clientId=str(r["client_id"]),
                currency=r["currency"],
                direction=r["direction"],
            )
            for r in rows
        ],
        nextPageToken=token,
    )
