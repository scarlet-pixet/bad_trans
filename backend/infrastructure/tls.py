"""Use a CA-verified gRPC channel and bind exactly one transport per service."""

import os
from pathlib import Path

import grpc


def grpc_channel(address):
    root = os.getenv("GRPC_TLS_CA")
    if root:
        credentials = grpc.ssl_channel_credentials(root_certificates=Path(root).read_bytes())
        return grpc.secure_channel(address, credentials)
    if os.getenv("NETWORK_MODE") == "TLS":
        raise RuntimeError("GRPC_TLS_CA is required in TLS mode")
    return grpc.insecure_channel(address)


def grpc_bind(server, address):
    certificate = os.getenv("GRPC_TLS_CERT")
    key = os.getenv("GRPC_TLS_KEY")
    if certificate and key and os.getenv("GRPC_TLS_CA"):
        credentials = grpc.ssl_server_credentials([
            (Path(key).read_bytes(), Path(certificate).read_bytes())
        ])
        port = server.add_secure_port(address, credentials)
    elif certificate or key or os.getenv("GRPC_TLS_CA") or os.getenv("NETWORK_MODE") == "TLS":
        raise RuntimeError("gRPC TLS requires certificate, key and CA")
    else:
        port = server.add_insecure_port(address)
    if port == 0:
        raise RuntimeError(f"Cannot bind gRPC endpoint {address}")
