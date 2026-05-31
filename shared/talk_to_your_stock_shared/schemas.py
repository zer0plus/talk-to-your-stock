from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .enums import MessageRole, MessageStatus, RunStatus


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class Page(BaseModel):
    has_more: bool = False
    next_cursor: str | None = None


class User(BaseModel):
    id: UUID
    email: EmailStr
    name: str | None = None
    avatar_url: str | None = None
    created_at: datetime
    updated_at: datetime


class Thread(BaseModel):
    id: UUID
    user_id: UUID
    title: str
    message_count: int = 0
    last_message_at: datetime | None = None
    latest_run_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    id: UUID
    thread_id: UUID
    role: MessageRole
    content: str
    status: MessageStatus = MessageStatus.COMPLETE
    run_id: UUID | None = None
    created_at: datetime


class Run(BaseModel):
    id: UUID
    thread_id: UUID
    trigger_message_id: UUID
    status: RunStatus
    target_ticker: str
    peer_tickers: list[str]
    currency: str = "USD"
    as_of: datetime
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class MinMedianMax(BaseModel):
    min: float | None
    median: float | None
    max: float | None


class CompsRow(BaseModel):
    ticker: str
    company_name: str | None = None
    is_target: bool
    currency: str = "USD"
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


class CompsSummary(BaseModel):
    stats: dict[str, MinMedianMax]


class CompsTable(BaseModel):
    run_id: UUID
    target_ticker: str
    currency: str = "USD"
    as_of: datetime
    rows: list[CompsRow]
    summary: CompsSummary


class TraceInput(BaseModel):
    field: str
    value: float | str | None
    source: str
    as_of: datetime


class TraceFormula(BaseModel):
    ticker: str
    output_field: str
    expression: str
    output_value: float | None
    inputs: list[TraceInput]


class TraceResponse(BaseModel):
    run_id: UUID
    formulas: list[TraceFormula]


class GenerateCompsToolRequest(BaseModel):
    invocation_id: UUID
    thread_id: UUID
    trigger_message_id: UUID
    target_ticker: str
    peer_tickers: list[str] = Field(min_length=1)
    currency: str = "USD"
    as_of_date: str | None = None


class GenerateCompsToolResponse(BaseModel):
    run: Run
    table: CompsTable
    trace: TraceResponse


class AgentRequest(BaseModel):
    thread_id: UUID
    trigger_message_id: UUID
    content: str
    history: list[Message] = Field(default_factory=list)


class AgentResponse(BaseModel):
    assistant_content: str
    run: Run | None = None
    table: CompsTable | None = None


class Readiness(BaseModel):
    status: str
    checks: dict[str, str]
    time: datetime


class ModelDumpMixin(BaseModel):
    model_config = ConfigDict(from_attributes=True)
