from datetime import UTC, datetime

import pytest

from backend.common.data import DomainError
from backend.services.analysis.profiles.history_policy import reference, validate_reference


@pytest.mark.parametrize("revision", [-1, True, "1"])
def test_revision_is_nonnegative_integer(revision):
    with pytest.raises(DomainError):
        reference(revision, datetime.now(UTC), "SCHEDULED")


def test_policy_and_knowledge_mode_are_explicit():
    with pytest.raises(DomainError):
        reference(0, datetime.now(UTC), "UNSPECIFIED")
    value = reference(1, datetime.now(UTC), "SCHEDULED")
    assert validate_reference(value) == value
    value["policyHash"] = "wrong"
    with pytest.raises(DomainError):
        validate_reference(value)
    with pytest.raises(DomainError):
        validate_reference({})
