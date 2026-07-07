from __future__ import annotations

from enum import Enum


class ServiceName(str, Enum):
    WEB_BFF = "web-bff"
    AGENT_SERVICE = "agent-service"
    COMPS_SERVICE = "comps-service"


class ServiceStatus(str, Enum):
    OK = "ok"


class ReadinessState(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"


class DependencyStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAIL = "fail"


class ErrorCode(str, Enum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class MessageStatus(str, Enum):
    COMPLETE = "complete"
    STREAMING = "streaming"
    FAILED = "failed"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PeerSelectionMode(str, Enum):
    USER_SUPPLIED = "user_supplied"


class AnalysisPeriod(str, Enum):
    LATEST = "latest"


class TraceOutputField(str, Enum):
    EQUITY_VALUE = "equity_value"
    ENTERPRISE_VALUE = "enterprise_value"
    NET_DEBT = "net_debt"
    EV_TO_REVENUE = "ev_to_revenue"
    EV_TO_EBIT = "ev_to_ebit"
    EV_TO_EBITDA = "ev_to_ebitda"
    PE = "pe"


class EventType(str, Enum):
    MESSAGE_CREATED = "message.created"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    RUN_STATUS = "run.status"
    RUN_PROGRESS = "run.progress"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    ERROR = "error"
