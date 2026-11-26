"""A displayed score and threshold must belong to one saved result."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from streamlit.testing.v1 import AppTest

from tests.profile_metrics.test_presentation import NOW


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({}, "порог неизвестен"),
        ({"threshold": None}, "порог неизвестен"),
        ({"threshold": 0}, "0"),
        ({"threshold": 40}, "40"),
        ({"threshold": 75}, "75"),
    ],
)
def test_case_metric_uses_saved_threshold(fields, expected):
    details = dict(resultId="saved", score=35, **fields)
    app = AppTest.from_string(
        "from frontend.assessment_view import render_case_score\n"
        f"render_case_score(dict(latestResultId='saved',latestScore=99),{details!r})",
        default_timeout=15,
    ).run()
    assert not app.exception
    assert app.metric[0].value == f"35 / {expected}"


@pytest.mark.parametrize(
    "details", [None, {}, {"resultId": "other", "score": 80, "threshold": 75}]
)
def test_case_metric_does_not_mix_different_results(details):
    app = AppTest.from_string(
        "from frontend.assessment_view import render_case_score\n"
        f"render_case_score(dict(latestResultId='saved',latestScore=35),{details!r})",
        default_timeout=15,
    ).run()
    assert not app.exception
    assert app.metric[0].value == "35 / порог неизвестен"


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({}, "порог неизвестен"),
        ({"threshold": None}, "порог неизвестен"),
        ({"threshold": 0}, "0"),
        ({"threshold": 75}, "75"),
    ],
)
def test_operation_history_displays_saved_threshold(fields, expected):
    def api(query, args=None):
        if "resultHistory" in query:
            return dict(
                resultHistory=dict(
                    items=[
                        dict(resultId="old", runId="run", calculatedAt=NOW.isoformat())
                    ],
                    nextPageToken=None,
                )
            )
        if "createSnapshotReport" in query:
            raise AssertionError("Read-only rendering must not create a report")
        assert args["r"] == "old"
        return dict(
            detectionContext=dict(
                resultId="old", score=35, profiles=[], checks=[], **fields
            )
        )

    with patch("builtins.threshold_api", api, create=True):
        app = AppTest.from_string(
            "from frontend.workspace_view import operation_workspace\nimport builtins\n"
            "operation_workspace(builtins.threshold_api,dict(id='tx'))",
            default_timeout=15,
        ).run()
    assert not app.exception
    assert app.metric[0].value == "35"
    assert any(
        f"Порог рассмотрения: {expected}" in caption.value for caption in app.caption
    )


def test_analysis_context_keeps_authoritative_saved_result_fields():
    from backend.services.analysis.profiles.service import ProfileService

    row = dict(
        id="result",
        transaction_id="tx",
        run_id="run",
        profile_id=None,
        score=35,
        threshold=40,
        data_status="SUFFICIENT",
        missing_reasons=[],
        calculated_at=NOW,
        details=dict(threshold=75, score=99, resultId="different", profiles=[]),
    )
    service = ProfileService(SimpleNamespace(one=lambda *args: row), None)
    actor = SimpleNamespace(role="MANAGER", is_active=True)
    with (
        patch("backend.infrastructure.workspace_access.transaction_access"),
        patch(
            "backend.services.analysis.assessments.queries.uuid", side_effect=lambda value: value
        ),
    ):
        context = service.get_detection_context(actor, "tx", "result")
    assert (context["resultId"], context["score"], context["threshold"]) == (
        "result",
        35,
        40,
    )


def test_gateway_case_context_requests_exact_case_result_after_reassessment():
    from backend.gateway.product import Query

    case = dict(transactionId="tx", latestResultId="case-result")
    analysis = Mock()
    analysis._invoke.return_value = dict(resultId="case-result", score=35, threshold=75)
    clients = SimpleNamespace(
        case=SimpleNamespace(get_case=lambda **kwargs: case),
        transaction=SimpleNamespace(get_transaction=lambda **kwargs: dict(id="tx")),
        analysis=analysis,
    )
    result = asyncio.run(
        Query.caseContext(None, SimpleNamespace(context=dict(clients=clients)), "case")
    )
    analysis._invoke.assert_called_once_with(
        "get_detection_context", dict(transactionId="tx", resultId="case-result")
    )
    assert result["detection"]["threshold"] == 75


def test_history_switches_score_and_threshold_together_and_preserves_old_result():
    saved = {
        "old": dict(resultId="old", score=35, threshold=40, profiles=[], checks=[]),
        "new": dict(resultId="new", score=80, threshold=75, profiles=[], checks=[]),
    }

    def api(query, args=None):
        if "resultHistory" in query:
            return dict(
                resultHistory=dict(
                    items=[
                        dict(
                            resultId=identifier,
                            runId=identifier,
                            calculatedAt=NOW.isoformat(),
                        )
                        for identifier in saved
                    ],
                    nextPageToken=None,
                )
            )
        return dict(detectionContext=dict(saved[args["r"]]))

    with patch("builtins.threshold_history_api", api, create=True):
        app = AppTest.from_string(
            "from frontend.workspace_view import operation_workspace\nimport builtins\n"
            "operation_workspace(builtins.threshold_history_api,dict(id='tx'))",
            default_timeout=15,
        ).run()
        assert not app.exception
        assert app.metric[0].value == "35"
        assert any("Порог рассмотрения: 40" in c.value for c in app.caption)
        app.selectbox[0].set_value("new").run()
        assert not app.exception
        assert app.metric[0].value == "80"
        assert any("Порог рассмотрения: 75" in c.value for c in app.caption)
        saved["later"] = dict(
            resultId="later", score=90, threshold=95, profiles=[], checks=[]
        )
        app.selectbox[0].set_value("old").run()
        assert not app.exception
        assert app.metric[0].value == "35"
        assert any("Порог рассмотрения: 40" in c.value for c in app.caption)
