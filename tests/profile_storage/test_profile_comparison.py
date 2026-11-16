from datetime import datetime, UTC, timedelta
from types import SimpleNamespace
from uuid import uuid4

from psycopg.types.json import Jsonb
import pytest

from backend.common.data import DomainError
from backend.infrastructure.product_migrations import migrate
from backend.services.analysis.profiles.workspace import WorkspaceService
from tests.profile_metrics.test_profile_comparison import profile


def test_real_postgres_saved_pair_stays_fixed_when_new_current_profile_appears(
    database,
):
    db = database("analysis")
    migrate(db, "analysis")
    client = str(uuid4())
    t = datetime(2026, 11, 1, tzinfo=UTC)
    ids = [str(uuid4()) for _ in range(3)]

    def insert(index, amount):
        metrics = profile(medianAmount=amount)["metrics"]
        with db.connect() as c:
            c.execute(
                """INSERT INTO profile_versions(id,client_id,currency,direction,window_days,version,as_of,ingested_before,origin,metrics,metrics_schema_version)
                VALUES(%s,%s,'BYN','OUT',30,%s,%s,%s,'MANUAL',%s,4)""",
                (
                    ids[index],
                    client,
                    index + 1,
                    t + timedelta(days=index),
                    t + timedelta(days=index),
                    Jsonb(metrics),
                ),
            )

    insert(0, "100")
    insert(1, "150")
    actor = SimpleNamespace(id=str(uuid4()), role="MANAGER", is_active=True)
    svc = WorkspaceService(db)
    before = svc.compare_profile_versions(actor, ids[0], ids[1])
    assert before["rows"][0]["delta"] == "50"
    insert(2, "900")
    assert svc.compare_profile_versions(actor, ids[0], ids[1]) == before
    assert (
        svc.compare_profile_versions(actor, ids[0], ids[0])["rows"][0]["delta"] == "0"
    )
    with pytest.raises(DomainError, match="не найдена"):
        svc.compare_profile_versions(actor, ids[0], str(uuid4()))
