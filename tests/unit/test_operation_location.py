import pytest

from backend.common.data import DomainError
from backend.services.transaction.ingest.location import normalize_location


@pytest.mark.parametrize(("channel", "kind"), [
    ("POS", "POINT_OF_SALE"), ("ATM", "ATM_DEVICE"), ("ONLINE", "MERCHANT_REGISTRATION"),
])
def test_location_retains_explicit_subject(channel, kind):
    result = normalize_location({"kind": kind, "countryCode": " by ", "city": " Минск "}, channel)
    assert result == {"kind": kind, "countryCode": "BY", "city": "Минск"}


@pytest.mark.parametrize(("channel", "data"), [
    ("UNKNOWN", {"kind": "POINT_OF_SALE", "countryCode": "BY"}),
    ("ONLINE", {"kind": "POINT_OF_SALE", "countryCode": "BY"}),
    ("ATM", {"kind": "ATM_DEVICE", "countryCode": "БY"}),
    ("POS", {"kind": "CUSTOMER", "countryCode": "BY"}),
    ("ONLINE", {"kind": "MERCHANT_REGISTRATION", "countryCode": "PL", "placeRef": "X"}),
    ("POS", {"kind": "POINT_OF_SALE", "countryCode": "BY", "city": ""}),
])
def test_wrong_or_ambiguous_location_is_rejected(channel, data):
    with pytest.raises(DomainError) as error:
        normalize_location(data, channel)
    assert error.value.code == "BAD_INPUT"


def test_missing_location_is_unknown_even_for_a_physical_channel():
    assert normalize_location(None, "ATM") is None
