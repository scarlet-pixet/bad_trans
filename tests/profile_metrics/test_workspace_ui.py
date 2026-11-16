from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from tests.profile_metrics.test_presentation import NOW, profile
import pytest


def test_legacy_operation_displays_its_actual_score():
    def api(q, a=None):
        if "resultHistory" in q:
            return dict(
                resultHistory=dict(
                    items=[
                        dict(resultId="old", runId="run", calculatedAt=NOW.isoformat())
                    ],
                    nextPageToken=None,
                )
            )
        return dict(
            detectionContext=dict(score=35, threshold=60, profiles=[], checks=[])
        )

    with patch("builtins.workspace_test_api", api, create=True):
        app = AppTest.from_string(
            'from frontend.workspace_view import operation_workspace\nimport builtins\noperation_workspace(builtins.workspace_test_api,dict(id="tx"))',
            default_timeout=20,
        ).run()
    assert not app.exception and app.metric[0].value == "35"
    assert any("Исторический результат" in i.value for i in app.info)


def test_selected_historical_result_is_forwarded_and_never_latest_fallback():
    calls = []

    def api(query, args=None):
        calls.append((query, args))
        if "resultHistory" in query:
            return dict(
                resultHistory=dict(
                    items=[
                        dict(
                            resultId="old", runId="first", calculatedAt=NOW.isoformat()
                        ),
                        dict(
                            resultId="new", runId="second", calculatedAt=NOW.isoformat()
                        ),
                    ],
                    nextPageToken=None,
                )
            )
        return dict(
            detectionContext=dict(
                resultId=args["r"], profiles=[], checks=[], assessmentStatus="PARTIAL"
            )
        )

    with patch("builtins.workspace_test_api", api, create=True):
        app = AppTest.from_string(
            'from frontend.workspace_view import operation_workspace\nimport builtins\noperation_workspace(builtins.workspace_test_api,dict(id="tx",externalId="operation"))',
            default_timeout=20,
        ).run()
        assert not app.exception
        app.selectbox[0].set_value("new").run()
    selected = [args for q, args in calls if "detectionContext" in q]
    assert selected[0]["r"] == "old" and selected[-1]["r"] == "new"
    assert all("resultId:$r" in q for q, _ in calls if "detectionContext" in q)


def test_profile_navigation_without_cases_and_empty_currency_state():
    calls = []
    p = profile()
    p["currency"] = "USD"

    def api(query, args=None):
        calls.append((query, args))
        if "behaviorProfilePage" in query:
            return dict(
                behaviorProfilePage=dict(
                    items=[] if args["cur"] == "BYN" else [p],
                    totalCount=1,
                    nextPageToken=None,
                )
            )
        return dict(
            profileVersion=dict(
                metadata={
                    k: p[k]
                    for k in [
                        "id",
                        "clientId",
                        "currency",
                        "direction",
                        "version",
                        "origin",
                        "asOf",
                    ]
                },
                metrics=p,
                servedAt=NOW.isoformat(),
            )
        )

    with patch("builtins.workspace_test_api", api, create=True):
        app = AppTest.from_string(
            'from frontend.workspace_view import client_workspace\nimport builtins\nclient_workspace(builtins.workspace_test_api,dict(id="client",accounts=[dict(currency="BYN"),dict(currency="USD")]))',
            default_timeout=20,
        ).run()
        assert not app.exception
        app.selectbox[0].set_value("BYN").run()
        assert not app.exception and any("пока нет" in m.value for m in app.info)
    assert not any("case" in q.lower() for q, _ in calls)


def test_network_error_does_not_display_empty_profile_as_success():
    with patch("builtins.workspace_test_api", lambda *a, **k: None, create=True):
        app = AppTest.from_string(
            'from frontend.workspace_view import client_workspace\nimport builtins\nclient_workspace(builtins.workspace_test_api,dict(id="client",accounts=[]))',
            default_timeout=20,
        ).run()
    assert not app.exception and not app.metric and not app.info


def test_legacy_profile_without_weekday_distribution_is_rendered_without_empty_chart_error():
    p = profile()
    p.pop("weekdayShareUtc")
    p.pop("quality")
    p.pop("metricsSchemaVersion")
    app = AppTest.from_string(
        "from frontend.profile_card import render_profiles\nrender_profiles("
        + repr([p])
        + ")",
        default_timeout=20,
    ).run()
    assert not app.exception
    assert any("не сохранено" in item.value for item in app.info)


def test_new_result_arrival_does_not_replace_selected_result_and_access_is_rechecked():
    state = {"new": False, "denied": False}
    seen = []

    def api(q, a=None):
        if "resultHistory" in q:
            if state["denied"]:
                return None
            items = [
                dict(resultId="old", runId="old-run", calculatedAt=NOW.isoformat())
            ]
            if state["new"]:
                items.insert(
                    0,
                    dict(resultId="new", runId="new-run", calculatedAt=NOW.isoformat()),
                )
            return dict(resultHistory=dict(items=items, nextPageToken=None))
        seen.append(a["r"])
        return dict(
            detectionContext=dict(profiles=[], checks=[], assessmentStatus="PARTIAL")
        )

    with patch("builtins.workspace_test_api", api, create=True):
        app = AppTest.from_string(
            'from frontend.workspace_view import operation_workspace\nimport builtins\noperation_workspace(builtins.workspace_test_api,dict(id="tx"))',
            default_timeout=20,
        ).run()
        state["new"] = True
        app.run()
        assert seen == ["old", "old"] and app.selectbox[0].options[0].endswith("old")
        state["denied"] = True
        app.run()
        assert seen == ["old", "old"] and not app.selectbox and not app.exception


def test_frozen_profile_page_next_previous_and_filter_reset():
    seen = []
    p = profile()

    def api(q, a=None):
        if "behaviorProfilePage" in q:
            seen.append(a.copy())
            return dict(
                behaviorProfilePage=dict(
                    items=[p],
                    totalCount=2,
                    nextPageToken="fixed-page-two" if a["t"] is None else None,
                )
            )
        return dict(
            profileVersion=dict(
                metadata={
                    k: p[k]
                    for k in [
                        "id",
                        "clientId",
                        "currency",
                        "direction",
                        "version",
                        "origin",
                        "asOf",
                    ]
                },
                metrics=p,
                servedAt=NOW.isoformat(),
            )
        )

    with patch("builtins.workspace_test_api", api, create=True):
        app = AppTest.from_string(
            'from frontend.workspace_view import client_workspace\nimport builtins\nclient_workspace(builtins.workspace_test_api,dict(id="client",accounts=[dict(currency="BYN")]))',
            default_timeout=20,
        ).run()
        next(b for b in app.button if b.label == "Следующая страница").click().run()
        assert seen[-1]["t"] == "fixed-page-two" and not app.exception
        next(b for b in app.button if b.label == "Предыдущая страница").click().run()
        assert seen[-1]["t"] is None
        app.selectbox[0].set_value("BYN").run()
        assert seen[-1]["cur"] == "BYN" and seen[-1]["t"] is None


@pytest.mark.parametrize("role", ["ANALYST", "ADMIN", "MANAGER"])
def test_role_navigation_preserves_workspaces(role):
    from tests.profile_metrics.test_presentation import ROOT

    seen = []

    def post(url, json, **kwargs):
        q = json["query"]
        seen.append(q)
        if "me{" in q:
            data = dict(me=dict(id="staff", login=role.lower(), role=role))
        elif "reviewCases" in q:
            data = dict(reviewCases=dict(items=[]))
        elif "managerDashboard" in q:
            data = dict(
                managerDashboard=dict(components={}, partialErrors=[], totals=[])
            )
        elif "sources" in q:
            data = dict(sources=[])
        else:
            data = {}
        from unittest.mock import Mock

        return Mock(status_code=200, json=lambda: dict(data=data))

    with patch("requests.post", side_effect=post):
        app = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=20)
        app.session_state.token = "test-only"
        app.run()
    assert not app.exception
    options = app.sidebar.radio[0].options
    if role == "ANALYST":
        assert (
            "Предупреждения" in options
            and "Профили клиентов" not in options
            and "Операции" not in options
        )
        assert not any("clients(" in q or "transactions(" in q for q in seen)
    elif role == "ADMIN":
        assert "Сотрудники" in options and "Ввод транзакций" in options
    else:
        assert "Профили клиентов" in options and "Операции" in options


def test_profile_first_page_keeps_selection_until_explicit_refresh():
    seen = []
    state = {"new": False}
    old = profile()
    new = dict(old, id="new-profile", version=old["version"] + 1)

    def api(q, a=None):
        if "behaviorProfilePage" in q:
            seen.append(a["t"])
            selected = new if state["new"] and a["t"] is None else old
            return dict(behaviorProfilePage=dict(items=[selected], totalCount=1, currentPageToken="first-frozen", nextPageToken=None))
        selected = new if a["id"] == "new-profile" else old
        return dict(profileVersion=dict(metadata={}, metrics=selected, servedAt=NOW.isoformat()))

    with patch("builtins.workspace_test_api", api, create=True):
        app = AppTest.from_string(
            'from frontend.workspace_view import client_workspace\nimport builtins\nclient_workspace(builtins.workspace_test_api,dict(id="client",accounts=[]))', default_timeout=20
        ).run()
        state["new"] = True
        app.run()
        assert seen == [None, "first-frozen"] and not app.exception
        assert "new-profile" not in str(app.selectbox[-1].value)
        next(b for b in app.button if b.label == "Обновить список профилей").click().run()
        assert seen[-1] is None and app.selectbox[-1].value == "new-profile"
