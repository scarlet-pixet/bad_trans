from types import SimpleNamespace
from unittest.mock import patch

import pytest
from backend.common.data import DomainError
from backend.common.navigation import navigation

from streamlit.testing.v1 import AppTest


def test_client_cursor_resets_when_filters_change_even_when_returning_to_prior_filter():
    requests = []

    def api(query, variables):
        requests.append(variables)
        cursor = variables["page"]["cursor"]
        number = 101 if cursor else 1
        return {"clients": {
            "items": [{"id": str(number), "externalId": f"CL-{number}",
                       "displayName": "Имя", "accounts": []}],
            "totalCount": 121,
            "nextCursor": None if cursor else "page-two",
        }}

    with patch("builtins.navigation_api", api, create=True):
        app = AppTest.from_string(
            'import builtins\nfrom frontend.navigation import choose_client\n'
            'choose_client(builtins.navigation_api,"test-picker")'
        ).run()
        app.button(key="test-picker:next").click().run()
        assert requests[-1]["page"]["cursor"] == "page-two"
        assert next(box for box in app.selectbox if box.label == "Клиент").value == "101"
        external = app.text_input(key="test-picker:external")
        external.set_value("CL-101").run()
        assert requests[-1]["f"] == {"externalId": "CL-101"}
        assert requests[-1]["page"]["cursor"] is None
        app.text_input(key="test-picker:external").set_value("").run()
        assert requests[-1]["f"] == {} and requests[-1]["page"]["cursor"] is None
        assert not app.exception


def test_back_reuses_first_cursor_and_refresh_clears_it():
    requests = []

    def api(query, variables):
        cursor = variables["page"]["cursor"]
        requests.append(cursor)
        return {"transactions": {
            "items": [], "totalCount": 42,
            "currentCursor": cursor or "fixed-first",
            "nextCursor": None if cursor == "fixed-second" else "fixed-second",
        }}

    with patch("builtins.navigation_api", api, create=True):
        app = AppTest.from_string(
            'import builtins\nfrom frontend.navigation import cursor_page\n'
            'cursor_page(builtins.navigation_api,"query","transactions",{},"id","nav")'
        ).run()
        app.button(key="nav:next").click().run()
        assert requests[-1] == "fixed-second"
        app.button(key="nav:back").click().run()
        assert requests[-1] == "fixed-first"
        app.button(key="nav:refresh").click().run()
        assert requests[-1] is None
        assert not app.exception


def test_signed_cursor_checks_role_scope_limit_and_expiration(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "test-cursor-signing-key")
    actor = SimpleNamespace(id="staff", role="MANAGER")
    allowed = {"id": "id"}
    scope = dict(actor=actor, owner="notifications", filters={"unreadOnly": True})
    page = navigation(None, allowed, "id", **scope)
    state = page.first(total=5, snapshot="1:2:")
    token = page.token(state)
    assert navigation({"cursor": token}, allowed, "id", **scope).state == state
    with pytest.raises(DomainError, match="Обновить список"):
        navigation({"cursor": token, "limit": 25}, allowed, "id", **scope)
    other = dict(scope, actor=SimpleNamespace(id="staff", role="ANALYST"))
    with pytest.raises(DomainError, match="Обновить список"):
        navigation({"cursor": token}, allowed, "id", **other)
    monkeypatch.setattr("backend.common.navigation.time.time", lambda: state["expires"] + 1)
    with pytest.raises(DomainError, match="Обновить список"):
        navigation({"cursor": token}, allowed, "id", **scope)
