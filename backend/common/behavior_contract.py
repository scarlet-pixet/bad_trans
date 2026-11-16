"""Canonical behavior-history contract shared without database access."""

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from backend.common.data import DomainError

CONTRACT = json.loads(
    (
        Path(__file__).resolve().parents[1] / "contracts/behavior_history.schema.json"
    ).read_text(encoding="utf-8")
)


def validate(name, value):
    try:
        Draft202012Validator(
            {"$ref": f"#/$defs/{name}", "$defs": CONTRACT["$defs"]}
        ).validate(value)
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
