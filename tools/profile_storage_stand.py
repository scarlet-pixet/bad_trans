"""Create/remove an explicit disposable PostgreSQL for profile storage tests.

Never connects to the main deployment. Images must already exist locally.
Generated credentials stay in the supplied private directory.
"""

import argparse
import json
import secrets
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
LABEL = "profile-storage-stage2"


def docker(*args):
    return subprocess.check_output(
        ["docker", *args], text=True, encoding="utf-8"
    ).strip()


def create(directory):
    directory.mkdir(parents=True, exist_ok=False)
    image = docker("image", "inspect", "--format", "{{.Id}}", "postgres:17-alpine")
    name = "bad-trans-profile-tests-" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    env = directory / "postgres.env"
    env.write_text(
        f"POSTGRES_USER=profile_audit\nPOSTGRES_PASSWORD={password}\n", encoding="utf-8"
    )
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--label",
        f"bad_trans.audit={LABEL}",
        "--memory",
        "384m",
        "--cpus",
        "0.75",
        "--env-file",
        str(env),
        "-p",
        "127.0.0.1::5432",
        image,
        "-c",
        "shared_buffers=32MB",
        "-c",
        "max_connections=30",
    )
    port = int(
        json.loads(
            docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", name)
        )["5432/tcp"][0]["HostPort"]
    )
    stand = dict(
        container=name,
        host="127.0.0.1",
        port=port,
        user="profile_audit",
        password=password,
    )
    path = directory / "stand.json"
    path.write_text(json.dumps(stand), encoding="utf-8")
    # Save the cleanup handle before initialization, including on init failure.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            admin = psycopg.connect(
                **{k: v for k, v in stand.items() if k != "container"},
                dbname="postgres",
                autocommit=True,
                connect_timeout=1,
            )
            break
        except psycopg.OperationalError:
            time.sleep(0.25)
    else:
        raise RuntimeError(
            f"Audit PostgreSQL did not become ready; cleanup handle: {path}"
        )
    with admin:
        for owner in ("transaction", "analysis"):
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier("bank_" + owner))
            )
    for owner in ("transaction", "analysis"):
        with psycopg.connect(
            **{k: v for k, v in stand.items() if k != "container"},
            dbname="bank_" + owner,
        ) as c:
            for script in sorted((ROOT / "infra/postgres" / owner).glob("*.sql")):
                if int(script.name[:3]) <= 5:
                    c.execute(script.read_text(encoding="utf-8"))
            c.execute(
                "CREATE TABLE product_schema_migrations(name TEXT PRIMARY KEY,applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            c.execute(
                "INSERT INTO product_schema_migrations(name) VALUES ('005_product')"
            )
    print(f"Isolated test PostgreSQL ready. Set PROFILE_STORAGE_STAND to {path}")


def cleanup(directory):
    path = directory / "stand.json"
    stand = json.loads(path.read_text(encoding="utf-8"))
    raw = json.loads(docker("inspect", stand["container"]))[0]
    if (
        raw["Config"]["Labels"].get("bad_trans.audit") != LABEL
        or not stand["container"].startswith("bad-trans-profile-tests-")
        or any(
            m["Type"] == "bind" or m.get("Name", "").startswith("bad_trans_")
            for m in raw.get("Mounts", [])
        )
    ):
        raise RuntimeError(
            "Refusing cleanup: resource is not this tool's isolated test PostgreSQL"
        )
    docker("rm", "-f", "-v", stand["container"])
    print(
        "Removed only the verified test container and its anonymous volume; private files retained"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "cleanup"))
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to(ROOT / ".local"):
        parser.error("Use a fresh private directory inside this project's .local")
    (create if args.action == "create" else cleanup)(directory)
