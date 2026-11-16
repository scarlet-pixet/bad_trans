"""Versioned human-decision selection; automatic risk never excludes history."""

from backend.common.behavior_contract import digest
from backend.common.data import DomainError
from backend.services.analysis.profiles.observation import timestamp

VERSION = "CONFIRMED_UNUSUAL_EXCLUSION-v1"
HASH = digest(
    dict(
        version=VERSION,
        closedDecision="UNUSUAL_CONFIRMED",
        automaticAlertExclusion=False,
        knowledge="COMMITTED_REVISION_PREFIX",
    )
)
LEGACY_VERSION = "ALL_OBSERVED-v1"
LEGACY_HASH = digest(dict(version=LEGACY_VERSION, excludeAutomaticAlerts=False))


def reference(revision, captured_at, mode) -> dict:
    if type(revision) is not int or revision < 0:
        raise DomainError("BAD_INPUT", "Invalid feedback revision")
    if mode not in (
        "FIRST_REQUEST_KNOWLEDGE",
        "WITH_CURRENT_KNOWLEDGE",
        "SCHEDULED",
        "MANUAL_PROFILE",
    ):
        raise DomainError("BAD_INPUT", "Invalid feedback knowledge mode")
    return dict(
        revision=revision,
        capturedAt=timestamp(captured_at).isoformat(),
        knowledgeMode=mode,
        policyVersion=VERSION,
        policyHash=HASH,
    )


def validate_reference(value):
    try:
        expected = reference(
            value["revision"], value["capturedAt"], value["knowledgeMode"]
        )
        if value != expected:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise DomainError("CONFLICT", "Unsupported feedback reference") from None
    return expected
