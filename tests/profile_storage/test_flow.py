"""Canonical and legacy profile projections stay pinned under new assessment."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.common.behavior_contract import digest
from backend.common.data import DomainError
from backend.common.events import make_event
from backend.infrastructure.product_migrations import migrate
from backend.services.analysis.events.transactions import AnalysisEventService
from backend.services.analysis.profiles.storage import fetch_snapshot_history
from backend.services.analysis.profiles.service import ProfileService
from backend.services.transaction.demo.source import SourceService


class HistoryClient:
    def __init__(self):
        self.t = datetime(2026, 11, 5, 12, tzinfo=UTC)
        self.tx = dict(
            id=str(uuid4()),
            clientId=str(uuid4()),
            accountId=str(uuid4()),
            counterpartyId=None,
            amount="1000.00",
            currency="BYN",
            direction="OUT",
            occurredAt=self.t.isoformat(),
            ingestedAt=self.t.isoformat(),
        )
        self.rows = [
            dict(
                self.tx,
                id=str(uuid4()),
                amount="100.00",
                occurredAt=(self.t - timedelta(days=d)).isoformat(),
                ingestedAt=(self.t - timedelta(hours=1)).isoformat(),
            )
            for d in range(1, 21)
        ]
        self.snapshot = dict(
            snapshotId=str(uuid4()),
            digest=digest(self.rows),
            itemCount=len(self.rows),
            capturedAt=self.t.isoformat(),
            coverage=dict(
                mode="UNKNOWN",
                revision="unknown-v1",
                intervals=[],
                declaredBySource=None,
            ),
        )

    def get_transaction(self, identifier):
        return self.tx

    def prepare_behavior_history(self, parameters):
        self.snapshot["selection"] = parameters
        return self.snapshot

    def get_behavior_history_page(self, parameters):
        return dict(
            snapshotId=self.snapshot["snapshotId"],
            digest=self.snapshot["digest"],
            itemCount=len(self.rows),
            items=self.rows,
            nextPageToken=None,
        )


def test_event_writes_primary_fk_identical_legacy_metrics_and_old_result_stays_pinned(
    database,
):
    db = database("analysis")
    migrate(db, "analysis")
    client = HistoryClient()
    event = make_event(dict(transaction_id=client.tx["id"]))
    event["occurred_at"] = client.t.isoformat()
    with db.connect() as c:
        AnalysisEventService(client).process(c, event)
    result = db.one(
        "SELECT * FROM analysis_results WHERE transaction_id=%s", (client.tx["id"],)
    )
    assert result["calculation_id"] is not None
    versions = db.query(
        "SELECT * FROM profile_versions WHERE calculation_id=%s",
        (result["calculation_id"],),
    )
    primary = next(v for v in versions if v["window_days"] == 30)
    assert all(v["metrics_schema_version"]==4 and v["metrics"]["metricsSchemaVersion"]==4 for v in versions)
    assert all(v["algorithm_version"]=="behavior-profile-v3.2" for v in versions)
    legacy = db.one(
        "SELECT * FROM behavior_profiles WHERE id=%s", (result["profile_id"],)
    )
    assert Decimal(primary["metrics"]["medianAmount"]) == legacy["median_amount"]
    assert legacy["algorithm_version"]==primary["algorithm_version"]
    for key, value in primary["metrics"].items():
        assert legacy["metrics"][key] == value
    hit = db.one(
        "SELECT h.limit_value,v.parameters FROM rule_hits h JOIN rule_versions v ON v.id=h.rule_version_id JOIN analysis_rules r ON r.id=v.rule_id WHERE h.result_id=%s AND r.code='AMOUNT_DEVIATION'",
        (result["id"],),
    )
    assert hit and hit["limit_value"]["upper"] == "125.00000000"
    actor = SimpleNamespace(id=str(uuid4()), role="MANAGER", is_active=True)
    before = ProfileService(db, None).get_detection_context(
        actor, client.tx["id"], str(result["id"])
    )
    assert before["primaryProfileVersionId"] == str(primary["id"])
    with db.connect() as c:
        from backend.services.analysis.profiles.storage import save_profile_versions

        history = fetch_snapshot_history(
            client, client.tx, client.t + timedelta(days=1), client.t
        )
        save_profile_versions(
            c,
            {k: v for k, v in client.tx.items() if k != "id"},
            history,
            client.t + timedelta(days=1),
            client.t,
            origin="SCHEDULED",
        )
    after = ProfileService(db, None).get_detection_context(
        actor, client.tx["id"], str(result["id"])
    )
    assert after == before
    assert (
        len(
            db.query(
                "SELECT * FROM analysis_result_profiles WHERE result_id=%s",
                (result["id"],),
            )
        )
        == 1
    )


def test_history_digest_mismatch_and_system_only_storage(database):
    client = HistoryClient()
    client.snapshot["digest"] = "0" * 64
    with pytest.raises(DomainError, match="digest"):
        fetch_snapshot_history(client, client.tx, client.t, client.t)
    actor = SimpleNamespace(id=str(uuid4()), role="MANAGER", is_active=True)
    with pytest.raises(DomainError) as e:
        SourceService(None).prepare_behavior_history(actor, {})
    assert e.value.code == "FORBIDDEN"
