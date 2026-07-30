from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class InternalArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class NormalizedCompanyInput(InternalArtifactModel):
    ticker: str
    company_name: str | None
    currency: str
    share_price: float
    shares_outstanding: float
    cash: float
    total_debt: float
    revenue_ltm: float | None
    ebit_ltm: float | None
    ebitda_ltm: float | None
    net_income_ltm: float | None
    as_of: datetime
    sources: dict[str, str]
    source_as_of: dict[str, datetime]


class SourceSnapshot(InternalArtifactModel):
    run_id: UUID
    raw_provider_evidence: dict[str, object]
    normalized_inputs: list[NormalizedCompanyInput]
    created_at: datetime
