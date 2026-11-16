import ast
from pathlib import Path
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('alive,expected',[(True,True),(False,False),(None,False)])
def test_actual_readiness_function_distinguishes_workers_from_database(alive,expected,monkeypatch):
    monkeypatch.setenv('KAFKA_BOOTSTRAP','audit:9092')
    from backend.infrastructure import broker_health
    monkeypatch.setattr(broker_health,'observation',lambda:(True,'2026-11-10T00:00:00Z'))
    tree=ast.parse(Path('backend/infrastructure/server.py').read_text(encoding='utf-8'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='readiness')
    scope=dict(DB=SimpleNamespace(health=lambda:dict(database='audit',tables=12)),
               WORKERS=[] if alive is None else [SimpleNamespace(name='audit-worker',is_alive=lambda:alive)],
               SERVICE='analysis',os=__import__('os'),AdminClient=lambda _:SimpleNamespace(list_topics=lambda **k:{}))
    exec(compile(ast.Module(body=[node],type_ignores=[]),'actual-readiness','exec'),scope)
    value=scope['readiness']()
    assert value['ready'] is True and value['workersReady']==expected
    assert value['asynchronousReady']==expected and value['activity']['state']=='UNKNOWN'
