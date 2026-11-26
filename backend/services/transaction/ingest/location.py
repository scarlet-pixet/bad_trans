"""Validate the meaning of source-provided location evidence."""

import re

from backend.common.commands import required_text
from backend.common.data import DomainError

LOCATION_BY_CHANNEL = {
    "POS": "POINT_OF_SALE",
    "ATM": "ATM_DEVICE",
    "ONLINE": "MERCHANT_REGISTRATION",
}


def normalize_location(value, channel):
    if value is None:
        return None
    if channel not in LOCATION_BY_CHANNEL or value.get("kind") != LOCATION_BY_CHANNEL[channel]:
        raise DomainError("BAD_INPUT", "Указанное место не соответствует каналу операции")
    country = required_text(value.get("countryCode"), "countryCode", 2).upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise DomainError("BAD_INPUT", "Код страны задаётся двумя латинскими буквами")
    city = value.get("city")
    place_ref = value.get("placeRef")
    if place_ref is not None and channel == "ONLINE":
        raise DomainError("BAD_INPUT", "Для места регистрации продавца ID точки или устройства не применяется")
    return {
        "kind": LOCATION_BY_CHANNEL[channel],
        "countryCode": country,
        **({"city": required_text(city, "city", 128)} if city is not None else {}),
        **({"placeRef": required_text(place_ref, "placeRef", 100)} if place_ref is not None else {}),
    }
