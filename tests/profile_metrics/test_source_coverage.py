from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from backend.common.data import DomainError
from backend.common.import_csv import normalize_transaction
from backend.common.time_precision import observation_time
from backend.services.analysis.assessments.calculation import assess, default_rules
from backend.services.analysis.profiles.metrics import build_metrics
from backend.services.transaction.history.source_coverage import resolve_scopes
from tools.profile_control import NIGHT, fixture

START = datetime(2026, 10, 1, tzinfo=UTC)
END = START + timedelta(days=30)
FULL = [{"from": START.isoformat(), "to": END.isoformat()}]


def declaration(account, feature, intervals=FULL, source="bank"):
    return {"account_id": account, "source": source, "channel": "ONLINE", "feature": feature,
            "intervals": intervals, "id": str(uuid4())}


def test_daily_and_hourly_coverage_require_all_accounts_and_known_feeds():
    assert resolve_scopes(["a"], [], [], START, END) is None
    legacy = resolve_scopes(["a","b"], [("a","bank","ONLINE")], [], START, END)
    assert legacy is not None
    assert legacy["mode"] == "UNKNOWN" and legacy["gaps"]["accountsWithoutScope"] == 2
    both = [declaration("a", "DAY"), declaration("a", "HOUR")]
    missing = resolve_scopes(["a","b"], [], both, START, END)
    assert missing is not None
    assert missing["mode"] == "UNKNOWN"
    assert missing["gaps"] == {"accountsWithoutScope": 1, "day": 1, "hour": 1}
    both.append(declaration("b","DAY"))
    day_only = resolve_scopes(["a","b"], [], both, START, END)
    assert day_only is not None
    assert day_only["intervals"] == FULL and day_only["hourIntervals"] == []
    assert day_only["gaps"] == {"accountsWithoutScope": 0, "day": 0, "hour": 1}
    both.append(declaration("b","HOUR"))
    complete = resolve_scopes(["a","b"], [], both, START, END)
    assert complete is not None
    assert complete["intervals"] == complete["hourIntervals"] == FULL
    extra = resolve_scopes(["a","b"], [("b","other-source","ATM")], both, START, END)
    assert extra is not None
    assert extra["mode"] == "UNKNOWN" and extra["gaps"]["day"] == 1


def test_only_the_intersection_of_confirmed_dates_is_published():
    cut = START+timedelta(days=3)
    short = [{"from":cut.isoformat(),"to":END.isoformat()}]
    coverage = resolve_scopes(["a","b"],[],[declaration("a","DAY"),declaration("b","DAY",short)],START,END)
    assert coverage is not None
    assert coverage["intervals"] == short and coverage["gaps"]["day"] == 1


def test_date_from_csv_stays_daily_and_explicit_precision_validates():
    fields = {"source":"bank","externalId":"A01","accountId":str(uuid4()),"amount":"100.00",
              "currency":"BYN","direction":"OUT","occurredAt":"2026-11-25"}
    parsed = normalize_transaction(fields,"fingerprint",2)
    assert parsed["timePrecision"] == "DATE" and parsed["occurredAt"] == "2026-11-25T00:00:00+00:00"
    assert observation_time("banksim","2026-11-25T00:00:00Z")[1] == "DATE"
    with pytest.raises(DomainError):
        observation_time("bank","2026-11-25T12:00:00Z","DATE")


def test_compared_rules_pass_for_an_exact_hour_and_skip_for_missing_time():
    args = fixture(NIGHT)
    tx, rows = args["transaction"], args["history"]
    summary = build_metrics(rows,NIGHT,NIGHT,"BYN","OUT",30,coverage=args["coverage"],client_id=tx["clientId"])
    summary["id"] = str(uuid4())
    def by_code(transaction, profile, history):
        return {r["code"]:r for r in assess(transaction,profile,history,default_rules())["checks"]}
    expected = by_code(tx,summary,rows)
    assert expected["UNUSUAL_TIME"]["status"] == "TRIGGERED"
    date_only = by_code({**tx,"timePrecision":"DATE"},summary,rows)
    for code in ("UNUSUAL_TIME","PROFILE_FREQUENCY"):
        assert date_only[code]["dataReasons"] == ["SUBJECT_TIME_UNKNOWN"] and date_only[code]["contribution"] == 0
    modified = [{**rows[0],"timePrecision":"DATE","occurredAt":rows[0]["occurredAt"][:10]+"T00:00:00Z"},*rows[1:]]
    reduced = build_metrics(modified,NIGHT,NIGHT,"BYN","OUT",30,coverage=args["coverage"],client_id=tx["clientId"])
    reduced["id"] = summary["id"]
    assert reduced["sampleSize"] == summary["sampleSize"] and reduced["observation"]["unknownTimeCount"] == 1
    assert reduced["hourCountsUtc"]["0"] == 0
    assert by_code(tx,reduced,modified)["UNUSUAL_TIME"]["dataReasons"] == ["HISTORY_TIME_UNKNOWN"]
    assert "HISTORY_TIME_UNKNOWN" in reduced["quality"]["eligibility"]["time"]["reasons"]
    gap = build_metrics(rows,NIGHT,NIGHT,"BYN","OUT",30,
                        coverage={**args["coverage"],"hourIntervals":[]},client_id=tx["clientId"])
    assert gap["observation"]["fullWindowCovered"]
    assert not gap["quality"]["eligibility"]["time"]["eligible"]
