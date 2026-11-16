"""Probe the application and owned storage instead of an open TCP port."""
import json
import os

import grpc
from backend.generated import transport_pb2, transport_pb2_grpc


def main():
    with grpc.insecure_channel('127.0.0.1:50051') as channel:
        response = transport_pb2_grpc.BusinessServiceStub(channel).Invoke(
            transport_pb2.InvocationRequest(method='health'), timeout=3,
            metadata=(('x-service-token', os.environ['SERVICE_TOKEN']),))
    data = json.loads(response.result_json)
    if response.error_code or not data.get('ready', False):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
