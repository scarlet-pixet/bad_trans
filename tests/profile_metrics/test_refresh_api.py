from types import SimpleNamespace

import pytest
from graphql import build_schema

from backend.gateway.app import schema
from backend.gateway.security import QUERY_ROLES


@pytest.mark.parametrize(
    "query,method",
    [
        (
            'query{behaviorProfilePage(currency:"BYN",direction:"OUT",origin:"SCHEDULED",limit:1)}',
            "get_behavior_profile_page",
        ),
        ("query{profileRefreshStatus}", "get_profile_refresh_status"),
    ],
)
def test_actual_graphql_schema_forwards_new_queries(query, method):
    calls = []

    class Client:
        def _invoke(self, name, arguments):
            calls.append((name, arguments))
            return {"ok": True}

    # Strawberry resolvers are async; use the real executable schema.
    import asyncio

    result = asyncio.run(
        schema.execute(
            query, context_value={"clients": SimpleNamespace(analysis=Client())}
        )
    )
    assert result.errors is None and calls[0][0] == method
    if method == "get_behavior_profile_page":
        assert calls[0][1]["currency"] == "BYN" and calls[0][1]["limit"] == 1


def test_new_contract_is_published_and_roles_do_not_expand_old_access():
    from pathlib import Path

    published = build_schema(
        Path("backend/contracts/schema.graphql").read_text(encoding="utf-8")
    )
    import json
    required=json.loads(Path('backend/contracts/required_api.json').read_text())
    assert set(required['query']) <= set(published.query_type.fields)
    assert set(required['mutation']) <= set(published.mutation_type.fields)
    assert "behaviorProfilePage" not in QUERY_ROLES["ANALYST"]
    assert {'profileVersion','resultHistory'} <= QUERY_ROLES['ANALYST']
    assert "behaviorProfilePage" not in QUERY_ROLES["ADMIN"]
    assert "profileRefreshStatus" in QUERY_ROLES["ADMIN"]
    assert "profileRefreshStatus" not in QUERY_ROLES["ANALYST"]
