"""Regenerate Python stubs from the canonical public and internal contracts."""
from pathlib import Path
import subprocess
import sys
import grpc_tools

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'backend/contracts/proto'
destination=ROOT/'backend/generated'
destination.mkdir(exist_ok=True)
google=Path(grpc_tools.__file__).parent/'_proto'
subprocess.run([sys.executable,'-m','grpc_tools.protoc',f'-I{source}',f'-I{google}',
                f'--python_out={destination}',f'--pyi_out={destination}',f'--grpc_python_out={destination}',
                str(source/'bank.proto'),str(source/'transport.proto')],check=True)
for name in ('bank','transport'):
    path=destination/f'{name}_pb2_grpc.py'
    path.write_text(path.read_text().replace(f'import {name}_pb2 as',f'from . import {name}_pb2 as'),encoding='utf-8')
print('Generated bank.v1 and bank.internal.v1 protobuf clients and servers.')
