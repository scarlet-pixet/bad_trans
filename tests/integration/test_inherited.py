import os
"""Live integration checks for the unified deployment."""
from pathlib import Path
import json
from uuid import uuid4
import grpc
import requests
from backend.generated import bank_pb2 as bank, bank_pb2_grpc as rpc
ROOT = Path(__file__).resolve().parents[2]
EVIDENCE=Path(os.getenv('BAD_TRANS_EVIDENCE_DIR',str(ROOT/'artifacts/verification')))
EVIDENCE.mkdir(parents=True,exist_ok=True)
API = os.getenv('GRAPHQL_URL','http://127.0.0.1:18100/graphql')

def query(text, variables=None, token=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    return requests.post(API, json={'query': text, 'variables': variables}, headers=headers, timeout=20)

def manager():
    response = query('mutation { signIn(login:"manager",password:"BadTrans-demo-2026!"){accessToken} }')
    assert response.status_code == 200
    return response.json()['data']['signIn']['accessToken']

def test_healthy_real_services_and_security():
    response = requests.get(os.getenv('GRAPHQL_URL','http://127.0.0.1:18100/graphql').removesuffix('/graphql')+'/health', timeout=15)
    assert response.status_code == 200
    status = response.json()
    assert status['allReady'] and status['transport'] == 'gRPC + Kafka'
    assert status['mutationCount'] > 0
    assert {item['service'] for item in status['services']} == {'transaction', 'analysis', 'case'}
    assert query('query { me { login } }').status_code == 401
    token = manager()
    result = query('query { me { login role } transactionSummary { transactionCount } }', token=token)
    assert result.status_code == 200 and result.json()['data']['me']['role'] == 'MANAGER'

def test_lr2_protobuf_and_lr3_reads_use_network():
    proof = json.loads((EVIDENCE / 'live-proof.json').read_text())
    config = dict((line.split('=', 1) for line in (ROOT / '.env').read_text().splitlines() if '=' in line))
    ids = json.loads((ROOT / 'infra/seed_ids.json').read_text())
    metadata = (('x-service-token', config['SERVICE_TOKEN']), ('x-actor-id', ids['manager']),
                ('x-actor-login', 'manager'), ('x-actor-role', 'MANAGER'))
    with grpc.insecure_channel(os.getenv('BAD_TRANS_TRANSACTION_GRPC','127.0.0.1:51052')) as channel:
        response = rpc.TransactionServiceStub(channel).GetTransaction(
            bank.GetTransactionRequest(id=proof['transaction']['id']), timeout=10, metadata=metadata,
        )
    assert response.data.id == proof['transaction']['id']
    token = manager()
    result = query('query($tx:ID!,$run:ID!,$case:ID!){transaction(id:$tx){id amount} analysisRun(id:$run){id} reviewCase(id:$case){id latestScore}}', {'tx': proof['transaction']['id'], 'run': proof['analysis']['runId'], 'case': proof['case']['id']}, token)
    assert result.status_code == 200, result.text
    data = result.json()['data']
    assert data['transaction']['id'] == proof['transaction']['id']
    assert data['analysisRun']['id'] == proof['analysis']['runId']
    assert data['reviewCase']['latestScore'] == proof['analysis']['score']

def test_kafka_flow_persisted_in_three_databases():
    proof = json.loads((EVIDENCE / 'live-proof.json').read_text())
    assert proof['transactionOutboxPublished'] and proof['transactionOutboxCount'] == 1
    assert proof['idempotentRepeatSameId']
    assert proof['analysis']['inboxCount'] > 0 and proof['case']['inboxCount'] > 0
    assert proof['analysis']['score'] >= proof['analysis']['threshold']
    assert proof['graphqlCase']['transactionId'] == proof['transaction']['id']

def test_analysis_to_transaction_grpc_on_client_activity():
    proof = json.loads((EVIDENCE / 'live-proof.json').read_text())
    token = manager()
    response = query('query($client:ID!,$run:ID!){clientActivity(clientId:$client,period:{from:"2026-09-01T00:00:00Z",to:"2026-10-01T00:00:00Z"},currency:"BYN",runId:$run){client{id} transactions{id} partialErrors}}', {'client': proof['transaction']['clientId'], 'run': proof['analysis']['runId']}, token)
    assert response.status_code == 200, response.text
    assert 'errors' not in response.json(), response.json().get('errors')
    data = response.json()['data']['clientActivity']
    assert data['client']['id'] == proof['transaction']['clientId']
    assert proof['transaction']['id'] in {item['id'] for item in data['transactions']}
    assert data['partialErrors'] == []

def test_all_read_operations_preserve_the_lr2_contract_over_grpc():
    ids = json.loads((ROOT / 'infra/seed_ids.json').read_text())
    token = manager()
    fields = ['me{login role}', 'clients{totalCount items{id accounts{id}}}', 'transactions{totalCount items{id amount occurredAt ingestedAt}}', f'''transaction(id:"{ids['transaction']}"){{id occurredAt}}''', f'''clientActivity(clientId:"{ids['client']}",period:{{from:"2026-09-01T00:00:00Z",to:"2026-10-01T00:00:00Z"}},currency:"BYN",runId:"{ids['run2']}"){{client{{id}} partialErrors}}''', f'''profile(id:"{ids['profile2']}"){{id sampleSize windowStart windowEnd ingestedBefore medianAmount madAmount}}''', f'''analysisResult(id:"{ids['result2']}"){{id calculatedAt hits{{ruleCode}}}}''', f'''analysisRun(id:"{ids['run2']}"){{id status createdAt completedAt}}''', f'''compareRuns(firstId:"{ids['run1']}",secondId:"{ids['run2']}"){{commonCount changedScores}}''', f'''ruleVersions(ruleId:"{ids['AMOUNT']}"){{id version publishedAt}}''', 'reviewCases{totalCount items{id comments{id createdAt} decisions{id decidedAt}}}', f'''reviewCase(id:"{ids['case']}"){{id comments{{id createdAt}} decisions{{id decidedAt}}}}''', f'''report(id:"{ids['report']}"){{id snapshot periodStart periodEnd createdAt}}''', f'''exportReport(id:"{ids['report']}"){{fileName contentBase64}}''', 'notifications{totalCount items{id createdAt readAt}}', 'transactionSummary{transactionCount totalAmount}']
    response = query('query{' + ' '.join(fields) + '}', token=token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert not body.get('errors'), body
    assert len(body['data']) == 16 and all((value is not None for value in body['data'].values()))
    assert body['data']['clientActivity']['partialErrors'] == []
    missing = query('query{transaction(id:"00000000-0000-0000-0000-000000000001"){id}}', token=token)
    assert missing.json() == {'data': {'transaction': None}}

def test_analyst_investigation_and_registration_use_verified_role():
    ids = json.loads((ROOT / 'infra/seed_ids.json').read_text())
    signed_in = query('mutation{signIn(login:"analyst",password:"BadTrans-demo-2026!"){accessToken}}').json()
    token = signed_in['data']['signIn']['accessToken']
    response = query('query($tx:ID!,$result:ID!){transaction(id:$tx){id} analysisResult(id:$result){hits{ruleCode}}}', {'tx': ids['transaction'], 'result': ids['result2']}, token)
    assert response.status_code == 200 and (not response.json().get('errors')), response.text
    assert response.json()['data']['analysisResult']['hits']
    assert query('query{transactionSummary{transactionCount}}', token=token).status_code == 403
    values = {'input': {'source': 'analyst', 'externalId': uuid4().hex, 'accountId': ids['account'], 'amount': '10.00', 'currency': 'BYN', 'direction': 'OUT', 'occurredAt': '2026-10-18T12:00:00Z', 'idempotencyKey': uuid4().hex}}
    mutation = 'mutation($input:TransactionInput!){registerTransaction(input:$input){id amount}}'
    assert query(mutation, values, token).status_code == 403
    manager_token = manager()
    invalid = query(mutation, {'input': {**values['input'], 'amount': '-1.00'}}, manager_token)
    assert invalid.json()['errors'][0]['extensions']['code'] == 'BAD_INPUT'
    response = query(mutation, values, manager_token)
    assert response.status_code == 200 and (not response.json().get('errors')), response.text
    assert response.json()['data']['registerTransaction']['amount'] == '10.00'
    conflicting = query(mutation, {'input': {**values['input'], 'amount': '11.00'}}, manager_token)
    assert conflicting.json()['errors'][0]['extensions']['code'] == 'CONFLICT'
    duplicate = query(mutation, {'input': {**values['input'], 'idempotencyKey': uuid4().hex}}, manager_token)
    assert duplicate.json()['errors'][0]['extensions']['code'] == 'CONFLICT'

    def execute(text, values=None, principal='manager'):
        selected = {'manager': manager_token, 'analyst': token, 'admin': admin_token}[principal]
        result = query(text, values, selected)
        assert result.status_code == 200 and (not result.json().get('errors')), result.text
        return result.json()['data']
    admin_token = query('mutation{signIn(login:"admin",password:"BadTrans-demo-2026!"){accessToken}}').json()['data']['signIn']['accessToken']
    directory_client = execute('mutation($x:ClientInput!){createClient(input:$x){id}}', {'x': {'externalId': 'L6-ADMIN-' + uuid4().hex, 'displayName': 'Контроль справочника', 'idempotencyKey': uuid4().hex}}, 'admin')['createClient']
    counterparty = execute('mutation($x:CounterpartyInput!){registerCounterparty(input:$x){id}}', {'x': {'externalId': 'L6-CP-' + uuid4().hex, 'displayName': 'Контроль контрагента', 'idempotencyKey': uuid4().hex}}, 'admin')['registerCounterparty']
    get_case = 'query($id:ID!){reviewCase(id:$id){id status rowVersion}}'
    case = execute(get_case, {'id': ids['case']})['reviewCase']
    close = 'mutation($x:CaseDecisionInput!){closeCase(input:$x){id status rowVersion decisions{outcome}}}'
    if case['status'] != 'CLOSED':
        if case['status'] == 'ASSIGNED':
            case = execute('mutation($id:ID!,$v:Int!,$k:String!){takeCase(caseId:$id,expectedVersion:$v,idempotencyKey:$k){id status rowVersion}}', {'id': case['id'], 'v': case['rowVersion'], 'k': uuid4().hex}, 'analyst')['takeCase']
        case = execute(close, {'x': {'caseId': case['id'], 'outcome': 'EXPLAINED', 'reason': 'Подготовка проверки', 'expectedVersion': case['rowVersion'], 'idempotencyKey': uuid4().hex}})['closeCase']
    case = execute('mutation($id:ID!,$v:Int!,$k:String!){reopenCase(caseId:$id,reason:"Повторная проверка",expectedVersion:$v,idempotencyKey:$k){id status rowVersion}}', {'id': case['id'], 'v': case['rowVersion'], 'k': uuid4().hex})['reopenCase']
    states = [case['status']]
    take = 'mutation($id:ID!,$v:Int!,$k:String!){takeCase(caseId:$id,expectedVersion:$v,idempotencyKey:$k){id status rowVersion}}'

    def attempt(case, principal):
        return query(take, {'id': case['id'], 'v': case['rowVersion'], 'k': uuid4().hex}, principal)

    denied = attempt(case, token)
    assert denied.json()['errors'][0]['extensions']['code'] == 'CONFLICT'
    assert execute(get_case, {'id': case['id']})['reviewCase'] == case
    distribution = execute('mutation($x:DistributionInput!){distributeCases(input:$x){assignments}}',
                           {'x': {'limit': 100, 'idempotencyKey': uuid4().hex}})['distributeCases']
    assert {'caseId': case['id'], 'assigneeId': ids['analyst']} in distribution['assignments']
    case = execute(get_case, {'id': case['id']})['reviewCase']
    states.append(case['status'])

    credentials = {'login': 'case_other_' + uuid4().hex[:12], 'password': 'Audit-' + uuid4().hex + '!'}
    person = execute('mutation($x:StaffInput!){registerStaff(input:$x){id}}',
                     {'x': {**credentials, 'roleCode': 'ANALYST', 'idempotencyKey': uuid4().hex}}, 'admin')['registerStaff']
    other = query('mutation($login:String!,$password:String!){signIn(login:$login,password:$password){accessToken}}',
                  credentials).json()['data']['signIn']['accessToken']
    try:
        forbidden = attempt(case, other)
        assert forbidden.json()['errors'][0]['extensions']['code'] == 'CONFLICT'
        assert execute(get_case, {'id': case['id']})['reviewCase'] == case
    finally:
        execute('mutation($id:ID!,$k:String!){blockStaff(userId:$id,idempotencyKey:$k){id}}',
                {'id': person['id'], 'k': uuid4().hex}, 'admin')

    accepted = attempt(case, token)
    assert accepted.status_code == 200 and not accepted.json().get('errors'), accepted.text
    case = accepted.json()['data']['takeCase']
    states.append(case['status'])
    stale_version = case['rowVersion']
    case = execute('mutation($x:CaseCommentInput!){addCaseComment(input:$x){id status rowVersion comments{text}}}', {'x': {'caseId': case['id'], 'text': 'Проверена передача actor по gRPC', 'idempotencyKey': uuid4().hex}}, 'analyst')['addCaseComment']
    escalate = 'mutation($id:ID!,$v:Int!,$k:String!){escalateCase(caseId:$id,reason:"Требуется решение",expectedVersion:$v,idempotencyKey:$k){id status priority rowVersion}}'
    stale = query(escalate, {'id': case['id'], 'v': stale_version, 'k': uuid4().hex}, token)
    assert stale.json()['errors'][0]['extensions']['code'] == 'CONFLICT'
    case = execute(escalate, {'id': case['id'], 'v': case['rowVersion'], 'k': uuid4().hex}, 'analyst')['escalateCase']
    states.append(case['status'])
    case = execute(close, {'x': {'caseId': case['id'], 'outcome': 'EXPLAINED', 'reason': 'Контроль завершён', 'expectedVersion': case['rowVersion'], 'idempotencyKey': uuid4().hex}})['closeCase']
    states.append(case['status'])
    assert states == ['NEW', 'ASSIGNED', 'IN_PROGRESS', 'ESCALATED', 'CLOSED']
    notification = execute('query{notifications(page:{limit:1}){items{id}}}')['notifications']['items'][0]
    read = execute('mutation($id:ID!){markNotificationRead(id:$id){id readAt}}', {'id': notification['id']})['markNotificationRead']
    assert read['readAt']
    (EVIDENCE / 'business-transport-proof.json').write_text(json.dumps({'caseId': case['id'], 'states': states, 'finalVersion': case['rowVersion'], 'staleVersionCode': 'CONFLICT', 'unassignedTakeCode': 'CONFLICT', 'foreignTakeCode': 'CONFLICT', 'notification': read, 'adminClientId': directory_client['id'], 'adminCounterpartyId': counterparty['id'], 'managerTransaction': response.json()['data']['registerTransaction'], 'transport': 'HTTP GraphQL -> typed bank.v1 gRPC -> owned PostgreSQL'}, ensure_ascii=False, indent=2), encoding='utf-8')
