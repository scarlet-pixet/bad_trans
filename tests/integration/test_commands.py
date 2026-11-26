import os
"""Execute the formerly missing course operations over GraphQL and typed gRPC."""
import base64
import hashlib
import json
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
import requests
ROOT = Path(__file__).resolve().parents[2]
EVIDENCE=Path(os.getenv('BAD_TRANS_EVIDENCE_DIR',str(ROOT/'artifacts/verification')))
EVIDENCE.mkdir(parents=True,exist_ok=True)
IDS = json.loads((ROOT / 'infra/seed_ids.json').read_text())
env_path = ROOT / '.env'
ENV = dict(line.split('=', 1) for line in env_path.read_text(encoding='utf-8').splitlines()
           if '=' in line) if env_path.exists() else {}
API = os.getenv('GRAPHQL_URL','http://127.0.0.1:18100/graphql')

def sql(owner, text, params=()):
    port = {'transaction': int(os.getenv('BAD_TRANS_TRANSACTION_DB_PORT','57742')), 'analysis': int(os.getenv('BAD_TRANS_ANALYSIS_DB_PORT','57743')), 'case': int(os.getenv('BAD_TRANS_CASE_DB_PORT','57744')), 'auth': int(os.getenv('BAD_TRANS_AUTH_DB_PORT','57741'))}[owner]
    with psycopg.connect(host='127.0.0.1', port=port, user='bank_' + owner, password=ENV['DB_PASSWORD'], dbname='bank_' + owner, row_factory=dict_row) as c:
        return c.execute(text, params).fetchall()

def login(name='manager'):
    r = requests.post(API, json={'query': 'mutation($login:String!){signIn(login:$login,password:"BadTrans-demo-2026!"){accessToken}}', 'variables': {'login': name}}, timeout=20).json()
    assert not r.get('errors'), r.get('errors')
    return r['data']['signIn']['accessToken']

def graphql(query, values=None, token=None, error=None):
    r = requests.post(API, json={'query': query, 'variables': values or {}}, headers={'Authorization': 'Bearer ' + (token or login())}, timeout=40)
    assert r.status_code == 200, r.text
    body = r.json()
    if error:
        assert body['errors'][0]['extensions']['code'] == error, body
        return body
    assert not body.get('errors'), body.get('errors')
    return body['data']

def wait_until(callback, seconds=35):
    until = monotonic() + seconds
    while monotonic() < until:
        result = callback()
        if result:
            return result
        sleep(0.4)
    raise AssertionError('Timed out waiting for durable business result')

def test_import_is_atomic_deduplicated_and_idempotent():
    token = login()
    suffix = uuid4().hex
    tx = {'source': 'bad_trans_import', 'externalId': suffix, 'accountId': IDS['account'], 'counterpartyId': IDS['counterparty'], 'amount': '12.30', 'currency': 'BYN', 'direction': 'OUT', 'occurredAt': '2026-09-28T12:00:00Z', 'idempotencyKey': suffix}
    input = {'items': [tx], 'fileHash': hashlib.sha256(suffix.encode()).hexdigest(), 'idempotencyKey': suffix}
    mutation = 'mutation($x:ImportBatchInput!){importTransactions(input:$x){batchId status receivedCount newCount duplicateCount errors{row code}}}'
    first = graphql(mutation, {'x': input}, token)['importTransactions']
    assert first['status'] == 'ACCEPTED' and first['newCount'] == 1
    assert graphql(mutation, {'x': input}, token)['importTransactions'] == first
    duplicate = {**input, 'fileHash': hashlib.sha256((suffix + 'duplicate').encode()).hexdigest(), 'idempotencyKey': uuid4().hex}
    again = graphql(mutation, {'x': duplicate}, token)['importTransactions']
    assert again['newCount'] == 0 and again['duplicateCount'] == 1
    invalid = {**input, 'items': [{**tx, 'externalId': suffix + 'valid'}, {**tx, 'externalId': suffix + 'invalid', 'amount': '-1'}], 'fileHash': hashlib.sha256((suffix + 'bad').encode()).hexdigest(), 'idempotencyKey': uuid4().hex}
    failed = graphql(mutation, {'x': invalid}, token)['importTransactions']
    assert failed['status'] == 'REJECTED' and failed['newCount'] == 0 and (failed['errors'][0]['row'] == 2)
    assert sql('transaction', 'SELECT count(*) AS n FROM transactions WHERE external_id=%s', (suffix + 'valid',))[0]['n'] == 0
    assert sql('transaction', "SELECT count(*) AS n FROM outbox_events WHERE payload->'payload'->>'transaction_id' IN (SELECT id::text FROM transactions WHERE source='bad_trans_import' AND external_id=%s)", (suffix,))[0]['n'] == 1
    (EVIDENCE / 'import-proof.json').write_text(json.dumps({'accepted': first, 'duplicate': again, 'rejected': failed, 'rolledBackValidRow': True}, indent=2), encoding='utf-8')

def test_profile_rules_reanalysis_distribution_report_and_notifications():
    token = login()
    analyst = login('analyst')
    suffix = uuid4().hex
    proof = json.loads((EVIDENCE / 'live-proof.json').read_text())
    p = {'clientId': IDS['client'], 'currency': 'BYN', 'direction': 'OUT', 'cutoff': '2026-09-30T12:00:00Z', 'idempotencyKey': suffix}
    q = 'mutation($x:ProfileInput!){buildProfile(input:$x){id mode status counters}}'
    profile = graphql(q, {'x': p}, token)['buildProfile']
    assert profile['status'] == 'COMPLETED'
    assert graphql(q, {'x': p}, token)['buildProfile'] == profile
    stored = sql('analysis', 'SELECT * FROM behavior_profiles WHERE run_id=%s', (profile['id'],))[0]
    assert stored['sample_size'] >= 5 and stored['median_amount'] is not None
    analyze = 'mutation($id:ID!,$key:String!){analyzeTransaction(transactionId:$id,idempotencyKey:$key){id status}}'
    values = {'id': proof['transaction']['id'], 'key': suffix}
    run = graphql(analyze, values, token)['analyzeTransaction']
    assert graphql(analyze, values, token)['analyzeTransaction'] == run
    version = sql('analysis', 'SELECT max(version) AS v FROM rule_versions WHERE rule_id=%s', (IDS['AMOUNT'],))[0]['v'] + 1
    rule = {'ruleId': IDS['AMOUNT'], 'version': version, 'weight': 60, 'parameters': {'min_samples': 5, 'multiplier': 3}, 'controlExamples': [{'amount': 100, 'median': 100, 'expected': False}, {'amount': 1000, 'median': 100, 'expected': True}], 'idempotencyKey': suffix}
    publish = 'mutation($x:RuleVersionInput!){publishRuleVersion(input:$x){id version weight parameters status}}'
    bad = {**rule, 'weight': 101, 'idempotencyKey': uuid4().hex}
    graphql(publish, {'x': bad}, token, error='BAD_INPUT')
    published = graphql(publish, {'x': rule}, token)['publishRuleVersion']
    assert published['status'] == 'PUBLISHED' and published['version'] == version
    assert graphql(publish, {'x': rule}, token)['publishRuleVersion'] == published
    catalog = graphql('query{ruleCatalog}', token=token)['ruleCatalog']
    amount_rule = next(item for item in catalog if item['code'] == 'AMOUNT_DEVIATION')
    ids = [amount_rule['versions'][0]['id']]
    params = {'period': {'from': '2026-09-30T11:00:00Z', 'to': '2026-09-30T12:00:00Z'}, 'clientId': IDS['client'], 'ruleVersionIds': ids, 'idempotencyKey': suffix}
    reanalyze = 'mutation($x:ReanalysisInput!){reanalyzePeriod(input:$x){id mode status counters}}'
    reanalysis = graphql(reanalyze, {'x': params}, token)['reanalyzePeriod']
    assert reanalysis['mode'] == 'REANALYSIS' and reanalysis['status'] == 'COMPLETED'
    assert graphql(reanalyze, {'x': params}, token)['reanalyzePeriod'] == reanalysis
    assert sql('analysis', 'SELECT count(*) AS n FROM run_rule_versions WHERE run_id=%s', (reanalysis['id'],))[0]['n'] == len(ids)
    compare = graphql('query($a:ID!,$b:ID!){compareRuns(firstId:$a,secondId:$b){commonCount onlyFirstCount onlySecondCount changedScores}}', {'a': run['id'], 'b': reanalysis['id']}, token)['compareRuns']
    assert compare['commonCount'] == 1
    distribution = graphql('mutation($x:DistributionInput!){distributeCases(input:$x){assignedCount unassignedCount assignments}}', {'x': {'limit': 100, 'idempotencyKey': suffix}}, token)['distributeCases']
    assert distribution['assignedCount'] >= 1
    wait_until(lambda: sql('case', "SELECT id FROM notifications WHERE type='ASSIGNED'"))
    report_input = {'period': {'from': '2026-01-01T00:00:00Z', 'to': '2027-01-01T00:00:00Z'}, 'idempotencyKey': suffix}
    report_query = 'mutation($x:ReportInput!){createReport(input:$x){id snapshot createdAt}}'
    report = graphql(report_query, {'x': report_input}, token)['createReport']
    assert graphql(report_query, {'x': report_input}, token)['createReport'] == report
    own = graphql(report_query, {'x': {**report_input, 'idempotencyKey': uuid4().hex}}, analyst)['createReport']
    hidden = graphql('query($id:ID!){report(id:$id){id}}', {'id': report['id']}, analyst)
    assert hidden['report'] is None
    export = graphql('query($id:ID!){exportReport(id:$id){fileName contentBase64}}', {'id': own['id']}, analyst)['exportReport']
    assert 'decisionCount' in base64.b64decode(export['contentBase64']).decode('utf-8-sig')
    notifications = graphql('query{notifications{items{id readAt}}}', token=token)['notifications']['items']
    assert notifications
    marked = graphql('mutation($id:ID!){markNotificationRead(id:$id){id readAt}}', {'id': notifications[0]['id']}, token)['markNotificationRead']
    assert marked['readAt']
    denied = requests.post(API, json={'query': q, 'variables': {'x': p}},
                           headers={'Authorization': 'Bearer ' + analyst}, timeout=20)
    assert denied.status_code == 403
    analyst_analysis = graphql(analyze, {'id': proof['transaction']['id'], 'key': uuid4().hex}, analyst)['analyzeTransaction']
    assert analyst_analysis['status'] == 'COMPLETED'
    output = {'profileRun': profile, 'profileSampleSize': stored['sample_size'], 'manualRun': run, 'publishedRule': published, 'reanalysis': reanalysis, 'comparison': compare, 'distribution': distribution, 'report': report, 'analystOwnReport': own, 'analystAnalysis': analyst_analysis, 'foreignReportHidden': True, 'csvDecoded': True, 'notificationRead': marked}
    (EVIDENCE / 'completed-commands.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
