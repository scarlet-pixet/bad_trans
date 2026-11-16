"""Rollback-only PostgreSQL checks for LR6/LR7 domain event consistency."""
from datetime import datetime, timezone
import importlib
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def case_database():
    dsn = os.getenv("CASE_EVENT_TEST_DSN")
    if not dsn:
        pytest.skip("Set CASE_EVENT_TEST_DSN to a disposable PostgreSQL database")
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        schema = "case_event_" + uuid4().hex
        connection.execute(f'CREATE SCHEMA "{schema}"')
        connection.execute(f'SET LOCAL search_path TO "{schema}"')
        source = (ROOT / "infra/postgres/case/001_initial.sql").read_text(encoding="utf-8")
        connection.execute(source.replace("BEGIN;", "").replace("COMMIT;", ""))
        # The deployed consumer writes assessment fields introduced by 005.
        connection.execute((ROOT / "infra/postgres/case/005_assessment.sql").read_text(encoding="utf-8"))
        try:
            yield connection
        finally:
            connection.rollback()


def seed_case(connection, status):
    case_id, transaction_id, client_id, result_id = (str(uuid4()) for _ in range(4))
    stamp = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    connection.execute(
        "INSERT INTO review_cases(id,transaction_id,client_id,initial_result_id,latest_result_id,"
        "latest_score,latest_calculated_at,status,priority,assignee_id,review_cycle,row_version,created_at,updated_at) VALUES "
        "(%s,%s,%s,%s,%s,90,%s,%s,'HIGH',NULL,1,3,%s,%s)",
        (case_id, transaction_id, client_id, result_id, result_id, stamp, status, stamp, stamp),
    )
    return case_id, transaction_id, client_id, stamp


@pytest.mark.parametrize("module", ["backend.services.case.events.analysis"])
@pytest.mark.parametrize("status,priority", [("ESCALATED", "HIGH"), ("IN_PROGRESS", "LOW")])
def test_new_score_preserves_escalation_but_updates_regular_priority(case_database, module, status, priority):
    connection = case_database
    case_id, transaction_id, client_id, _ = seed_case(connection, status)
    new_result = str(uuid4())
    event = {"payload": {
        "transaction_id": transaction_id, "client_id": client_id, "result_id": new_result,
        "score": 30, "threshold": 60, "calculated_at": "2026-11-02T12:00:00+00:00",
    }}

    importlib.import_module(module).CaseEventService().process(connection, event)

    row = connection.execute("SELECT * FROM review_cases WHERE id=%s", (case_id,)).fetchone()
    assert row["status"] == status
    assert row["priority"] == priority
    assert row["latest_score"] == 30
    assert str(row["latest_result_id"]) == new_result
    assert row["row_version"] == 4


@pytest.mark.parametrize("module", ["backend.services.case.events.analysis"])
def test_old_analysis_event_does_not_overwrite_latest_snapshot(case_database, module):
    connection = case_database
    case_id, transaction_id, client_id, stamp = seed_case(connection, "ESCALATED")
    event = {"payload": {
        "transaction_id": transaction_id, "client_id": client_id, "result_id": str(uuid4()),
        "score": 10, "threshold": 60, "calculated_at": "2026-10-31T12:00:00+00:00",
    }}

    importlib.import_module(module).CaseEventService().process(connection, event)

    row = connection.execute("SELECT * FROM review_cases WHERE id=%s", (case_id,)).fetchone()
    assert (row["latest_score"], row["priority"], row["row_version"], row["latest_calculated_at"]) == (
        90, "HIGH", 3, stamp,
    )
