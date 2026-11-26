"""Issue a local CA and the private leaf pairs for compose.tls.yaml."""

import argparse
import ipaddress
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

SERVICES = ("edge", "frontend", "gateway", "auth-service", "transaction-service",
            "analysis-service", "case-service")


def to_san(host):
    try:
        return f"IP:{ipaddress.ip_address(host)}"
    except ValueError:
        if not re.fullmatch(r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*", host):
            raise ValueError("Provide a DNS name or an IP address without a scheme or port") from None
        return f"DNS:{host}"


def issue(target, hosts, openssl, ca_dir=None):
    edge_names = sorted({to_san(host) for host in ("localhost", "127.0.0.1", "edge", *hosts)})
    target.mkdir(parents=True, exist_ok=False)
    private = target / "private"
    private.mkdir(mode=0o700)
    cert, key = target / "ca.crt", private / "ca.key"

    def run(*args):
        subprocess.run([openssl, *map(str, args)], check=True)

    if ca_dir:
        shutil.copyfile(ca_dir / "ca.crt", cert)
        shutil.copyfile(ca_dir / "private" / "ca.key", key)
    else:
        run("req", "-x509", "-newkey", "rsa:3072", "-nodes", "-sha256", "-days", "365",
            "-subj", "/CN=Bad Trans Training CA", "-keyout", key, "-out", cert,
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    key.chmod(0o600)
    with TemporaryDirectory() as work:
        csr, extension = Path(work) / "leaf.csr", Path(work) / "leaf.ext"
        for service in SERVICES:
            folder = target / "leaves" / service
            folder.mkdir(parents=True, mode=0o700)
            server_key = folder / "server.key"
            names = edge_names if service == "edge" else [to_san(service)]
            extension.write_text(
                "basicConstraints=critical,CA:FALSE\n"
                "keyUsage=critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\nsubjectAltName=" + ",".join(names) + "\n",
                encoding="ascii",
            )
            run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-sha256",
                "-subj", f"/CN={service}", "-keyout", server_key, "-out", csr)
            server_key.chmod(0o600)
            run("x509", "-req", "-in", csr, "-CA", cert, "-CAkey", key,
                "-set_serial", str(int.from_bytes(os.urandom(18), "big") + 1),
                "-days", "60", "-sha256", "-extfile", extension, "-out", folder / "server.crt")
            run("verify", "-CAfile", cert, folder / "server.crt")


def main():
    default = shutil.which("openssl")
    if not default and os.name == "nt":
        git_openssl = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/openssl.exe"
        if git_openssl.is_file():
            default = str(git_openssl)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path.home() / ".bad-trans/tls/current")
    parser.add_argument("--public-host", action="append", default=[])
    parser.add_argument("--ca-dir", type=Path, help="Reuse the CA from a previous output directory")
    parser.add_argument("--openssl", default=default)
    args = parser.parse_args()
    if not args.openssl:
        parser.error("OpenSSL is required; pass --openssl with the executable path")
    output = args.output.resolve()
    repository = Path(__file__).resolve().parents[1]
    if output.is_relative_to(repository) and not output.is_relative_to(repository / ".local"):
        parser.error("Within the repository, TLS material must be stored under .local")
    issue(output, args.public_host, args.openssl, args.ca_dir)


if __name__ == "__main__":
    main()
