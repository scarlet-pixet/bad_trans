"""Run coursework acceptance with new Compose volumes, ports and credentials."""
import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import requests

ROOT = Path(__file__).resolve().parents[1]
OWNERS = ("auth", "transaction", "analysis", "case")
SERVICES = {
    "gateway": {8000: "GATEWAY"},
    "frontend": {8501: "FRONTEND"},
    "auth-service": {8001: "AUTH_HTTP", 50051: "AUTH_GRPC"},
    "transaction-service": {50051: "TRANSACTION_GRPC"},
    "analysis-service": {50051: "ANALYSIS_GRPC"},
    "case-service": {50051: "CASE_GRPC"},
    **{f"{owner}-db": {5432: f"{owner.upper()}_DB"} for owner in OWNERS},
    "redis": {6379: "REDIS"},
    "cache": {6379: "CACHE"},
    "kafka": {19100: "KAFKA"},
}
SECRET_NAMES = ("DB_PASSWORD", "JWT_SECRET", "PGADMIN_PASSWORD", "REDIS_PASSWORD",
                "REPORT_SIGNING_KEY", "SERVICE_TOKEN")
OPTIONAL_TEST_CONFIG = (
    "PROFILE_STORAGE_RUNTIME", "REPORT_RUNTIME", "REPORT_EXPORT_RUNTIME", "HEALTH_STAND_CONFIG",
    "PRODUCT_COMPOSE", "PRODUCT_TRANSACTION_DB_PORT", "FINAL_ACCEPTANCE_ARTIFACTS",
)


def environment_ports():
    # Keep all sockets open until the addresses have been selected.
    sockets = []
    try:
        ports = {}
        for service in SERVICES.values():
            for key in service.values():
                sock = socket.socket()
                sockets.append(sock)
                sock.bind(("127.0.0.1", 0))
                ports[key] = sock.getsockname()[1]
        return ports
    finally:
        for sock in sockets:
            sock.close()


def run(args, directory, env, capture=False):
    return subprocess.run(args, cwd=directory, env=env, check=True, text=True,
                          encoding="utf-8", capture_output=capture)


def copy_source(directory):
    ignored = shutil.ignore_patterns("__pycache__", ".pytest_cache")
    for name in ("backend", "frontend", "infra", "tests", "tools", ".streamlit"):
        shutil.copytree(ROOT / name, directory / name, ignore=ignored)
    for name in ("compose.yaml", "compose.tls.yaml", "Dockerfile", ".dockerignore",
                 "constraints.txt", "requirements.txt", "pytest.ini"):
        shutil.copy2(ROOT / name, directory / name)


def compose_config(directory, env, project, ports):
    config = json.loads(run(["docker", "compose", "--env-file", ".env", "-p", project,
                             "config", "--format", "json"], directory, env, capture=True).stdout)
    if config["name"] != project:
        raise RuntimeError("Unexpected project name")
    for resources in ("volumes", "networks"):
        for resource in config[resources].values():
            if resource.get("external") or not resource["name"].startswith(project + "_"):
                raise RuntimeError("Non-isolated Compose resource")
    for name, service in config["services"].items():
        bindings = SERVICES.get(name, {})
        service.pop("ports", None)
        if bindings:
            service["ports"] = [
                {"target": target, "published": str(ports[key]), "host_ip": "127.0.0.1", "protocol": "tcp"}
                for target, key in bindings.items()
            ]
        if "build" in service:
            service["image"] = f"{project}-{name}:local"
    config["services"]["kafka"]["environment"]["KAFKA_ADVERTISED_LISTENERS"] = (
        f"INTERNAL://kafka:9092,EXTERNAL://127.0.0.1:{ports['KAFKA']}"
    )
    config["services"]["frontend"]["environment"]["PUBLIC_GATEWAY_URL"] = (
        f"http://127.0.0.1:{ports['GATEWAY']}"
    )
    (directory / "test-compose.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


def verify_bindings(directory, env, project, ports):
    for name, bindings in SERVICES.items():
        container = f"{project}-{name}-1"
        details = json.loads(run(["docker", "inspect", container], directory, env, capture=True).stdout)[0]
        if details["Config"]["Labels"].get("com.docker.compose.project") != project:
            raise RuntimeError(f"Unexpected container owner for {name}")
        for target, key in bindings.items():
            assigned = details["NetworkSettings"]["Ports"].get(f"{target}/tcp") or []
            if assigned != [{"HostIp": "127.0.0.1", "HostPort": str(ports[key])}]:
                raise RuntimeError(f"Unexpected host binding for {name}/{target}")
        if name.endswith("-db") and not any(
            mount["Type"] == "volume" and mount["Name"] == f"{project}_{name[:-3]}-data"
            for mount in details["Mounts"]
        ):
            raise RuntimeError(f"Unexpected PostgreSQL volume for {name}")


def wait_ready(url):
    with requests.Session() as session:
        session.trust_env = False
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                response = session.get(url + "/health", timeout=12)
                if response.ok and response.json().get("allReady"):
                    return
            except (requests.RequestException, ValueError):
                pass
            time.sleep(2)
    raise RuntimeError("The isolated Gateway did not report allReady")


def test_environment(env, directory, project, ports, db_password):
    gateway = f"http://127.0.0.1:{ports['GATEWAY']}"
    api = gateway + "/graphql"
    evidence = directory / "evidence"
    evidence.mkdir()
    env.update(
        BAD_TRANS_PROJECT=project,
        BAD_TRANS_EVIDENCE_DIR=str(evidence),
        GRAPHQL_URL=api, BAD_TRANS_API=api, PRODUCT_API=api, ADMIN_FIX_API=api,
        AUTH_URL=f"http://127.0.0.1:{ports['AUTH_HTTP']}",
        BAD_TRANS_KAFKA_BOOTSTRAP=f"127.0.0.1:{ports['KAFKA']}",
        BAD_TRANS_REDIS_PORT=str(ports["REDIS"]), BAD_TRANS_CACHE_PORT=str(ports["CACHE"]),
        PROFILE_STORAGE_STAND=str(directory / ".local" / "pg" / "stand.json"),
    )
    for owner in OWNERS:
        env[f"BAD_TRANS_{owner.upper()}_DB_PORT"] = str(ports[f"{owner.upper()}_DB"])
        env[f"BAD_TRANS_{owner.upper()}_GRPC_PORT"] = str(ports[f"{owner.upper()}_GRPC"])
        env[f"BAD_TRANS_{owner.upper()}_GRPC"] = f"127.0.0.1:{ports[f'{owner.upper()}_GRPC']}"
    def dsn(owner):
        return f"postgresql://bank_{owner}:{db_password}@127.0.0.1:{ports[f'{owner.upper()}_DB']}/bank_{owner}"
    env.update(SUMMARY_TEST_DSN=dsn("transaction"), CASE_EVENT_TEST_DSN=dsn("case"))
    return gateway, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Keep the test Compose project for browser inspection")
    parser.add_argument("tests", nargs="*", help="Optional pytest file paths or node IDs")
    args = parser.parse_args()
    project = "bad_trans_test_" + uuid4().hex[:10]
    directory = ROOT / ".local" / project
    directory.mkdir(parents=True)
    copy_source(directory)
    credentials = {name: secrets.token_urlsafe(32) for name in SECRET_NAMES}
    (directory / ".env").write_text("".join(f"{k}={v}\n" for k, v in credentials.items()), encoding="utf-8")
    env = os.environ.copy()
    for key in (*OPTIONAL_TEST_CONFIG, *SECRET_NAMES, "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_ENV_FILES"):
        env.pop(key, None)
    env.update(ENABLE_TEST_FAULTS="1", PROFILE_REFRESH_SECONDS="10", PROFILE_POLL_SECONDS="1",
               PYTHONPATH=str(directory), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHONUNBUFFERED="1")
    ports = environment_ports()
    compose_config(directory, env, project, ports)
    compose = ["docker", "compose", "-p", project, "-f", "test-compose.json"]
    gateway, evidence = test_environment(env, directory, project, ports, credentials["DB_PASSWORD"])
    print(f"Test project: {project}\nEvidence: {evidence}\nFrontend: http://127.0.0.1:{ports['FRONTEND']}"
          f"\nGateway: {gateway}", flush=True)
    pg = [sys.executable, "tools/profile_storage_stand.py"]
    pg_directory = ["--directory", ".local/pg"]
    try:
        infrastructure = [*(f"{owner}-db" for owner in OWNERS), "redis", "cache", "kafka"]
        applications = [name for name in SERVICES if name not in infrastructure]
        run(compose + ["build", *applications], directory, env)
        start = compose + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "240"]
        run(start + infrastructure, directory, env)
        run(start + applications, directory, env)
        verify_bindings(directory, env, project, ports)
        wait_ready(gateway)
        run(pg + ["create", *pg_directory], directory, env)
        run([sys.executable, "tools/prove_flow.py"], directory, env)
        run([sys.executable, "-m", "pytest", "-q", f"--junitxml={evidence / 'pytest.xml'}", *args.tests],
            directory, env)
    finally:
        try:
            if (directory / ".local" / "pg" / "stand.json").is_file():
                run(pg + ["cleanup", *pg_directory], directory, env)
        finally:
            if not args.keep:
                run(compose + ["down", "--volumes"], directory, env)


if __name__ == "__main__":
    main()
