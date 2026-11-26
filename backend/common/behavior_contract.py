"""Canonical behavior-history contract shared without database access."""

import hashlib
import json
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from backend.common.data import DomainError

CONTRACT = json.loads(
    (
        Path(__file__).resolve().parents[1] / "contracts/behavior_history.schema.json"
    ).read_text(encoding="utf-8")
)
MAX_HISTORY_ITEMS = CONTRACT["$defs"]["Snapshot"]["properties"]["itemCount"]["maximum"]
MAX_HISTORY_PAGE = CONTRACT["$defs"]["PageRequest"]["properties"]["limit"]["maximum"]


@cache
def validator(name):
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": CONTRACT["$defs"]})


def validate(name, value):
    try:
        validator(name).validate(value)
    except ValidationError:
        raise DomainError("BAD_INPUT", f"Invalid behavior history {name}") from None
    return value


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def digest_array(items):
    """Hash canonical serialized rows as a JSON array without joining them."""
    result = hashlib.sha256(b"[")
    separator = b""
    for item in items:
        result.update(separator)
        result.update(item)
        separator = b","
    result.update(b"]")
    return result.hexdigest()
