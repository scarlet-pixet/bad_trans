from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from streamlit.testing.v1 import AppTest

from backend.services.analysis.profiles.metrics import build_metrics
from backend.services.analysis.profiles.service import ProfileService

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def profile(empty=False):
    row = dict(
        id="one",
        clientId="client",
        currency="BYN",
        direction="OUT",
        amount="100.00",
        counterpartyId=None,
        occurredAt=(NOW - timedelta(days=1)).isoformat(),
        ingestedAt=(NOW - timedelta(minutes=1)).isoformat(),
    )
    return dict(
        build_metrics([] if empty else [row], NOW, NOW, "BYN", "OUT", 30),
        id="version",
        clientId="client",
        currency="BYN",
        direction="OUT",
        version=1,
        origin="SCHEDULED",
    )


def test_age_is_response_metadata_and_does_not_change_immutable_metrics(monkeypatch):
    monkeypatch.setenv("PROFILE_REFRESH_SECONDS", "300")
    metrics = profile()
    row = dict(
        id="version",
        client_id="client",
        currency="BYN",
        direction="OUT",
        version=1,
        origin="SCHEDULED",
        created_at=NOW,
        metrics=metrics,
    )
    result = ProfileService.profile_wire(row, served_at=NOW + timedelta(seconds=600))
    assert result["freshness"]["ageSeconds"] == 600
    assert result["freshness"]["status"] == "STALE"
    assert "freshness" not in metrics


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_manager_profile_page_handles_unknown_rates_and_shows_data_quality(
    legacy, empty
):
    p = profile(empty)
    if legacy:
        for name in ["quality", "metricsSchemaVersion", "operationsPerDay"]:
            p.pop(name, None)
        p["dailyAverage"] = 0.033

    def post(_url, json, **kwargs):
        query = json["query"]
        if "me{" in query:
            data = {"me": {"id": "manager", "login": "manager", "role": "MANAGER"}}
        elif "managerDashboard" in query:
            data = {
                "managerDashboard": dict(components={}, partialErrors=[], totals=[])
            }
        elif "clients(" in query:
            data = {
                "clients": {
                    "items": [
                        {
                            "id": "client",
                            "displayName": "Проверка профиля",
                            "accounts": [],
                        }
                    ]
                }
            }
        elif "behaviorProfilePage" in query:
            data = {"behaviorProfilePage":dict(items=[p],totalCount=1,nextPageToken=None)}
        elif "profileVersion" in query:
            data = {"profileVersion":dict(metadata={k:p[k] for k in ['id','clientId','currency','direction','version','origin','asOf']},metrics=p,servedAt=NOW.isoformat())}
        else:
            data = {}
        return Mock(status_code=200, json=lambda: {"data": data})

    with patch("requests.post", side_effect=post):
        app = AppTest.from_file(str(ROOT / "frontend/app.py"), default_timeout=20)
        app.session_state.token = "test-token"
        # Select the profile workspace before the first full application run.
        app.session_state["Раздел"] = "Профили клиентов"
        app.run()
        if app.sidebar.radio[0].value != "Профили клиентов":
            app.sidebar.radio[0].set_value("Профили клиентов").run()
        assert not app.exception, [e.message for e in app.exception]
        captions = " ".join(x.value for x in app.caption)
        assert "None" not in captions
        assert "период наблюдения не подтверждён" in captions.lower()
        assert any(
            m.label == "Операций" and m.value == str(0 if empty else 1)
            for m in app.metric
        )
        if not legacy:
            assert any(
                m.label == "Объём операций" and ("0.00" if empty else "100") in m.value
                for m in app.metric
            )
            assert any(x.label == "Качество истории" for x in app.expander)


def test_wire_schema_and_identity_come_from_relational_metadata():
    from backend.services.analysis.profiles.storage import version_wire
    row=dict(id="canonical-id",version=3,calculation_id="calculation-id",metrics_schema_version=2,
             history_policy_version="ALL_OBSERVED-v1",metrics=dict(id="payload-id",version=99,
             metricsSchemaVersion=2,sampleSize=5))
    result=version_wire(row)
    assert result["id"]=="canonical-id"
    assert result["version"]==3
    assert result["metricsSchemaVersion"]==2
    assert result["sampleSize"]==5
