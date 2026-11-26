import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class User(_message.Message):
    __slots__ = ("id", "login", "role", "is_active")
    ID_FIELD_NUMBER: _ClassVar[int]
    LOGIN_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    IS_ACTIVE_FIELD_NUMBER: _ClassVar[int]
    id: str
    login: str
    role: str
    is_active: bool
    def __init__(self, id: _Optional[str] = ..., login: _Optional[str] = ..., role: _Optional[str] = ..., is_active: _Optional[bool] = ...) -> None: ...

class AuthPayload(_message.Message):
    __slots__ = ("access_token", "expires_at", "user")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    USER_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    expires_at: _timestamp_pb2.Timestamp
    user: User
    def __init__(self, access_token: _Optional[str] = ..., expires_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., user: _Optional[_Union[User, _Mapping]] = ...) -> None: ...

class Client(_message.Message):
    __slots__ = ("id", "external_id", "display_name", "segment", "is_active", "accounts")
    ID_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    SEGMENT_FIELD_NUMBER: _ClassVar[int]
    IS_ACTIVE_FIELD_NUMBER: _ClassVar[int]
    ACCOUNTS_FIELD_NUMBER: _ClassVar[int]
    id: str
    external_id: str
    display_name: str
    segment: str
    is_active: bool
    accounts: _containers.RepeatedCompositeFieldContainer[Account]
    def __init__(self, id: _Optional[str] = ..., external_id: _Optional[str] = ..., display_name: _Optional[str] = ..., segment: _Optional[str] = ..., is_active: _Optional[bool] = ..., accounts: _Optional[_Iterable[_Union[Account, _Mapping]]] = ...) -> None: ...

class Account(_message.Message):
    __slots__ = ("id", "client_id", "external_id", "currency", "status")
    ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    id: str
    client_id: str
    external_id: str
    currency: str
    status: str
    def __init__(self, id: _Optional[str] = ..., client_id: _Optional[str] = ..., external_id: _Optional[str] = ..., currency: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class Counterparty(_message.Message):
    __slots__ = ("id", "external_id", "display_name", "category")
    ID_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    id: str
    external_id: str
    display_name: str
    category: str
    def __init__(self, id: _Optional[str] = ..., external_id: _Optional[str] = ..., display_name: _Optional[str] = ..., category: _Optional[str] = ...) -> None: ...

class Transaction(_message.Message):
    __slots__ = ("id", "external_id", "source", "client_id", "account_id", "counterparty_id", "amount", "currency", "direction", "occurred_at", "ingested_at", "time_precision", "operation_kind", "operation_channel")
    ID_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    COUNTERPARTY_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    INGESTED_AT_FIELD_NUMBER: _ClassVar[int]
    TIME_PRECISION_FIELD_NUMBER: _ClassVar[int]
    OPERATION_KIND_FIELD_NUMBER: _ClassVar[int]
    OPERATION_CHANNEL_FIELD_NUMBER: _ClassVar[int]
    id: str
    external_id: str
    source: str
    client_id: str
    account_id: str
    counterparty_id: str
    amount: str
    currency: str
    direction: str
    occurred_at: _timestamp_pb2.Timestamp
    ingested_at: _timestamp_pb2.Timestamp
    time_precision: str
    operation_kind: str
    operation_channel: str
    def __init__(self, id: _Optional[str] = ..., external_id: _Optional[str] = ..., source: _Optional[str] = ..., client_id: _Optional[str] = ..., account_id: _Optional[str] = ..., counterparty_id: _Optional[str] = ..., amount: _Optional[str] = ..., currency: _Optional[str] = ..., direction: _Optional[str] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., ingested_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., time_precision: _Optional[str] = ..., operation_kind: _Optional[str] = ..., operation_channel: _Optional[str] = ...) -> None: ...

class ImportError(_message.Message):
    __slots__ = ("row", "code", "message")
    ROW_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    row: int
    code: str
    message: str
    def __init__(self, row: _Optional[int] = ..., code: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class ImportResult(_message.Message):
    __slots__ = ("batch_id", "status", "received_count", "new_count", "duplicate_count", "errors")
    BATCH_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    RECEIVED_COUNT_FIELD_NUMBER: _ClassVar[int]
    NEW_COUNT_FIELD_NUMBER: _ClassVar[int]
    DUPLICATE_COUNT_FIELD_NUMBER: _ClassVar[int]
    ERRORS_FIELD_NUMBER: _ClassVar[int]
    batch_id: str
    status: str
    received_count: int
    new_count: int
    duplicate_count: int
    errors: _containers.RepeatedCompositeFieldContainer[ImportError]
    def __init__(self, batch_id: _Optional[str] = ..., status: _Optional[str] = ..., received_count: _Optional[int] = ..., new_count: _Optional[int] = ..., duplicate_count: _Optional[int] = ..., errors: _Optional[_Iterable[_Union[ImportError, _Mapping]]] = ...) -> None: ...

class BehaviorProfile(_message.Message):
    __slots__ = ("id", "run_id", "client_id", "currency", "direction", "window_start", "window_end", "cutoff_transaction_id", "ingested_before", "sample_size", "median_amount", "mad_amount", "metrics", "data_status")
    ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    WINDOW_START_FIELD_NUMBER: _ClassVar[int]
    WINDOW_END_FIELD_NUMBER: _ClassVar[int]
    CUTOFF_TRANSACTION_ID_FIELD_NUMBER: _ClassVar[int]
    INGESTED_BEFORE_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_SIZE_FIELD_NUMBER: _ClassVar[int]
    MEDIAN_AMOUNT_FIELD_NUMBER: _ClassVar[int]
    MAD_AMOUNT_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    DATA_STATUS_FIELD_NUMBER: _ClassVar[int]
    id: str
    run_id: str
    client_id: str
    currency: str
    direction: str
    window_start: _timestamp_pb2.Timestamp
    window_end: _timestamp_pb2.Timestamp
    cutoff_transaction_id: str
    ingested_before: _timestamp_pb2.Timestamp
    sample_size: int
    median_amount: str
    mad_amount: str
    metrics: _struct_pb2.Value
    data_status: str
    def __init__(self, id: _Optional[str] = ..., run_id: _Optional[str] = ..., client_id: _Optional[str] = ..., currency: _Optional[str] = ..., direction: _Optional[str] = ..., window_start: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., window_end: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., cutoff_transaction_id: _Optional[str] = ..., ingested_before: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., sample_size: _Optional[int] = ..., median_amount: _Optional[str] = ..., mad_amount: _Optional[str] = ..., metrics: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., data_status: _Optional[str] = ...) -> None: ...

class RuleHit(_message.Message):
    __slots__ = ("rule_code", "version", "feature_value", "limit_value", "contribution")
    RULE_CODE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    FEATURE_VALUE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_VALUE_FIELD_NUMBER: _ClassVar[int]
    CONTRIBUTION_FIELD_NUMBER: _ClassVar[int]
    rule_code: str
    version: int
    feature_value: _struct_pb2.Value
    limit_value: _struct_pb2.Value
    contribution: int
    def __init__(self, rule_code: _Optional[str] = ..., version: _Optional[int] = ..., feature_value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., limit_value: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., contribution: _Optional[int] = ...) -> None: ...

class AnalysisResult(_message.Message):
    __slots__ = ("id", "transaction_id", "run_id", "profile_id", "score", "threshold", "data_status", "hits", "missing_reasons", "calculated_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    TRANSACTION_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    DATA_STATUS_FIELD_NUMBER: _ClassVar[int]
    HITS_FIELD_NUMBER: _ClassVar[int]
    MISSING_REASONS_FIELD_NUMBER: _ClassVar[int]
    CALCULATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    transaction_id: str
    run_id: str
    profile_id: str
    score: int
    threshold: int
    data_status: str
    hits: _containers.RepeatedCompositeFieldContainer[RuleHit]
    missing_reasons: _containers.RepeatedScalarFieldContainer[str]
    calculated_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., transaction_id: _Optional[str] = ..., run_id: _Optional[str] = ..., profile_id: _Optional[str] = ..., score: _Optional[int] = ..., threshold: _Optional[int] = ..., data_status: _Optional[str] = ..., hits: _Optional[_Iterable[_Union[RuleHit, _Mapping]]] = ..., missing_reasons: _Optional[_Iterable[str]] = ..., calculated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AnalysisRun(_message.Message):
    __slots__ = ("id", "mode", "status", "counters", "created_at", "completed_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    COUNTERS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    mode: str
    status: str
    counters: _struct_pb2.Value
    created_at: _timestamp_pb2.Timestamp
    completed_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., mode: _Optional[str] = ..., status: _Optional[str] = ..., counters: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., completed_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class RunComparison(_message.Message):
    __slots__ = ("common_count", "only_first_count", "only_second_count", "changed_scores", "new_hits")
    COMMON_COUNT_FIELD_NUMBER: _ClassVar[int]
    ONLY_FIRST_COUNT_FIELD_NUMBER: _ClassVar[int]
    ONLY_SECOND_COUNT_FIELD_NUMBER: _ClassVar[int]
    CHANGED_SCORES_FIELD_NUMBER: _ClassVar[int]
    NEW_HITS_FIELD_NUMBER: _ClassVar[int]
    common_count: int
    only_first_count: int
    only_second_count: int
    changed_scores: _struct_pb2.Value
    new_hits: _struct_pb2.Value
    def __init__(self, common_count: _Optional[int] = ..., only_first_count: _Optional[int] = ..., only_second_count: _Optional[int] = ..., changed_scores: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., new_hits: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...

class RuleVersion(_message.Message):
    __slots__ = ("id", "rule_id", "version", "weight", "parameters", "status", "published_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    RULE_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    WEIGHT_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PUBLISHED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    rule_id: str
    version: int
    weight: int
    parameters: _struct_pb2.Value
    status: str
    published_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., rule_id: _Optional[str] = ..., version: _Optional[int] = ..., weight: _Optional[int] = ..., parameters: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., status: _Optional[str] = ..., published_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class CaseComment(_message.Message):
    __slots__ = ("id", "author_id", "kind", "text", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    author_id: str
    kind: str
    text: str
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., author_id: _Optional[str] = ..., kind: _Optional[str] = ..., text: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class CaseDecision(_message.Message):
    __slots__ = ("id", "author_id", "review_cycle", "outcome", "reason", "decided_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_ID_FIELD_NUMBER: _ClassVar[int]
    REVIEW_CYCLE_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    DECIDED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    author_id: str
    review_cycle: int
    outcome: str
    reason: str
    decided_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., author_id: _Optional[str] = ..., review_cycle: _Optional[int] = ..., outcome: _Optional[str] = ..., reason: _Optional[str] = ..., decided_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ReviewCase(_message.Message):
    __slots__ = ("id", "transaction_id", "client_id", "latest_result_id", "latest_score", "status", "priority", "assignee_id", "review_cycle", "row_version", "comments", "decisions")
    ID_FIELD_NUMBER: _ClassVar[int]
    TRANSACTION_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    LATEST_RESULT_ID_FIELD_NUMBER: _ClassVar[int]
    LATEST_SCORE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    ASSIGNEE_ID_FIELD_NUMBER: _ClassVar[int]
    REVIEW_CYCLE_FIELD_NUMBER: _ClassVar[int]
    ROW_VERSION_FIELD_NUMBER: _ClassVar[int]
    COMMENTS_FIELD_NUMBER: _ClassVar[int]
    DECISIONS_FIELD_NUMBER: _ClassVar[int]
    id: str
    transaction_id: str
    client_id: str
    latest_result_id: str
    latest_score: int
    status: str
    priority: str
    assignee_id: str
    review_cycle: int
    row_version: int
    comments: _containers.RepeatedCompositeFieldContainer[CaseComment]
    decisions: _containers.RepeatedCompositeFieldContainer[CaseDecision]
    def __init__(self, id: _Optional[str] = ..., transaction_id: _Optional[str] = ..., client_id: _Optional[str] = ..., latest_result_id: _Optional[str] = ..., latest_score: _Optional[int] = ..., status: _Optional[str] = ..., priority: _Optional[str] = ..., assignee_id: _Optional[str] = ..., review_cycle: _Optional[int] = ..., row_version: _Optional[int] = ..., comments: _Optional[_Iterable[_Union[CaseComment, _Mapping]]] = ..., decisions: _Optional[_Iterable[_Union[CaseDecision, _Mapping]]] = ...) -> None: ...

class Report(_message.Message):
    __slots__ = ("id", "period_start", "period_end", "filters", "snapshot", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    PERIOD_START_FIELD_NUMBER: _ClassVar[int]
    PERIOD_END_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    period_start: _timestamp_pb2.Timestamp
    period_end: _timestamp_pb2.Timestamp
    filters: _struct_pb2.Value
    snapshot: _struct_pb2.Value
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., period_start: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., period_end: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., filters: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., snapshot: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class Notification(_message.Message):
    __slots__ = ("id", "case_id", "type", "created_at", "read_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    READ_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    case_id: str
    type: str
    created_at: _timestamp_pb2.Timestamp
    read_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., case_id: _Optional[str] = ..., type: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., read_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ClientActivity(_message.Message):
    __slots__ = ("client", "currency", "transactions", "profiles", "score_distribution", "partial_errors")
    CLIENT_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    TRANSACTIONS_FIELD_NUMBER: _ClassVar[int]
    PROFILES_FIELD_NUMBER: _ClassVar[int]
    SCORE_DISTRIBUTION_FIELD_NUMBER: _ClassVar[int]
    PARTIAL_ERRORS_FIELD_NUMBER: _ClassVar[int]
    client: Client
    currency: str
    transactions: _containers.RepeatedCompositeFieldContainer[Transaction]
    profiles: _containers.RepeatedCompositeFieldContainer[BehaviorProfile]
    score_distribution: _struct_pb2.Value
    partial_errors: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, client: _Optional[_Union[Client, _Mapping]] = ..., currency: _Optional[str] = ..., transactions: _Optional[_Iterable[_Union[Transaction, _Mapping]]] = ..., profiles: _Optional[_Iterable[_Union[BehaviorProfile, _Mapping]]] = ..., score_distribution: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., partial_errors: _Optional[_Iterable[str]] = ...) -> None: ...

class DistributionResult(_message.Message):
    __slots__ = ("assigned_count", "unassigned_count", "assignments")
    ASSIGNED_COUNT_FIELD_NUMBER: _ClassVar[int]
    UNASSIGNED_COUNT_FIELD_NUMBER: _ClassVar[int]
    ASSIGNMENTS_FIELD_NUMBER: _ClassVar[int]
    assigned_count: int
    unassigned_count: int
    assignments: _struct_pb2.Value
    def __init__(self, assigned_count: _Optional[int] = ..., unassigned_count: _Optional[int] = ..., assignments: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...

class ReportExport(_message.Message):
    __slots__ = ("file_name", "media_type", "content_base64")
    FILE_NAME_FIELD_NUMBER: _ClassVar[int]
    MEDIA_TYPE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_BASE64_FIELD_NUMBER: _ClassVar[int]
    file_name: str
    media_type: str
    content_base64: str
    def __init__(self, file_name: _Optional[str] = ..., media_type: _Optional[str] = ..., content_base64: _Optional[str] = ...) -> None: ...

class ClientPage(_message.Message):
    __slots__ = ("items", "next_cursor", "total_count", "current_cursor")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    CURRENT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[Client]
    next_cursor: str
    total_count: int
    current_cursor: str
    def __init__(self, items: _Optional[_Iterable[_Union[Client, _Mapping]]] = ..., next_cursor: _Optional[str] = ..., total_count: _Optional[int] = ..., current_cursor: _Optional[str] = ...) -> None: ...

class TransactionPage(_message.Message):
    __slots__ = ("items", "next_cursor", "total_count", "current_cursor")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    CURRENT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[Transaction]
    next_cursor: str
    total_count: int
    current_cursor: str
    def __init__(self, items: _Optional[_Iterable[_Union[Transaction, _Mapping]]] = ..., next_cursor: _Optional[str] = ..., total_count: _Optional[int] = ..., current_cursor: _Optional[str] = ...) -> None: ...

class CasePage(_message.Message):
    __slots__ = ("items", "next_cursor", "total_count", "current_cursor")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    CURRENT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[ReviewCase]
    next_cursor: str
    total_count: int
    current_cursor: str
    def __init__(self, items: _Optional[_Iterable[_Union[ReviewCase, _Mapping]]] = ..., next_cursor: _Optional[str] = ..., total_count: _Optional[int] = ..., current_cursor: _Optional[str] = ...) -> None: ...

class NotificationPage(_message.Message):
    __slots__ = ("items", "next_cursor", "total_count", "current_cursor")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    CURRENT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[Notification]
    next_cursor: str
    total_count: int
    current_cursor: str
    def __init__(self, items: _Optional[_Iterable[_Union[Notification, _Mapping]]] = ..., next_cursor: _Optional[str] = ..., total_count: _Optional[int] = ..., current_cursor: _Optional[str] = ...) -> None: ...

class PeriodInput(_message.Message):
    __slots__ = ("to",)
    FROM_FIELD_NUMBER: _ClassVar[int]
    TO_FIELD_NUMBER: _ClassVar[int]
    to: _timestamp_pb2.Timestamp
    def __init__(self, to: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., **kwargs) -> None: ...

class PageInput(_message.Message):
    __slots__ = ("limit", "cursor", "sort_by", "sort_direction")
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    SORT_BY_FIELD_NUMBER: _ClassVar[int]
    SORT_DIRECTION_FIELD_NUMBER: _ClassVar[int]
    limit: int
    cursor: str
    sort_by: str
    sort_direction: str
    def __init__(self, limit: _Optional[int] = ..., cursor: _Optional[str] = ..., sort_by: _Optional[str] = ..., sort_direction: _Optional[str] = ...) -> None: ...

class ClientFilter(_message.Message):
    __slots__ = ("external_id", "segment", "is_active")
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    SEGMENT_FIELD_NUMBER: _ClassVar[int]
    IS_ACTIVE_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    segment: str
    is_active: bool
    def __init__(self, external_id: _Optional[str] = ..., segment: _Optional[str] = ..., is_active: _Optional[bool] = ...) -> None: ...

class StaffInput(_message.Message):
    __slots__ = ("login", "password", "role_code", "idempotency_key")
    LOGIN_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    ROLE_CODE_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    login: str
    password: str
    role_code: str
    idempotency_key: str
    def __init__(self, login: _Optional[str] = ..., password: _Optional[str] = ..., role_code: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ClientInput(_message.Message):
    __slots__ = ("external_id", "display_name", "segment", "idempotency_key")
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    SEGMENT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    display_name: str
    segment: str
    idempotency_key: str
    def __init__(self, external_id: _Optional[str] = ..., display_name: _Optional[str] = ..., segment: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class AccountInput(_message.Message):
    __slots__ = ("client_id", "external_id", "currency", "idempotency_key")
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    client_id: str
    external_id: str
    currency: str
    idempotency_key: str
    def __init__(self, client_id: _Optional[str] = ..., external_id: _Optional[str] = ..., currency: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class CounterpartyInput(_message.Message):
    __slots__ = ("external_id", "display_name", "category", "idempotency_key")
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    external_id: str
    display_name: str
    category: str
    idempotency_key: str
    def __init__(self, external_id: _Optional[str] = ..., display_name: _Optional[str] = ..., category: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class TransactionInput(_message.Message):
    __slots__ = ("source", "external_id", "account_id", "counterparty_id", "amount", "currency", "direction", "occurred_at", "idempotency_key", "time_precision")
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    COUNTERPARTY_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    TIME_PRECISION_FIELD_NUMBER: _ClassVar[int]
    source: str
    external_id: str
    account_id: str
    counterparty_id: str
    amount: str
    currency: str
    direction: str
    occurred_at: _timestamp_pb2.Timestamp
    idempotency_key: str
    time_precision: str
    def __init__(self, source: _Optional[str] = ..., external_id: _Optional[str] = ..., account_id: _Optional[str] = ..., counterparty_id: _Optional[str] = ..., amount: _Optional[str] = ..., currency: _Optional[str] = ..., direction: _Optional[str] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., idempotency_key: _Optional[str] = ..., time_precision: _Optional[str] = ...) -> None: ...

class ImportBatchInput(_message.Message):
    __slots__ = ("file_hash", "items", "idempotency_key")
    FILE_HASH_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    file_hash: str
    items: _containers.RepeatedCompositeFieldContainer[TransactionInput]
    idempotency_key: str
    def __init__(self, file_hash: _Optional[str] = ..., items: _Optional[_Iterable[_Union[TransactionInput, _Mapping]]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ProfileInput(_message.Message):
    __slots__ = ("client_id", "currency", "direction", "cutoff", "idempotency_key")
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    CUTOFF_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    client_id: str
    currency: str
    direction: str
    cutoff: _timestamp_pb2.Timestamp
    idempotency_key: str
    def __init__(self, client_id: _Optional[str] = ..., currency: _Optional[str] = ..., direction: _Optional[str] = ..., cutoff: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ReanalysisInput(_message.Message):
    __slots__ = ("period", "client_id", "rule_version_ids", "idempotency_key")
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    RULE_VERSION_IDS_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    period: PeriodInput
    client_id: str
    rule_version_ids: _containers.RepeatedScalarFieldContainer[str]
    idempotency_key: str
    def __init__(self, period: _Optional[_Union[PeriodInput, _Mapping]] = ..., client_id: _Optional[str] = ..., rule_version_ids: _Optional[_Iterable[str]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class RuleVersionInput(_message.Message):
    __slots__ = ("rule_id", "version", "parameters", "weight", "control_examples", "idempotency_key")
    RULE_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    WEIGHT_FIELD_NUMBER: _ClassVar[int]
    CONTROL_EXAMPLES_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    rule_id: str
    version: int
    parameters: _struct_pb2.Value
    weight: int
    control_examples: _struct_pb2.Value
    idempotency_key: str
    def __init__(self, rule_id: _Optional[str] = ..., version: _Optional[int] = ..., parameters: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., weight: _Optional[int] = ..., control_examples: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class CaseDecisionInput(_message.Message):
    __slots__ = ("case_id", "outcome", "reason", "expected_version", "idempotency_key")
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_VERSION_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    case_id: str
    outcome: str
    reason: str
    expected_version: int
    idempotency_key: str
    def __init__(self, case_id: _Optional[str] = ..., outcome: _Optional[str] = ..., reason: _Optional[str] = ..., expected_version: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class CaseCommentInput(_message.Message):
    __slots__ = ("case_id", "text", "idempotency_key")
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    case_id: str
    text: str
    idempotency_key: str
    def __init__(self, case_id: _Optional[str] = ..., text: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class DistributionInput(_message.Message):
    __slots__ = ("limit", "idempotency_key")
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    limit: int
    idempotency_key: str
    def __init__(self, limit: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class CaseFilter(_message.Message):
    __slots__ = ("status", "priority", "assignee_id", "client_id", "period")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    ASSIGNEE_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    status: str
    priority: str
    assignee_id: str
    client_id: str
    period: PeriodInput
    def __init__(self, status: _Optional[str] = ..., priority: _Optional[str] = ..., assignee_id: _Optional[str] = ..., client_id: _Optional[str] = ..., period: _Optional[_Union[PeriodInput, _Mapping]] = ...) -> None: ...

class TransactionFilter(_message.Message):
    __slots__ = ("client_id", "period", "currency", "amount_from", "amount_to", "direction")
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FROM_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_TO_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    client_id: str
    period: PeriodInput
    currency: str
    amount_from: str
    amount_to: str
    direction: str
    def __init__(self, client_id: _Optional[str] = ..., period: _Optional[_Union[PeriodInput, _Mapping]] = ..., currency: _Optional[str] = ..., amount_from: _Optional[str] = ..., amount_to: _Optional[str] = ..., direction: _Optional[str] = ...) -> None: ...

class ReportInput(_message.Message):
    __slots__ = ("period", "outcome", "assignee_id", "idempotency_key")
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    ASSIGNEE_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    period: PeriodInput
    outcome: str
    assignee_id: str
    idempotency_key: str
    def __init__(self, period: _Optional[_Union[PeriodInput, _Mapping]] = ..., outcome: _Optional[str] = ..., assignee_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class GetCurrentUserRequest(_message.Message):
    __slots__ = ("access_token",)
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    def __init__(self, access_token: _Optional[str] = ...) -> None: ...

class GetCurrentUserResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: User
    def __init__(self, data: _Optional[_Union[User, _Mapping]] = ...) -> None: ...

class SearchClientsRequest(_message.Message):
    __slots__ = ("access_token", "filter", "page")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    FILTER_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    filter: ClientFilter
    page: PageInput
    def __init__(self, access_token: _Optional[str] = ..., filter: _Optional[_Union[ClientFilter, _Mapping]] = ..., page: _Optional[_Union[PageInput, _Mapping]] = ...) -> None: ...

class SearchClientsResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: ClientPage
    def __init__(self, data: _Optional[_Union[ClientPage, _Mapping]] = ...) -> None: ...

class SearchTransactionsRequest(_message.Message):
    __slots__ = ("access_token", "filter", "page")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    FILTER_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    filter: TransactionFilter
    page: PageInput
    def __init__(self, access_token: _Optional[str] = ..., filter: _Optional[_Union[TransactionFilter, _Mapping]] = ..., page: _Optional[_Union[PageInput, _Mapping]] = ...) -> None: ...

class SearchTransactionsResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: TransactionPage
    def __init__(self, data: _Optional[_Union[TransactionPage, _Mapping]] = ...) -> None: ...

class GetTransactionRequest(_message.Message):
    __slots__ = ("access_token", "id")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    id: str
    def __init__(self, access_token: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class GetTransactionResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: Transaction
    def __init__(self, data: _Optional[_Union[Transaction, _Mapping]] = ...) -> None: ...

class GetClientActivityRequest(_message.Message):
    __slots__ = ("access_token", "client_id", "period", "currency", "run_id")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    client_id: str
    period: PeriodInput
    currency: str
    run_id: str
    def __init__(self, access_token: _Optional[str] = ..., client_id: _Optional[str] = ..., period: _Optional[_Union[PeriodInput, _Mapping]] = ..., currency: _Optional[str] = ..., run_id: _Optional[str] = ...) -> None: ...

class GetClientActivityResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: ClientActivity
    def __init__(self, data: _Optional[_Union[ClientActivity, _Mapping]] = ...) -> None: ...

class GetProfileRequest(_message.Message):
    __slots__ = ("access_token", "id")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    id: str
    def __init__(self, access_token: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class GetProfileResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: BehaviorProfile
    def __init__(self, data: _Optional[_Union[BehaviorProfile, _Mapping]] = ...) -> None: ...

class GetResultRequest(_message.Message):
    __slots__ = ("access_token", "id")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    id: str
    def __init__(self, access_token: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class GetResultResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: AnalysisResult
    def __init__(self, data: _Optional[_Union[AnalysisResult, _Mapping]] = ...) -> None: ...

class GetRunRequest(_message.Message):
    __slots__ = ("access_token", "id")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    id: str
    def __init__(self, access_token: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class GetRunResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: AnalysisRun
    def __init__(self, data: _Optional[_Union[AnalysisRun, _Mapping]] = ...) -> None: ...

class CompareRunsRequest(_message.Message):
    __slots__ = ("access_token", "first_id", "second_id")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    FIRST_ID_FIELD_NUMBER: _ClassVar[int]
    SECOND_ID_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    first_id: str
    second_id: str
    def __init__(self, access_token: _Optional[str] = ..., first_id: _Optional[str] = ..., second_id: _Optional[str] = ...) -> None: ...

class CompareRunsResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: RunComparison
    def __init__(self, data: _Optional[_Union[RunComparison, _Mapping]] = ...) -> None: ...

class ListRuleVersionsRequest(_message.Message):
    __slots__ = ("access_token", "rule_id")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    RULE_ID_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    rule_id: str
    def __init__(self, access_token: _Optional[str] = ..., rule_id: _Optional[str] = ...) -> None: ...

class ListRuleVersionsResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: _containers.RepeatedCompositeFieldContainer[RuleVersion]
    def __init__(self, data: _Optional[_Iterable[_Union[RuleVersion, _Mapping]]] = ...) -> None: ...

class SearchCasesRequest(_message.Message):
    __slots__ = ("access_token", "filter", "page")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    FILTER_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    filter: CaseFilter
    page: PageInput
    def __init__(self, access_token: _Optional[str] = ..., filter: _Optional[_Union[CaseFilter, _Mapping]] = ..., page: _Optional[_Union[PageInput, _Mapping]] = ...) -> None: ...

class SearchCasesResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: CasePage
    def __init__(self, data: _Optional[_Union[CasePage, _Mapping]] = ...) -> None: ...

class GetCaseRequest(_message.Message):
    __slots__ = ("access_token", "id")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    id: str
    def __init__(self, access_token: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class GetCaseResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: ReviewCase
    def __init__(self, data: _Optional[_Union[ReviewCase, _Mapping]] = ...) -> None: ...

class GetReportRequest(_message.Message):
    __slots__ = ("access_token", "id")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    id: str
    def __init__(self, access_token: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class GetReportResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: Report
    def __init__(self, data: _Optional[_Union[Report, _Mapping]] = ...) -> None: ...

class ExportReportRequest(_message.Message):
    __slots__ = ("access_token", "id")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    id: str
    def __init__(self, access_token: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class ExportReportResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: ReportExport
    def __init__(self, data: _Optional[_Union[ReportExport, _Mapping]] = ...) -> None: ...

class ListNotificationsRequest(_message.Message):
    __slots__ = ("access_token", "unread_only", "page")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    UNREAD_ONLY_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    unread_only: bool
    page: PageInput
    def __init__(self, access_token: _Optional[str] = ..., unread_only: _Optional[bool] = ..., page: _Optional[_Union[PageInput, _Mapping]] = ...) -> None: ...

class ListNotificationsResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: NotificationPage
    def __init__(self, data: _Optional[_Union[NotificationPage, _Mapping]] = ...) -> None: ...

class LoginRequest(_message.Message):
    __slots__ = ("access_token", "login", "password")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    LOGIN_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    login: str
    password: str
    def __init__(self, access_token: _Optional[str] = ..., login: _Optional[str] = ..., password: _Optional[str] = ...) -> None: ...

class LoginResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: AuthPayload
    def __init__(self, data: _Optional[_Union[AuthPayload, _Mapping]] = ...) -> None: ...

class LogoutRequest(_message.Message):
    __slots__ = ("access_token",)
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    def __init__(self, access_token: _Optional[str] = ...) -> None: ...

class LogoutResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: bool
    def __init__(self, data: _Optional[bool] = ...) -> None: ...

class RegisterStaffRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: StaffInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[StaffInput, _Mapping]] = ...) -> None: ...

class RegisterStaffResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: User
    def __init__(self, data: _Optional[_Union[User, _Mapping]] = ...) -> None: ...

class BlockStaffRequest(_message.Message):
    __slots__ = ("access_token", "user_id", "idempotency_key")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    user_id: str
    idempotency_key: str
    def __init__(self, access_token: _Optional[str] = ..., user_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class BlockStaffResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: User
    def __init__(self, data: _Optional[_Union[User, _Mapping]] = ...) -> None: ...

class ChangeStaffRoleRequest(_message.Message):
    __slots__ = ("access_token", "user_id", "role_code", "idempotency_key")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_CODE_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    user_id: str
    role_code: str
    idempotency_key: str
    def __init__(self, access_token: _Optional[str] = ..., user_id: _Optional[str] = ..., role_code: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ChangeStaffRoleResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: User
    def __init__(self, data: _Optional[_Union[User, _Mapping]] = ...) -> None: ...

class CreateClientRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: ClientInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[ClientInput, _Mapping]] = ...) -> None: ...

class CreateClientResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: Client
    def __init__(self, data: _Optional[_Union[Client, _Mapping]] = ...) -> None: ...

class OpenAccountRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: AccountInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[AccountInput, _Mapping]] = ...) -> None: ...

class OpenAccountResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: Account
    def __init__(self, data: _Optional[_Union[Account, _Mapping]] = ...) -> None: ...

class RegisterCounterpartyRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: CounterpartyInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[CounterpartyInput, _Mapping]] = ...) -> None: ...

class RegisterCounterpartyResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: Counterparty
    def __init__(self, data: _Optional[_Union[Counterparty, _Mapping]] = ...) -> None: ...

class ImportTransactionsRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: ImportBatchInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[ImportBatchInput, _Mapping]] = ...) -> None: ...

class ImportTransactionsResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: ImportResult
    def __init__(self, data: _Optional[_Union[ImportResult, _Mapping]] = ...) -> None: ...

class RegisterTransactionRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: TransactionInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[TransactionInput, _Mapping]] = ...) -> None: ...

class RegisterTransactionResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: Transaction
    def __init__(self, data: _Optional[_Union[Transaction, _Mapping]] = ...) -> None: ...

class StartProfileRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: ProfileInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[ProfileInput, _Mapping]] = ...) -> None: ...

class StartProfileResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: AnalysisRun
    def __init__(self, data: _Optional[_Union[AnalysisRun, _Mapping]] = ...) -> None: ...

class StartAnalysisRequest(_message.Message):
    __slots__ = ("access_token", "transaction_id", "idempotency_key")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    TRANSACTION_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    transaction_id: str
    idempotency_key: str
    def __init__(self, access_token: _Optional[str] = ..., transaction_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class StartAnalysisResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: AnalysisRun
    def __init__(self, data: _Optional[_Union[AnalysisRun, _Mapping]] = ...) -> None: ...

class PublishRuleVersionRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: RuleVersionInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[RuleVersionInput, _Mapping]] = ...) -> None: ...

class PublishRuleVersionResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: RuleVersion
    def __init__(self, data: _Optional[_Union[RuleVersion, _Mapping]] = ...) -> None: ...

class ReanalyzePeriodRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: ReanalysisInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[ReanalysisInput, _Mapping]] = ...) -> None: ...

class ReanalyzePeriodResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: AnalysisRun
    def __init__(self, data: _Optional[_Union[AnalysisRun, _Mapping]] = ...) -> None: ...

class DistributeCasesRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: DistributionInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[DistributionInput, _Mapping]] = ...) -> None: ...

class DistributeCasesResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: DistributionResult
    def __init__(self, data: _Optional[_Union[DistributionResult, _Mapping]] = ...) -> None: ...

class TakeCaseRequest(_message.Message):
    __slots__ = ("access_token", "case_id", "expected_version", "idempotency_key")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_VERSION_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    case_id: str
    expected_version: int
    idempotency_key: str
    def __init__(self, access_token: _Optional[str] = ..., case_id: _Optional[str] = ..., expected_version: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class TakeCaseResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: ReviewCase
    def __init__(self, data: _Optional[_Union[ReviewCase, _Mapping]] = ...) -> None: ...

class AddCaseCommentRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: CaseCommentInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[CaseCommentInput, _Mapping]] = ...) -> None: ...

class AddCaseCommentResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: ReviewCase
    def __init__(self, data: _Optional[_Union[ReviewCase, _Mapping]] = ...) -> None: ...

class EscalateCaseRequest(_message.Message):
    __slots__ = ("access_token", "case_id", "reason", "expected_version", "idempotency_key")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_VERSION_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    case_id: str
    reason: str
    expected_version: int
    idempotency_key: str
    def __init__(self, access_token: _Optional[str] = ..., case_id: _Optional[str] = ..., reason: _Optional[str] = ..., expected_version: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class EscalateCaseResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: ReviewCase
    def __init__(self, data: _Optional[_Union[ReviewCase, _Mapping]] = ...) -> None: ...

class CloseCaseRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: CaseDecisionInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[CaseDecisionInput, _Mapping]] = ...) -> None: ...

class CloseCaseResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: ReviewCase
    def __init__(self, data: _Optional[_Union[ReviewCase, _Mapping]] = ...) -> None: ...

class ReopenCaseRequest(_message.Message):
    __slots__ = ("access_token", "case_id", "reason", "expected_version", "idempotency_key")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CASE_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_VERSION_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    case_id: str
    reason: str
    expected_version: int
    idempotency_key: str
    def __init__(self, access_token: _Optional[str] = ..., case_id: _Optional[str] = ..., reason: _Optional[str] = ..., expected_version: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ReopenCaseResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: ReviewCase
    def __init__(self, data: _Optional[_Union[ReviewCase, _Mapping]] = ...) -> None: ...

class CreateReportRequest(_message.Message):
    __slots__ = ("access_token", "input")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    input: ReportInput
    def __init__(self, access_token: _Optional[str] = ..., input: _Optional[_Union[ReportInput, _Mapping]] = ...) -> None: ...

class CreateReportResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: Report
    def __init__(self, data: _Optional[_Union[Report, _Mapping]] = ...) -> None: ...

class MarkNotificationReadRequest(_message.Message):
    __slots__ = ("access_token", "id")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    id: str
    def __init__(self, access_token: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class MarkNotificationReadResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: Notification
    def __init__(self, data: _Optional[_Union[Notification, _Mapping]] = ...) -> None: ...

class ValidateSessionRequest(_message.Message):
    __slots__ = ("access_token",)
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    def __init__(self, access_token: _Optional[str] = ...) -> None: ...

class ValidateSessionResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: User
    def __init__(self, data: _Optional[_Union[User, _Mapping]] = ...) -> None: ...

class ListActiveAnalystsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListActiveAnalystsResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: _containers.RepeatedCompositeFieldContainer[User]
    def __init__(self, data: _Optional[_Iterable[_Union[User, _Mapping]]] = ...) -> None: ...

class GetHistoryRequest(_message.Message):
    __slots__ = ("client_id", "currency", "direction", "window_start", "before_at", "before_id", "ingested_before", "page_token", "limit")
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    CURRENCY_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    WINDOW_START_FIELD_NUMBER: _ClassVar[int]
    BEFORE_AT_FIELD_NUMBER: _ClassVar[int]
    BEFORE_ID_FIELD_NUMBER: _ClassVar[int]
    INGESTED_BEFORE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    client_id: str
    currency: str
    direction: str
    window_start: _timestamp_pb2.Timestamp
    before_at: _timestamp_pb2.Timestamp
    before_id: str
    ingested_before: _timestamp_pb2.Timestamp
    page_token: str
    limit: int
    def __init__(self, client_id: _Optional[str] = ..., currency: _Optional[str] = ..., direction: _Optional[str] = ..., window_start: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., before_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., before_id: _Optional[str] = ..., ingested_before: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., page_token: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class GetHistoryResponse(_message.Message):
    __slots__ = ("items", "next_page_token")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[Transaction]
    next_page_token: str
    def __init__(self, items: _Optional[_Iterable[_Union[Transaction, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class HealthRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HealthResponse(_message.Message):
    __slots__ = ("service", "database", "table_count", "status")
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    DATABASE_FIELD_NUMBER: _ClassVar[int]
    TABLE_COUNT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    service: str
    database: str
    table_count: int
    status: str
    def __init__(self, service: _Optional[str] = ..., database: _Optional[str] = ..., table_count: _Optional[int] = ..., status: _Optional[str] = ...) -> None: ...
