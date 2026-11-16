"""Validate modern rule controls through the same pure checker used at runtime."""

from copy import deepcopy
from uuid import uuid4

from backend.common.data import DomainError
from backend.services.analysis.assessments.calculation import assess, validate_rules
from backend.services.analysis.profiles.observation import timestamp
from backend.services.analysis.profiles.metrics import build_metrics


def validate_controls(code, cfg, weight, examples):
    rule: dict = dict(
        id=str(uuid4()), code=code, parameters=deepcopy(cfg), weight=weight
    )
    validate_rules([rule])
    if not isinstance(examples, list) or not 2 <= len(examples) <= 16:
        raise DomainError(
            "BAD_INPUT", "Нужны 2–16 контрольных примеров с обоими исходами"
        )
    outcomes = set()
    for example in examples:
        try:
            if (
                not isinstance(example, dict)
                or type(example.get("expected")) is not bool
            ):
                raise ValueError()
            tx = example["transaction"]
            rows = example["history"]
            if not isinstance(rows, list) or len(rows) > 1000:
                raise ValueError()
            cutoff = timestamp(tx["occurredAt"])
            watermark = timestamp(example.get("ingestedBefore", tx["occurredAt"]))
            profile = build_metrics(
                rows,
                cutoff,
                watermark,
                tx["currency"],
                tx["direction"],
                30,
                tx.get("id"),
                coverage=example.get("coverage"),
                client_id=tx["clientId"],
            )
            profile["id"] = "control-profile"
            enabled = deepcopy(rule)
            enabled["parameters"]["enabled"] = True
            selected = set(profile["history_ids"])
            result = assess(
                tx, profile, [r for r in rows if str(r["id"]) in selected], [enabled]
            )["checks"][0]
            if result["status"] not in ("TRIGGERED", "NOT_TRIGGERED"):
                raise ValueError()
            actual = result["status"] == "TRIGGERED"
            if actual != example["expected"]:
                raise ValueError()
            outcomes.add(actual)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            raise DomainError(
                "BAD_INPUT", "Контрольный пример невалиден, неполон или не пройден"
            ) from None
    if outcomes != {True, False}:
        raise DomainError(
            "BAD_INPUT", "Нужны положительный и отрицательный контрольные исходы"
        )
