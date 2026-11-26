import asyncio
import json
from collections.abc import Callable
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock
from uuid import uuid4

import pytest
from graphql import GraphQLError
from strawberry import ID

from backend.common.data import DomainError
from backend.gateway.product import Query
from backend.services.analysis.assessments.processing import (
    delivery_state,
    get_processing,
)

TX, EVENT, CLIENT = [str(uuid4()) for _ in range(3)]
NOW = "2026-11-16T12:00:00Z"
TOPIC = "transaction.registered.v1"


def failure(status, review=None, tx=TX, event=EVENT):
    payload = {"event_id": event, "correlation_id": event, "schema_version": 1, "occurred_at": NOW,
                   "payload": {"transaction_id": tx, "client_id": CLIENT}}
    return {"topic": TOPIC, "status": status, "review_state": review, "payload": json.dumps(payload).encode()}


@pytest.mark.parametrize("rows,wanted", [
    ([], "PENDING"),
    ([failure("PUBLISHED")], "FAILED"),
    ([failure("PUBLISHED", "CLOSED")], "FAILED"),
    ([failure("PUBLISHED", "WAITING")], "PENDING"),
    ([failure("RETRY_PENDING")], "PENDING"),
    ([failure("RESOLVED")], "UNKNOWN"),
    ([failure("PUBLISHED", tx=CLIENT), failure("PUBLISHED", event=CLIENT)], "PENDING"),
    ([dict(failure("PUBLISHED"), payload=f'{{"id":"{EVENT}"}}'.encode())], "PENDING"),
])
def test_only_exact_valid_failure_changes_state(rows, wanted):
    assert delivery_state(rows, EVENT, TX) == wanted


@pytest.mark.parametrize("saved,inbox,event,expected", [
    ({"assessment_status": "UNAVAILABLE", "assessment_score": None, "score": 0}, False, EVENT, "UNAVAILABLE"),
    ({"assessment_status": "PARTIAL", "assessment_score": 0, "score": 0}, False, EVENT, "PARTIAL"),
    ({"assessment_status": "COMPLETE", "assessment_score": 0, "score": 0}, False, EVENT, "SCORED"),
    ({"assessment_status": None, "assessment_score": None, "score": 0}, False, EVENT, "SCORED"),
    (None, True, EVENT, "UNKNOWN"),
    (None, False, None, "UNKNOWN"),
    (None, False, EVENT, "FAILED"),
])
def test_saved_result_wins_and_missing_evidence_is_unknown(saved, inbox, event, expected):
    connection = Mock()
    assessment, accepted, failures = Mock(), Mock(), Mock()
    assessment.fetchone.return_value = {"id": CLIENT, **saved} if saved else None
    accepted.fetchone.return_value = {"accepted": True} if inbox else None
    failures.fetchall.return_value = [failure("PUBLISHED")]
    connection.execute.side_effect = [Mock(), assessment, accepted, failures]

    @contextmanager
    def connect():
        yield connection

    result = get_processing(SimpleNamespace(connect=connect), SimpleNamespace(role="MANAGER", is_active=True), TX, event)
    assert result == {"state": expected, "resultId": CLIENT if saved else None}


@pytest.mark.parametrize("queued,expected", [(True, "PENDING"), (False, "UNKNOWN")])
def test_gateway_uses_only_durable_pending_evidence_during_outage(queued, expected):
    tx, analysis = Mock(), Mock()
    tx._invoke.return_value = {"eventId": EVENT, "queued": queued}
    analysis._invoke.side_effect = DomainError("DEPENDENCY_UNAVAILABLE", "private technical detail")
    info = SimpleNamespace(context={"clients": SimpleNamespace(transaction=tx, analysis=analysis)})
    result = asyncio.run(cast(Callable[..., Any], Query().operationProcessing)(info, transactionId=ID(TX)))
    assert result.state.value == expected
    assert result.resultId is None


def test_gateway_does_not_query_analysis_without_transaction_grant():
    tx, analysis = Mock(), Mock()
    tx._invoke.side_effect = DomainError("FORBIDDEN", "Нет доступа")
    info = SimpleNamespace(context={"clients": SimpleNamespace(transaction=tx, analysis=analysis)})
    with pytest.raises(GraphQLError) as error:
        asyncio.run(cast(Callable[..., Any], Query().operationProcessing)(info, transactionId=ID(TX)))
    assert error.value.extensions and error.value.extensions["code"] == "FORBIDDEN"
    analysis._invoke.assert_not_called()
