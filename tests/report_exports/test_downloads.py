import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest
from starlette.requests import Request
from backend.common.data import DomainError
from backend.gateway.downloads import download_report
from backend.infrastructure.resilience import Retry,Details
from backend.generated.transport_pb2 import InvocationRequest
import grpc

ID='00000000-0000-0000-0000-000000000109'
USER=dict(id=ID,login='manager',role='MANAGER',isActive=True)


def request(auth,token='session',format='PDF'):
    body=urlencode(dict(token=token,format=format)).encode()
    async def receive():return dict(type='http.request',body=body,more_body=False)
    return Request(dict(type='http',method='POST',path='/report-files/'+ID,headers=[(b'content-type',b'application/x-www-form-urlencoded')],app=SimpleNamespace(state=SimpleNamespace(auth_api=auth))),receive)


def test_download_checks_auth_each_click_and_never_caches_binary(monkeypatch):
    auth=SimpleNamespace(validate=Mock(return_value=USER));invoke=Mock(return_value=dict(contentBase64=base64.b64encode(b'pdf-file').decode(),mediaType='application/pdf',fileName='report.pdf',snapshotHash='a'*64,exportVersion='report-export-v1'))
    monkeypatch.setattr('backend.gateway.downloads.RemoteClient',lambda *a,**k:SimpleNamespace(_invoke=invoke))
    response=asyncio.run(download_report(request(auth),ID))
    assert response.status_code==200 and response.body==b'pdf-file'
    assert response.headers['cache-control']=='no-store, private' and 'attachment' in response.headers['content-disposition']
    assert auth.validate.call_count==2 and invoke.call_count==1
    auth.validate.side_effect=DomainError('UNAUTHENTICATED','session revoked')
    again=asyncio.run(download_report(request(auth),ID))
    assert again.status_code==401 and invoke.call_count==1


@pytest.mark.parametrize('role',['ADMIN','ANALYST'])
def test_download_role_and_owner_deny_without_file(monkeypatch,role):
    auth=SimpleNamespace(validate=Mock(return_value=dict(USER,role=role)))
    invoke=Mock(side_effect=DomainError('FORBIDDEN','assignment revoked'))
    monkeypatch.setattr('backend.gateway.downloads.RemoteClient',lambda *a,**k:SimpleNamespace(_invoke=invoke))
    response=asyncio.run(download_report(request(auth),ID))
    assert response.status_code==403 and b'pdf-file' not in response.body
    assert invoke.call_count==(0 if role=='ADMIN' else 1)


def test_role_change_during_render_and_no_credentials_are_denied(monkeypatch):
    auth=SimpleNamespace(validate=Mock(side_effect=[USER,dict(USER,role='ADMIN')]))
    invoke=Mock(return_value=dict(contentBase64=base64.b64encode(b'file').decode(),mediaType='application/pdf',fileName='x.pdf',snapshotHash='a'*64,exportVersion='v1'))
    monkeypatch.setattr('backend.gateway.downloads.RemoteClient',lambda *a,**k:SimpleNamespace(_invoke=invoke))
    assert asyncio.run(download_report(request(auth),ID)).status_code==403
    assert asyncio.run(download_report(request(auth,token=''),ID)).status_code==401


def test_export_deadline_is_bounded_single_attempt_and_other_reads_unchanged(monkeypatch):
    monkeypatch.setenv('RPC_READ_TIMEOUT','0.8');monkeypatch.setenv('REPORT_EXPORT_TIMEOUT_SECONDS','30')
    seen=[]
    def continuation(details,request):seen.append(details);return SimpleNamespace(code=lambda:grpc.StatusCode.UNAVAILABLE)
    details=Details('/bad.Business/Invoke',30,[],None,None,None)
    Retry('case').intercept_unary_unary(continuation,details,InvocationRequest(method='export_snapshot_report'))
    assert len(seen)==1 and seen[0].timeout==30
    seen.clear();Retry('case').intercept_unary_unary(continuation,details,InvocationRequest(method='get_report'))
    assert len(seen)==3 and all(x.timeout==0.8 for x in seen)


def test_browser_forms_post_own_session_without_url_credentials(monkeypatch):
    from frontend.export_buttons import download_buttons
    from streamlit.testing.v1 import AppTest
    with __import__('unittest.mock',fromlist=['patch']).patch('streamlit.components.v1.html') as render:
        app=AppTest.from_string('import streamlit as st\nfrom frontend.export_buttons import download_buttons\nst.session_state.token="test-own-session"\ndownload_buttons("00000000-0000-0000-0000-000000000109")',default_timeout=20).run()
    assert not app.exception
    html=render.call_args.args[0]
    assert html.count('method="post"')==3 and '<script' not in html
    assert 'name="token" value="test-own-session"' in html
    assert '?token' not in html and '/report-files/00000000-0000-0000-0000-000000000109' in html
