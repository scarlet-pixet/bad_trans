import os
"""Exercise the canonical Auth/Health protobuf methods against real processes."""
import grpc
import pytest
from uuid import uuid4

from backend.generated import bank_pb2 as bank, bank_pb2_grpc as rpc
from .test_commands import ENV

METADATA=(('x-service-token',ENV['SERVICE_TOKEN']),)

@pytest.mark.parametrize('owner,port',[('auth',51051),('transaction',51052),('analysis',51053),('case',51054)])
def test_typed_health_is_implemented_for_every_service(owner,port):
    port=int(os.getenv('BAD_TRANS_'+owner.upper()+'_GRPC_PORT',str(port)))
    with grpc.insecure_channel(f'127.0.0.1:{port}') as channel:
        stub=getattr(rpc,owner.title()+'ServiceStub')(channel)
        reply=stub.Health(bank.HealthRequest(),metadata=METADATA,timeout=5)
        assert reply.service==owner and reply.database=='bank_'+owner
        assert reply.table_count>0 and reply.status=='READY'
        with pytest.raises(grpc.RpcError) as denied:
            stub.Health(bank.HealthRequest(),timeout=5)
        assert denied.value.code()==grpc.StatusCode.PERMISSION_DENIED


def test_full_auth_grpc_contract_uses_real_jwt_roles_and_sessions():
    with grpc.insecure_channel(os.getenv('BAD_TRANS_AUTH_GRPC','127.0.0.1:51051')) as channel:
        stub=rpc.AuthServiceStub(channel)
        def call(name,request):
            return getattr(stub,name)(request,metadata=METADATA,timeout=8)
        with pytest.raises(grpc.RpcError) as bad_login:
            call('Login',bank.LoginRequest(login='manager',password='wrong-password'))
        assert bad_login.value.code()==grpc.StatusCode.UNAUTHENTICATED
        manager=call('Login',bank.LoginRequest(login='manager',password='BadTrans-demo-2026!')).data
        assert manager.access_token and manager.user.role=='MANAGER'
        assert call('GetCurrentUser',bank.GetCurrentUserRequest(access_token=manager.access_token)).data.id==manager.user.id
        assert call('ValidateSession',bank.ValidateSessionRequest(access_token=manager.access_token)).data.role=='MANAGER'
        analysts=call('ListActiveAnalysts',bank.ListActiveAnalystsRequest()).data
        assert analysts and all(user.role=='ANALYST' and user.is_active for user in analysts)
        admin=call('Login',bank.LoginRequest(login='admin',password='BadTrans-demo-2026!')).data
        values=bank.StaffInput(login='rpc_'+uuid4().hex[:12],password='Rpc-demo-'+uuid4().hex+'!',role_code='ANALYST',idempotency_key=uuid4().hex)
        with pytest.raises(grpc.RpcError) as forbidden:
            call('RegisterStaff',bank.RegisterStaffRequest(access_token=manager.access_token,input=values))
        assert forbidden.value.code()==grpc.StatusCode.PERMISSION_DENIED
        request=bank.RegisterStaffRequest(access_token=admin.access_token,input=values)
        person=call('RegisterStaff',request).data
        assert call('RegisterStaff',request).data.id==person.id
        token=call('Login',bank.LoginRequest(login=values.login,password=values.password)).data.access_token
        changed=call('ChangeStaffRole',bank.ChangeStaffRoleRequest(access_token=admin.access_token,user_id=person.id,role_code='MANAGER',idempotency_key=uuid4().hex))
        assert changed.data.role=='MANAGER'
        assert call('ValidateSession',bank.ValidateSessionRequest(access_token=token)).data.role=='MANAGER'
        blocked=call('BlockStaff',bank.BlockStaffRequest(access_token=admin.access_token,user_id=person.id,idempotency_key=uuid4().hex))
        assert not blocked.data.is_active
        with pytest.raises(grpc.RpcError) as revoked:
            call('ValidateSession',bank.ValidateSessionRequest(access_token=token))
        assert revoked.value.code()==grpc.StatusCode.UNAUTHENTICATED
        assert call('Logout',bank.LogoutRequest(access_token=manager.access_token)).data
        with pytest.raises(grpc.RpcError) as logged_out:
            call('GetCurrentUser',bank.GetCurrentUserRequest(access_token=manager.access_token))
        assert logged_out.value.code()==grpc.StatusCode.UNAUTHENTICATED
