"""Health probes use the same CA and server identity as application clients."""

import ssl
import os
import sys
from urllib.request import urlopen


def main(url, auth_grpc=None):
    context = ssl.create_default_context(cafile="/trust/ca.crt")
    with urlopen(url, context=context, timeout=2) as response:
        if response.status != 200:
            raise SystemExit(1)
    if auth_grpc:
        from backend.generated import bank_pb2, bank_pb2_grpc
        from backend.infrastructure.tls import grpc_channel

        with grpc_channel(auth_grpc) as channel:
            result = bank_pb2_grpc.AuthServiceStub(channel).Health(
                bank_pb2.HealthRequest(), timeout=2,
                metadata=(("x-service-token", os.environ["SERVICE_TOKEN"]),),
            )
            if result.status != "READY":
                raise SystemExit(1)


if __name__ == "__main__":
    main(*sys.argv[1:])
