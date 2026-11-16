"""Versioned workspace transport envelopes; legacy metrics remain opaque."""

import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from backend.common.data import DomainError

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "contracts/workspace.schema.json").read_text(
        encoding="utf-8"
    )
)


def checked(name, value):
    try:
        Draft202012Validator(
            {"$ref": "#/$defs/" + name, "$defs": SCHEMA["$defs"]},
            format_checker=FormatChecker(),
        ).validate(value)
    except ValidationError:
        raise DomainError(
            "BAD_INPUT" if name.endswith("Request") else "DATA_ACCESS_ERROR",
            "Некорректный контракт рабочего пространства: " + name,
        ) from None
    return value
