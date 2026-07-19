from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from talk_to_your_stock_shared.enums import (
    AnalysisPeriod,
    DependencyStatus,
    ErrorCode,
    MessageRole,
    MessageStatus,
    PeerSelectionMode,
    ReadinessState,
    RunStatus,
    ServiceName,
    ServiceStatus,
    TraceOutputField,
)

Ticker = Annotated[str, Field(pattern=r"^[A-Z.]{1,10}$")]
TickerCandidate = Annotated[str, Field(pattern=r"^[A-Za-z.]{1,10}$")]
MAX_EXPLICIT_PEER_TICKERS = 10
Currency = str


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(ContractModel):
    status: ServiceStatus
    service: ServiceName
    time: datetime


class ReadinessCheck(ContractModel):
    status: DependencyStatus
    message: str | None = None


class ReadinessResponse(ContractModel):
    status: ReadinessState
    service: ServiceName
    checks: dict[str, ReadinessCheck]
    time: datetime


class ErrorDetail(ContractModel):
    code: ErrorCode
    message: str
    details: dict[str, object] | None = None
    request_id: str | None = None


class ErrorResponse(ContractModel):
    error: ErrorDetail


class PaginationMeta(ContractModel):
    has_more: bool
    next_cursor: str | None


class CreateThreadRequest(ContractModel):
    title: str = Field(min_length=1, max_length=120)


class User(ContractModel):
    id: UUID
    email: str
    name: str | None = None
    avatar_url: str | None = None
    created_at: datetime
    updated_at: datetime


class Thread(ContractModel):
    id: UUID
    user_id: UUID
    title: str = Field(max_length=120)
    message_count: int = Field(ge=0)
    last_message_at: datetime | None = None
    latest_run_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class ThreadResponse(ContractModel):
    thread: Thread


class ThreadListResponse(ContractModel):
    threads: list[Thread]
    page: PaginationMeta


class CreateMessageRequest(ContractModel):
    content: str = Field(min_length=1, max_length=5000)


class Message(ContractModel):
    id: UUID
    thread_id: UUID
    role: MessageRole
    content: str = Field(min_length=1)
    status: MessageStatus
    run_id: UUID | None = None
    created_at: datetime


class MessageListResponse(ContractModel):
    messages: list[Message]
    page: PaginationMeta


class Run(ContractModel):
    id: UUID
    thread_id: UUID
    trigger_message_id: UUID
    status: RunStatus
    target_ticker: Ticker
    peer_tickers: list[Ticker] = Field(min_length=1)
    currency: Currency = Field(min_length=3, max_length=3)
    as_of: datetime
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RunResponse(ContractModel):
    run: Run


class CompsRow(ContractModel):
    ticker: Ticker
    company_name: str | None = None
    is_target: bool
    currency: Currency = Field(min_length=3, max_length=3)
    share_price: float | None = None
    shares_outstanding: float | None = None
    market_cap: float | None = None
    cash: float | None = None
    total_debt: float | None = None
    net_debt: float | None = None
    enterprise_value: float | None = None
    revenue_ltm: float | None = None
    ebit_ltm: float | None = None
    ebitda_ltm: float | None = None
    net_income_ltm: float | None = None
    ev_to_revenue: float | None = None
    ev_to_ebit: float | None = None
    ev_to_ebitda: float | None = None
    pe: float | None = None
    as_of: datetime


class MinMedianMax(ContractModel):
    min: float | None
    median: float | None
    max: float | None


class RunTableSummaryStats(ContractModel):
    ev_to_revenue: MinMedianMax
    ev_to_ebit: MinMedianMax
    ev_to_ebitda: MinMedianMax
    pe: MinMedianMax


class RunTableSummary(ContractModel):
    stats: RunTableSummaryStats


class RunTableResponse(ContractModel):
    run_id: UUID
    target_ticker: Ticker
    currency: Currency = Field(min_length=3, max_length=3)
    as_of: datetime
    rows: list[CompsRow]
    summary: RunTableSummary


class TraceInput(ContractModel):
    field: str
    value: float | str | None
    source: str
    as_of: datetime


class TraceFormula(ContractModel):
    ticker: Ticker
    output_field: TraceOutputField
    expression: str
    output_value: float | None
    inputs: list[TraceInput]


class TraceResponse(ContractModel):
    run_id: UUID
    formulas: list[TraceFormula]


class GenerateCompsToolRequest(ContractModel):
    invocation_id: UUID
    thread_id: UUID
    trigger_message_id: UUID
    target_ticker: TickerCandidate
    peer_tickers: list[TickerCandidate] = Field(
        default_factory=list,
        max_length=MAX_EXPLICIT_PEER_TICKERS,
    )
    peer_selection_mode: PeerSelectionMode
    analysis_period: AnalysisPeriod
    currency: Currency = Field(default="USD", min_length=3, max_length=3)

    @model_validator(mode="after")
    def require_peers_for_user_supplied_mode(self) -> GenerateCompsToolRequest:
        # Future auto mode should allow empty peers and route to peer selection.
        if (
            self.peer_selection_mode == PeerSelectionMode.USER_SUPPLIED
            and not self.peer_tickers
        ):
            raise ValueError(
                "peer_tickers is required when peer_selection_mode is user_supplied"
            )
        return self


class GenerateCompsToolResponse(ContractModel):
    run: Run
    table: RunTableResponse
    trace: TraceResponse
    warnings: list[str] = Field(default_factory=list)


class AgentMessageRequest(ContractModel):
    user_id: UUID
    thread_id: UUID
    user_message_id: UUID
    content: str = Field(min_length=1)


class AgentMessageResponse(ContractModel):
    content: str = Field(min_length=1)
    run: Run | None = None


class CreateMessageResponse(ContractModel):
    user_message: Message
    assistant_message: Message
    run: Run | None = None
    events_url: str | None = None
