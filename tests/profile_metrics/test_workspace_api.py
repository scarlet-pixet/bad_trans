from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
from datetime import datetime, UTC

import pytest

from backend.common.data import DomainError


def test_history_rejects_foreign_cursor_before_query():
    from backend.services.analysis.profiles.workspace import WorkspaceService
    from backend.services.analysis.profiles.pages import encode

    db = Mock()
    actor = SimpleNamespace(id=str(uuid4()), role="MANAGER", is_active=True)
    with pytest.raises(DomainError):
        WorkspaceService(db).get_result_history(
            actor,
            str(uuid4()),
            pageToken=encode(
                dict(
                    transactionId=str(uuid4()),
                    id=str(uuid4()),
                    at=datetime.now(UTC).isoformat(),
                )
            ),
        )
    db.query.assert_not_called()


def test_selected_profile_metadata_is_authoritative_and_raw_metrics_unchanged():
    from backend.services.analysis.profiles.workspace import WorkspaceService

    now = datetime.now(UTC)
    identifier = str(uuid4())
    client = str(uuid4())
    db = Mock()
    raw = dict(id="not-authoritative", sampleSize=0)
    db.one.return_value = dict(
        id=identifier,
        client_id=client,
        currency="BYN",
        direction="OUT",
        window_days=30,
        version=2,
        origin="SCHEDULED",
        as_of=now,
        created_at=now,
        calculation_id=None,
        metrics_schema_version=None,
        metrics=raw,
    )
    actor = SimpleNamespace(id=str(uuid4()), role="MANAGER", is_active=True)
    value = WorkspaceService(db).get_profile_version(actor, identifier)
    assert value["metadata"]["id"] == identifier and value["metrics"] == raw
    assert value["reportAvailability"] == "AVAILABLE"
