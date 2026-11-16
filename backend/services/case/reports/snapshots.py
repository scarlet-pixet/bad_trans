"""Case-owned snapshot storage; only signed server composition is accepted."""

from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
from typing import cast

from psycopg.types.json import Jsonb

from backend.common.commands import require_role
from backend.common.data import DomainError, period, uuid
from backend.common.events import ident
from backend.common.receipts import receipt
from backend.common.report_contract import MAX_ROWS, cut, cutoff_time, digest, normalize_request, validate_snapshot, verify, wire


class ReportSnapshots:
    def __init__(self, db):
        self.db = db

    def export_snapshot_report(self, actor, id, format, table=None):
        import base64
        from backend.reporting.exporters import render_export
        from backend.reporting.tables import VERSION
        report = cast(dict, self.get_snapshot_report(actor, id))
        content, media_type, suffix = render_export(report, format, table)
        # Revocation during an expensive render also prevents delivery.
        self.get_snapshot_report(actor, id)
        return dict(fileName=f"report-{report['id']}-v1{suffix}", mediaType=media_type,
                    contentBase64=base64.b64encode(content).decode("ascii"), snapshotHash=report["snapshotHash"], exportVersion=VERSION)

    @staticmethod
    def scope(c, actor, request):
        require_role(actor, "MANAGER", "ANALYST")
        if actor.role != "ANALYST":
            return
        if request["reportType"] != "OPERATION":
            raise DomainError("FORBIDDEN", "Аналитику доступен отчёт операции своего случая")
        row = c.execute("SELECT assignee_id,status FROM review_cases WHERE transaction_id=%s FOR SHARE", (uuid(request["transactionId"]),)).fetchone()
        if not row or not (str(row["assignee_id"]) == actor.id or (row["assignee_id"] is None and row["status"] == "NEW")):
            raise DomainError("FORBIDDEN", "Доступ к случаю отозван")

    def find_snapshot_report(self, actor, input):
        request = normalize_request(input)
        with self.db.connect() as c:
            self.scope(c, actor, request)
            row = c.execute("SELECT request_hash,response FROM command_receipts WHERE actor_id=%s AND command='createSnapshotReport' AND idempotency_key=%s", (uuid(actor.id), request["idempotencyKey"])).fetchone()
            if not row:
                return None
            expected = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()
            if row["request_hash"] != expected:
                raise DomainError("CONFLICT", "Ключ отчёта использован с другими селекторами")
        return self.get_snapshot_report(actor, row["response"]["id"])

    def store_snapshot_report(self, actor, input, snapshot, signature):
        request = normalize_request(input)
        verify(actor, request, snapshot, signature)
        validate_snapshot(actor, request, snapshot)
        with self.db.connect() as c:
            self.scope(c, actor, request)

            def perform(conn):
                report_id = ident()
                assembled = datetime.fromisoformat(snapshot["assembledAt"])
                start, end = period(request["period"]) if request["reportType"] == "PERIOD" else (assembled, assembled + timedelta(microseconds=1))
                conn.execute('''INSERT INTO reports(id,author_id,period_start,period_end,filters,snapshot,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,now())''', (report_id, uuid(actor.id), start, end, Jsonb({k: v for k, v in request.items() if k != "idempotencyKey"}), Jsonb(snapshot)))
                return dict(id=report_id)

            value = receipt(c, actor, "createSnapshotReport", request["idempotencyKey"], request, perform)
        return self.get_snapshot_report(actor, value["id"])

    def get_snapshot_report(self, actor, id):
        with self.db.connect() as c:
            row = c.execute("SELECT id,author_id,created_at,filters,snapshot FROM reports WHERE id=%s", (uuid(id),)).fetchone()
            if not row or row["snapshot"].get("reportSchemaVersion") != 1:
                raise DomainError("NOT_FOUND", "Содержательный отчёт не найден")
            self.scope(c, actor, row["filters"])
            if actor.role == "ANALYST" and str(row["author_id"]) != actor.id:
                raise DomainError("FORBIDDEN", "Доступен только собственный отчёт")
            return wire(dict(id=row["id"], createdAt=row["created_at"], snapshot=row["snapshot"], snapshotHash=digest(row["snapshot"])))

    def list_snapshot_reports(self, actor, limit=20, offset=0):
        require_role(actor, "MANAGER", "ANALYST")
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 10000:
            raise DomainError("BAD_INPUT", "Некорректная страница отчётов")
        rows = self.db.query('''SELECT p.id,p.created_at,p.snapshot->>'reportType' AS "reportType",p.snapshot->'author' AS author
            FROM reports p LEFT JOIN review_cases r ON r.transaction_id=CASE WHEN p.snapshot->>'reportType'='OPERATION' THEN (p.filters->>'transactionId')::uuid END
            WHERE p.snapshot->>'reportSchemaVersion'='1' AND (%s='MANAGER' OR
            (p.author_id=%s AND p.snapshot->>'reportType'='OPERATION' AND (r.assignee_id=%s OR (r.assignee_id IS NULL AND r.status='NEW'))))
            ORDER BY p.created_at DESC,p.id DESC LIMIT %s OFFSET %s''', (actor.role, uuid(actor.id), uuid(actor.id), limit + 1, offset))
        return wire(dict(items=rows[:limit], nextOffset=offset + limit if len(rows) > limit else None))

    def report_case_part(self, actor, transactionId, cutoff):
        at = cutoff_time(cutoff)
        with self.db.connect() as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self.scope(c, actor, dict(reportType="OPERATION", transactionId=transactionId))
            case = c.execute("SELECT * FROM review_cases WHERE transaction_id=%s AND created_at<=%s", (uuid(transactionId), at)).fetchone()
            comments, decisions = [], []
            if case:
                comments = c.execute("SELECT * FROM case_comments WHERE case_id=%s AND created_at<=%s ORDER BY created_at,id LIMIT %s", (case["id"], at, MAX_ROWS + 1)).fetchall()
                decisions = c.execute("SELECT * FROM case_decisions WHERE case_id=%s AND decided_at<=%s ORDER BY decided_at,id LIMIT %s", (case["id"], at, MAX_ROWS + 1)).fetchall()
            if len(comments) > MAX_ROWS or len(decisions) > MAX_ROWS:
                raise DomainError("BAD_INPUT", "Слишком много действий случая для одного отчёта")
            captured = c.execute("SELECT transaction_timestamp() AS at").fetchone()["at"]
        return dict(data=wire(dict(case=case, comments=comments, decisions=decisions, statusAxis="current at capturedAt; actions<=cutoff, not state at selected result time")), cut=cut("case", captured, at, dict(case=str(case["id"]) if case else None, comments=[str(r["id"]) for r in comments], decisions=[str(r["id"]) for r in decisions])))

    def report_cases_part(self, actor, transactionIds, periodFilter, cutoff):
        require_role(actor, "MANAGER")
        if not isinstance(transactionIds, list) or len(transactionIds) > MAX_ROWS:
            raise DomainError("BAD_INPUT", "Слишком большая подборка случаев")
        ids = [uuid(v) for v in transactionIds]
        start, end = period(periodFilter)
        at = cutoff_time(cutoff)
        with self.db.connect() as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cases = c.execute("SELECT id,status,created_at FROM review_cases WHERE transaction_id=ANY(%s::uuid[]) AND created_at<=%s", (ids, at)).fetchall()
            decisions = c.execute('''SELECT d.id,d.case_id,d.outcome,d.decided_at FROM case_decisions d JOIN review_cases r ON r.id=d.case_id
                WHERE r.transaction_id=ANY(%s::uuid[]) AND d.decided_at>=%s AND d.decided_at<%s AND d.decided_at<=%s
                ORDER BY d.decided_at,d.id LIMIT %s''', (ids, start, end, at, MAX_ROWS + 1)).fetchall()
            if len(decisions) > MAX_ROWS:
                raise DomainError("BAD_INPUT", "Более 2000 решений; сузьте период")
            captured = c.execute("SELECT transaction_timestamp() AS at").fetchone()["at"]
        data = dict(caseCount=len(cases), newCasesInPeriod=sum(start <= r["created_at"] < end for r in cases), statuses=dict(Counter(r["status"] for r in cases)),
                    decisionCount=len(decisions), decidedCases=len({r["case_id"] for r in decisions}), outcomes=dict(Counter(r["outcome"] for r in decisions)),
                    caseIds=[str(r["id"]) for r in cases], decisions=wire(decisions), axes=dict(cases="cohort transactions; current status at capturedAt", newCases="created_at in [from,to)", decisions="decided_at in [from,to), <=cutoff; cycles counted separately"))
        return dict(data=data, cut=cut("case", captured, at, dict(cases=data["caseIds"], decisions=[str(r["id"]) for r in decisions])))
