from streamlit.testing.v1 import AppTest
import pytest
from tests.profile_metrics.test_assessment import fixture, evaluate


@pytest.mark.parametrize("status", ["COMPLETE", "PARTIAL", "UNAVAILABLE"])
def test_assessment_ui_explains_completeness_and_all_checks(status):
    tx, p, history = fixture()
    details = evaluate(tx, p, history)
    details["assessmentStatus"] = status
    if status == "UNAVAILABLE":
        details["assessmentScore"] = None
    app = AppTest.from_string(
        "from frontend.assessment_view import render_assessment,shown_score\nimport streamlit as st\ndetails="
        + repr(details)
        + '\nst.metric("Оценка",str(shown_score(details)))\nrender_assessment(details)',
        default_timeout=15,
    ).run()
    assert not app.exception and len(app.dataframe[0].value) == 5
    if status == "UNAVAILABLE":
        assert app.metric[0].value == "недоступна" and app.warning
    elif status == "PARTIAL":
        assert app.warning
