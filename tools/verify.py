"""Inspect the real unified deployment; save facts without credentials."""
import ast
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import subprocess
import sys

import psycopg
import requests
import grpc
from graphql import build_schema
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.generated import bank_pb2 as bank, bank_pb2_grpc as rpc

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'artifacts/verification'
OUTPUT.mkdir(parents=True, exist_ok=True)
settings = dict(line.split('=', 1) for line in (ROOT/'.env').read_text().splitlines()
                if '=' in line and not line.startswith('#'))

def main():
    checks = []
    def checked(name, condition, **facts):
        checks.append(dict(name=name, ok=bool(condition), **facts))
        if not condition:
            raise AssertionError(name)

    ids = subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.project=bad_trans'], text=True).split()
    checked('all_containers_running', len(ids) == 15, running=len(ids), expected=15)
    inspected = json.loads(subprocess.check_output(['docker','inspect',*ids],text=True,encoding='utf-8'))
    containers = [dict(service=item['Config']['Labels']['com.docker.compose.service'],
                       running=item['State']['Running'],health=item['State'].get('Health',{}).get('Status'))
                  for item in inspected]
    checked('container_health',all(item['running'] and item['health'] in (None,'healthy') for item in containers))
    with requests.get('http://127.0.0.1:18100/health',timeout=20) as response:
        response.raise_for_status()
        health=response.json()
    checked('service_readiness',health['allReady'] and health['auth']['redisReady'] and health['cache']['ready'])
    actual_fault_mode=any(entry=='ENABLE_TEST_FAULTS=1' for item in inspected for entry in item['Config'].get('Env',[]))
    expected_fault_mode=os.getenv('ENABLE_TEST_FAULTS')=='1'
    checked('test_fault_mode',actual_fault_mode==expected_fault_mode,enabled=actual_fault_mode)
    schema_text=requests.get('http://127.0.0.1:18100/schema.graphql',timeout=10).text
    schema=build_schema(schema_text)
    required=json.loads((ROOT/'backend/contracts/required_api.json').read_text())
    checked('complete_api',set(required['query'])<=set(schema.query_type.fields) and set(required['mutation'])<=set(schema.mutation_type.fields),
            queries=list(schema.query_type.fields),mutations=list(schema.mutation_type.fields))
    checked('published_schema_matches_runtime',build_schema((ROOT/'backend/contracts/schema.graphql').read_text(encoding='utf-8')).to_kwargs()['query'].fields.keys()==schema.query_type.fields.keys())
    (OUTPUT/'schema.graphql').write_text(schema_text,encoding='utf-8')
    databases=[]
    for index,owner in enumerate(('auth','transaction','analysis','case')):
        with psycopg.connect(host='127.0.0.1',port=57741+index,user='bank_'+owner,
                             dbname='bank_'+owner,password=settings['DB_PASSWORD'],connect_timeout=5) as connection:
            current_user,database=connection.execute('SELECT current_user,current_database()').fetchone()
            tables=[row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
        checked(owner+'_database_password',current_user=='bank_'+owner and database=='bank_'+owner)
        databases.append(dict(service=owner,database=database,port=57741+index,tables=tables))
    for database in databases:
        owner=database['service']
        expected=set()
        for script in (ROOT/'infra/postgres'/owner).glob('*.sql'):
            expected.update(re.findall(r'CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)',script.read_text(encoding='utf-8'),re.I))
        checked(owner+'_required_tables',expected<=set(database['tables']),missing=sorted(expected-set(database['tables'])))
    checked('physical_data_model',len(databases)==4 and all(item['tables'] for item in databases),tables=sum(len(item['tables']) for item in databases))
    for index,owner in enumerate(('auth','transaction','analysis','case')):
        with grpc.insecure_channel(f'127.0.0.1:{51051+index}') as channel:
            response=getattr(rpc,owner.title()+'ServiceStub')(channel).Health(
                bank.HealthRequest(),metadata=(('x-service-token',settings['SERVICE_TOKEN']),),timeout=5)
        checked(owner+'_typed_grpc_health',response.status=='READY' and response.database=='bank_'+owner)
    for name,url in (('frontend','http://127.0.0.1:18500/_stcore/health'),('graphiql','http://127.0.0.1:18100/graphql'),
                     ('auth_swagger','http://127.0.0.1:18110/docs'),('pgadmin','http://127.0.0.1:18180/login'),('kafka_ui','http://127.0.0.1:18190')):
        response=requests.get(url,headers={'Accept':'text/html'},timeout=20)
        checked(name+'_http',response.status_code==200,status=response.status_code,url=url)
    topics=subprocess.check_output(['docker','compose','--project-name','bad_trans','exec','-T','kafka',
        '/opt/kafka/bin/kafka-topics.sh','--bootstrap-server','localhost:9092','--list'],cwd=ROOT,text=True).splitlines()
    checked('domain_topics',{'transaction.registered.v1','analysis.completed.v1','case.feedback.v1','bank.dead-letter.v1'}<=set(topics))
    checked('no_historical_imports',not any(isinstance(node,ast.ImportFrom) and (node.module or '').startswith('back.')
        for path in (ROOT/'backend').rglob('*.py') for node in ast.walk(ast.parse(path.read_text(encoding='utf-8')))))
    volumes=subprocess.check_output(['docker','volume','ls','--filter','label=com.docker.compose.project=bad_trans','--format','{{.Name}}'],text=True).splitlines()
    checked('independent_data_volumes',len(volumes)==7 and all(name.startswith('bad_trans_') for name in volumes))
    result=dict(checkedAt=datetime.now(timezone.utc).isoformat(),checks=checks,containers=containers,
                databases=databases,topics=topics,volumes=volumes,health=health)
    (OUTPUT/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"Passed {len(checks)} checks: 15 containers, 4 database password logins, {sum(len(d['tables']) for d in databases)} tables, {len(schema.query_type.fields)} Query / {len(schema.mutation_type.fields)} Mutation.")

if __name__=='__main__':
    main()
