"""Compose boundaries for the unified bad_trans application."""
from pathlib import Path
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_builds_storage_and_startup_boundaries(tmp_path):
    blank = tmp_path / '.env'
    blank.write_text('', encoding='utf-8')
    env = dict(os.environ)
    env.update({key: 'test-value' for key in ('DB_PASSWORD','REDIS_PASSWORD','JWT_SECRET','SERVICE_TOKEN','PGADMIN_PASSWORD','REPORT_SIGNING_KEY')})
    result = subprocess.run(['docker','compose','--env-file',str(blank),'config','--format','json'],
                            cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',check=True)
    d = json.loads(result.stdout)
    assert d['name'] == 'bad_trans'
    services = d['services']
    apps = ('auth-service','transaction-service','analysis-service','case-service','gateway','frontend')
    assert len({services[name]['image'] for name in apps}) == len(apps)
    assert all(services[name]['build']['dockerfile'] == 'Dockerfile' for name in apps)
    for owner in ('transaction','analysis','case'):
        app = services[owner+'-service']
        assert set(app['depends_on']) == {owner+'-db'}
        assert f'bank_{owner}' in app['environment']['DATABASE_URL']
        assert app['healthcheck']['test'] == ['CMD','python','-m','backend.infrastructure.healthcheck']
    assert not services['gateway'].get('depends_on')
    assert '/livez' in services['gateway']['healthcheck']['test'][-1]
    assert services['frontend']['depends_on']['gateway']['condition'] == 'service_started'

    assert services['gateway']['environment']['REPORT_SIGNING_KEY'] == 'test-value'
    assert services['case-service']['environment']['REPORT_SIGNING_KEY'] == 'test-value'
    assert all('REPORT_SIGNING_KEY' not in services[name].get('environment',{}) for name in ('transaction-service','analysis-service','auth-service','frontend'))
