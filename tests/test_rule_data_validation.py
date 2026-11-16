"""Invalid JSON control data must fail as domain input, without partial writes."""
from contextlib import nullcontext
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from backend.services.analysis.persistence.repository import AnalysisRepository
from backend.common.data import DomainError
from backend.services.analysis.application.commands import AnalysisCommands


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def rule_service():
    dsn = os.getenv("CASE_EVENT_TEST_DSN")
    if not dsn:
        pytest.skip("Set CASE_EVENT_TEST_DSN to a disposable PostgreSQL database")
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        schema = "rule_data_" + uuid4().hex
        connection.execute(f'CREATE SCHEMA "{schema}"')
        connection.execute(f'SET LOCAL search_path TO "{schema}"')
        for name in ("001_initial.sql", "002_demo.sql"):
            sql = (ROOT / "infra/postgres/analysis" / name).read_text(encoding="utf-8")
            connection.execute(sql.replace("BEGIN;", "").replace("COMMIT;", ""))

        class Database:
            def connect(self):
                return nullcontext(connection)

            def query(self, sql, params=()):
                return connection.execute(sql, params).fetchall()

        rule = connection.execute("SELECT id FROM analysis_rules WHERE code='AMOUNT'").fetchone()
        version = connection.execute("SELECT max(version)+1 AS next FROM rule_versions WHERE rule_id=%s", (rule["id"],)).fetchone()["next"]
        actor = SimpleNamespace(id=str(uuid4()), role="MANAGER", is_active=True)
        service = AnalysisCommands(AnalysisRepository(Database()), None)
        try:
            yield service, connection, actor, str(rule["id"]), version
        finally:
            connection.rollback()


@pytest.mark.parametrize("amount,expected", [("NaN", False), ("Infinity", True), ("not-a-number", False), ("-1", False)])
def test_invalid_control_amount_is_bad_input_without_publishing(rule_service, amount, expected):
    service, connection, actor, rule_id, version = rule_service
    before = connection.execute("SELECT count(*) AS n FROM rule_versions").fetchone()["n"]
    data = {
        "ruleId": rule_id, "version": version, "weight": 60,
        "parameters": {"min_samples": 5, "multiplier": 3},
        "controlExamples": [
            {"amount": amount, "median": "100", "expected": expected},
            {"amount": "100", "median": "100", "expected": False},
            {"amount": "1000", "median": "100", "expected": True},
        ],
        "idempotencyKey": uuid4().hex,
    }

    with pytest.raises(DomainError) as error:
        service.publish_rule_version(actor, data)

    assert error.value.code == "BAD_INPUT"
    assert connection.execute("SELECT count(*) AS n FROM rule_versions").fetchone()["n"] == before
    assert connection.execute("SELECT count(*) AS n FROM command_receipts").fetchone()["n"] == 0


@pytest.mark.parametrize('count', [-1, True, 1.5, float('nan')])
def test_invalid_frequency_count_cannot_be_used_as_a_control_example(rule_service, count):
    service, connection, actor, _, _ = rule_service
    rule = connection.execute("SELECT id FROM analysis_rules WHERE code='FREQUENCY'").fetchone()['id']
    version = connection.execute('SELECT max(version)+1 AS v FROM rule_versions WHERE rule_id=%s', (rule,)).fetchone()['v']
    data = {'ruleId': str(rule), 'version': version, 'weight': 20,
            'parameters': {'window_minutes': 60, 'limit': 5},
            'controlExamples': [{'count': count, 'expected': False},
                                {'count': 0, 'expected': False}, {'count': 10, 'expected': True}],
            'idempotencyKey': uuid4().hex}
    with pytest.raises(DomainError) as error:
        service.publish_rule_version(actor, data)
    assert error.value.code == 'BAD_INPUT'
