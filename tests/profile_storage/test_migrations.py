"""Real PostgreSQL migration acceptance, including races and rollback."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil

import psycopg
import pytest

from backend.infrastructure.product_migrations import migrate

ROOT = Path(__file__).resolve().parents[2] / "infra/postgres"


def migration_copy(tmp_path, owner="analysis"):
    shutil.copytree(ROOT / owner, tmp_path / owner)
    return tmp_path


@pytest.mark.parametrize("owner", ["transaction", "analysis", "case"])
def test_clean_repeat_and_owner_boundaries(database, owner):
    db = database(owner)
    migrate(db, owner)
    before = db.query("SELECT * FROM product_schema_migrations ORDER BY name")
    migrate(db, owner)
    assert db.query("SELECT * FROM product_schema_migrations ORDER BY name") == before
    assert {r["name"] for r in before} == {p.stem for p in (ROOT/owner).glob("*.sql") if int(p.name[:3]) >= 5}
    assert all(len(r["sha256"]) == 64 for r in before)
    with pytest.raises(ValueError, match="owner"):
        migrate(db, "../analysis")


@pytest.mark.parametrize("owner", ["transaction", "analysis"])
def test_upgrade_preserves_legacy_rows_without_fabricated_profiles(database, owner):
    db = database(owner, restored=True)
    table = "analysis_results" if owner == "analysis" else "transactions"
    before = db.query(f"SELECT * FROM {table} ORDER BY id")
    migrate(db, owner)
    after = db.query(f"SELECT * FROM {table} ORDER BY id")
    assert [{key: row[key] for key in before[0]} for row in after] == before
    if owner == "analysis":
        assert all(row["calculation_id"] is None for row in after)
    old = db.one(
        "SELECT sha256 FROM product_schema_migrations WHERE name='005_product'"
    )
    assert old["sha256"] is None  # Do not pretend a historical hash was recorded.
    if owner == "analysis":
        assert db.one("SELECT count(*) AS n FROM analysis_result_profiles")["n"] == 0
        assert (
            db.one(
                "SELECT count(*) AS n FROM profile_versions WHERE calculation_id IS NOT NULL"
            )["n"]
            == 0
        )


def test_failure_rolls_back_whole_batch_and_retry_succeeds(database, tmp_path):
    db = database()
    root = migration_copy(tmp_path)
    (root / "analysis/010_probe.sql").write_text("CREATE TABLE migration_probe(id int)")
    broken = root / "analysis/011_broken.sql"
    broken.write_text("SELECT definitely_missing_column")
    with pytest.raises(psycopg.Error):
        migrate(db, "analysis", migrations_root=root)
    assert db.one("SELECT to_regclass('migration_probe') AS table")["table"] is None
    assert (
        db.one("SELECT to_regclass('product_schema_migrations') AS table")["table"]
        is None
    )
    broken.write_text("CREATE TABLE migration_probe_two(id int)")
    migrate(db, "analysis", migrations_root=root)
    assert db.one("SELECT count(*) AS n FROM product_schema_migrations")["n"] == 7


def test_hash_mismatch_and_missing_applied_file_are_errors(database, tmp_path):
    db = database()
    root = migration_copy(tmp_path)
    migrate(db, "analysis", migrations_root=root)
    path = root / "analysis/006_profile_storage.sql"
    original = path.read_bytes()
    path.write_bytes(original + b"\n-- changed\n")
    with pytest.raises(ValueError, match="hash"):
        migrate(db, "analysis", migrations_root=root)
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        migrate(db, "analysis", migrations_root=root)


def test_concurrent_start_and_duplicate_number(database, tmp_path):
    db = database()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: migrate(db, "analysis"), range(4)))
    assert db.one("SELECT count(*) AS n FROM product_schema_migrations")["n"] == 5
    root = migration_copy(tmp_path)
    (root / "analysis/006_duplicate.sql").write_text("SELECT 1")
    with pytest.raises(ValueError, match="duplicate"):
        migrate(db, "analysis", migrations_root=root)
