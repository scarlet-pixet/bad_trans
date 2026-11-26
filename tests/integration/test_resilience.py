"""Real network and container failure checks; every stopped dependency is restored."""
import json
import logging
import os
from pathlib import Path
import subprocess
from time import monotonic, sleep
from uuid import uuid4
import pytest
import redis
import requests
from confluent_kafka import Producer
from backend.common.data import DomainError
from backend.infrastructure.clients import RemoteClient
from tests.integration.test_commands import ROOT, ENV, IDS, API, EVIDENCE, login, graphql, sql, wait_until

def docker(*args):
    result = subprocess.run(['docker', *(arg.replace('bad_trans-',os.getenv('BAD_TRANS_PROJECT','bad_trans')+'-') for arg in args)], capture_output=True, text=True, encoding='utf-8', check=True)
    return result.stdout + result.stderr

def save(name, data):
    (EVIDENCE / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def test_retry_is_bounded_and_only_for_transient_errors(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv('SERVICE_TOKEN', ENV['SERVICE_TOKEN'])
    monkeypatch.setenv('TRANSACTION_GRPC', os.getenv('BAD_TRANS_TRANSACTION_GRPC','127.0.0.1:51052'))
    monkeypatch.setenv('RPC_READ_TIMEOUT', '0.25')
    client = RemoteClient('transaction', {'id': IDS['manager'], 'login': 'manager', 'role': 'MANAGER'})
    base = client._metadata()
    request_id = str(uuid4())
    client._metadata = lambda: base + (('x-test-fault', 'unavailable-once'), ('x-correlation-id', request_id))
    assert client.get_transaction(id=IDS['transaction'])['id'] == IDS['transaction']
    transient = [json.loads(r.message) for r in caplog.records if 'grpc.client' in r.message]
    assert [r['status'] for r in transient] == ['UNAVAILABLE', 'OK']
    caplog.clear()
    client._metadata = lambda: base + (('x-test-fault', 'delay'), ('x-correlation-id', str(uuid4())))
    start = monotonic()
    with pytest.raises(DomainError) as timeout_error:
        client.get_transaction(id=IDS['transaction'])
    assert timeout_error.value.code == 'DEPENDENCY_UNAVAILABLE'
    elapsed = monotonic() - start
    delayed = [json.loads(r.message) for r in caplog.records if 'grpc.client' in r.message]
    assert len(delayed) == 3 and 0.7 < elapsed < 2.5
    caplog.clear()
    client._metadata = lambda: base
    with pytest.raises(DomainError) as invalid:
        client.get_transaction(id='invalid-uuid')
    assert invalid.value.code == 'BAD_INPUT'
    permanent = [json.loads(r.message) for r in caplog.records if 'grpc.client' in r.message]
    assert len(permanent) == 1 and permanent[0]['status'] == 'INVALID_ARGUMENT'
    save('retry-proof.json', {'transientAttempts': transient, 'timeoutAttempts': delayed, 'timeoutElapsedSeconds': round(elapsed, 3), 'permanentAttempts': permanent, 'finiteAttempts': 3})

def test_partial_service_outage_does_not_stop_other_operations():
    token = login()
    try:
        docker('stop', 'bad_trans-transaction-service-1')
        start = monotonic()
        error = graphql('query{transactions{totalCount}}', token=token, error='DEPENDENCY_UNAVAILABLE')
        elapsed = monotonic() - start
        assert elapsed < 5
        others = graphql('query{me{login} reviewCases{totalCount}}', token=token)
        assert others['me']['login'] == 'manager'
    finally:
        docker('start', 'bad_trans-transaction-service-1')
    wait_until(lambda: requests.get(os.getenv('GRAPHQL_URL','http://127.0.0.1:18100/graphql').removesuffix('/graphql')+'/health', timeout=15).status_code == 200)
    recovered = graphql('query{transactions{totalCount}}', token=token)
    save('partial-outage-proof.json', {'failedOperationCode': error['errors'][0]['extensions']['code'], 'elapsedSeconds': round(elapsed, 3), 'independentOperations': others, 'recovered': recovered})

def test_cache_hit_expiry_isolation_and_cache_failure():
    token = login()
    cache = redis.Redis(host='127.0.0.1', port=int(os.getenv('BAD_TRANS_CACHE_PORT','57790')), decode_responses=True)
    keys = list(cache.scan_iter('bad_trans:cache:*'))
    if keys:
        cache.delete(*keys)
    q = 'query($id:ID!){profile(id:$id){id sampleSize medianAmount}}'

    def fetch():
        return requests.post(API, json={'query': q, 'variables': {'id': IDS['profile2']}}, headers={'Authorization': 'Bearer ' + token}, timeout=15)
    first = fetch()
    second = fetch()
    assert first.json() == second.json() and (not first.json().get('errors'))
    keys = list(cache.scan_iter('bad_trans:cache:*'))
    assert len(keys) == 1
    ttl = cache.ttl(keys[0])
    assert 0 < ttl <= 30
    logs = docker('logs', 'bad_trans-analysis-service-1')
    calls = [json.loads(line) for line in logs.splitlines() if line.startswith('{') and 'grpc.server' in line]
    first_calls = [r for r in calls if r.get('correlationId') == first.headers['X-Correlation-ID'] and r.get('method') == 'GetProfile']
    second_calls = [r for r in calls if r.get('correlationId') == second.headers['X-Correlation-ID'] and r.get('method') == 'GetProfile']
    assert len(first_calls) == 1 and len(second_calls) == 0
    cache.expire(keys[0], 1)
    sleep(1.2)
    third = fetch()
    assert third.json() == first.json()
    try:
        docker('stop', 'bad_trans-cache-1')
        bypass = fetch()
        assert bypass.json() == first.json()
        assert graphql('query{me{login}}', token=token)['me']['login'] == 'manager'
    finally:
        docker('start', 'bad_trans-cache-1')
    save('cache-proof.json', {'responsesEqual': True, 'observedTtlSeconds': ttl, 'firstRequestGrpcCalls': 1, 'secondRequestGrpcCalls': 0, 'afterExpiryRefetched': True, 'cacheDownBypass': True, 'sessionsRemainValid': True, 'scope': 'principal id + role + method + arguments'})

def test_bad_kafka_message_is_parked_and_next_message_is_processed():
    marker = uuid4().hex
    producer = Producer({'bootstrap.servers': os.getenv('BAD_TRANS_KAFKA_BOOTSTRAP','127.0.0.1:19100'), 'message.timeout.ms': 5000})
    producer.produce('transaction.registered.v1', key=marker, value=json.dumps({'bad_bad_trans_marker': marker}).encode())
    assert producer.flush(6) == 0
    parked = wait_until(lambda: sql('analysis', "SELECT id,status,attempts,error FROM event_failures WHERE convert_from(payload,'UTF8') LIKE %s", ('%' + marker + '%',)))
    failure = parked[0]
    assert failure['attempts'] == 1 and 'ValidationError' in failure['error']
    wait_until(lambda: sql('analysis', "SELECT id FROM event_failures WHERE id=%s AND status='PUBLISHED'", (failure['id'],)))
    proof = json.loads((EVIDENCE / 'live-proof.json').read_text())
    event = sql('transaction', "SELECT payload FROM outbox_events WHERE payload->'payload'->>'transaction_id'=%s", (proof['transaction']['id'],))[0]['payload']
    before = sql('analysis', 'SELECT count(*) AS n FROM analysis_results WHERE transaction_id=%s', (proof['transaction']['id'],))[0]['n']
    for _ in range(2):
        producer.produce('transaction.registered.v1', key=proof['transaction']['clientId'], value=json.dumps(event).encode())
    assert producer.flush(6) == 0
    sleep(1.5)
    after = sql('analysis', 'SELECT count(*) AS n FROM analysis_results WHERE transaction_id=%s', (proof['transaction']['id'],))[0]['n']
    assert before == after
    tx = {'source': 'bad_trans_after_poison', 'externalId': marker, 'accountId': IDS['account'], 'counterpartyId': IDS['counterparty'], 'amount': '10000.00', 'currency': 'BYN', 'direction': 'OUT', 'occurredAt': '2026-09-30T11:40:00Z', 'idempotencyKey': marker}
    created = graphql('mutation($x:TransactionInput!){registerTransaction(input:$x){id}}', {'x': tx})['registerTransaction']
    result = wait_until(lambda: sql('analysis', 'SELECT id FROM analysis_results WHERE transaction_id=%s', (created['id'],)))
    save('kafka-error-proof.json', {'failureId': str(failure['id']), 'attempts': failure['attempts'], 'error': 'ValidationError', 'deadLetterPublished': True, 'duplicateEventAddedResults': after - before, 'nextTransactionId': created['id'], 'nextResultId': str(result[0]['id'])})

def test_broker_outage_preserves_outbox_then_completes_business_flow():
    project = os.environ.get("BAD_TRANS_PROJECT")
    if not project or project == "bad_trans":
        pytest.skip("Kafka outage requires an explicitly isolated Compose project")

    def case_kafka_ready():
        response = requests.get(API.removesuffix("/graphql") + "/health", timeout=10)
        case = next(service for service in response.json()["services"] if service["service"] == "case")
        return case["kafkaReady"]

    case_process = docker("inspect", "--format", "{{.Id}}|{{.State.StartedAt}}", "bad_trans-case-service-1")
    wait_until(lambda: case_kafka_ready() is True)
    token = login()
    suffix = uuid4().hex
    tx = {'source': 'bad_trans_broker_outage', 'externalId': suffix, 'accountId': IDS['account'], 'counterpartyId': IDS['counterparty'], 'amount': '10000.00', 'currency': 'BYN', 'direction': 'OUT', 'occurredAt': '2026-09-30T11:50:00Z', 'idempotencyKey': suffix}
    mutation = 'mutation($x:TransactionInput!){registerTransaction(input:$x){id}}'
    try:
        docker('stop', 'bad_trans-kafka-1')
        wait_until(lambda: case_kafka_ready() is False)
        created = graphql(mutation, {'x': tx}, token)['registerTransaction']
        assert graphql(mutation, {'x': tx}, token)['registerTransaction'] == created
        row = sql('transaction', "SELECT id,published_at FROM outbox_events WHERE payload->'payload'->>'transaction_id'=%s", (created['id'],))
        assert len(row) == 1 and row[0]['published_at'] is None
        unaffected = graphql('query{me{login} transactionSummary{transactionCount}}', token=token)
    finally:
        docker('start', 'bad_trans-kafka-1')
    wait_until(lambda: case_kafka_ready() is True, seconds=70)
    assert docker("inspect", "--format", "{{.Id}}|{{.State.StartedAt}}", "bad_trans-case-service-1") == case_process
    case = wait_until(lambda: sql('case', 'SELECT id,latest_score FROM review_cases WHERE transaction_id=%s', (created['id'],)), seconds=70)[0]
    published = sql('transaction', "SELECT count(*) AS n FROM outbox_events WHERE payload->'payload'->>'transaction_id'=%s AND published_at IS NOT NULL", (created['id'],))[0]['n']
    assert published == 1
    save('broker-recovery-proof.json', {'transactionId': created['id'], 'durablePendingOutbox': True, 'sameIdOnRepeat': True, 'onePublishedOutboxAfterRecovery': True, 'caseId': str(case['id']), 'score': case['latest_score'], 'independentOperationsDuringOutage': unaffected, 'caseKafkaReadiness': [True, False, True], 'sameCaseProcess': True})

def test_exhausted_transient_event_resumes_automatically_after_recovery():
    proof = json.loads((EVIDENCE / 'live-proof.json').read_text())
    from datetime import datetime, timezone
    event = {'event_id': str(uuid4()), 'schema_version': 1, 'occurred_at': datetime.now(timezone.utc).isoformat(), 'correlation_id': str(uuid4()), 'payload': {'transaction_id': proof['transaction']['id'], 'client_id': proof['transaction']['clientId']}}
    try:
        docker('stop', 'bad_trans-transaction-service-1')
        producer = Producer({'bootstrap.servers': os.getenv('BAD_TRANS_KAFKA_BOOTSTRAP','127.0.0.1:19100'), 'message.timeout.ms': 5000})
        producer.produce('transaction.registered.v1', key=event['payload']['client_id'], value=json.dumps(event).encode())
        assert producer.flush(6) == 0
        failure = wait_until(lambda: sql('analysis', "SELECT id,attempts FROM event_failures WHERE convert_from(payload,'UTF8') LIKE %s", ('%' + event['event_id'] + '%',)), seconds=40)[0]
        assert failure['attempts'] >= 3
        assert not sql('analysis', 'SELECT 1 FROM inbox_events WHERE event_id=%s', (event['event_id'],))
    finally:
        docker('start', 'bad_trans-transaction-service-1')
    wait_until(lambda: requests.get(os.getenv('GRAPHQL_URL','http://127.0.0.1:18100/graphql').removesuffix('/graphql')+'/health', timeout=15).status_code == 200)
    wait_until(lambda:sql('analysis','SELECT 1 FROM inbox_events WHERE event_id=%s',(event['event_id'],)))
    assert wait_until(lambda:sql('analysis',"SELECT id FROM event_failures WHERE id=%s AND status='RESOLVED'",(failure['id'],)))
    save('replay-proof.json', {'failureId': str(failure['id']), 'attemptsBeforeParking': 3, 'wasNotFalselyCompleted': True, 'automaticallyResumedAfterRecovery': True, 'originalEventId': event['event_id'], 'inboxCommitted': True})
