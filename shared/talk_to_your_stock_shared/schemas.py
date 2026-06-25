from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from talk_to_your_stock_shared.enums import (
    AnalysisPeriod,
    DependencyStatus,
    MessageRole,
    MessageStatus,
    PeerSelectionMode,
    ReadinessState,
    RunStatus,
    ServiceName,
    ServiceStatus,
)

Ticker = str
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
    code: str
    message: str
    details: dict[str, object] | None = None
    request_id: str | None = None


class ErrorResponse(ContractModel):
    error: ErrorDetail


class PaginationMeta(ContractModel):
    has_more: bool
    next_cursor: str | None


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


class Message(ContractModel):
    id: UUID
    thread_id: UUID
    role: MessageRole
    content: str = Field(min_length=1)
    status: MessageStatus
    run_id: UUID | None = None
    created_at: datetime


class Run(ContractModel):
    id: UUID
    thread_id: UUID
    trigger_message_id: UUID
    status: RunStatus
    target_ticker: Ticker = Field(pattern=r"^[A-Z.]{1,10}$")
    peer_tickers: list[Ticker] = Field(default_factory=list)
    currency: Currency = Field(min_length=3, max_length=3)
    as_of: datetime
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class CompsRow(ContractModel):
    ticker: Ticker = Field(pattern=r"^[A-Z.]{1,10}$")
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
    target_ticker: Ticker = Field(pattern=r"^[A-Z.]{1,10}$")
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
    ticker: Ticker = Field(pattern=r"^[A-Z.]{1,10}$")
    output_field: str
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
    target_ticker: Ticker = Field(pattern=r"^[A-Z.]{1,10}$")
    peer_tickers: list[Ticker] = Field(default_factory=list)
    peer_selection_mode: PeerSelectionMode
    analysis_period: AnalysisPeriod = AnalysisPeriod.LATEST
    currency: Currency = Field(default="USD", min_length=3, max_length=3)
    as_of_date: date | None = None


class GenerateCompsToolResponse(ContractModel):
    run: Run
    table: RunTableResponse
    trace: TraceResponse
    warnings: list[str] = Field(default_factory=list)
