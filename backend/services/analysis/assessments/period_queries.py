"""Read saved period assessments and page differences between finished runs."""
from backend.common.commands import require_role
from backend.common.data import DomainError, uuid
from backend.common.reassessment_contract import page_bounds


def get(db, actor, run_id, offset, limit):
    require_role(actor, "MANAGER")
    page_bounds(offset, limit)
    run_id = uuid(run_id)
    with db.connect() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = connection.execute(
            """SELECT r.*,j.context,j.selection,j.last_error,j.discovery_attempts
            FROM analysis_runs r LEFT JOIN period_reassessment_jobs j ON j.run_id=r.id
            WHERE r.id=%s AND r.mode='REANALYSIS'""",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        if row["context"] is not None:
            rows = connection.execute(
                """SELECT a.id,i.transaction_id,i.state,i.attempts,i.last_error,i.finished_at,
                    a.assessment_status,a.assessment_score,a.threshold
                FROM period_reassessment_items i LEFT JOIN analysis_results a USING (run_id,transaction_id)
                WHERE i.run_id=%s ORDER BY i.position LIMIT %s OFFSET %s""",
                (run_id, limit + 1, offset),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT id,transaction_id,assessment_status,assessment_score,threshold,
                    'COMPLETED' AS state,NULL AS attempts,NULL AS last_error,calculated_at AS finished_at
                FROM analysis_results WHERE run_id=%s ORDER BY transaction_id LIMIT %s OFFSET %s""",
                (run_id, limit + 1, offset),
            ).fetchall()
    context = row["context"] or {}
    return dict(
        id=run_id, status=row["status"], counters=row["counters"], error=row["last_error"],
        createdAt=row["created_at"], completedAt=row["completed_at"],
        nextOffset=offset+limit if len(rows) > limit else None, selection=row["selection"],
        scope=row["scope"], authorId=row["initiated_by"], discoveryAttempts=row["discovery_attempts"],
        ruleVersionIds=row["scope"].get("ruleVersionIds", []), rules=context.get("rules"),
        policyVersion=(context.get("policy") or {}).get("version"),
        ingestedBefore=context.get("ingestedBefore"), feedbackReference=context.get("feedbackReference"),
        results=[
            dict(id=item["id"], transactionId=item["transaction_id"], state=item["state"],
                 status=item["assessment_status"], score=item["assessment_score"],
                 threshold=item["threshold"], attempts=item["attempts"], error=item["last_error"],
                 finishedAt=item["finished_at"])
            for item in rows[:limit]
        ],
    )


def result_hits(connection, run_ids):
    hits = {}
    for row in connection.execute(
        """SELECT h.result_id,r.code FROM analysis_results a
        JOIN rule_hits h ON h.result_id=a.id
        JOIN rule_versions v ON v.id=h.rule_version_id
        JOIN analysis_rules r ON r.id=v.rule_id
        WHERE a.run_id=ANY(%s::uuid[])
        UNION ALL
        SELECT a.id,signal->>'code' FROM analysis_results a
        JOIN detection_context d ON d.result_id=a.id
        CROSS JOIN LATERAL jsonb_array_elements(coalesce(d.details->'behavioralSignals','[]'::jsonb)) AS signal
        WHERE a.run_id=ANY(%s::uuid[]) AND a.assessment_status IS NULL
            AND signal->>'contribution' <> '0'""",
        (run_ids, run_ids),
    ):
        hits.setdefault(row["result_id"], set()).add(row["code"])
    return hits


def compare(db, actor, first_id, second_id, offset, limit):
    require_role(actor, "MANAGER")
    page_bounds(offset, limit)
    first_id, second_id = uuid(first_id), uuid(second_id)
    if first_id == second_id:
        raise DomainError("BAD_INPUT", "Укажите два разных запуска")
    run_ids = [first_id, second_id]
    with db.connect() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        runs = connection.execute(
            "SELECT status FROM analysis_runs WHERE id=ANY(%s::uuid[])", (run_ids,)
        ).fetchall()
        if len(runs) != 2:
            raise DomainError("NOT_FOUND", "Один из запусков не найден")
        if any(row["status"] in ("PENDING", "RUNNING") for row in runs):
            raise DomainError("CONFLICT", "Дождитесь завершения обоих запусков")
        rows = connection.execute(
            """WITH first AS (
                SELECT id,transaction_id,assessment_status,assessment_score,score
                FROM analysis_results WHERE run_id=%s
            ), second AS (
                SELECT id,transaction_id,assessment_status,assessment_score,score
                FROM analysis_results WHERE run_id=%s
            )
            SELECT coalesce(a.transaction_id,b.transaction_id) AS transaction_id,
                a.id AS first_id,b.id AS second_id,
                CASE WHEN a.assessment_status IS NULL THEN a.score ELSE a.assessment_score END AS before,
                CASE WHEN b.assessment_status IS NULL THEN b.score ELSE b.assessment_score END AS after,
                a.assessment_status AS before_status,b.assessment_status AS after_status
            FROM first a FULL JOIN second b USING (transaction_id) ORDER BY transaction_id""",
            (first_id, second_id),
        ).fetchall()
        hits = result_hits(connection, run_ids)
    common = only_first = only_second = difference_count = 0
    differences = []
    for row in rows:
        a, b = row["first_id"], row["second_id"]
        new_rules = sorted(hits.get(b, set()) - hits.get(a, set())) if a and b else []
        if a and b:
            common += 1
            if row["before"] == row["after"] and row["before_status"] == row["after_status"] and not new_rules:
                continue
        elif a:
            only_first += 1
        else:
            only_second += 1
        if offset <= difference_count < offset + limit:
            differences.append(dict(
                transactionId=row["transaction_id"], firstResultId=a, secondResultId=b,
                before=row["before"], after=row["after"], beforeStatus=row["before_status"],
                afterStatus=row["after_status"], newRules=new_rules,
            ))
        difference_count += 1
    return dict(
        commonCount=common, onlyFirstCount=only_first, onlySecondCount=only_second, items=differences,
        nextOffset=offset + limit if offset + limit < difference_count else None,
    )
