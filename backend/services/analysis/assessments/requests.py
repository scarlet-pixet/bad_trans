"""Pin policy, subject, knowledge time, and history before retryable scoring."""

import psycopg
from psycopg.types.json import Jsonb

from backend.common.behavior_contract import digest
from backend.common.data import DomainError, uuid
from backend.common.commands import owned_connection
from backend.services.analysis.assessments.calculation import POLICY, validate_rules
from backend.services.analysis.assessments.comparison import COMPARISON_POLICY
from backend.services.analysis.profiles.observation import timestamp
from backend.services.analysis.profiles.storage import (
    prepare_snapshot,
    read_snapshot_history,
)


def connection(c) -> psycopg.Connection[dict]:
    # Same owned DB only. Never expose the connection string in logs.
    return owned_connection(c)


def freeze(c, client, event, rule_ids, request_key, knowledge_mode='FIRST_REQUEST_KNOWLEDGE', *, frozen_context=None) -> dict:
    identifier = uuid(event["payload"]["transaction_id"])
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
        raise DomainError("BAD_INPUT", "Invalid assessment request key")
    if rule_ids is not None and (
        not isinstance(rule_ids, list)
        or not rule_ids
        or len(rule_ids) > 5
        or len(set(map(str, rule_ids))) != len(rule_ids)
    ):
        raise DomainError("BAD_INPUT", "Нужен непустой набор разных современных версий")
    ids = sorted(uuid(x) for x in rule_ids) if rule_ids is not None else None
    intent_parameters = dict(transactionId=identifier, ruleVersionIds=ids)
    if frozen_context is not None:
        intent_parameters['runContext'] = digest(frozen_context)
    intent = digest(intent_parameters)
    with connection(c) as gate:
        gate.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("assessment:" + request_key,),
        )
        row = gate.execute(
            "SELECT * FROM assessment_requests WHERE request_key=%s", (request_key,)
        ).fetchone()
        if row:
            if row["intent_hash"] != intent:
                raise DomainError("CONFLICT", "Assessment key has another intent")
            return row
        if frozen_context is not None:
            rules = frozen_context['rules']
        elif ids is None:
            rules = gate.execute(
                "SELECT DISTINCT ON(v.rule_id) v.*,r.code FROM rule_versions v JOIN analysis_rules r ON r.id=v.rule_id WHERE v.status='PUBLISHED' AND r.code NOT IN ('AMOUNT','FREQUENCY','NEW_COUNTERPARTY') ORDER BY v.rule_id,v.version DESC"
            ).fetchall()
        else:
            rules = gate.execute(
                "SELECT v.*,r.code FROM rule_versions v JOIN analysis_rules r ON r.id=v.rule_id WHERE v.status='PUBLISHED' AND v.id=ANY(%s::uuid[])",
                (ids,),
            ).fetchall()
            if len(rules) != len(ids):
                raise DomainError(
                    "BAD_INPUT", "Версия правила отсутствует или не опубликована"
                )
        rules = validate_rules(rules)
        tx = client.get_transaction(identifier)
        if not tx:
            raise DomainError("NOT_FOUND", "Transaction missing")
        context = dict(
            transaction=tx,
            rules=rules,
            policy=POLICY,
            asOf=timestamp(tx["occurredAt"]).isoformat(),
            ingestedBefore=timestamp(event["occurred_at"]).isoformat(),
        )
        from backend.services.analysis.feedback.events import capture
        if frozen_context is not None:
            context['policy'] = frozen_context['policy']
            context['feedbackReference'] = frozen_context['feedbackReference']
            if frozen_context.get("comparisonPolicy"):
                context["comparisonPolicy"] = frozen_context["comparisonPolicy"]
        else:
            context['feedbackReference'] = capture(gate, knowledge_mode)
            context["comparisonPolicy"] = COMPARISON_POLICY
        saved = gate.execute(
            "INSERT INTO assessment_requests(request_key,intent_hash,context) VALUES (%s,%s,%s) RETURNING *",
            (request_key, intent, Jsonb(context)),
        ).fetchone()
        if saved is None:
            raise RuntimeError("Assessment request was not saved")
        return saved


def history(c, client, request, *, table='assessment_requests'):
    if table not in ('assessment_requests','profile_build_requests'):
        raise ValueError('Unsupported history request owner table')
    p = request["context"]
    if table=='assessment_requests' and p["policy"] != POLICY:
        raise DomainError(
            "CONFLICT", "Frozen assessment policy is not supported by this binary"
        )
    snapshot = request["snapshot"]
    if snapshot is None:
        snapshot = prepare_snapshot(
            client,
            p["transaction"],
            timestamp(p["asOf"]),
            timestamp(p["ingestedBefore"]),
        )
        with connection(c) as gate:
            from psycopg import sql
            gate.execute(
                sql.SQL("UPDATE {} SET snapshot=%s WHERE request_key=%s AND snapshot IS NULL").format(sql.Identifier(table)),
                (Jsonb(snapshot), request["request_key"]),
            )
            saved = gate.execute(
                sql.SQL("SELECT snapshot FROM {} WHERE request_key=%s").format(sql.Identifier(table)),
                (request["request_key"],),
            ).fetchone()
            if saved is None:
                raise DomainError("NOT_FOUND", "Assessment request is missing")
            snapshot = saved["snapshot"]
    return read_snapshot_history(client, snapshot)
