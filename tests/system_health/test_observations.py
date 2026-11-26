import os,json,subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
import psycopg
from psycopg.rows import dict_row
from backend.infrastructure.health_activity import activity
from backend.infrastructure import broker_health


def test_real_owner_activity_is_readonly():
    path=os.getenv('HEALTH_STAND_CONFIG')
    if not path:pytest.skip('Explicit isolated health stand required')
    config=json.loads(Path(path).read_text())
    for owner in ['transaction','analysis','case']:
        name='bad-trans-health10-audit-'+owner+'-db-1'
        d=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
        assert d['Config']['Labels']['bad_trans.audit']=='system-health-stage10'
        port=int(d['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']);env=config['services'][owner+'-db']['environment']
        class DB:
            def connect(self):return psycopg.connect(host='127.0.0.1',port=port,user=env['POSTGRES_USER'],password=env['POSTGRES_PASSWORD'],dbname=env['POSTGRES_DB'],row_factory=dict_row,options='-c default_transaction_read_only=on')
        result=activity(DB(),owner)
        assert result['pendingOutbox']>=0 and result['measuredAt']
        if owner=='analysis':assert 'refreshJobs' in result


@pytest.mark.parametrize('failed',[False,True])
def test_broker_background_observations_and_expiry(monkeypatch,failed):
    monkeypatch.setenv('KAFKA_BOOTSTRAP','isolated:9092')
    def probe(**kwargs):
        if failed:raise RuntimeError('private')
        return SimpleNamespace(brokers={1:'audit'})
    monkeypatch.setattr(broker_health,'AdminClient',lambda _:SimpleNamespace(list_topics=probe))
    def stop(_):raise StopIteration
    monkeypatch.setattr(broker_health.time,'sleep',stop)
    with pytest.raises(StopIteration):broker_health.loop()
    ready,stamp=broker_health.observation();assert ready is (not failed) and stamp
    monkeypatch.setattr(broker_health,'_observation',(True,stamp,0))
    monkeypatch.setattr(broker_health.time,'monotonic',lambda:21)
    assert broker_health.observation()==(None,stamp)


def test_broker_probe_reconnects_after_a_failed_observation(monkeypatch):
    states, connections = [], []
    monkeypatch.setenv("KAFKA_BOOTSTRAP", "isolated:9092")
    monkeypatch.setattr(broker_health, "_observation", (None, None, 0.0))

    def admin(config):
        assert config == {"bootstrap.servers": "isolated:9092"}
        unavailable = not connections
        connections.append(config)

        def topics(timeout):
            assert timeout == 0.5
            if unavailable:
                raise TimeoutError("broker changed")
            return SimpleNamespace(brokers={1: "isolated"})

        return SimpleNamespace(list_topics=topics)

    def pause(_seconds):
        states.append(broker_health.observation()[0])
        if len(states) == 3:
            raise StopIteration

    monkeypatch.setattr(broker_health, "AdminClient", admin)
    monkeypatch.setattr(broker_health.time, "sleep", pause)
    with pytest.raises(StopIteration):
        broker_health.loop()
    assert states == [False, True, True]
    assert len(connections) == 2
