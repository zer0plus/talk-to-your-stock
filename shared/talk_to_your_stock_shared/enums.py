from __future__ import annotations

from enum import StrEnum


class ServiceName(StrEnum):
    WEB_BFF = "web-bff"
    AGENT_SERVICE = "agent-service"
    COMPS_SERVICE = "comps-service"


class ServiceStatus(StrEnum):
    OK = "ok"


class ReadinessState(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"


class DependencyStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    FAIL = "fail"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class MessageStatus(StrEnum):
    COMPLETE = "complete"
    STREAMING = "streaming"
    FAILED = "failed"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PeerSelectionMode(StrEnum):
    USER_SUPPLIED = "user_supplied"
    AUTO = "auto"


class AnalysisPeriod(StrEnum):
    LATEST = "latest"


class EventType(StrEnum):
    MESSAGE_CREATED = "message.created"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    RUN_STATUS = "run.status"
    RUN_PROGRESS = "run.progress"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    ERROR = "error"
