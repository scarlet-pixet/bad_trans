"""Query errors must use the same stable public code as other data operations."""
import asyncio
from types import SimpleNamespace

from graphql import GraphQLError
import psycopg
import pytest

from backend.gateway.operations import Query


def test_summary_storage_error_is_classified_and_details_are_hidden():
    def unavailable(**kwargs):
        raise psycopg.errors.QueryCanceled('private SQL diagnostic')

    info = SimpleNamespace(context={'clients': SimpleNamespace(
        transaction=SimpleNamespace(transaction_summary=unavailable))})
    with pytest.raises(GraphQLError) as error:
        asyncio.run(Query().transactionSummary(info))
    assert error.value.extensions == {'code': 'DATA_ACCESS_ERROR', 'service': 'transaction'}
    assert 'private SQL' not in str(error.value)
