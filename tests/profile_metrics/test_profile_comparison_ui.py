from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from tests.profile_metrics.test_profile_comparison import compare, profile


def test_selectors_reach_other_pages_and_render_hand_checked_deltas():
    a = profile()
    a["metadata"].update(id="old", version=1)
    b = profile(medianAmount="150", operationsPerDay="3", quality=dict(status="READY"))
    b["metadata"].update(id="new", version=2)
    c = profile(medianAmount="50")
    c["metadata"].update(id="older", version=3)
    saved = dict(old=a, new=b, older=c)
    calls = []

    def api(query, args=None):
        if "behaviorProfilePage" in query:
            older = args["t"] == "older-page"
            return dict(
                behaviorProfilePage=dict(
                    items=[
                        saved[k]["metadata"]
                        for k in (["older"] if older else ["new", "old"])
                    ],
                    totalCount=3,
                    currentPageToken="older-page" if older else "first-page",
                    nextPageToken=None if older else "older-page",
                )
            )
        calls.append(args)
        return dict(compareProfileVersions=compare(saved[args["a"]], saved[args["b"]]))

    with patch("builtins.comparison_api", api, create=True):
        app = AppTest.from_string(
            "import builtins\nfrom frontend.profile_comparison import render_profile_comparison\n"
            f"render_profile_comparison(builtins.comparison_api,{a['metadata']!r})",
            default_timeout=15,
        ).run()
        assert not app.exception
        row = app.dataframe[0].value.iloc[0]
        assert (
            row["Изменение"] == "50" and row["Было"] == "100" and row["Стало"] == "150"
        )
        app.button[1].click().run()
        assert not app.exception
        assert calls[-1] == dict(a="older", b="new")
        assert app.dataframe[0].value.iloc[0]["Изменение"] == "100"


def test_api_failure_does_not_render_a_false_empty_comparison():
    a = profile()

    def api(query, args=None):
        return None

    with patch("builtins.comparison_api", api, create=True):
        app = AppTest.from_string(
            "import builtins\nfrom frontend.profile_comparison import render_profile_comparison\n"
            + f"render_profile_comparison(builtins.comparison_api,{a['metadata']!r})",
            default_timeout=15,
        ).run()
    assert not app.exception
    assert not app.dataframe
