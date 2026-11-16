import requests
import redis
import pytest
from backend.gateway import health


@pytest.mark.parametrize('failure',[None,'auth','kafka','cache','owner'])
def test_failures_and_recovery(monkeypatch,failure):
    monkeypatch.setenv('AUTH_URL','http://auth');monkeypatch.setenv('CACHE_URL','redis://cache')
    class Client:
        def __init__(self,name,*args):self.name=name
        def _invoke(self,*args):
            if failure=='owner' and self.name=='analysis':raise RuntimeError('private error')
            return dict(ready=True,kafkaReady=failure!='kafka',asynchronousReady=failure!='kafka',database=self.name)
    class Cache:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def ping(self):
            if failure=='cache':raise redis.ConnectionError('secret')
            return True
        def dbsize(self):return 0
    def auth(*args,**kwargs):
        if failure=='auth':raise requests.ConnectionError('private url')
        class Response:
            def json(self):return dict(ready=True,redisReady=True,database='auth',password='secret')
        return Response()
    monkeypatch.setattr(health,'RemoteClient',Client);monkeypatch.setattr(health.requests,'get',auth)
    monkeypatch.setattr(health.redis.Redis,'from_url',lambda *a,**k:Cache())
    value=health.collect_health(33,26)
    assert value['allReady']==(failure in (None,'cache'))
    assert value['degraded']==(failure=='cache')
    assert value['synchronousReady']==(failure not in ('auth','owner'))
    assert 'secret' not in str(value) and 'private' not in str(value)
    assert value['measuredAt'] and value['gateway']['processAlive']
    failure=None
    assert health.collect_health(33,26)['allReady']


def test_ui_renders_all_services_and_unknowns():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_string('''from frontend.system_health import render_health
render_health(dict(services=[dict(service=n) for n in ['transaction','analysis','case']],auth={},cache=dict(mode='bypass'),gateway=dict(processAlive=True)))''').run(timeout=10)
    assert not app.exception
    assert len(app.dataframe[0].value)==5
    assert any('Деградация' in str(x.value) for x in app.markdown)
