"""Apply additive migrations only to the calling service's owned database."""

import hashlib
import re
from pathlib import Path


def migrate(db, owner: str, *, migrations_root: Path | None = None) -> None:
    """Apply additive owner migrations atomically; never replay initial/seed SQL.

    Historical ledger rows intentionally keep a NULL hash. A hash is evidence
    only when it was recorded by the transaction that applied that migration.
    """
    if owner not in ("transaction", "analysis", "case"):
        raise ValueError("Unsupported migration owner")
    root = migrations_root or Path(__file__).resolve().parents[2] / "infra/postgres"
    scripts = {}
    numbers = set()
    for path in (root / owner).glob("*.sql"):
        match = re.fullmatch(r"(\d{3})_[a-z0-9_]+\.sql", path.name)
        if not match:
            raise ValueError(f"Invalid migration filename: {path.name}")
        number = int(match[1])
        if number < 5:
            continue
        if number in numbers:
            raise ValueError(f"duplicate migration number: {number}")
        numbers.add(number)
        content = path.read_bytes()
        scripts[path.stem] = (number, content, hashlib.sha256(content).hexdigest())
    with db.connect() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"product-migrations:{owner}",),
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS product_schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        conn.execute("ALTER TABLE product_schema_migrations ADD COLUMN IF NOT EXISTS sha256 CHAR(64)")
        applied = {row["name"]: row["sha256"] for row in conn.execute(
            "SELECT name,sha256 FROM product_schema_migrations"
        )}
        for name, digest in applied.items():
            if name not in scripts:
                raise ValueError(f"Applied migration file missing: {name}")
            _, content, current_digest = scripts[name]
            if digest is not None and digest != current_digest:
                # Older checkouts could change only line endings between Windows and Linux.
                normalized = content.replace(b"\r\n", b"\n")
                candidates = (normalized, normalized.replace(b"\n", b"\r\n"))
                if not any(hashlib.sha256(candidate).hexdigest() == digest for candidate in candidates):
                    raise ValueError(f"Migration hash mismatch: {name}")
        maximum = max((scripts[name][0] for name in applied), default=4)
        for name, (number, content, digest) in sorted(scripts.items(), key=lambda item: item[1][0]):
            if name in applied:
                continue
            if number < maximum:
                raise ValueError(f"Out-of-order migration: {name}")
            conn.execute(content.decode("utf-8-sig"))
            conn.execute(
                "INSERT INTO product_schema_migrations(name,sha256) VALUES (%s,%s)", (name, digest)
            )
