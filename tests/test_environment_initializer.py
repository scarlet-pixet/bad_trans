"""Environment initialization must preserve active deployment credentials."""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('initial', ('', '# template\nJWT_SECRET=keep-existing\nSERVICE_TOKEN=\n'))
def test_initializer_completes_existing_environment(tmp_path, initial):
    tools = tmp_path / 'tools'
    tools.mkdir()
    source = Path(__file__).parents[1] / 'tools/init_env.ps1'
    script = tools / 'init_env.ps1'
    script.write_bytes(source.read_bytes())
    target = tmp_path / '.env'
    if initial:
        target.write_text(initial, encoding='utf-8')
    subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)], check=True, capture_output=True)
    first = target.read_bytes()
    values = dict(line.split('=', 1) for line in first.decode('utf-8-sig').splitlines() if '=' in line and not line.startswith('#'))
    assert all(values.get(key) for key in ('DB_PASSWORD','REDIS_PASSWORD','JWT_SECRET','SERVICE_TOKEN','PGADMIN_PASSWORD','REPORT_SIGNING_KEY'))
    if initial:
        assert values['JWT_SECRET'] == 'keep-existing'
    subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)], check=True, capture_output=True)
    assert target.read_bytes() == first
