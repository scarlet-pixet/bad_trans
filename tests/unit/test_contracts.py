"""Guard the public contract and the boundaries affected by consolidation."""
import ast
from pathlib import Path

from graphql import build_schema
from backend.gateway.app import schema
from backend.generated import bank_pb2

ROOT=Path(__file__).resolve().parents[2]

def test_all_public_fields_and_arguments_are_implemented():
    published=build_schema((ROOT/'backend/contracts/schema.graphql').read_text(encoding='utf-8'))
    actual=build_schema(schema.as_str())
    for before,after in ((published.query_type,actual.query_type),(published.mutation_type,actual.mutation_type)):
        assert set(before.fields)<=set(after.fields)
        for name,expected in before.fields.items():
            implemented=after.fields[name]
            assert str(expected.type)==str(implemented.type),name
            assert {key:str(value.type) for key,value in expected.args.items()}=={key:str(value.type) for key,value in implemented.args.items()},name
    import json
    required=json.loads((ROOT/'backend/contracts/required_api.json').read_text())
    assert set(required['query']) <= set(actual.query_type.fields)
    assert set(required['mutation']) <= set(actual.mutation_type.fields)
    assert 'password' not in actual.get_type('User').fields

def test_public_protobuf_keeps_all_four_service_contracts():
    assert bank_pb2.DESCRIPTOR.package=='bank.v1'
    assert set(bank_pb2.DESCRIPTOR.services_by_name)=={'AuthService','TransactionService','AnalysisService','CaseService'}

def test_runtime_has_no_imports_from_the_old_lab_tree():
    for path in (ROOT/'backend').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node,ast.ImportFrom):
                assert not (node.module or '').startswith(('back.','tests.')),str(path)
            if isinstance(node,ast.Import):
                assert not any(item.name.startswith('back.') for item in node.names),str(path)

def test_gateway_never_reads_database_configuration():
    for path in (ROOT/'backend/gateway').glob('*.py'):
        source=path.read_text(encoding='utf-8')
        assert 'DATABASE_URL' not in source,str(path)
        assert 'psycopg.connect' not in source,str(path)


def test_no_protobuf_rpc_is_left_as_an_unimplemented_generated_stub(monkeypatch):
    monkeypatch.setenv('SERVICE_NAME','transaction')
    monkeypatch.setenv('SERVICE_TOKEN','contract-check-only')
    monkeypatch.setenv('DATABASE_URL','postgresql://unused:unused@127.0.0.1/unused')
    from backend.infrastructure.server import TypedTransaction, TypedAnalysis, TypedCase
    from backend.services.auth.transport.grpc import AuthGrpcService
    from backend.generated import bank_pb2_grpc as rpc
    classes={'AuthService':AuthGrpcService,'TransactionService':TypedTransaction,
             'AnalysisService':TypedAnalysis,'CaseService':TypedCase}
    for name,descriptor in bank_pb2.DESCRIPTOR.services_by_name.items():
        generated=getattr(rpc,name+'Servicer')
        for method in descriptor.methods:
            assert getattr(classes[name],method.name) is not getattr(generated,method.name),(name,method.name)
