from backend.common.events import utc, ident
from datetime import datetime
import logging


class CaseEventService:
    def process(self, c, event):
        x = event["payload"]
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("case-transaction:" + x["transaction_id"],),
        )
        modern = "assessment_status" in x
        assessment = x.get("assessment_score")
        assessment_status = x.get("assessment_status")
        policy = x.get("assessment_policy_version")
        if modern:
            if (
                assessment_status not in ("COMPLETE", "PARTIAL", "UNAVAILABLE")
                or policy != "behavior-demo-v1"
            ):
                raise ValueError("Unsupported assessment contract")
            if assessment_status == "UNAVAILABLE":
                if (
                    assessment is not None
                    or x.get("review_required") is not None
                    or x["score"] != 0
                ):
                    raise ValueError("Unavailable assessment cannot imply a decision")
            elif (
                type(assessment) is not int
                or not 0 <= assessment <= 100
                or x["score"] != assessment
                or type(x.get("review_required")) is not bool
                or x["review_required"] != (assessment >= x["threshold"])
            ):
                raise ValueError("Inconsistent assessment score/decision")
        row = c.execute(
            "SELECT * FROM review_cases WHERE transaction_id=%s FOR UPDATE",
            (x["transaction_id"],),
        ).fetchone()
        stamp = datetime.fromisoformat(x["calculated_at"])
        priority = (
            "HIGH" if x["score"] >= 80 else "MEDIUM" if x["score"] >= 60 else "LOW"
        )
        if row:
            if assessment_status == "UNAVAILABLE":
                priority = row["priority"]
            if row["status"] == "ESCALATED":
                priority = "HIGH"
            if (stamp, x["result_id"]) > (
                row["latest_calculated_at"],
                str(row["latest_result_id"]),
            ):
                c.execute(
                    "UPDATE review_cases SET latest_result_id=%s,latest_score=%s,latest_calculated_at=%s,priority=%s,row_version=row_version+1,updated_at=%s,latest_assessment_score=%s,latest_assessment_status=%s,latest_assessment_policy_version=%s WHERE id=%s",
                    (
                        x["result_id"],
                        x["score"],
                        stamp,
                        priority,
                        utc(),
                        assessment,
                        assessment_status,
                        policy,
                        row["id"],
                    ),
                )
        elif x["review_required"] is True if modern else x["score"] >= x["threshold"]:
            c.execute(
                "INSERT INTO review_cases(id,transaction_id,client_id,initial_result_id,latest_result_id,latest_score,latest_calculated_at,status,priority,assignee_id,review_cycle,row_version,created_at,updated_at,latest_assessment_score,latest_assessment_status,latest_assessment_policy_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,1,1,%s,%s,%s,%s,%s)",
                (
                    ident(),
                    x["transaction_id"],
                    x["client_id"],
                    x["result_id"],
                    x["result_id"],
                    x["score"],
                    stamp,
                    "NEW",
                    priority,
                    utc(),
                    utc(),
                    assessment,
                    assessment_status,
                    policy,
                ),
            )
        logging.info("CASE event processed transaction=%s", x["transaction_id"])
