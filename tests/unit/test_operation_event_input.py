from uuid import uuid4

import pytest

from backend.common.data import DomainError
from backend.services.transaction.ingest.lifecycle import event_input

ACCOUNT = str(uuid4())
PURCHASE = {
    "source": "test-source", "operationRef": "purchase", "eventRef": "first",
    "kind": "PURCHASE", "channel": "POS", "status": "AUTHORIZED",
    "accountId": ACCOUNT, "currency": "BYN", "direction": "OUT",
    "amount": "100", "occurredAt": "2026-11-25T15:00:00+03:00",
}


def test_equivalent_amounts_and_time_have_the_same_receipt_intent():
    assert event_input(PURCHASE) == event_input({
        **PURCHASE, "amount": "100.000", "occurredAt": "2026-11-25T12:00:00Z",
    })
    assert event_input(PURCHASE)["amount"] == "100.00"


@pytest.mark.parametrize("override", [
    {"kind": "WITHDRAWAL"},
    {"channel": "PHONE"},
    {"status": "PENDING"},
    {"direction": "IN"},
    {"amount": "100.001"},
    {"amount": "10000000000000000.00"},
    {"occurredAt": "2026-11-25T15:00:00"},
    {"kind": "REFUND", "status": "POSTED", "direction": "IN"},
    {"kind": "REVERSAL", "status": "POSTED", "direction": "OUT", "originalTransactionId": ACCOUNT},
])
def test_invalid_events_are_rejected_before_writes(override):
    with pytest.raises(DomainError) as error:
        event_input({**PURCHASE, **override})
    assert error.value.code == "BAD_INPUT"
