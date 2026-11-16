from types import SimpleNamespace

import pytest

from backend.common.data import DomainError
from backend.services.analysis.profiles.service import ProfileService
from backend.services.transaction.demo.source import SourceService


def actor(role):
    return SimpleNamespace(
        id="43817961-b437-5313-9754-19535c80b1b9", role=role, is_active=True
    )


@pytest.mark.parametrize("value", ["no-number", None, True, "NaN", "Infinity", 0, 31])
def test_source_interval_validation_is_a_domain_error(value):
    with pytest.raises(DomainError) as exc:
        SourceService(None).start_source(actor("MANAGER"), "test", value)
    assert exc.value.code == "BAD_INPUT"


def test_source_commands_and_quality_are_role_restricted():
    with pytest.raises(DomainError, match="прав"):
        SourceService(None).start_source(actor("ANALYST"), "test")
    with pytest.raises(DomainError, match="прав"):
        ProfileService(None, None).get_quality(actor("ANALYST"), "any")
    with pytest.raises(DomainError, match="прав"):
        ProfileService(None, None).get_behavior_profiles(actor("ADMIN"))
