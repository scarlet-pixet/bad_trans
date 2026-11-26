import json
from uuid import uuid4

from backend.common.events import make_event
from backend.infrastructure.failure_review import original


def test_replay_uses_the_owner_contract_and_preserves_event_identity():
    event = make_event({"transaction_id":str(uuid4()), "client_id":str(uuid4())})
    row = {"topic":"transaction.registered.v1", "payload":json.dumps(event).encode()}
    assert original(row,"analysis") == event
    assert original(row,"case") is None
    assert original(dict(row,payload=b'{"event_id":"only-an-id"}'),"analysis") is None
