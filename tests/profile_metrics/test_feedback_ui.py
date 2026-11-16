from datetime import timedelta
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from backend.services.analysis.profiles.history_policy import reference
from tests.profile_metrics.test_presentation import NOW, ROOT, profile


def test_profile_shows_observed_baseline_policy_and_decision_evidence():
    p = profile()
    p.update(
        observedCount=1,
        includedCount=0,
        excludedCount=1,
        feedbackReference=reference(1, NOW, "MANUAL_PROFILE"),
        exclusions=[
            dict(
                transactionId="transaction",
                decisionId="decision",
                decidedAt=(NOW - timedelta(seconds=1)).isoformat(),
            )
        ],
    )
    p["quality"]["status"] = "BASELINE_EMPTY"

    def post(_url, json, **kwargs):
        q = json["query"]
        if "me{" in q:
            data = dict(me=dict(id="manager", login="manager", role="MANAGER"))
        elif "managerDashboard" in q:
            data = dict(
                managerDashboard=dict(components={}, partialErrors=[], totals=[])
            )
        elif "clients(" in q:
            data = dict(
                clients=dict(
                    items=[dict(id="client", displayName="Feedback test", accounts=[])]
                )
            )
        elif "behaviorProfilePage" in q:
            data = {"behaviorProfilePage":dict(items=[p],totalCount=1,nextPageToken=None)}
        elif "profileVersion" in q:
            data = {"profileVersion":dict(metadata={k:p[k] for k in ['id','clientId','currency','direction','version','origin','asOf']},metrics=p,servedAt=NOW.isoformat())}
        else:
            data = {}
        return Mock(status_code=200, json=lambda: dict(data=data))

    with patch("requests.post", side_effect=post):
        app = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=20)
        app.session_state.token = "test-token"
        app.session_state["Раздел"] = "Профили клиентов"
        app.run()
        if app.sidebar.radio[0].value != "Профили клиентов":
            app.sidebar.radio[0].set_value("Профили клиентов").run()
    assert not app.exception
    captions = " ".join(x.value for x in app.caption)
    assert (
        "Наблюдалось операций: 1" in captions and "исключено по решениям: 1" in captions
    )
    assert "база сравнения пуста" in captions and "None" not in captions
    assert any(
        "Решение" in frame.value.columns
        and "decision" in frame.value["Решение"].tolist()
        for frame in app.dataframe
    )
