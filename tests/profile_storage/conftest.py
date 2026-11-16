"""Explicitly isolated PostgreSQL only; never fall back to the main deployment."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.common.commands import WritableDatabase

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def storage_stand():
    filename = os.getenv("PROFILE_STORAGE_STAND")
    if not filename:
        pytest.skip("Set PROFILE_STORAGE_STAND to an explicitly isolated audit stand")
    stand = json.loads(Path(filename).read_text(encoding="utf-8"))
    label = subprocess.check_output(
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "bad_trans.audit"}}',
            stand["container"],
        ],
        text=True,
    ).strip()
    if (label != "profile-storage-stage2" and not (label == "system-health-stage10" and stand["container"].startswith("bad-trans-health10-audit-"))) or stand["port"] in range(57741, 57745):
        pytest.fail("Refusing an unlabelled or main PostgreSQL deployment")
    return stand


@pytest.fixture
def database(storage_stand):
    stand = storage_stand

    def create(owner="analysis", restored=False):
        name = f"st2_{owner}_{uuid4().hex[:12]}"
        params = {k: stand[k] for k in ("host", "port", "user", "password")}
        with psycopg.connect(**params, dbname="postgres", autocommit=True) as c:
            statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
            if restored:
                statement += sql.SQL(" TEMPLATE {}").format(
                    sql.Identifier("bank_" + owner)
                )
            c.execute(statement)
        db = WritableDatabase(make_conninfo(**params, dbname=name), owner)
        if not restored:
            with db.connect() as c:
                for path in sorted((ROOT / "infra/postgres" / owner).glob("*.sql")):
                    if int(path.name[:3]) < 5:
                        c.execute(path.read_text(encoding="utf-8"))
        return db

    # Audit databases are retained for evidence/debugging; only the isolated
    # audit container owns them. No DROP DATABASE or main-server writes here.
    return create
