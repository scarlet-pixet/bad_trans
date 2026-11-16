"""Auth REST maps dependency failures to availability errors, not client input."""
import asyncio

from backend.common.data import DomainError
from backend.services.auth.transport.http import domain_error


def test_auth_dependency_error_is_http_503():
    response = asyncio.run(domain_error(None, DomainError('DEPENDENCY_UNAVAILABLE', 'Unavailable')))
    assert response.status_code == 503


def test_auth_storage_error_is_http_500():
    response = asyncio.run(domain_error(None, DomainError('DATA_ACCESS_ERROR', 'Storage failed')))
    assert response.status_code == 500
