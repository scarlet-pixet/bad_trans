from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class InvocationRequest(_message.Message):
    __slots__ = ("method", "actor_id", "actor_login", "actor_role", "arguments_json")
    METHOD_FIELD_NUMBER: _ClassVar[int]
    ACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    ACTOR_LOGIN_FIELD_NUMBER: _ClassVar[int]
    ACTOR_ROLE_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_JSON_FIELD_NUMBER: _ClassVar[int]
    method: str
    actor_id: str
    actor_login: str
    actor_role: str
    arguments_json: bytes
    def __init__(self, method: _Optional[str] = ..., actor_id: _Optional[str] = ..., actor_login: _Optional[str] = ..., actor_role: _Optional[str] = ..., arguments_json: _Optional[bytes] = ...) -> None: ...

class InvocationResponse(_message.Message):
    __slots__ = ("result_json", "error_code", "error_message")
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    result_json: bytes
    error_code: str
    error_message: str
    def __init__(self, result_json: _Optional[bytes] = ..., error_code: _Optional[str] = ..., error_message: _Optional[str] = ...) -> None: ...
